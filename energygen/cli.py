"""python -m energygen <generate|validate|repro-check|dictionary> ..."""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

from energygen import derive, evaluate as ev
from energygen.export import load_tables, sha256, write_dataset
from energygen.generate import generate
from energygen.params import FULL, STARTER, Params
from energygen.schema import BY_NAME, LOAD_ORDER, SOURCE_TABLES, DERIVED_TABLES, ISSUE_TYPES
from energygen.validate import validate


def _params(a) -> Params:
    base = {"starter": STARTER, "full": FULL}[a.preset].as_dict()
    for k in ("seed", "plants", "meters", "days", "batches", "start_date"):
        v = getattr(a, k, None)
        if v is not None:
            base[k] = v
    return Params(**base)


def _command(a, p: Params) -> str:
    return (f"python -m energygen generate --out {a.out} --seed {p.seed} --plants {p.plants} --meters {p.meters} "
            f"--days {p.days} --batches {p.batches} --start-date {p.start_date}")


def _validation_md(rep, evl) -> str:
    L = [f"# Validation summary: **{rep['overall']}**", "",
         f"* checks: {rep['checks_passed']}/{rep['checks_total']} passed",
         f"* violations explained by ground-truth labels (deliberate anomalies): {rep['labelled_violations_total']}",
         f"* **unexplained violations: {rep['unexplained_violations_total']}**", "",
         "| group | check | violations | labelled | unexplained | status |", "|---|---|---|---|---|---|"]
    for c in rep["checks"]:
        if c["violations"] or c["status"] == "FAIL" or c["group"] in ("reconciliation", "lifecycle", "hierarchy"):
            L.append(f"| {c['group']} | {c['check']} | {c['violations']} | {c['labelled_by_ground_truth']} | {c['unexplained']} | {c['status']} |")
    L += ["", "## Detection quality (pipeline from data only vs ground truth)", "",
          "| issue_type | granularity | TP | FP | FN | precision | recall | F1 |", "|---|---|---|---|---|---|---|---|"]
    for t, m in evl["detection"].items():
        L.append(f"| {t} | {m['granularity']} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']} | {m['recall']} | {m['f1']} |")
    L += ["", "## Recommendations", "", "```json", json.dumps(evl["recommendations"], indent=2), "```", ""]
    return "\n".join(L)


def run_all(tables, derived, gt, out: Path):
    rep = validate(tables, derived, gt)
    evl = ev.evaluate(tables, derived, gt)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    (out / "reports" / "validation_summary.json").write_text(json.dumps(rep, indent=1) + "\n")
    (out / "reports" / "evaluation.json").write_text(json.dumps(evl, indent=1) + "\n")
    (out / "reports" / "validation_summary.md").write_text(_validation_md(rep, evl))
    return rep, evl


def cmd_generate(a):
    p = _params(a)
    out = Path(a.out)
    t = time.perf_counter()
    tables, derived, gt = generate(p)
    gen_s = time.perf_counter() - t
    write_dataset(out, tables, derived, gt, p, tuple(a.formats.split(",")), _command(a, p))
    rep, evl = run_all(tables, derived, gt, out)
    print(f"generated in {gen_s:.1f}s -> {out}")
    for n in LOAD_ORDER:
        print(f"  {n:28s}{len(tables[n]):>9,}")
    print(f"  {'ground_truth':28s}{len(gt):>9,}")
    print(f"validation: {rep['overall']} ({rep['checks_passed']}/{rep['checks_total']}), labelled={rep['labelled_violations_total']}, "
          f"unexplained={rep['unexplained_violations_total']}")
    for t_, m in evl["detection"].items():
        print(f"  detect {t_:26s} P={m['precision']:.3f} R={m['recall']:.3f}")
    return 0 if rep["overall"] == "PASS" else 1


def cmd_validate(a):
    out = Path(a.data)
    tables, gt = load_tables(out)
    derived = derive.compute(tables)
    rep, evl = run_all(tables, derived, gt, out)
    print(f"validation: {rep['overall']} ({rep['checks_passed']}/{rep['checks_total']}), "
          f"unexplained={rep['unexplained_violations_total']}")
    return 0 if rep["overall"] == "PASS" else 1


def _fingerprint(out: Path):
    res = {}
    for p in sorted(out.rglob("*.csv")):
        res[p.relative_to(out).as_posix()] = {"rows": sum(1 for _ in open(p, encoding="utf-8")) - 1, "sha256": sha256(p)}
    return res


def cmd_repro(a):
    p = _params(a)
    runs = []
    for i in (1, 2):
        with tempfile.TemporaryDirectory() as d:
            tables, derived, gt = generate(p)
            write_dataset(Path(d), tables, derived, gt, p, ("csv",), "repro")
            runs.append(_fingerprint(Path(d)))
    same = runs[0] == runs[1]
    diff = [k for k in runs[0] if runs[0][k] != runs[1].get(k)]
    res = {"seed": p.seed, "parameters": p.as_dict(), "identical": same, "files_compared": len(runs[0]),
           "differences": diff, "run_1": runs[0], "run_2_matches": same}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(f"reproducibility: {'IDENTICAL' if same else 'DIFFERENT'} ({len(runs[0])} CSV files, row counts + sha256 compared)")
    return 0 if same else 1


def cmd_dictionary(a):
    L = ["# Data dictionary", "",
         "Generated from `energygen/schema.py` (single source of truth for DDL, validation and this document).", "",
         "Conventions: `snake_case`; timestamps ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`); dates `YYYY-MM-DD` (plant-local where stated); "
         "empty CSV cell = NULL; energy in **kWh** (interval energy), demand in **kW** (average over the interval), "
         "sampling cadence **15 minutes**, so `energy_kwh = avg_kw x 0.25`.", "",
         "## ID conventions", "", "| entity | pattern | example |", "|---|---|---|",
         "| plant | `P<nn>` | P01 |", "| meter | `MTR-<plant-id>-<nnn>` | MTR-P01-001 |",
         "| machine | `MCH-<plant-id>-<nnn>` | MCH-P01-003 |",
         "| energy reading | `ENG-<meter-id>-<yyyymmddThhmmssZ>` | ENG-MTR-P01-001-20260701T001500Z |",
         "| production batch | `BATCH-<year>-<nnnnnn>` (ordered by start) | BATCH-2026-000001 |",
         "| tariff | `TAR-<year>-<nnn>` | TAR-2026-001 |", "| recommendation | `REC-<year>-<nnnnnn>` | REC-2026-000001 |",
         "| shift | `SHF-<plant>-<yyyymmdd>-<A/B/C>` | SHF-P01-20260701-A |",
         "| other events | `BEV/MSE/MNT/REV-<year>-<nnnnnn>`, `WO-<year>-<nnnn>`, `ASG-<machine>-<nn>`, `BM-<batch>-<nn>`, `MDT-<meter>-<yyyymmdd>`, `WX-<plant>-<yyyymmddhh>` | |",
         "| ground truth | `GT-<nnnnnn>` | GT-000001 |", "",
         "IDs are deterministic (counters zero-padded, assigned in a fixed sort order) and immutable; foreign keys use the exact same strings.", ""]
    for group, tabs in (("Source tables", SOURCE_TABLES), ("Derived tables (computed from source tables)", DERIVED_TABLES)):
        L += [f"## {group}", ""]
        for t in tabs:
            L += [f"### `{t.name}`", "", t.description, "", f"* **Primary key:** `{', '.join(t.pk)}`"]
            if t.relationships:
                L.append(f"* **Relationships / cardinality:** {t.relationships}")
            L += ["", "| field | type | required | allowed values | unit | unique | validation rule | references |", "|---|---|---|---|---|---|---|---|"]
            for c in t.cols:
                uniq = "yes" if c.unique else ("part of PK" if c.name in t.pk else "")
                L.append(f"| `{c.name}` | {c.type} | {'yes' if c.required else 'no'} | {', '.join(c.allowed) if c.allowed else ''} | "
                         f"{c.unit} | {uniq} | {(c.rule + ('; ' if c.rule and c.pattern else '') + ('pattern `' + c.pattern + '`' if c.pattern else '')).replace('|', '/')} | "
                         f"{(c.fk[0] + '.' + c.fk[1]) if c.fk else ''} |")
            L.append("")
    L += ["## Ground truth (`ground_truth/ground_truth.csv|json`)", "",
          "Kept in a separate folder; never an input feature. One row per labelled episode / record.", "",
          "| field | meaning |", "|---|---|"]
    meanings = {"truth_id": "GT-<nnnnnn>", "issue_type": "see table below", "entity_type": "table/entity the label refers to",
                "entity_id": "key of the affected entity (comma list for tariff rows)", "plant_id": "", "meter_id": "", "machine_id": "",
                "start_ts_utc": "episode start", "end_ts_utc": "episode end (exclusive)", "expected_decision": "what a correct pipeline should do",
                "magnitude": "size of the issue (kWh, kW or currency)", "magnitude_unit": "", "rule_id": "documented rule that produced the label",
                "details": "JSON with rule parameters"}
    L += [f"| `{c}` | {meanings[c]} |" for c in ["truth_id", "issue_type", "entity_type", "entity_id", "plant_id", "meter_id", "machine_id", "start_ts_utc", "end_ts_utc", "expected_decision", "magnitude", "magnitude_unit", "rule_id", "details"]]
    L += ["", "| issue_type | rule_id | expected_decision |", "|---|---|---|"]
    L += [f"| {k} | {v[0]} | {v[1]} |" for k, v in ISSUE_TYPES.items()]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(L) + "\n")
    print("wrote", a.out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="energygen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", required=True)
    g.add_argument("--preset", choices=["starter", "full"], default="full")
    for k, t in (("seed", int), ("plants", int), ("meters", int), ("days", int), ("batches", int), ("start_date", str)):
        g.add_argument("--" + k.replace("_", "-"), dest=k, type=t, default=None)
    g.add_argument("--formats", default="csv,json,sql")
    g.set_defaults(fn=cmd_generate)
    v = sub.add_parser("validate")
    v.add_argument("--data", required=True)
    v.set_defaults(fn=cmd_validate)
    r = sub.add_parser("repro-check")
    r.add_argument("--preset", choices=["starter", "full"], default="full")
    for k, t in (("seed", int), ("plants", int), ("meters", int), ("days", int), ("batches", int), ("start_date", str)):
        r.add_argument("--" + k.replace("_", "-"), dest=k, type=t, default=None)
    r.add_argument("--out")
    r.set_defaults(fn=cmd_repro)
    d = sub.add_parser("dictionary")
    d.add_argument("--out", default="docs/DATA_DICTIONARY.md")
    d.set_defaults(fn=cmd_dictionary)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
