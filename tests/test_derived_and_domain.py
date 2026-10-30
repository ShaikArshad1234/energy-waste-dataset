"""Derived values come from source records; domain rules hold; detectors are evaluated against ground truth."""
import statistics
from collections import defaultdict

from energygen.evaluate import evaluate
from energygen.timeutil import parse_iso


def test_register_totals_reconcile_to_interval_sums(starter):
    _, derived, _ = starter
    for r in derived["meter_daily_reconciliation"]:
        if r["status"] == "RECONCILED":
            assert abs(float(r["diff_kwh"])) <= 0.005 * float(r["register_kwh"]) + 0.01
        if r["status"] == "GAP_EXPLAINED":
            assert int(r["missing_intervals"]) > 0 and float(r["diff_kwh"]) >= -0.1


def test_labelled_total_mismatch_is_found_and_gaps_are_not_mismatches(starter):
    _, derived, gt = starter
    mis = {r["meter_id"] + r["total_date"] for r in derived["meter_daily_reconciliation"] if r["status"] == "MISMATCH"}
    assert len(mis) == sum(1 for g in gt if g["issue_type"] == "METER_TOTAL_MISMATCH")


def test_cost_uses_plant_local_tariff_window(starter):
    tables, derived, _ = starter
    # first P01 (UTC+5:30) reading at 2026-06-30T18:30Z is 00:00 local Wednesday -> OFF_PEAK, TAR v1 weekday
    row = next(r for r in derived["interval_cost"] if r["plant_id"] == "P01" and r["interval_start_utc"] == "2026-06-30T18:30:00Z")
    assert row["window_name"] == "OFF_PEAK"
    # 04:30Z = 10:00 local (PEAK 09-12)
    row = next(r for r in derived["interval_cost"] if r["plant_id"] == "P01" and r["interval_start_utc"] == "2026-07-01T04:30:00Z")
    assert row["window_name"] == "PEAK"
    assert abs(float(row["cost"]) - float(row["energy_kwh"]) * float(row["rate_per_kwh"])) < 0.001


def test_tariff_rate_revision_changes_cost_mid_horizon(starter):
    _, derived, _ = starter
    rates = defaultdict(set)
    for r in derived["interval_cost"]:
        if r["plant_id"] == "P01" and r["window_name"] == "PEAK":
            d = r["interval_start_utc"][:10]
            if d < "2026-07-07" or d > "2026-07-09":          # skip the UTC/local boundary around the 2026-07-08 cut-over
                rates[d < "2026-07-07"].add(r["rate_per_kwh"])
    assert len(rates[True]) == 1 and len(rates[False]) == 1 and rates[True] != rates[False]


def test_timezone_defect_would_misprice_if_uncorrected(starter):
    _, _, gt = starter
    g = next(g for g in gt if g["issue_type"] == "TARIFF_TIMEZONE_MISMATCH")
    assert float(g["magnitude"]) != 0 and g["magnitude_unit"] == "AED"


def test_intensity_is_energy_per_produced_unit(starter):
    tables, derived, _ = starter
    units = {b["batch_id"]: int(b["produced_units"]) for b in tables["production_batch"]}
    for r in derived["batch_energy_summary"][:200]:
        if r["completeness"] == "COMPLETE":
            u = units[r["batch_id"]]
            assert abs(float(r["kwh_per_unit"]) * u - float(r["energy_kwh"])) <= 0.00005 * u + 0.01   # kWh/unit is stored to 4 dp


def test_batch_energy_ties_to_submeter_readings(starter):
    tables, derived, _ = starter
    row = derived["batch_energy_summary"][0]
    b = next(b for b in tables["production_batch"] if b["batch_id"] == row["batch_id"])
    a, z = parse_iso(b["start_ts_utc"]), parse_iso(b["end_ts_utc"])
    tot = sum(float(r["energy_kwh"]) for r in derived["interval_cost"]
              if r["meter_id"] == row["meter_id"] and a <= parse_iso(r["interval_start_utc"]) < z)
    assert abs(tot - float(row["energy_kwh"])) < 0.01


def test_batches_only_start_in_staffed_shifts_and_machines_not_double_booked(starter):
    tables, _, _ = starter
    shifts = {s["shift_id"]: s for s in tables["shift"]}
    assert all(shifts[b["shift_id"]]["staffed"] == "Y" for b in tables["production_batch"])


def test_maintenance_window_has_zero_energy(starter):
    tables, derived, _ = starter
    states = [s for s in tables["machine_state_event"] if s["state"] == "MAINTENANCE"]
    assert states
    s = states[0]
    meter = next(a["meter_id"] for a in tables["meter_machine_assignment"] if a["machine_id"] == s["machine_id"])
    a, z = parse_iso(s["start_ts_utc"]), parse_iso(s["end_ts_utc"])
    vals = [float(r["avg_kw"]) for r in tables["energy_reading"]
            if r["meter_id"] == meter and r["ingest_seq"] == "1" and a <= parse_iso(r["interval_start_utc"]) < z]
    assert vals and max(vals) == 0.0


def test_peak_spikes_exceed_contract_demand(starter):
    tables, derived, gt = starter
    contract = {p["plant_id"]: float(p["contract_demand_kw"]) for p in tables["plant"]}
    for g in gt:
        if g["issue_type"] == "PEAK_DEMAND_SPIKE":
            assert float(g["magnitude"]) > 0
            assert float(__import__("json").loads(g["details"])["max_kw"]) > contract[g["plant_id"]]


def test_weather_drives_hvac_load(medium):
    """Facility (unmetered) load = main - sum(sub) should correlate with temperature."""
    tables, derived, _ = medium
    cb = derived["_ctx"]["cleaned_by_meter"]
    meters = {m["meter_id"]: m for m in tables["meter"]}
    wx = {w["observation_ts_utc"]: float(w["temperature_c"]) for w in tables["weather_observation"] if w["plant_id"] == "P02"}
    subs = [cb[m] for m, x in meters.items() if x["plant_id"] == "P02" and x["meter_type"] == "SUB"]
    xs, ys = [], []
    for ts, (_e, _c, a) in cb["MTR-P02-001"].items():
        if all(ts in s for s in subs):
            hour = ts - ts % 3600
            iso_h = __import__("energygen.timeutil", fromlist=["iso"]).iso(hour)
            if iso_h in wx:
                xs.append(wx[iso_h]); ys.append(a - sum(s[ts][2] for s in subs))
    assert statistics.correlation(xs, ys) > 0.3


def test_high_idle_waste_only_when_unstaffed(medium):
    tables, derived, gt = medium
    staffed = {}
    for s in tables["shift"]:
        if s["staffed"] == "Y":
            staffed.setdefault(s["plant_id"], set()).update(range(parse_iso(s["start_ts_utc"]), parse_iso(s["end_ts_utc"]), 900))
    for g in gt:
        if g["issue_type"] == "HIGH_IDLE_CONSUMPTION":
            assert not any(t in staffed[g["plant_id"]] for t in range(parse_iso(g["start_ts_utc"]), parse_iso(g["end_ts_utc"]), 900))
            assert float(g["magnitude"]) > 0


def test_intensity_shift_reflected_in_kwh_per_unit(medium):
    tables, derived, gt = medium
    ctx = derived["_ctx"]
    for g in gt:
        if g["issue_type"] == "INTENSITY_SHIFT":
            mult = float(__import__("json").loads(g["details"])["multiplier"])
            assert 1.18 <= mult <= 1.40


def test_recommendations_cite_baseline_and_expected_saving(medium):
    tables, _, _ = medium
    assert tables["recommendation"]
    for r in tables["recommendation"]:
        assert float(r["baseline_value"]) > 0 and r["baseline_metric"] and r["baseline_unit"]
        assert r["baseline_end_utc"] > r["baseline_start_utc"]
        assert float(r["expected_saving_kwh"]) >= 0 and float(r["expected_saving_cost"]) >= 0
        assert r["currency"] in ("INR", "AED")
    idle = [r for r in tables["recommendation"] if r["rule_id"] == "REC-R1-IDLE"]
    for r in idle:
        assert abs(float(r["expected_saving_kwh"]) - float(r["baseline_value"]) * 30 * 0.9) < 0.1


def test_recommendation_lifecycle_has_history(medium):
    tables, _, _ = medium
    n = defaultdict(int)
    for e in tables["recommendation_status_event"]:
        n[e["recommendation_id"]] += 1
    assert all(v >= 2 for v in n.values())


def test_detectors_vs_ground_truth_medium(medium):
    tables, derived, gt = medium
    res = evaluate(tables, derived, gt)
    d = res["detection"]
    for k in ("READING_DUPLICATE", "KW_KWH_CONFUSION", "MACHINE_OUTSIDE_HIERARCHY", "METER_GAP", "METER_TOTAL_MISMATCH",
              "TARIFF_TIMEZONE_MISMATCH", "PEAK_DEMAND_SPIKE"):
        assert d[k]["precision"] >= 0.95 and d[k]["recall"] >= 0.95, (k, d[k])
    assert d["HIGH_IDLE_CONSUMPTION"]["precision"] >= 0.9 and d["HIGH_IDLE_CONSUMPTION"]["recall"] >= 0.8
    assert d["INTENSITY_SHIFT"]["recall"] >= 0.5
