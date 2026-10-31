"""Build a self-contained HTML dashboard (data embedded, no server) from a generated dataset.

    python scripts/build_dashboard.py --data data/full --out docs/dashboard.html

Open the output file in any browser. Standard library only. Charts are drawn with inline SVG.
"""
import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


def rows(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ms(ts):  # 2026-07-01T00:00:00Z -> epoch ms
    from datetime import datetime, timezone
    return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def collect(data: Path):
    src, der, gt_dir, rep = data / "source", data / "derived", data / "ground_truth", data / "reports"
    meters = rows(src / "meter.csv")
    main = {m["meter_id"]: m["plant_id"] for m in meters if m["meter_type"] == "MAIN"}
    plants = {p["plant_id"]: p for p in rows(src / "plant.csv")}

    energy = defaultdict(list)
    for r in rows(der / "meter_daily_reconciliation.csv"):
        if r["meter_id"] in main:
            energy[main[r["meter_id"]]].append([r["total_date"], round(float(r["register_kwh"]), 1)])
    for v in energy.values():
        v.sort()

    peak = defaultdict(dict)
    for r in rows(der / "peak_demand_daily.csv"):
        d = peak[r["plant_id"]].setdefault(r["local_date"], [r["local_date"], 0.0, float(r["contract_demand_kw"])])
        d[1] = max(d[1], float(r["max_kw"]))
    peak = {p: sorted([a, round(b, 1), c] for a, b, c in v.values()) for p, v in peak.items()}

    gt = rows(gt_dir / "ground_truth.csv")
    gt_counts = defaultdict(int)
    for r in gt:
        gt_counts[r["issue_type"]] += 1
    found = defaultdict(int)
    idle = defaultdict(lambda: [0, 0.0])
    for r in rows(der / "data_quality_findings.csv"):
        found[r["finding_type"]] += 1
        if r["finding_type"] == "HIGH_IDLE_CONSUMPTION":
            mach = r["entity_id"].split("@")[0]
            idle[mach][0] += 1
            idle[mach][1] += float(r["detail"].split("=")[1])
    ev = json.loads((rep / "evaluation.json").read_text())["detection"]
    anomalies = [{"type": t, "labelled": gt_counts[t], "detected": found.get(t, 0),
                  "recall": ev.get(t, {}).get("recall"), "grain": ev.get(t, {}).get("granularity", "")}
                 for t in sorted(gt_counts, key=lambda k: -gt_counts[k])]
    idle_rows = sorted(({"machine": m, "plant": m.split("-")[1], "days": v[0], "kwh": round(v[1], 1)}
                        for m, v in idle.items()), key=lambda r: -r["kwh"])

    # intensity drift for machines that carry an INTENSITY_SHIFT label
    shift = {r["machine_id"]: (ms(r["start_ts_utc"]), ms(r["end_ts_utc"])) for r in gt if r["issue_type"] == "INTENSITY_SHIFT"}
    start = {b["batch_id"]: ms(b["start_ts_utc"]) for b in rows(src / "production_batch.csv")}
    t0 = min(start.values())
    by_mp = defaultdict(list)
    for b in rows(der / "batch_energy_summary.csv"):
        if b["completeness"] == "COMPLETE" and b["machine_id"] in shift and b["kwh_per_unit"]:
            by_mp[(b["machine_id"], b["product_code"])].append((start[b["batch_id"]], b["batch_id"], float(b["kwh_per_unit"])))
    intensity = {}
    for (m, prod), lst in by_mp.items():
        base = [v for t, _, v in lst if t < t0 + 14 * 86400000] or [v for _, _, v in lst]
        med = statistics.median(base)
        for t, bid, v in lst:
            intensity.setdefault(m, []).append([t, round(v / med, 3), bid, prod, round(v, 4)])
    for m in intensity:
        intensity[m].sort()
    shifts = {m: list(w) for m, w in shift.items() if m in intensity}

    recs = [{"id": r["recommendation_id"], "plant": r["plant_id"], "machine": r["machine_id"], "rule": r["rule_id"],
             "status": r["status"], "kwh": float(r["expected_saving_kwh"] or 0),
             "kw": float(r["expected_saving_kw"]) if r["expected_saving_kw"] else None,
             "cost": float(r["expected_saving_cost"]), "cur": r["currency"], "created": r["created_ts_utc"][:10]}
            for r in rows(src / "recommendation.csv")]

    val = json.loads((rep / "validation_summary.json").read_text())
    n_read = sum(1 for _ in open(src / "energy_reading.csv", encoding="utf-8")) - 1
    n_batch = sum(1 for _ in open(src / "production_batch.csv", encoding="utf-8")) - 1
    return {
        "kpi": {"readings": n_read, "batches": n_batch, "labelled": len(gt),
                "checks": val["checks_total"], "passed": val["checks_passed"],
                "unexplained": val["unexplained_violations_total"]},
        "plants": {k: {"name": v["plant_name"] if "plant_name" in v else k, "currency": v.get("currency", "")} for k, v in plants.items()},
        "energy": energy, "peak": peak, "anomalies": anomalies, "idle": idle_rows,
        "intensity": intensity, "shifts": shifts, "recs": recs,
    }


TEMPLATE = (Path(__file__).with_name("dashboard_template.html")).read_text(encoding="utf-8") if Path(__file__).with_name("dashboard_template.html").exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/full")
    ap.add_argument("--out", default="docs/dashboard.html")
    ap.add_argument("--fragment", action="store_true", help="write body fragment only (for embedding)")
    a = ap.parse_args()
    if TEMPLATE is None:
        raise SystemExit("scripts/dashboard_template.html is missing")
    payload = json.dumps(collect(Path(a.data)), separators=(",", ":"))
    html = TEMPLATE.replace("/*__DATA__*/null", payload)
    if not a.fragment:
        html = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                "</head><body>\n" + html + "\n</body></html>\n")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(html, encoding="utf-8")
    print(f"wrote {a.out} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
