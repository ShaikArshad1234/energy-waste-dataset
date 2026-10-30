"""Recommendations built from measured (derived) data only. Each cites a baseline and an expected saving.

Rules (documented in README):
  REC-R1-IDLE      a machine's off-shift excess energy (above standby) >= 3 kWh on >= 3 days  -> shut it down off-shift
  REC-R2-INTENSITY weekly median kWh/unit index > 1.12 vs its own machine+product baseline    -> inspect machine
  REC-R3-PEAK      MAIN-meter demand above contract demand on >= 2 days                         -> stagger starts
Savings are expressed per 30 days with a 90% capture factor.
"""
import statistics
from collections import defaultdict

from energygen.derive import IDLE_DAY_THRESHOLD_KWH, INTENSITY_ALERT_RATIO, MIN_BATCHES_PER_WEEK, f, weekly_flags
from energygen.tariff import TariffIndex
from energygen.timeutil import date_to_days, iso, parse_iso
from datetime import date

CAPTURE = 0.9
MATERIAL_KWH = 150.0
MATERIAL_KW = 10.0


def build(tables, derived, p):
    ctx = derived["_ctx"]
    plants = {r["plant_id"]: r for r in tables["plant"]}
    machines = {r["machine_id"]: r for r in tables["machine"]}
    meters = {r["meter_id"]: r for r in tables["meter"]}
    off, first_day, ndays = ctx["off"], ctx["first_day"], ctx["ndays"]
    year = date.fromordinal(first_day + 719163).year
    tidx = TariffIndex(tables["tariff"], tables["plant"])
    day_ts = lambda pid, d: d * 86400 - off[pid] * 60    # noqa: E731  local midnight of day d (UTC epoch)
    horizon_end = {pid: day_ts(pid, first_day + ndays) for pid in plants}
    maint = defaultdict(list)    # machine -> [(completed_ts, event_id)] for corrective work orders
    wo_type = {m["work_order_id"]: m["maintenance_type"] for m in tables["maintenance_event"]}
    for m in tables["maintenance_event"]:
        if m["status"] == "COMPLETED" and wo_type[m["work_order_id"]] == "CORRECTIVE":
            maint[m["machine_id"]].append((parse_iso(m["event_ts_utc"]), m["maintenance_event_id"]))
    recs = []

    # ---------------- R1 idle ----------------
    for mach, days in sorted(ctx["idle_daily"].items()):
        pid = machines[mach]["plant_id"]
        flagged = sorted(d for d, v in days.items() if v[0] >= IDLE_DAY_THRESHOLD_KWH)
        episodes, cur = [], []
        for d in flagged:
            if cur and d - cur[-1] > 6:      # shift calendar is weekly: allow a week-long gap
                episodes.append(cur)
                cur = []
            cur.append(d)
        if cur:
            episodes.append(cur)
        series = ctx["cleaned_by_meter"][ctx["machine_meter"][mach]]
        for ep in episodes:
            if len(ep) < 3:
                continue
            w0 = ep[0]
            w1 = min(ep[0] + 6, ep[-1])
            wdays = w1 - w0 + 1
            ex = sum(days[d][0] for d in range(w0, w1 + 1) if d in days)
            cost = sum(days[d][1] for d in range(w0, w1 + 1) if d in days)
            lo, hi = day_ts(pid, w0), day_ts(pid, w1 + 1)
            total = sum(series[t][0] for t in range(lo, hi, 900) if t in series)
            base = ex / wdays
            created = hi
            ongoing = ep[-1] >= first_day + ndays - (7 if ndays >= 30 else 4)
            saving = base * 30 * CAPTURE
            events = [("OPEN", created)]
            if ongoing:
                if created >= horizon_end[pid] - 3 * 86400:
                    status = "OPEN"
                elif saving >= MATERIAL_KWH:
                    status, events = "ACCEPTED", events + [("ACCEPTED", created + 86400)]
                else:
                    status, events = "REJECTED", events + [("REJECTED", created + 86400)]
            else:
                impl = max(created + 2 * 86400, day_ts(pid, ep[-1] + 1))
                status, events = "IMPLEMENTED", events + [("ACCEPTED", created + 86400), ("IMPLEMENTED", impl)]
            recs.append({"plant_id": pid, "meter_id": ctx["machine_meter"][mach], "machine_id": mach,
                         "recommendation_type": "REDUCE_IDLE_LOAD", "rule_id": "REC-R1-IDLE", "status": status,
                         "created_ts": created, "baseline_metric": "offshift_excess_kwh_per_day",
                         "baseline_value": base, "baseline_unit": "kWh/day", "baseline_start": lo, "baseline_end": hi,
                         "observed_value": base, "saving_kwh": saving, "saving_kw": None,
                         "saving_cost": cost / wdays * 30 * CAPTURE, "currency": plants[pid]["currency"],
                         "saving_pct": 100.0 * ex / total if total else 0.0, "maint": None, "events": events})

    # ---------------- R2 intensity ----------------
    flags = weekly_flags(ctx)
    for mach, weeks in sorted(flags.items()):
        hot = [w for w, idx in sorted(weeks.items()) if idx > INTENSITY_ALERT_RATIO]
        if not hot:
            continue
        w = hot[0]
        pid = machines[mach]["plant_id"]
        rows = ctx["weekly"][mach][w]
        excess = sum(r[1] for r in rows)
        cost = sum(r[2] for r in rows)
        energy = sum(r[1] + 0.0 for r in rows)
        bl_vals = [v for (m, _), v in ctx["baseline"].items() if m == mach]
        base = statistics.median(bl_vals)
        created = day_ts(pid, first_day + (w + 1) * 7)
        week_total = sum(ctx["cleaned_by_meter"][ctx["machine_meter"][mach]][t][0]
                         for t in range(day_ts(pid, first_day + w * 7), created, 900)
                         if t in ctx["cleaned_by_meter"][ctx["machine_meter"][mach]])
        done = next(((ts, eid) for ts, eid in sorted(maint[mach]) if ts > created), None)
        events = [("OPEN", created), ("ACCEPTED", created + 86400)]
        status = "ACCEPTED"
        if done:
            events.append(("IMPLEMENTED", done[0]))
            status = "IMPLEMENTED"
        recs.append({"plant_id": pid, "meter_id": ctx["machine_meter"][mach], "machine_id": mach,
                     "recommendation_type": "INSPECT_ENERGY_INTENSITY", "rule_id": "REC-R2-INTENSITY", "status": status,
                     "created_ts": created, "baseline_metric": "kwh_per_unit", "baseline_value": base,
                     "baseline_unit": "kWh/unit", "baseline_start": day_ts(pid, first_day),
                     "baseline_end": day_ts(pid, first_day + ctx["baseline_days"]), "observed_value": base * weeks[w],
                     "saving_kwh": excess * 30 / 7 * CAPTURE, "saving_kw": None,
                     "saving_cost": cost * 30 / 7 * CAPTURE, "currency": plants[pid]["currency"],
                     "saving_pct": 100.0 * excess / week_total if week_total else 0.0,
                     "maint": done[1] if done else None, "events": events})

    # ---------------- R3 peak ----------------
    by_plant = defaultdict(list)
    for pid, a, b in ctx["peak_runs"]:
        by_plant[pid].append((a, b))
    for pid, runs in sorted(by_plant.items()):
        pk_days = {(a + off[pid] * 60) // 86400 for a, _ in runs}
        if len(pk_days) < 2:
            continue
        contract = float(plants[pid]["contract_demand_kw"])
        main = ctx["main_series"][pid]
        top_ts, top = max(((ts, a) for ts, a, _ in main if a > contract), key=lambda x: x[1])
        hit = tidx.lookup(pid, top_ts)
        saving_kw = top - contract
        created = min(horizon_end[pid], runs[-1][1] + 900 + 86400)
        status = "ACCEPTED" if saving_kw >= MATERIAL_KW else "REJECTED"
        recs.append({"plant_id": pid, "meter_id": f"MTR-{pid}-001", "machine_id": None,
                     "recommendation_type": "SHIFT_PEAK_LOAD", "rule_id": "REC-R3-PEAK", "status": status,
                     "created_ts": created, "baseline_metric": "peak_main_kw", "baseline_value": top,
                     "baseline_unit": "kW", "baseline_start": runs[0][0], "baseline_end": runs[-1][1] + 900,
                     "observed_value": top, "saving_kwh": 0.0, "saving_kw": saving_kw,
                     "saving_cost": saving_kw * (hit[3] if hit else 0.0), "currency": plants[pid]["currency"],
                     "saving_pct": 100.0 * saving_kw / top, "maint": None,
                     "events": [("OPEN", created), (status, created + 86400)]})

    recs.sort(key=lambda r: (r["created_ts"], r["plant_id"], r["recommendation_type"], r["machine_id"] or ""))
    rows, ev_rows, ev_n = [], [], 0
    for n, r in enumerate(recs, 1):
        rid = f"REC-{year}-{n:06d}"
        rows.append({"recommendation_id": rid, "plant_id": r["plant_id"], "meter_id": r["meter_id"],
                     "machine_id": r["machine_id"] or "", "recommendation_type": r["recommendation_type"],
                     "rule_id": r["rule_id"], "status": r["status"], "created_ts_utc": iso(r["created_ts"]),
                     "baseline_metric": r["baseline_metric"], "baseline_value": f(r["baseline_value"]),
                     "baseline_unit": r["baseline_unit"], "baseline_start_utc": iso(r["baseline_start"]),
                     "baseline_end_utc": iso(r["baseline_end"]), "observed_value": f(r["observed_value"]),
                     "expected_saving_kwh": f(r["saving_kwh"]),
                     "expected_saving_kw": f(r["saving_kw"]) if r["saving_kw"] is not None else "",
                     "expected_saving_cost": f(r["saving_cost"]), "currency": r["currency"],
                     "expected_saving_pct": f(r["saving_pct"]), "related_maintenance_event_id": r["maint"] or ""})
        for seq, (status, ts) in enumerate(r["events"], 1):
            ev_n += 1
            ev_rows.append({"rec_event_id": f"REV-{year}-{ev_n:06d}", "recommendation_id": rid, "event_seq": str(seq),
                            "status": status, "event_ts_utc": iso(ts)})
    return rows, ev_rows
