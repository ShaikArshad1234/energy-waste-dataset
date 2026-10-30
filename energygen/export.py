"""Write CSV / JSON / SQL / ground truth / manifest / reports. No timestamps or host data -> outputs are byte-reproducible."""
import csv
import hashlib
import json
import os
from pathlib import Path

from energygen import __version__
from energygen.schema import BY_NAME, DERIVED_TABLES, GROUND_TRUTH_COLUMNS, LOAD_ORDER, SOURCE_TABLES, ddl_sql

JSON_ROW_LIMIT = 60000


def _write_csv(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sql_value(v, typ):
    if v == "":
        return "NULL"
    if typ in ("INTEGER", "REAL"):
        return v
    return "'" + v.replace("'", "''") + "'"


def write_sql(out, tables):
    d = out / "sql"
    d.mkdir(parents=True, exist_ok=True)
    (d / "schema.sql").write_text(ddl_sql())
    with open(d / "seed.sql", "w", encoding="utf-8") as f:
        f.write("-- Seed data (synthetic). Load order respects foreign keys.\nBEGIN;\n")
        for name in LOAD_ORDER:
            t = BY_NAME[name]
            cols = [c.name for c in t.cols]
            rows = tables[name]
            for i in range(0, len(rows), 500):
                chunk = rows[i:i + 500]
                vals = ",\n".join("(" + ",".join(_sql_value(r[c.name], c.type) for c in t.cols) + ")" for r in chunk)
                f.write(f"INSERT INTO {name} ({', '.join(cols)}) VALUES\n{vals};\n")
        f.write("COMMIT;\n")


def write_dataset(out, tables, derived, gt, params, formats=("csv", "json", "sql"), command=""):
    out = Path(out)
    for name in LOAD_ORDER:
        _write_csv(out / "source" / f"{name}.csv", [c.name for c in BY_NAME[name].cols], tables[name])
    for t in DERIVED_TABLES:
        _write_csv(out / "derived" / f"{t.name}.csv", [c.name for c in t.cols], derived[t.name])
    _write_csv(out / "ground_truth" / "ground_truth.csv", GROUND_TRUTH_COLUMNS, gt)
    (out / "ground_truth" / "ground_truth.json").write_text(
        json.dumps([{**g, "details": json.loads(g["details"])} for g in gt], indent=1, sort_keys=True) + "\n")
    skipped = []
    if "json" in formats:
        for name in LOAD_ORDER:
            if len(tables[name]) <= JSON_ROW_LIMIT:
                (out / "json").mkdir(parents=True, exist_ok=True)
                (out / "json" / f"{name}.json").write_text(json.dumps(tables[name], separators=(",", ":")) + "\n")
            else:
                skipped.append(name)
    if "sql" in formats:
        write_sql(out, tables)
    (out / "generator_command.txt").write_text(command + "\n")
    write_manifest(out, tables, derived, gt, params, skipped, command)


def write_manifest(out, tables, derived, gt, params, json_skipped, command):
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name not in ("source_manifest.json",) and "reports" not in p.parts:
            rel = p.relative_to(out).as_posix()
            entry = {"path": rel, "bytes": p.stat().st_size, "sha256": sha256(p)}
            if p.suffix == ".csv":
                entry["rows"] = sum(1 for _ in open(p, encoding="utf-8")) - 1
            files.append(entry)
    manifest = {
        "generator": {"name": "energygen", "version": __version__, "command": command, "parameters": params.as_dict(),
                      "rng": "Mersenne Twister random() via sha256(seed:label) derived streams (stable across Python versions)"},
        "provenance": {
            "type": "fully synthetic", "external_data_used": False, "personal_or_confidential_data": False,
            "license": "MIT (code and generated data); no third-party data, so no attribution or terms apply",
            "public_reference_data": "none used; table design was inspired by common smart-meter / MES schemas only",
            "retrieval_date": None, "fields_discarded": [], "transformations": "n/a (no external source)"},
        "ground_truth": {"files": ["ground_truth/ground_truth.csv", "ground_truth/ground_truth.json"],
                         "rows": len(gt), "note": "never read by generator derived tables or detectors; used only by validate/evaluate"},
        "json_export_skipped_for_size": json_skipped,
        "files": files}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


def load_tables(out):
    out = Path(out)

    def read(p):
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    tables = {n: read(out / "source" / f"{n}.csv") for n in LOAD_ORDER}
    gt = read(out / "ground_truth" / "ground_truth.csv")
    return tables, gt
