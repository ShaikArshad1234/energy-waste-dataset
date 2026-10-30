"""Deterministic generator: master data -> events -> energy readings -> labelled anomalies -> ground truth.

Everything that is "derived" (daily totals, intensity, cost, recommendations) is computed from the generated source
records by energygen.derive / energygen.recommend, never drawn independently.
"""
import json
import math
from datetime import date, timedelta

from energygen import derive, recommend
from energygen.params import PLANT_DEFS, Params
from energygen.rng import Rng
from energygen.schema import GROUND_TRUTH_COLUMNS, ISSUE_TYPES, LOAD_ORDER
from energygen.tariff import TariffIndex
from energygen.timeutil import compact, date_to_days, days_to_date, iso

S_STANDBY, S_IDLE, S_RUN, S_MAINT = 0, 1, 2, 3
STATE_NAMES = ["STANDBY", "IDLE", "RUN", "MAINTENANCE"]
PRODUCTS = {"PRD-A": 120, "PRD-B": 200, "PRD-C": 80, "PRD-D": 340, "PRD-E": 150}   # units per hour
MACHINE_TYPES = [  # name, rated kW range, idle fraction, standby kW, weather sensitivity %/degC
    ("CNC_MILL", (35, 60), 0.10, 0.6, 0.2),
    ("INJECTION_MOLDER", (60, 95), 0.14, 0.8, 0.3),
    ("AIR_COMPRESSOR", (45, 75), 0.22, 0.8, 1.2),
    ("INDUSTRIAL_OVEN", (90, 140), 0.25, 1.0, 0.8),
    ("HYDRAULIC_PRESS", (70, 110), 0.09, 0.7, 0.2),
    ("CONVEYOR_LINE", (10, 22), 0.12, 0.3, 0.1),
    ("CHILLER", (50, 85), 0.18, 0.9, 2.0),
]
# local-time windows (start, end, name) -- tile the day exactly
WEEKDAY_WINDOWS = [("00:00", "06:00", "OFF_PEAK"), ("06:00", "09:00", "SHOULDER"), ("09:00", "12:00", "PEAK"),
                   ("12:00", "17:00", "SHOULDER"), ("17:00", "22:00", "PEAK"), ("22:00", "24:00", "OFF_PEAK")]
WEEKEND_WINDOWS = [("00:00", "08:00", "OFF_PEAK"), ("08:00", "22:00", "SHOULDER"), ("22:00", "24:00", "OFF_PEAK")]
RATE_IDX = {"OFF_PEAK": 0, "SHOULDER": 1, "PEAK": 2}
STACK_START_Q, STACK_WINDOW = 40, (38, 60)    # 10:00 local; baseline excludes 09:30-15:00 on stacking days


def staffed_at(q, wd):
    """Shift calendar rule. A 06-14 Mon-Sat, B 14-22 Mon-Fri, C 22-06 starts Mon-Wed nights."""
    if 24 <= q < 56:
        return wd <= 5
    if 56 <= q < 88:
        return wd <= 4
    if q >= 88:
        return wd <= 2
    return ((wd - 1) % 7) <= 2


def _runs(indexes):
    """Contiguous runs of sorted ints -> [(first, last)]."""
    out, start, prev = [], None, None
    for i in indexes:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append((start, prev))
            start = prev = i
    if start is not None:
        out.append((start, prev))
    return out


class _Plant:
    pass


def _simulate_plant(p: Params, idx: int, n_meters: int) -> _Plant:
    P = _Plant()
    pd = PLANT_DEFS[idx % len(PLANT_DEFS)]
    P.idx, P.pid = idx, f"P{idx + 1:02d}"
    P.name, P.country, P.tz, P.off, P.cur, P.rates, P.demand_charge, base_temp = pd
    if idx >= len(PLANT_DEFS):
        P.name = f"{P.name} {idx + 1}"
    P.n_int = n_int = p.days * 96
    P.start_day = start_day = date_to_days(date.fromisoformat(p.start_date))
    P.t0 = t0 = start_day * 86400 - P.off * 60
    n_sub = n_meters - 1
    P.n_sub = n_sub

    # ---------------- weather (hourly, UTC hour boundaries) ----------------
    rw = Rng(p.seed, f"{P.pid}:weather")
    P.base_hour = base_hour = (t0 // 3600) * 3600
    n_obs = (n_int * 900 + (t0 - base_hour) + 3599) // 3600 + 1
    P.weather, ar = [], 0.0
    for h in range(n_obs):
        ts = base_hour + h * 3600
        local_hour = ((ts + P.off * 60) % 86400) / 3600
        ar = 0.9 * ar + rw.gauss(0, 0.5)
        temp = base_temp + 5.0 * math.sin(2 * math.pi * (local_hour - 9) / 24) + 0.03 * ((ts - t0) / 86400) + ar
        hum = min(98.0, max(15.0, 60 - 1.6 * (temp - base_temp) + rw.gauss(0, 3)))
        P.weather.append((ts, round(temp, 1), round(hum, 1)))
    P.temp_k = [P.weather[(t0 + k * 900 - base_hour) // 3600][1] for k in range(n_int)]

    # ---------------- shift calendar -> staffed flag per interval ----------------
    staffed = [False] * n_int
    for k in range(n_int):
        staffed[k] = staffed_at(k % 96, (start_day + k // 96 + 3) % 7)
    next_staffed = [n_int] * (n_int + 1)   # first staffed index >= k
    run_end = [n_int] * (n_int + 1)        # end (exclusive) of the staffed run containing k
    for k in range(n_int - 1, -1, -1):
        next_staffed[k] = k if staffed[k] else next_staffed[k + 1]
        if staffed[k]:
            run_end[k] = run_end[k + 1] if (k + 1 < n_int and staffed[k + 1]) else k + 1
        else:
            run_end[k] = k
    P.staffed = staffed

    # ---------------- machines ----------------
    rm = Rng(p.seed, f"{P.pid}:machines")
    machines = []
    for i in range(n_sub):
        mt = MACHINE_TYPES[(i + idx * 3) % len(MACHINE_TYPES)]
        rated = float(rm.randint(mt[1][0], mt[1][1]))
        machines.append({
            "id": f"MCH-{P.pid}-{i + 1:03d}", "meter_id": f"MTR-{P.pid}-{i + 2:03d}", "type": mt[0], "rated": rated,
            "idle": round(rated * mt[2], 1), "standby": mt[3], "wpct": mt[4],
            "lf": {prod: round(rm.uniform(0.55, 0.9), 3) for prod in PRODUCTS},
            "commissioned": days_to_date(start_day - rm.randint(300, 3000)).isoformat()})
    P.machines = machines
    mids = [m["id"] for m in machines]
    mby = {m["id"]: m for m in machines}
    shuffled = list(mids)
    rm.shuffle(shuffled)
    n_degr = max(1, round(n_sub * 0.22))
    n_idle = max(1, round(n_sub * 0.22))
    degr_ids = shuffled[:n_degr]
    idle_ids = shuffled[n_degr:n_degr + n_idle]

    # ---------------- scenarios & maintenance ----------------
    rs = Rng(p.seed, f"{P.pid}:scenarios")
    bd = max(4, p.days // 6)                     # baseline period length (days): no scenario starts before it ends
    P.baseline_days = bd
    busy = {m: [] for m in mids}
    P.maint = []                                  # dict(machine, type, reason, events[(status, ts)])
    P.degr = {}
    for mid in degr_ids:
        d1 = bd + 3 + int(rs.random() * max(1, p.days * 0.25))
        lag = max(3, int(p.days * rs.uniform(0.12, 0.28)))
        mday = min(p.days - 3, d1 + lag)
        if mday - d1 < 3:
            d1 = max(bd + 1, mday - 3)
        m_start = mday * 96 + 32
        m_end = m_start + rs.randint(24, 40)
        mult = round(rs.uniform(1.18, 1.40), 3)
        P.degr[mid] = {"start_k": d1 * 96, "m_start": m_start, "m_end": m_end, "mult": mult}
        busy[mid].append((m_start, m_end))
        P.maint.append({"machine": mid, "type": "CORRECTIVE", "reason": "CORRECTIVE_REPAIR", "events": [
            ("SCHEDULED", max(t0, t0 + m_start * 900 - 86400)), ("IN_PROGRESS", t0 + m_start * 900),
            ("COMPLETED", t0 + m_end * 900)]})
    P.idle_on = {}
    for n_i, mid in enumerate(idle_ids):
        s_day = bd + int(rs.random() * max(1, p.days * 0.2))
        if n_i % 2 == 1:                          # every second idle machine is never fixed (ongoing at horizon end)
            e_day = p.days
        else:
            e_day = min(p.days - max(2, p.days // 9), s_day + max(5, int(p.days * rs.uniform(0.2, 0.35))))
        if e_day - s_day < 5:
            s_day = max(1, e_day - 5)
        P.idle_on[mid] = (s_day * 96, e_day * 96)
    if p.days >= 21:
        for mid in mids:
            for _ in range(8):
                day = rs.randint(bd + 1, p.days - 4)
                s = day * 96 + 88
                e = s + rs.randint(16, 32)
                if e < n_int and not any(a < e and b > s for a, b in busy[mid]):
                    inspect = rs.chance(0.2)
                    ev = [("SCHEDULED", max(t0, t0 + s * 900 - 3 * 86400))]
                    if rs.chance(0.12):
                        ev.append(("CANCELLED", t0 + s * 900))
                    else:
                        busy[mid].append((s, e))
                        ev += [("IN_PROGRESS", t0 + s * 900), ("COMPLETED", t0 + e * 900)]
                    P.maint.append({"machine": mid, "type": "INSPECTION" if inspect else "PREVENTIVE",
                                    "reason": "INSPECTION_REQUEST" if inspect else "ROUTINE", "events": ev})
                    break
    maint_k = {m: [(a, b) for a, b in busy[m]] for m in mids}

    # ---------------- batches ----------------
    rb = Rng(p.seed, f"{P.pid}:batches")
    n_b = p.batches // p.plants + (1 if idx < p.batches % p.plants else 0)
    batches = []

    def find_slot(ms, desired, dur):
        t = desired
        for _ in range(4000):
            if t + dur > n_int:
                return None
            if not staffed[t]:
                t = next_staffed[t]
                continue
            if run_end[t] < t + dur:
                t = next_staffed[min(run_end[t], n_int)]
                continue
            conflict = -1
            for m in ms:
                for a, b in busy[m]:
                    if a < t + dur and b > t and b > conflict:
                        conflict = b
            if conflict < 0:
                return t
            t = conflict
        return None

    def place(ms, desired, dur, stack=False):
        t = find_slot(ms, desired, dur)
        if t is None:
            return False
        product = rb.weighted(list(PRODUCTS), [3, 3, 2, 1.5, 2])
        planned_h = dur / 4.0
        planned_units = int(round(PRODUCTS[product] * planned_h))
        degraded_now = any(m in P.degr and P.degr[m]["start_k"] <= t < P.degr[m]["m_end"] for m in ms)
        aborted = rb.chance(0.01 + (0.12 if degraded_now else 0.0))
        actual = dur
        if aborted:
            actual = max(4, int(dur * rb.uniform(0.3, 0.8)))
        yield_ = rb.uniform(0.93, 0.995)
        produced = int(PRODUCTS[product] * (actual / 4.0) * yield_)
        produced = max(1, min(produced, planned_units))
        for m in ms:
            busy[m].append((t, t + dur))
        batches.append({"plant": P, "machines": list(ms), "start_k": t, "end_k": t + actual, "product": product,
                        "planned_units": planned_units, "produced_units": produced,
                        "status": "ABORTED" if aborted else "COMPLETED", "stack": stack})
        return True

    P.stack_days = []
    eligible = [d for d in range(1, p.days - 1) if (start_day + d + 3) % 7 <= 3]
    n_stack = max(1, p.days // 15)
    rb.shuffle(eligible)
    P.stack_days = sorted(eligible[:n_stack])
    gsz = max(1, min(3, n_sub // 3 or 1))
    for day in P.stack_days:
        order = list(mids)
        rb.shuffle(order)
        for g in range(max(1, n_sub // gsz)):
            ms = order[g * gsz:(g + 1) * gsz]
            if ms:
                place(ms, day * 96 + STACK_START_Q, rb.randint(12, 16), stack=True)
    attempts = 0
    while len(batches) < n_b and attempts < 120 * n_b:
        attempts += 1
        desired = rb.randint(0, n_int - 9)
        if (start_day + desired // 96 + 3) % 7 >= 5 and rb.chance(0.6):
            continue
        if attempts < 40 * n_b:
            k_m = min(n_sub, rb.weighted([1, 2, 3], [3, 4, 2]))
            dur = rb.randint(4, 24)                 # 1-6 hours
        else:                                       # crowded calendar: fall back to small jobs
            k_m, dur = 1, rb.randint(4, 8)
        place(rb.sample(mids, k_m), desired, dur)
    if len(batches) < n_b:
        raise ValueError(f"cannot schedule {n_b} batches for plant {P.pid} ({n_sub} machines, {p.days} days): "
                         f"placed {len(batches)}. Reduce --batches or increase --days/--meters.")
    P.batches = batches

    # ---------------- machine power ----------------
    P.sub_kw, P.state, P.run_obj, P.waste_flag, P.extra_kwh = {}, {}, {}, {}, {}
    run_obj = {m: [None] * n_int for m in mids}
    for b in batches:
        for m in b["machines"]:
            row = run_obj[m]
            for k in range(b["start_k"], b["end_k"]):
                row[k] = b
    for m in machines:
        mid = m["id"]
        rn = Rng(p.seed, f"{mid}:power")
        kw, st, wf = [0.0] * n_int, [0] * n_int, []
        maint_mask = bytearray(n_int)
        for a, b in maint_k[mid]:
            for k in range(a, min(b, n_int)):
                maint_mask[k] = 1
        dg = P.degr.get(mid)
        io = P.idle_on.get(mid)
        extra = 0.0
        for k in range(n_int):
            if maint_mask[k]:
                st[k], v = S_MAINT, 0.0
            else:
                b = run_obj[mid][k]
                if b is not None:
                    st[k] = S_RUN
                    v = m["rated"] * m["lf"][b["product"]]
                    v *= 1 + m["wpct"] / 100.0 * max(0.0, P.temp_k[k] - 22.0)
                    v *= max(0.9, rn.gauss(1.0, 0.025))
                    mult = dg["mult"] if dg and dg["start_k"] <= k < dg["m_end"] else 1.0
                    v *= mult
                    if k == b["start_k"]:
                        v *= 1.35
                    if mult != 1.0:
                        extra += round(v, 3) * 0.25 * (1 - 1 / mult)
                elif staffed[k]:
                    st[k], v = S_IDLE, m["idle"] * max(0.8, rn.gauss(1.0, 0.05))
                elif io and io[0] <= k < io[1]:
                    st[k], v = S_IDLE, m["idle"] * max(0.8, rn.gauss(1.0, 0.05))
                    wf.append(k)
                else:
                    st[k], v = S_STANDBY, m["standby"] * max(0.7, rn.gauss(1.0, 0.1))
            kw[k] = round(v, 3)
        P.sub_kw[mid], P.state[mid], P.waste_flag[mid], P.extra_kwh[mid] = kw, st, wf, extra
    P.run_obj = run_obj

    # ---------------- facility (unmetered) load + MAIN meter ----------------
    rf = Rng(p.seed, f"{P.pid}:facility")
    connected = sum(m["rated"] for m in machines)
    P.connected = connected
    P.facility = [0.0] * n_int
    P.main_kw = [0.0] * n_int
    for k in range(n_int):
        on = staffed[k]
        base = (15 + 0.04 * connected) if on else (6 + 0.015 * connected)
        hvac = 0.008 * connected * max(0.0, P.temp_k[k] - 22.0) * (1.0 if on else 0.4)
        f = (base + hvac) * max(0.9, rf.gauss(1.0, 0.02))
        P.facility[k] = f
        P.main_kw[k] = round(sum(P.sub_kw[m["id"]][k] for m in machines) + f, 3)
    stack_k = set()
    for d in P.stack_days:
        stack_k.update(range(d * 96 + STACK_WINDOW[0], d * 96 + STACK_WINDOW[1]))
    base_vals = sorted(P.main_kw[k] for k in range(n_int) if k not in stack_k)
    v = base_vals[max(0, int(0.997 * len(base_vals)) - 1)]
    P.contract_kw = float(math.ceil(v * 1.02 / 5.0) * 5)
    return P


def _tariff_rows(p, plants):
    """Two tariff versions per plant (rate revision at mid-horizon). Last plant's v2 carries the timezone defect."""
    rows = []
    cut = p.days // 2
    start = date.fromisoformat(p.start_date)
    last = plants[-1].pid
    for P in plants:
        for ver in (1, 2):
            scale = 1.0 if ver == 1 else 1.06
            eff_from = start if ver == 1 else start + timedelta(days=cut)
            eff_to = (start + timedelta(days=cut - 1)) if ver == 1 else None
            defect = (ver == 2 and P.pid == last)
            for day_type, windows in (("WEEKDAY", WEEKDAY_WINDOWS), ("WEEKEND", WEEKEND_WINDOWS)):
                for s, e, name in windows:
                    rows.append({
                        "plant_id": P.pid, "tariff_version": ver, "window_name": name, "day_type": day_type,
                        "start_local_time": s, "end_local_time": e,
                        "timezone_name": "UTC" if defect else P.tz, "utc_offset_minutes": 0 if defect else P.off,
                        "rate_per_kwh": round(P.rates[RATE_IDX[name]] * scale, 4),
                        "demand_charge_per_kw": round(P.demand_charge * scale, 2), "currency": P.cur,
                        "effective_from": eff_from.isoformat(), "effective_to": eff_to.isoformat() if eff_to else None,
                        "status": "SUPERSEDED" if ver == 1 else "ACTIVE", "_defect": defect})
    for i, r in enumerate(rows, 1):
        r["tariff_id"] = f"TAR-{start.year}-{i:03d}"
    return rows


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-0") else s
    return str(v)


def generate(p: Params):
    """-> (tables {name: [row dict of str]}, derived {name: [...]}, ground_truth [row dict of str])."""
    p.validate()
    per, extra = divmod(p.meters, p.plants)
    plants = [_simulate_plant(p, i, per + (1 if i < extra else 0)) for i in range(p.plants)]
    start_year = date.fromisoformat(p.start_date).year
    T = {n: [] for n in LOAD_ORDER}
    gt = []

    def add_gt(issue, entity_type, entity_id, plant, meter, machine, start, end, magnitude, unit, details):
        rule, decision = ISSUE_TYPES[issue]
        gt.append({"issue_type": issue, "entity_type": entity_type, "entity_id": entity_id, "plant_id": plant,
                   "meter_id": meter, "machine_id": machine, "start_ts_utc": start, "end_ts_utc": end,
                   "expected_decision": decision, "magnitude": magnitude, "magnitude_unit": unit,
                   "rule_id": rule, "details": json.dumps(details, sort_keys=True, separators=(",", ":"))})

    # ---------- master data ----------
    for P in plants:
        T["plant"].append({"plant_id": P.pid, "plant_name": P.name, "country_code": P.country, "timezone_name": P.tz,
                           "utc_offset_minutes": P.off, "currency": P.cur, "contract_demand_kw": P.contract_kw,
                           "status": "ACTIVE", "effective_from": days_to_date(P.start_day - 365).isoformat()})
        main_id = f"MTR-{P.pid}-001"
        T["meter"].append({"meter_id": main_id, "plant_id": P.pid, "meter_name": f"{P.name} main incomer",
                           "meter_type": "MAIN", "parent_meter_id": None, "energy_unit": "kWh", "demand_unit": "kW",
                           "sampling_interval_minutes": 15, "status": "ACTIVE",
                           "installed_date": days_to_date(P.start_day - 900).isoformat(),
                           "rated_capacity_kw": float(math.ceil(P.connected * 1.2 / 10) * 10)})
        for m in P.machines:
            T["meter"].append({"meter_id": m["meter_id"], "plant_id": P.pid, "meter_name": f"{m['type']} sub-meter",
                               "meter_type": "SUB", "parent_meter_id": main_id, "energy_unit": "kWh",
                               "demand_unit": "kW", "sampling_interval_minutes": 15, "status": "ACTIVE",
                               "installed_date": days_to_date(P.start_day - 600).isoformat(),
                               "rated_capacity_kw": float(math.ceil(m["rated"] * 1.25))})
            T["machine"].append({"machine_id": m["id"], "plant_id": P.pid, "machine_name": f"{m['type']} {m['id'][-3:]}",
                                 "machine_type": m["type"], "rated_kw": m["rated"], "idle_kw": m["idle"],
                                 "standby_kw": m["standby"], "weather_sensitivity_pct_per_c": m["wpct"],
                                 "status": "ACTIVE", "commissioned_date": m["commissioned"]})
            T["meter_machine_assignment"].append({
                "assignment_id": f"ASG-{m['id']}-01", "meter_id": m["meter_id"], "machine_id": m["id"],
                "effective_from": days_to_date(P.start_day - 600).isoformat(), "effective_to": None, "status": "ACTIVE"})

    # ---------- shifts ----------
    for P in plants:
        for d in range(-1, p.days):
            wd = (P.start_day + d + 3) % 7
            day_start = P.t0 + d * 86400
            for name, s, e, staffed in (("A", 6, 14, wd <= 5), ("B", 14, 22, wd <= 4), ("C", 22, 30, wd <= 2)):
                if d == -1 and name != "C":
                    continue
                T["shift"].append({"shift_id": f"SHF-{P.pid}-{days_to_date(P.start_day + d).strftime('%Y%m%d')}-{name}",
                                   "plant_id": P.pid, "shift_date": days_to_date(P.start_day + d).isoformat(),
                                   "shift_name": name, "start_ts_utc": iso(day_start + s * 3600),
                                   "end_ts_utc": iso(day_start + e * 3600), "staffed": "Y" if staffed else "N"})

    # ---------- tariffs (+ timezone defect label) ----------
    trows = _tariff_rows(p, plants)
    for r in trows:
        T["tariff"].append({k: v for k, v in r.items() if k != "_defect"})

    # ---------- weather ----------
    for P in plants:
        for ts, temp, hum in P.weather:
            T["weather_observation"].append({"weather_id": f"WX-{P.pid}-{compact(ts)[:8]}{compact(ts)[9:11]}", "plant_id": P.pid,
                                             "observation_ts_utc": iso(ts), "temperature_c": temp,
                                             "relative_humidity_pct": hum, "source": "SYNTHETIC"})

    # ---------- batches (global ids ordered by start time) ----------
    allb = [b for P in plants for b in P.batches]
    allb.sort(key=lambda b: (b["plant"].t0 + b["start_k"] * 900, b["plant"].pid, b["machines"][0]))
    bev = 0
    for n, b in enumerate(allb, 1):
        P = b["plant"]
        b["batch_id"] = f"BATCH-{days_to_date((P.t0 + b['start_k'] * 900) // 86400).year}-{n:06d}"
        s_ts, e_ts = P.t0 + b["start_k"] * 900, P.t0 + b["end_k"] * 900
        q, day = b["start_k"] % 96, P.start_day + b["start_k"] // 96
        if q < 24:
            sname, sday = "C", day - 1
        elif q < 56:
            sname, sday = "A", day
        elif q < 88:
            sname, sday = "B", day
        else:
            sname, sday = "C", day
        b["shift_id"] = f"SHF-{P.pid}-{days_to_date(sday).strftime('%Y%m%d')}-{sname}"
        T["production_batch"].append({
            "batch_id": b["batch_id"], "plant_id": P.pid, "product_code": b["product"], "shift_id": b["shift_id"],
            "planned_units": b["planned_units"], "produced_units": b["produced_units"], "start_ts_utc": iso(s_ts),
            "end_ts_utc": iso(e_ts), "status": b["status"]})
        for i, m in enumerate(b["machines"], 1):
            T["batch_machine"].append({"batch_machine_id": f"BM-{b['batch_id']}-{i:02d}", "batch_id": b["batch_id"],
                                       "machine_id": m, "sequence_no": i})
        plan_ts = s_ts - 86400
        for seq, (status, ts) in enumerate((("PLANNED", plan_ts), ("RUNNING", s_ts), (b["status"], e_ts)), 1):
            bev += 1
            T["batch_status_event"].append({"batch_event_id": f"BEV-{start_year}-{bev:06d}", "batch_id": b["batch_id"],
                                            "event_seq": seq, "status": status, "event_ts_utc": iso(ts)})

    # ---------- machine state history ----------
    ev = []
    for P in plants:
        for m in P.machines:
            mid, st, ro = m["id"], P.state[m["id"]], P.run_obj[m["id"]]
            keyf = lambda k: (st[k], ro[k]["batch_id"] if ro[k] is not None else None)  # noqa: E731
            k = 0
            while k < P.n_int:
                key = keyf(k)
                j = k + 1
                while j < P.n_int and keyf(j) == key:
                    j += 1
                ev.append((P.t0 + k * 900, mid, STATE_NAMES[key[0]], P.t0 + j * 900, key[1]))
                k = j
    ev.sort()
    for n, (s, mid, state, e, batch) in enumerate(ev, 1):
        T["machine_state_event"].append({"state_event_id": f"MSE-{start_year}-{n:06d}", "machine_id": mid, "state": state,
                                         "start_ts_utc": iso(s), "end_ts_utc": iso(e), "batch_id": batch})

    # ---------- maintenance history ----------
    wos = sorted(((min(ts for _, ts in w["events"]), w["machine"], i, w) for P in plants for i, w in enumerate(P.maint)),
                 key=lambda x: (x[0], x[1], x[2]))
    rows = []
    for n, (_, _, _, w) in enumerate(wos, 1):
        for seq, (status, ts) in enumerate(w["events"], 1):
            rows.append((ts, w["machine"], n, seq, status, w))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    for n, (ts, mid, wo, seq, status, w) in enumerate(rows, 1):
        T["maintenance_event"].append({
            "maintenance_event_id": f"MNT-{start_year}-{n:06d}", "work_order_id": f"WO-{start_year}-{wo:04d}",
            "machine_id": mid, "maintenance_type": w["type"], "event_seq": seq, "status": status,
            "event_ts_utc": iso(ts), "reason_code": w["reason"]})

    # ---------- energy readings: clean baseline first ----------
    ra = Rng(p.seed, "anomalies")
    series = []   # (plant, meter_id, machine_id|None, avg list, peak list)
    for P in plants:
        series.append((P, f"MTR-{P.pid}-001", None, P.main_kw))
        for m in P.machines:
            series.append((P, m["meter_id"], m["id"], P.sub_kw[m["id"]]))
    meter_series = {}
    for P, meter_id, machine_id, kw in series:
        rp = Rng(p.seed, f"{meter_id}:peak")
        avg, energy, peak = [], [], []
        for k, v in enumerate(kw):
            a = round(v, 3)
            avg.append(a)
            energy.append(round(a * 0.25, 3))
            peak.append(round(a * (1 + max(0.0, rp.gauss(0.07, 0.03))), 3) if a > 0 else 0.0)
        meter_series[meter_id] = (P, machine_id, avg, energy, peak)

    # gaps (meter outages): rows removed, energy still counted by the register
    gaps = {}
    for P, meter_id, machine_id, _ in series:
        taken = []
        for _ in range(ra.randint(1, 2) + p.days // 40):
            ln = ra.randint(2, 40)
            s = ra.randint(96, P.n_int - 100 - ln)
            if any(s < b + 2 and s + ln + 2 > a for a, b in taken):
                continue
            taken.append((s, s + ln))
        gaps[meter_id] = sorted(taken)
    gap_k = {m: set(k for a, b in g for k in range(a, b)) for m, g in gaps.items()}
    gap_days = {m: set(k // 96 for k in ks) for m, ks in gap_k.items()}

    # pick anomaly rows (distinct rows; never inside a gap)
    n_rows = p.meters * p.days * 96
    used = set()
    meters = [m for _, m, _, _ in series]
    sub_meters = [m for _, m, mc, _ in series if mc]

    def pick(n, pool, min_avg=0.0):
        out, tries = [], 0
        while len(out) < n and tries < n * 200:
            tries += 1
            m = ra.choice(pool)
            k = ra.randint(0, meter_series[m][0].n_int - 1)
            if (m, k) in used or k in gap_k[m] or meter_series[m][2][k] < min_avg:
                continue
            used.add((m, k))
            out.append((m, k))
        return sorted(out)

    n_dup = max(2, round(n_rows * 0.0004))
    n_unit = max(2, round(n_rows * 0.0002))
    n_hier = max(2, round(n_rows * 0.00015))
    n_mis = max(2, round(p.meters * p.days * 0.005))
    dup_rows = pick(n_dup, meters, 0.0)
    unit_rows = pick(n_unit, meters, 5.0)
    hier_rows = pick(n_hier, sub_meters, 0.0)
    all_machines = [m["id"] for P in plants for m in P.machines]
    conflicting = set(dup_rows[i] for i in range(len(dup_rows)) if i % 6 == 5)

    readings = []
    for meter_id in meters:
        P, machine_id, avg, energy, peak = meter_series[meter_id]
        for k in range(P.n_int):
            if k in gap_k[meter_id]:
                continue
            ts = P.t0 + k * 900
            readings.append((ts, meter_id, machine_id, k, avg[k], energy[k], peak[k]))
    readings.sort(key=lambda r: (r[0], r[1]))
    unit_set, hier_set, dup_set = set(unit_rows), set(hier_rows), set(dup_rows)
    out_rows, dup_out = [], []
    for ts, meter_id, machine_id, k, a, e, pk in readings:
        rid = f"ENG-{meter_id}-{compact(ts)}"
        row = {"reading_id": rid, "ingest_seq": 1, "meter_id": meter_id, "machine_id": machine_id,
               "interval_start_utc": iso(ts), "interval_minutes": 15, "energy_kwh": e, "avg_kw": a, "peak_kw": pk}
        key = (meter_id, k)
        P = meter_series[meter_id][0]
        if key in unit_set:        # kW value written into the kWh field
            row["energy_kwh"] = a
            add_gt("KW_KWH_CONFUSION", "energy_reading", rid, P.pid, meter_id, machine_id, iso(ts), iso(ts + 900),
                   round(a - e, 3), "kWh", {"recorded_energy_kwh": a, "true_energy_kwh": e, "avg_kw": a})
        if key in hier_set:        # energy attributed to a machine outside this meter's hierarchy
            wrong = ra.choice([m for m in all_machines if m != machine_id])
            row["machine_id"] = wrong
            add_gt("MACHINE_OUTSIDE_HIERARCHY", "energy_reading", rid, P.pid, meter_id, machine_id, iso(ts),
                   iso(ts + 900), e, "kWh", {"expected_machine_id": machine_id, "recorded_machine_id": wrong})
        out_rows.append(row)
        if key in dup_set:         # later re-delivery of the same interval
            d = dict(row)
            d["ingest_seq"] = 2
            conflict = key in conflicting
            if conflict:
                d["avg_kw"] = round(a * (1 + ra.uniform(0.03, 0.10)) + 0.01, 3)
                d["energy_kwh"] = round(d["avg_kw"] * 0.25, 3)
                d["peak_kw"] = round(max(pk, d["avg_kw"]), 3)
            dup_out.append(d)
            add_gt("READING_DUPLICATE", "energy_reading", rid, P.pid, meter_id, machine_id, iso(ts), iso(ts + 900), 1,
                   "rows", {"ingest_seq": 2, "conflicting_values": conflict})
    T["energy_reading"] = out_rows + dup_out

    # gaps -> ground truth
    for P, meter_id, machine_id, _ in series:
        en = meter_series[meter_id][3]
        for a, b in gaps[meter_id]:
            add_gt("METER_GAP", "meter", meter_id, P.pid, meter_id, machine_id, iso(P.t0 + a * 900), iso(P.t0 + b * 900),
                   round(sum(en[a:b]), 3), "kWh", {"missing_intervals": b - a})

    # daily register totals (+ labelled mismatches on days without gaps)
    cand = [(m, d) for m in meters for d in range(p.days) if d not in gap_days[m]]
    mism = set()
    while len(mism) < min(n_mis, len(cand)):
        mism.add(ra.choice(cand))
    for P, meter_id, machine_id, _ in series:
        en = meter_series[meter_id][3]
        for d in range(p.days):
            true = math.fsum(en[d * 96:(d + 1) * 96])
            reg = round(true, 3)
            tid = f"MDT-{meter_id}-{days_to_date(P.start_day + d).strftime('%Y%m%d')}"
            if (meter_id, d) in mism:
                f = 1 + (1 if ra.chance(0.5) else -1) * ra.uniform(0.04, 0.12)
                reg = round(true * f, 3)
                add_gt("METER_TOTAL_MISMATCH", "meter_daily_total", tid, P.pid, meter_id, machine_id,
                       iso(P.t0 + d * 86400), iso(P.t0 + (d + 1) * 86400), round(reg - true, 3), "kWh",
                       {"true_total_kwh": round(true, 3), "register_kwh": reg})
            T["meter_daily_total"].append({"total_id": tid, "meter_id": meter_id,
                                           "total_date": days_to_date(P.start_day + d).isoformat(), "register_kwh": reg})

    # ---------- scenario ground truth (from the simulation state, by documented rules) ----------
    for P in plants:
        for mid, wf in P.waste_flag.items():
            m = next(x for x in P.machines if x["id"] == mid)
            en = meter_series[m["meter_id"]][3]
            for a, b in _runs(wf):
                waste = sum(en[k] - m["standby"] * 0.25 for k in range(a, b + 1))
                add_gt("HIGH_IDLE_CONSUMPTION", "machine", mid, P.pid, m["meter_id"], mid, iso(P.t0 + a * 900),
                       iso(P.t0 + (b + 1) * 900), round(waste, 3), "kWh",
                       {"idle_kw": m["idle"], "standby_kw": m["standby"], "intervals": b - a + 1})
        for mid, d in P.degr.items():
            m = next(x for x in P.machines if x["id"] == mid)
            add_gt("INTENSITY_SHIFT", "machine", mid, P.pid, m["meter_id"], mid, iso(P.t0 + d["start_k"] * 900),
                   iso(P.t0 + d["m_end"] * 900), round(P.extra_kwh[mid], 3), "kWh",
                   {"multiplier": d["mult"], "maintenance_start_utc": iso(P.t0 + d["m_start"] * 900)})
        over = [k for k in range(P.n_int) if P.main_kw[k] > P.contract_kw]
        for a, b in _runs(over):
            mx = max(P.main_kw[a:b + 1])
            add_gt("PEAK_DEMAND_SPIKE", "meter", f"MTR-{P.pid}-001", P.pid, f"MTR-{P.pid}-001", None,
                   iso(P.t0 + a * 900), iso(P.t0 + (b + 1) * 900), round(mx - P.contract_kw, 3), "kW",
                   {"max_kw": mx, "contract_demand_kw": P.contract_kw, "intervals": b - a + 1})

    # tariff timezone defect: cost impact if a naive engine used the declared timezone
    tariffs_s = [{k: fmt(v) for k, v in r.items()} for r in T["tariff"]]
    plants_s = [{k: fmt(v) for k, v in r.items()} for r in T["plant"]]
    right, naive = TariffIndex(tariffs_s, plants_s), TariffIndex(tariffs_s, plants_s, use_declared=True)
    for r in trows:
        if r["_defect"] and r["day_type"] == "WEEKDAY" and r["window_name"] == "OFF_PEAK" and r["start_local_time"] == "00:00":
            P = next(x for x in plants if x.pid == r["plant_id"])
            main_en = meter_series[f"MTR-{P.pid}-001"][3]
            delta = 0.0
            for k in range(P.n_int):
                ts = P.t0 + k * 900
                a_, b_ = right.lookup(P.pid, ts), naive.lookup(P.pid, ts)
                if a_ and b_:
                    delta += (b_[2] - a_[2]) * main_en[k]
            ids = [x["tariff_id"] for x in trows if x["_defect"]]
            add_gt("TARIFF_TIMEZONE_MISMATCH", "tariff", ",".join(ids), P.pid, None, None,
                   r["effective_from"] + "T00:00:00Z", iso(P.t0 + P.n_int * 900), round(delta, 2), P.cur,
                   {"declared_timezone": "UTC", "plant_timezone": P.tz, "tariff_ids": ids,
                    "main_meter_cost_delta_if_uncorrected": round(delta, 2)})
            break

    # ---------- finalize: stringify, id ground truth ----------
    order = list(ISSUE_TYPES)
    gt.sort(key=lambda g: (order.index(g["issue_type"]), g["start_ts_utc"], g["entity_id"]))
    for n, g in enumerate(gt, 1):
        g["truth_id"] = f"GT-{n:06d}"
    tables = {n: [{k: fmt(v) for k, v in r.items()} for r in rows] for n, rows in T.items()}
    gt_rows = [{c: fmt(g.get(c)) for c in GROUND_TRUTH_COLUMNS} for g in gt]
    derived = derive.compute(tables)
    rec_rows, rec_events = recommend.build(tables, derived, p)
    tables["recommendation"], tables["recommendation_status_event"] = rec_rows, rec_events
    return tables, derived, gt_rows
