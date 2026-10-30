"""Evaluate what the pipeline detects from the data alone against the separate ground truth.

Detectors (energygen.derive) never see ground_truth; this module is the only place they meet.
"""
import json
import statistics
from collections import defaultdict
from datetime import date

from energygen.timeutil import date_to_days, parse_iso


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0}


def evaluate(tables, derived, gt):
    ctx = derived["_ctx"]
    off, first_day, bd = ctx["off"], ctx["first_day"], ctx["baseline_days"]
    plants = {r["plant_id"]: r for r in tables["plant"]}
    pid_of_machine = {m["machine_id"]: m["plant_id"] for m in tables["machine"]}
    found = defaultdict(set)
    for f in derived["data_quality_findings"]:
        found[f["finding_type"]].add(f["entity_id"])

    truth = defaultdict(set)
    ignore = defaultdict(set)
    for g in gt:
        t = g["issue_type"]
        if t == "TARIFF_TIMEZONE_MISMATCH":
            truth[t].update(g["entity_id"].split(","))
        elif t in ("METER_GAP", "PEAK_DEMAND_SPIKE"):
            truth[t].add(f"{g['meter_id']}@{g['start_ts_utc']}")
        elif t == "HIGH_IDLE_CONSUMPTION":
            o = off[g["plant_id"]] * 60
            for d in range((parse_iso(g["start_ts_utc"]) + o) // 86400, (parse_iso(g["end_ts_utc"]) - 1 + o) // 86400 + 1):
                truth[t].add(f"{g['machine_id']}@{date.fromordinal(d + 719163).isoformat()}")
        elif t == "INTENSITY_SHIFT":
            o = off[g["plant_id"]] * 60
            d0 = (parse_iso(g["start_ts_utc"]) + o) // 86400 - first_day
            d1 = (parse_iso(g["end_ts_utc"]) - 1 + o) // 86400 - first_day
            for w in range(d0 // 7, d1 // 7 + 1):
                overlap = min(d1, w * 7 + 6) - max(d0, w * 7) + 1
                (truth if overlap >= 4 else ignore)[t].add(f"{g['machine_id']}@W{w}")
        else:
            truth[t].add(g["entity_id"])

    metrics = {}
    for t in sorted(truth):
        det = found[t]
        if t == "INTENSITY_SHIFT":          # weeks with only partial overlap are neither right nor wrong
            det = det - ignore[t]
        metrics[t] = _prf(len(det & truth[t]), len(det - truth[t]), len(truth[t] - det))
        metrics[t]["granularity"] = {"HIGH_IDLE_CONSUMPTION": "machine-day", "INTENSITY_SHIFT": "machine-week (>=4 degraded days)",
                                     "METER_GAP": "gap episode", "PEAK_DEMAND_SPIKE": "exceedance episode",
                                     "TARIFF_TIMEZONE_MISMATCH": "tariff row"}.get(t, "record")

    # ---- recommendations vs ground truth ----
    recs = tables["recommendation"]
    by_type = defaultdict(list)
    for r in recs:
        by_type[r["recommendation_type"]].append(r)
    gt_idle = defaultdict(float)
    gt_idle_span = {}
    gt_int = {}
    for g in gt:
        if g["issue_type"] == "HIGH_IDLE_CONSUMPTION":
            gt_idle[g["machine_id"]] += float(g["magnitude"])
            a, b = gt_idle_span.get(g["machine_id"], (g["start_ts_utc"], g["end_ts_utc"]))
            gt_idle_span[g["machine_id"]] = (min(a, g["start_ts_utc"]), max(b, g["end_ts_utc"]))
        if g["issue_type"] == "INTENSITY_SHIFT":
            days = (parse_iso(g["end_ts_utc"]) - parse_iso(g["start_ts_utc"])) / 86400
            gt_int[g["machine_id"]] = (float(g["magnitude"]), days)
    gt_peak = defaultdict(float)
    for g in gt:
        if g["issue_type"] == "PEAK_DEMAND_SPIKE":
            gt_peak[g["plant_id"]] = max(gt_peak[g["plant_id"]], float(g["magnitude"]))
    rec_idle = {r["machine_id"] for r in by_type["REDUCE_IDLE_LOAD"]}
    rec_int = {r["machine_id"] for r in by_type["INSPECT_ENERGY_INTENSITY"]}
    errs = []
    for r in by_type["INSPECT_ENERGY_INTENSITY"]:
        if r["machine_id"] in gt_int:
            mag, days = gt_int[r["machine_id"]]
            truth_30 = mag / days * 30 * 0.9
            errs.append(abs(float(r["expected_saving_kwh"]) - truth_30) / truth_30)
    peak_err = []
    for r in by_type["SHIFT_PEAK_LOAD"]:
        if r["plant_id"] in gt_peak:
            peak_err.append(abs(float(r["expected_saving_kw"]) - gt_peak[r["plant_id"]]) / gt_peak[r["plant_id"]])
    recommendation_eval = {
        "idle": _prf(len(rec_idle & set(gt_idle)), len(rec_idle - set(gt_idle)), len(set(gt_idle) - rec_idle)),
        "intensity": _prf(len(rec_int & set(gt_int)), len(rec_int - set(gt_int)), len(set(gt_int) - rec_int)),
        "intensity_saving_median_abs_pct_error": round(100 * statistics.median(errs), 1) if errs else None,
        "peak_saving_kw_median_abs_pct_error": round(100 * statistics.median(peak_err), 1) if peak_err else None,
        "recommendations_total": len(recs),
    }
    idle_err = []
    for r in by_type["REDUCE_IDLE_LOAD"]:
        if r["machine_id"] in gt_idle:
            a, b = gt_idle_span[r["machine_id"]]
            days = (parse_iso(b) - parse_iso(a)) / 86400
            truth_30 = gt_idle[r["machine_id"]] / days * 30 * 0.9      # GT waste averaged over the whole scenario span
            idle_err.append(abs(float(r["expected_saving_kwh"]) - truth_30) / truth_30)
    recommendation_eval["idle_saving_median_abs_pct_error"] = round(100 * statistics.median(idle_err), 1) if idle_err else None
    return {"detection": metrics, "recommendations": recommendation_eval}
