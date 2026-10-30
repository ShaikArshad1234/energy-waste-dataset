"""Structure, IDs, scale parameters, reproducibility, ground-truth separation, SQL seed."""
import json
import re
import sqlite3

import pytest

from energygen.generate import generate
from energygen.params import Params, STARTER
from energygen.schema import BY_NAME, LOAD_ORDER, SOURCE_TABLES, ddl_sql
from energygen.export import write_dataset


def test_all_tables_present_and_nonempty(starter):
    tables, derived, gt = starter
    for name in LOAD_ORDER:
        assert tables[name], name
    assert gt


def test_scale_is_parameterised(medium):
    tables, _, _ = medium
    assert len(tables["meter"]) == 10 and len(tables["production_batch"]) == 200
    assert len(tables["plant"]) == 2 and len(tables["machine"]) == 8        # 10 meters = 2 mains + 8 submeters
    assert len(generate(STARTER)[0]["production_batch"]) == STARTER.batches
    n = 10 * 30 * 96
    assert n - 1500 < len(tables["energy_reading"]) < n + 200                 # gaps removed, duplicates added


def test_full_spec_counts_without_running_full():
    # the headline spec: 20 meters, 90 days of 15-minute readings, 2000 batches
    p = Params()
    assert (p.meters, p.days, p.batches, p.seed, p.interval_minutes) == (20, 90, 2000, 20261022, 15)
    assert p.meters * p.days * 96 == 172800


def test_id_conventions(starter):
    t, _, _ = starter
    assert t["meter"][0]["meter_id"] == "MTR-P01-001"
    assert t["production_batch"][0]["batch_id"] == "BATCH-2026-000001"
    assert t["tariff"][0]["tariff_id"] == "TAR-2026-001"
    assert t["recommendation"][0]["recommendation_id"] == "REC-2026-000001"
    r = t["energy_reading"][0]
    assert re.fullmatch(r"ENG-MTR-P0\d-\d{3}-2026\d{4}T\d{6}Z", r["reading_id"])
    assert r["reading_id"] == f"ENG-{r['meter_id']}-" + r["interval_start_utc"].replace("-", "").replace(":", "")
    ids = [b["batch_id"] for b in t["production_batch"]]
    assert ids == sorted(ids) and ids == [f"BATCH-2026-{i:06d}" for i in range(1, len(ids) + 1)]
    starts = [b["start_ts_utc"] for b in t["production_batch"]]
    assert starts == sorted(starts)                                          # ids follow start time


def test_same_seed_same_data(starter):
    a = starter
    b = generate(STARTER)
    assert a[0] == b[0] and a[2] == b[2]


def test_different_seed_differs():
    a = generate(Params(seed=1, plants=2, meters=6, days=14, batches=60))
    b = generate(Params(seed=2, plants=2, meters=6, days=14, batches=60))
    assert a[0]["energy_reading"] != b[0]["energy_reading"]


def test_infeasible_batch_count_fails_loudly():
    with pytest.raises(ValueError, match="cannot schedule"):
        generate(Params(plants=2, meters=6, days=14, batches=4000))


@pytest.mark.parametrize("kw", [dict(meters=3), dict(days=5), dict(batches=5), dict(plants=9), dict(interval_minutes=30)])
def test_invalid_params_rejected(kw):
    with pytest.raises(ValueError):
        generate(Params(**{**STARTER.as_dict(), **kw}))


def test_ground_truth_is_separate_from_inputs(starter):
    tables, _, gt = starter
    forbidden = {"issue_type", "ground_truth", "expected_decision", "magnitude", "truth_id", "rule_id_gt"}
    for name in LOAD_ORDER:
        cols = set(tables[name][0])
        assert not (cols & forbidden), (name, cols & forbidden)
        assert "GT-" not in json.dumps(tables[name][:200])
    assert {g["issue_type"] for g in gt} >= {"KW_KWH_CONFUSION", "READING_DUPLICATE", "METER_GAP"}


def test_required_edge_cases_are_labelled(starter):
    kinds = {g["issue_type"] for g in starter[2]}
    for k in ["METER_TOTAL_MISMATCH", "KW_KWH_CONFUSION", "TARIFF_TIMEZONE_MISMATCH", "READING_DUPLICATE",
              "MACHINE_OUTSIDE_HIERARCHY", "METER_GAP", "PEAK_DEMAND_SPIKE", "HIGH_IDLE_CONSUMPTION", "INTENSITY_SHIFT"]:
        assert k in kinds, k


def test_anomalies_do_not_corrupt_unrelated_rows(starter):
    tables, _, gt = starter
    bad_ids = {g["entity_id"] for g in gt if g["entity_type"] == "energy_reading"}
    for r in tables["energy_reading"]:
        if r["reading_id"] in bad_ids or r["ingest_seq"] != "1":
            continue
        assert abs(float(r["energy_kwh"]) - float(r["avg_kw"]) * 0.25) <= 0.002


def test_sql_seed_loads_with_foreign_keys(tmp_path, starter):
    tables, derived, gt = starter
    write_dataset(tmp_path, tables, derived, gt, STARTER, ("csv", "sql"), "test")
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript((tmp_path / "sql" / "schema.sql").read_text())
    con.executescript((tmp_path / "sql" / "seed.sql").read_text())
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    for name in LOAD_ORDER:
        assert con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] == len(tables[name]), name
    # an actual relational join works: meter -> machine via the bridge
    n = con.execute("SELECT COUNT(*) FROM meter m JOIN meter_machine_assignment a ON a.meter_id=m.meter_id "
                    "JOIN machine x ON x.machine_id=a.machine_id WHERE m.meter_type='SUB'").fetchone()[0]
    assert n == len(tables["machine"])


def test_ddl_covers_every_source_column():
    sql = ddl_sql()
    for t in SOURCE_TABLES:
        assert f"CREATE TABLE {t.name} (" in sql
        for c in t.cols:
            assert f"  {c.name} " in sql
