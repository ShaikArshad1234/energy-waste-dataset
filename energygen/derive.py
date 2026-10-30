"""Derived values computed ONLY from source tables (no ground truth, no generator state).

Cleaning rules (documented in README):
  * duplicates: keep the first delivery (lowest ingest_seq) of a reading_id
  * unit repair: if energy_kwh differs from avg_kw * 0.25 by > max(0.01, 2%) the energy is recomputed from avg_kw
  * hierarchy: attribution always follows meter_machine_assignment, never the recorded machine_id
  * cost: tariff window matched in PLANT-LOCAL time, tariff version chosen by plant-local date
"""
import json
import statistics
from collections import defaultdict
from datetime import date

from energygen.tariff import TariffIndex
from energygen.timeutil import date_to_days, iso, parse_iso

IDLE_DAY_THRESHOLD_KWH = 3.0
INTENSITY_ALERT_RATIO = 1.12
MIN_BATCHES_PER_WEEK = 3


def f(x):
    return f"{x:.4f}".rstrip("0").rstrip(".") if x == x else ""


def compute(tables):
    plants = {r["plant_id"]: r for r in tables["plant"]}
    off = {pid: int(r["utc_offset_minutes"]) for pid, r in plants.items()}
    meters = {r["meter_id"]: r for r in tables["meter"]}
    machines = {r["machine_id"]: r for r in tables["machine"]}
    meter_machine = {a["meter_id"]: a["machine_id"] for a in tables["meter_machine_assignment"] if not a["effective_to"]}
    machine_meter = {v: k for k, v in meter_machine.items()}
    findings = []
    ndays = max(1, max((sum(1 for _ in g) for g in _group(tables["meter_daily_total"], "meter_id").values()), default=1))

    # ---------------- 1. clean readings ----------------
    tidx = TariffIndex(tables["tariff"], tables["plant"])
    seen, cleaned_by_meter, cost_rows = set(), defaultdict(dict), []
    main_series = defaultdict(list)
    for r in tables["energy_reading"]:
        rid, mid = r["reading_id"], r["meter_id"]
        pid = meters[mid]["plant_id"]
        if rid in seen:
            findings.append(("READING_DUPLICATE", rid, pid, f"ingest_seq={r['ingest_seq']}"))
            continue
        seen.add(rid)
        e, a = float(r["energy_kwh"]), float(r["avg_kw"])
        expected = a * 0.25
        if abs(e - expected) > max(0.01, 0.02 * expected):
            findings.append(("KW_KWH_CONFUSION", rid, pid, f"energy_kwh={f(e)} avg_kw*0.25={f(expected)}"))
            e = round(expected, 3)
        want = meter_machine.get(mid, "")
        if r["machine_id"] != want:
            findings.append(("MACHINE_OUTSIDE_HIERARCHY", rid, pid,
                             f"recorded={r['machine_id'] or 'NULL'} expected={want or 'NULL'}"))
        ts = parse_iso(r["interval_start_utc"])
        hit = tidx.lookup(pid, ts)
        tariff_id, window, rate = (hit[0], hit[1], hit[2]) if hit else ("", "", 0.0)
        cost = round(e * rate, 4)
        cleaned_by_meter[mid][ts] = (e, cost, a)
        cost_rows.append({"reading_id": rid, "meter_id": mid, "plant_id": pid, "interval_start_utc": r["interval_start_utc"],
                          "energy_kwh": f(e), "tariff_id": tariff_id, "window_name": window,
                          "rate_per_kwh": f(rate), "cost": f(cost)})
        if meters[mid]["meter_type"] == "MAIN":
            main_series[pid].append((ts, a, window))

    # tariff declared timezone vs plant timezone
    for t in tables["tariff"]:
        if t["timezone_name"] != plants[t["plant_id"]]["timezone_name"]:
            findings.append(("TARIFF_TIMEZONE_MISMATCH", t["tariff_id"], t["plant_id"],
                             f"declared={t['timezone_name']} plant={plants[t['plant_id']]['timezone_name']}"))

    # ---------------- 2. missing intervals (gaps) ----------------
    for mid, series in cleaned_by_meter.items():
        ts_sorted = sorted(series)
        for prev, cur in zip(ts_sorted, ts_sorted[1:]):
            if cur - prev > 900:
                findings.append(("METER_GAP", f"{mid}@{iso(prev + 900)}", meters[mid]["plant_id"],
                                 f"missing_intervals={(cur - prev) // 900 - 1}"))

    # ---------------- 3. meter daily reconciliation ----------------
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    for mid, series in cleaned_by_meter.items():
        o = off[meters[mid]["plant_id"]] * 60
        for ts, (e, _c, _a) in series.items():
            g = agg[(mid, (ts + o) // 86400)]
            g[0] += 1
            g[1] += e
            g[2] = max(g[2], e)
    recon = []
    for r in sorted(tables["meter_daily_total"], key=lambda r: (r["meter_id"], r["total_date"])):
        mid = r["meter_id"]
        day = date_to_days(date.fromisoformat(r["total_date"]))
        cnt, s, mx = agg.get((mid, day), [0, 0.0, 0.0])
        reg = float(r["register_kwh"])
        diff = reg - s
        tol = 0.005 * max(reg, 1.0) + 0.01
        missing = 96 - cnt
        if missing == 0:
            status = "RECONCILED" if abs(diff) <= tol else "MISMATCH"
        else:
            cap = float(meters[mid]["rated_capacity_kw"]) * 0.25     # physical upper bound per missing interval
            status = "GAP_EXPLAINED" if -tol <= diff <= missing * cap + tol else "MISMATCH"
        if status == "MISMATCH":
            findings.append(("METER_TOTAL_MISMATCH", r["total_id"], meters[mid]["plant_id"],
                             f"register={f(reg)} interval_sum={f(s)} missing={missing}"))
        recon.append({"meter_id": mid, "total_date": r["total_date"], "interval_count": cnt, "missing_intervals": missing,
                      "interval_sum_kwh": f(s), "register_kwh": f(reg), "diff_kwh": f(diff),
                      "diff_pct": f(100.0 * diff / reg) if reg else "0", "status": status})

    # ---------------- 4. batch energy + intensity (normalised by output) ----------------
    batches = {b["batch_id"]: b for b in tables["production_batch"]}
    bsum = []
    for bm in tables["batch_machine"]:
        b = batches[bm["batch_id"]]
        mach = bm["machine_id"]
        mid = machine_meter[mach]
        a0, a1 = parse_iso(b["start_ts_utc"]), parse_iso(b["end_ts_utc"])
        series = cleaned_by_meter[mid]
        found, kwh, cost = 0, 0.0, 0.0
        for ts in range(a0, a1, 900):
            v = series.get(ts)
            if v:
                found += 1
                kwh += v[0]
                cost += v[1]
        expected = (a1 - a0) // 900
        units = int(b["produced_units"])
        bsum.append({"batch_id": b["batch_id"], "machine_id": mach, "meter_id": mid, "plant_id": b["plant_id"],
                     "product_code": b["product_code"], "units_produced": units, "intervals_expected": expected,
                     "intervals_found": found, "energy_kwh": f(kwh), "energy_cost": f(cost),
                     "kwh_per_unit": f(kwh / units) if units else "", "completeness": "COMPLETE" if found == expected else "INCOMPLETE"})

    # ---------------- 5. peak demand ----------------
    peak, runs = [], []
    for pid, series in main_series.items():
        series.sort()
        contract = float(plants[pid]["contract_demand_kw"])
        groups = {}
        for ts, a, w in series:
            key = ((ts + off[pid] * 60) // 86400, w)
            if key not in groups or a > groups[key][0]:
                groups[key] = (a, ts)
        for (day, w), (mx, ts) in sorted(groups.items()):
            peak.append({"plant_id": pid, "local_date": date.fromordinal(day + 719163).isoformat(), "window_name": w,
                         "max_kw": f(mx), "max_kw_ts_utc": iso(ts), "contract_demand_kw": f(contract),
                         "exceeded": "Y" if mx > contract else "N", "exceed_kw": f(max(0.0, mx - contract))})
        over = [ts for ts, a, _ in series if a > contract]
        start = prev = None
        for ts in over:
            if start is None:
                start = prev = ts
            elif ts == prev + 900:
                prev = ts
            else:
                runs.append((pid, start, prev))
                start = prev = ts
        if start is not None:
            runs.append((pid, start, prev))
    for pid, a, b in runs:
        findings.append(("PEAK_DEMAND_SPIKE", f"MTR-{pid}-001@{iso(a)}", pid, f"intervals={(b - a) // 900 + 1}"))

    # ---------------- 6. idle excess per machine-day ----------------
    staffed = set()
    for s in tables["shift"]:
        if s["staffed"] == "Y":
            staffed.update(range(parse_iso(s["start_ts_utc"]), parse_iso(s["end_ts_utc"]), 900))
    busy = set()
    for bm in tables["batch_machine"]:
        b = batches[bm["batch_id"]]
        for ts in range(parse_iso(b["start_ts_utc"]), parse_iso(b["end_ts_utc"]), 900):
            busy.add((bm["machine_id"], ts))
    wo = defaultdict(dict)
    for m in tables["maintenance_event"]:
        wo[m["work_order_id"]][m["status"]] = (m["machine_id"], parse_iso(m["event_ts_utc"]))
    for ev in wo.values():
        if "IN_PROGRESS" in ev and "COMPLETED" in ev:
            for ts in range(ev["IN_PROGRESS"][1], ev["COMPLETED"][1], 900):
                busy.add((ev["IN_PROGRESS"][0], ts))
    idle_daily = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))   # machine -> day -> [excess kwh, excess cost, intervals]
    for mid, series in cleaned_by_meter.items():
        mach = meter_machine.get(mid)
        if not mach:
            continue
        o = off[meters[mid]["plant_id"]] * 60
        base = float(machines[mach]["standby_kw"]) * 0.25
        for ts, (e, c, _a) in series.items():
            if ts in staffed or (mach, ts) in busy:
                continue
            ex = e - base
            if ex > 0:
                d = idle_daily[mach][(ts + o) // 86400]
                d[0] += ex
                d[1] += c * ex / e if e else 0.0
                d[2] += 1

    # ---------------- 7. intensity index (vs per machine+product baseline) ----------------
    bd = max(4, ndays // 6)
    first_day = min(date_to_days(date.fromisoformat(r["total_date"])) for r in tables["meter_daily_total"])
    comp = []
    for r in bsum:
        if r["completeness"] != "COMPLETE" or not r["kwh_per_unit"]:
            continue
        b = batches[r["batch_id"]]
        day = (parse_iso(b["start_ts_utc"]) + off[r["plant_id"]] * 60) // 86400 - first_day
        comp.append((r["machine_id"], r["product_code"], day, float(r["kwh_per_unit"]), float(r["energy_kwh"]),
                     float(r["energy_cost"]), int(r["units_produced"]), r["batch_id"]))
    base_samples = defaultdict(list)
    for m, pr, day, kpu, *_ in comp:
        if day < bd:
            base_samples[(m, pr)].append(kpu)
    baseline = {k: statistics.median(v) for k, v in base_samples.items() if len(v) >= 1}
    weekly = defaultdict(lambda: defaultdict(list))   # machine -> week -> [(index, excess kwh, excess cost, units, day, batch)]
    for m, pr, day, kpu, kwh, cost, units, bid in comp:
        bl = baseline.get((m, pr))
        if bl and day >= bd:
            exp_kwh = bl * units
            excess = kwh - exp_kwh
            weekly[m][day // 7].append((kpu / bl, excess, cost * excess / kwh if kwh else 0.0, units, day, bid))

    pid_of = {m: machines[m]["plant_id"] for m in machines}
    for mach, days in idle_daily.items():
        for d, v in days.items():
            if v[0] >= IDLE_DAY_THRESHOLD_KWH:
                findings.append(("HIGH_IDLE_CONSUMPTION", f"{mach}@{date.fromordinal(d + 719163).isoformat()}", pid_of[mach],
                                 f"offshift_excess_kwh={f(v[0])}"))
    for mach, weeks in weekly.items():
        for w, rows in weeks.items():
            if len(rows) >= MIN_BATCHES_PER_WEEK:
                idx = statistics.median(x[0] for x in rows)
                if idx > INTENSITY_ALERT_RATIO:
                    findings.append(("INTENSITY_SHIFT", f"{mach}@W{w}", pid_of[mach], f"median_intensity_index={f(idx)}"))

    findings.sort(key=lambda x: (x[0], x[1]))
    derived = {
        "interval_cost": cost_rows,
        "meter_daily_reconciliation": recon,
        "batch_energy_summary": sorted(bsum, key=lambda r: (r["batch_id"], r["machine_id"])),
        "peak_demand_daily": peak,
        "data_quality_findings": [{"finding_type": a, "entity_id": b, "plant_id": c, "detail": d} for a, b, c, d in findings],
        "_ctx": {"cleaned_by_meter": cleaned_by_meter, "idle_daily": idle_daily, "weekly": weekly, "baseline": baseline,
                 "baseline_days": bd, "ndays": ndays, "first_day": first_day, "off": off, "meter_machine": meter_machine,
                 "machine_meter": machine_meter, "peak_runs": runs, "main_series": main_series},
    }
    return derived


def _group(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def weekly_flags(ctx):
    """machine -> {week: median intensity index} for weeks with enough batches."""
    out = {}
    for m, weeks in ctx["weekly"].items():
        out[m] = {w: statistics.median(x[0] for x in rows) for w, rows in weeks.items() if len(rows) >= MIN_BATCHES_PER_WEEK}
    return out
