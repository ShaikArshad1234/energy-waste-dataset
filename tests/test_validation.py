"""The validator passes clean data, classifies labelled anomalies, and FAILS on injected corruption."""
import copy

from energygen.validate import validate


def test_clean_dataset_passes(starter):
    tables, derived, gt = starter
    rep = validate(tables, derived, gt)
    assert rep["overall"] == "PASS" and rep["unexplained_violations_total"] == 0
    assert rep["labelled_violations_total"] > 0                  # deliberate anomalies are visible and explained


def test_without_ground_truth_anomalies_are_unexplained(starter):
    tables, derived, _ = starter
    rep = validate(tables, derived, None)
    assert rep["overall"] == "FAIL" and rep["unexplained_violations_total"] > 0


def _fails(fresh, mutate, expect):
    tables, derived, gt = fresh
    mutate(tables)
    rep = validate(tables, derived, gt)
    assert rep["overall"] == "FAIL"
    assert any(expect in c for c in rep["failed"]), rep["failed"]


def test_detects_duplicate_primary_key(fresh):
    _fails(fresh, lambda t: t["machine"].append(dict(t["machine"][0])), "machine: primary key unique")


def test_detects_broken_foreign_key(fresh):
    def m(t):
        t["production_batch"][0]["plant_id"] = "P99"
    _fails(fresh, m, "FK production_batch.plant_id")


def test_detects_bad_enum(fresh):
    def m(t):
        t["meter"][1]["meter_type"] = "SUBMETER"
    _fails(fresh, m, "meter: allowed values")


def test_detects_missing_required(fresh):
    def m(t):
        t["machine"][0]["rated_kw"] = ""
    _fails(fresh, m, "machine: required")


def test_detects_bad_date_order(fresh):
    def m(t):
        r = t["tariff"][0]
        r["effective_to"] = "2000-01-01"
    _fails(fresh, m, "tariff: effective dates")


def test_detects_lifecycle_violation(fresh):
    def m(t):
        evs = [e for e in t["batch_status_event"] if e["batch_id"] == t["production_batch"][0]["batch_id"]]
        evs[1]["status"] = "COMPLETED"                          # PLANNED -> COMPLETED skips RUNNING
    _fails(fresh, m, "batch lifecycle")


def test_detects_new_unlabelled_kw_kwh_confusion(fresh):
    def m(t):
        r = next(r for r in t["energy_reading"] if float(r["avg_kw"]) > 5 and r["ingest_seq"] == "1")
        r["energy_kwh"] = r["avg_kw"]
    _fails(fresh, m, "kWh = kW")


def test_detects_new_unlabelled_duplicate(fresh):
    def m(t):
        r = next(r for r in t["energy_reading"] if r["ingest_seq"] == "1" and r["avg_kw"] != "0")
        d = dict(r); d["ingest_seq"] = "2"
        t["energy_reading"].append(d)
    _fails(fresh, m, "no duplicate reading_id")


def test_detects_machine_double_booking(fresh):
    def m(t):
        b0 = t["production_batch"][0]
        mach = next(x["machine_id"] for x in t["batch_machine"] if x["batch_id"] == b0["batch_id"])
        other = next(b for b in t["production_batch"][1:] if b["plant_id"] == b0["plant_id"])
        other["start_ts_utc"], other["end_ts_utc"] = b0["start_ts_utc"], b0["end_ts_utc"]
        t["batch_machine"].append({"batch_machine_id": "BM-X", "batch_id": other["batch_id"], "machine_id": mach, "sequence_no": "9"})
    _fails(fresh, m, "never runs two batches")


def test_detects_wrong_tariff_window_in_cost(fresh):
    tables, derived, gt = fresh
    derived = copy.deepcopy({k: v for k, v in derived.items() if k != "_ctx"}) | {"_ctx": derived["_ctx"]}
    derived["interval_cost"][0]["tariff_id"] = "TAR-2026-999"
    rep = validate(tables, derived, gt)
    assert rep["overall"] == "FAIL"
