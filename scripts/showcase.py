"""Showcase: load a generated dataset (source + derived + ground truth CSVs) into in-memory SQLite and
run demo queries. Standard library only.

    python scripts/showcase.py                      # uses data/full
    python scripts/showcase.py --data data/starter
    python scripts/showcase.py --data out/full --md showcase.md
"""
import argparse
import csv
import sqlite3
from pathlib import Path

QUERIES = [
    ("1. Dataset size (rows per table)", None),
    ("2. Recommendations: count, status, expected saving",
     """SELECT rule_id, status, COUNT(*) AS n, ROUND(SUM(expected_saving_kwh)) AS saving_kwh,
               ROUND(SUM(expected_saving_kw),1) AS saving_kw, currency, ROUND(SUM(expected_saving_cost)) AS saving_cost
        FROM recommendation GROUP BY rule_id, status, currency ORDER BY rule_id"""),
    ("3. Anomalies injected (ground truth) by type",
     "SELECT issue_type, COUNT(*) AS n FROM ground_truth GROUP BY issue_type ORDER BY n DESC"),
    ("4. Findings the pipeline detected from source data only",
     "SELECT finding_type, COUNT(*) AS n FROM data_quality_findings GROUP BY finding_type ORDER BY n DESC"),
    ("5. Meter daily reconciliation: largest gaps between interval sum and register (kWh)",
     """SELECT m.plant_id, r.meter_id, r.total_date, ROUND(r.interval_sum_kwh,1) AS interval_kwh, ROUND(r.register_kwh,1) AS register_kwh,
               ROUND(r.diff_kwh,1) AS diff_kwh, r.status
        FROM meter_daily_reconciliation r JOIN meter m USING (meter_id)
        WHERE r.status <> 'RECONCILED' ORDER BY ABS(r.diff_kwh) DESC LIMIT 8"""),
    ("6. Energy cost by tariff window (P01 = INR, P02 = AED; plant-local windows)",
     """SELECT plant_id, window_name, ROUND(SUM(energy_kwh)) AS kwh, ROUND(AVG(rate_per_kwh),2) AS avg_rate,
               ROUND(SUM(cost)) AS cost
        FROM interval_cost GROUP BY plant_id, window_name ORDER BY plant_id, avg_rate"""),
    ("7. Peak-demand days above contract demand",
     """SELECT plant_id, local_date, window_name, max_kw, contract_demand_kw, exceed_kw
        FROM peak_demand_daily WHERE exceeded = 'Y' ORDER BY exceed_kw DESC LIMIT 8"""),
    ("8. Worst energy intensity: batches ranked by kWh per unit (COMPLETE batches)",
     """SELECT batch_id, machine_id, product_code, units_produced, ROUND(energy_kwh,1) AS kwh, kwh_per_unit
        FROM batch_energy_summary WHERE completeness = 'COMPLETE' ORDER BY kwh_per_unit DESC LIMIT 8"""),
    ("9. Intensity by machine and product vs the batch average (drift shows as a high ratio)",
     """SELECT machine_id, product_code, COUNT(*) AS batches, ROUND(MIN(kwh_per_unit),4) AS min_kwh_unit,
               ROUND(AVG(kwh_per_unit),4) AS avg_kwh_unit, ROUND(MAX(kwh_per_unit),4) AS max_kwh_unit,
               ROUND(MAX(kwh_per_unit)/AVG(kwh_per_unit),2) AS max_over_avg
        FROM batch_energy_summary WHERE completeness = 'COMPLETE'
        GROUP BY machine_id, product_code HAVING COUNT(*) >= 3 ORDER BY max_over_avg DESC LIMIT 8"""),
    ("10. Idle waste by machine (off-shift excess kWh)",
     """SELECT substr(entity_id,1,instr(entity_id,'@')-1) AS machine_id, COUNT(*) AS idle_days,
               ROUND(SUM(CAST(substr(detail, instr(detail,'=')+1) AS REAL)),1) AS excess_kwh
        FROM data_quality_findings WHERE finding_type = 'HIGH_IDLE_CONSUMPTION'
        GROUP BY 1 ORDER BY excess_kwh DESC LIMIT 8"""),
]


def load(conn, data):
    for sub in ("source", "derived", "ground_truth"):
        for f in sorted((data / sub).glob("*.csv")):
            with open(f, newline="", encoding="utf-8") as fh:
                rd = csv.reader(fh)
                cols = next(rd)
                conn.execute(f'CREATE TABLE "{f.stem}" ({",".join(chr(34)+c+chr(34) for c in cols)})')
                conn.executemany(f'INSERT INTO "{f.stem}" VALUES ({",".join("?"*len(cols))})', rd)
    # numeric affinity for the columns the demo sorts and sums on
    for t in ("energy_reading", "interval_cost", "batch_energy_summary", "meter_daily_reconciliation",
              "peak_demand_daily", "recommendation"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        for c in cols:
            if any(k in c for k in ("kwh", "kw", "cost", "rate", "pct", "units", "value", "diff", "count", "intervals")):
                conn.execute(f'UPDATE {t} SET "{c}" = CAST("{c}" AS REAL) WHERE "{c}" GLOB "[0-9-]*"')


def fmt(headers, rows):
    w = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    line = lambda r: " | ".join(str(v).ljust(w[i]) for i, v in enumerate(r))  # noqa: E731
    return "\n".join([line(headers), "-+-".join("-" * x for x in w)] + [line(r) for r in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/full")
    ap.add_argument("--md", help="also write the output to this Markdown file")
    a = ap.parse_args()
    conn = sqlite3.connect(":memory:")
    load(conn, Path(a.data))
    out = []
    for title, sql in QUERIES:
        if sql is None:
            tabs = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            rows = [(t, conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tabs]
            hdr = ["table", "rows"]
        else:
            cur = conn.execute(sql)
            hdr, rows = [d[0] for d in cur.description], cur.fetchall()
        out.append((title, fmt(hdr, rows)))
    text = "\n\n".join(f"{t}\n{'=' * len(t)}\n{b}" for t, b in out)
    print(text)
    if a.md:
        Path(a.md).write_text("# Dataset showcase\n\n" + "\n\n".join(f"## {t}\n\n```\n{b}\n```" for t, b in out) + "\n")
        print(f"\nwrote {a.md}")


if __name__ == "__main__":
    main()
