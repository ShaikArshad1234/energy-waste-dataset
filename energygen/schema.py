"""Single source of truth for every table: used for the data dictionary, SQL DDL and validation.

Types: TEXT, INTEGER, REAL, DATE (YYYY-MM-DD), TIMESTAMP (ISO-8601 UTC 'YYYY-MM-DDTHH:MM:SSZ').
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Col:
    name: str
    type: str = "TEXT"
    required: bool = True
    allowed: Optional[Tuple[str, ...]] = None
    unit: str = ""
    unique: bool = False          # unique on its own (for single-column PK / alternate keys)
    rule: str = ""
    fk: Optional[Tuple[str, str]] = None   # (table, column)
    pattern: Optional[str] = None          # regex for ID columns


@dataclass(frozen=True)
class Table:
    name: str
    pk: Tuple[str, ...]
    description: str
    cols: Tuple[Col, ...]
    kind: str = "source"          # source | derived
    relationships: str = ""

    def col(self, name):
        return next(c for c in self.cols if c.name == name)


T = Table
C = Col
ACTIVE = ("ACTIVE", "INACTIVE")

TABLES: List[Table] = [
    T("plant", ("plant_id",), "Manufacturing site. Parent of meters, machines, shifts, tariffs, weather, batches.", (
        C("plant_id", unique=True, pattern=r"^P\d{2}$", rule="P<nn>; immutable"),
        C("plant_name"),
        C("country_code", rule="ISO-3166 alpha-2"),
        C("timezone_name", rule="IANA name; fixed UTC offset used (no DST)"),
        C("utc_offset_minutes", "INTEGER", unit="minutes", rule="multiple of 15; matches timezone_name"),
        C("currency", allowed=("INR", "AED", "USD", "EUR")),
        C("contract_demand_kw", "REAL", unit="kW", rule="> 0; set from baseline operations (see README); "
                                                    "peak-demand exceedance threshold"),
        C("status", allowed=ACTIVE),
        C("effective_from", "DATE", rule="<= first reading date"),
    ), relationships="1:N meter, machine, shift, tariff, weather_observation, production_batch"),

    T("meter", ("meter_id",), "Energy meter. One MAIN meter per plant; SUB meters hang under it.", (
        C("meter_id", unique=True, pattern=r"^MTR-P\d{2}-\d{3}$", rule="MTR-<plant-id>-<nnn>"),
        C("plant_id", fk=("plant", "plant_id")),
        C("meter_name"),
        C("meter_type", allowed=("MAIN", "SUB")),
        C("parent_meter_id", required=False, fk=("meter", "meter_id"),
          rule="NULL for MAIN; the plant's MAIN meter for SUB"),
        C("energy_unit", allowed=("kWh",), unit="kWh", rule="interval energy"),
        C("demand_unit", allowed=("kW",), unit="kW", rule="average demand"),
        C("sampling_interval_minutes", "INTEGER", allowed=("15",), unit="minutes"),
        C("status", allowed=ACTIVE),
        C("installed_date", "DATE"),
        C("rated_capacity_kw", "REAL", unit="kW", rule="> 0"),
    ), relationships="N:1 plant; self 1:N (main->sub); 1:1 machine via meter_machine_assignment (SUB)"),

    T("machine", ("machine_id",), "Production machine. Metered by exactly one SUB meter at a time.", (
        C("machine_id", unique=True, pattern=r"^MCH-P\d{2}-\d{3}$", rule="MCH-<plant-id>-<nnn>"),
        C("plant_id", fk=("plant", "plant_id")),
        C("machine_name"),
        C("machine_type", allowed=("CNC_MILL", "INJECTION_MOLDER", "AIR_COMPRESSOR", "INDUSTRIAL_OVEN",
                                   "HYDRAULIC_PRESS", "CONVEYOR_LINE", "CHILLER")),
        C("rated_kw", "REAL", unit="kW", rule="> 0"),
        C("idle_kw", "REAL", unit="kW", rule="0 < idle_kw < rated_kw"),
        C("standby_kw", "REAL", unit="kW", rule="0 < standby_kw < idle_kw"),
        C("weather_sensitivity_pct_per_c", "REAL", unit="% per degC above 22C", rule=">= 0"),
        C("status", allowed=ACTIVE),
        C("commissioned_date", "DATE"),
    ), relationships="N:1 plant; N:M production_batch via batch_machine; 1:N maintenance_event"),

    T("meter_machine_assignment", ("assignment_id",), "Bridge: which SUB meter measures which machine, effective-dated.", (
        C("assignment_id", unique=True, pattern=r"^ASG-MCH-P\d{2}-\d{3}-\d{2}$"),
        C("meter_id", fk=("meter", "meter_id"), rule="meter_type = SUB"),
        C("machine_id", fk=("machine", "machine_id"), rule="same plant as meter"),
        C("effective_from", "DATE"),
        C("effective_to", "DATE", required=False, rule="NULL = open; > effective_from; no overlap per machine"),
        C("status", allowed=("ACTIVE", "ENDED")),
    ), relationships="N:M meter<->machine over time; at any instant 1:1"),

    T("shift", ("shift_id",), "Shift calendar. Three shifts per local day; staffed flag drives idle-vs-standby expectation.", (
        C("shift_id", unique=True, pattern=r"^SHF-P\d{2}-\d{8}-[ABC]$", rule="SHF-<plant>-<yyyymmdd>-<A|B|C>"),
        C("plant_id", fk=("plant", "plant_id")),
        C("shift_date", "DATE", rule="plant-local date the shift starts"),
        C("shift_name", allowed=("A", "B", "C"), rule="A 06-14, B 14-22, C 22-06 local"),
        C("start_ts_utc", "TIMESTAMP"),
        C("end_ts_utc", "TIMESTAMP", rule="> start_ts_utc"),
        C("staffed", allowed=("Y", "N"), rule="C shift not staffed on Sat/Sun nights"),
    ), relationships="N:1 plant; 1:N production_batch (shift in which a batch starts)"),

    T("tariff", ("tariff_id",), "Time-of-use tariff window, effective-dated (one row per window/day-type/version).", (
        C("tariff_id", unique=True, pattern=r"^TAR-\d{4}-\d{3}$", rule="TAR-<year>-<nnn>"),
        C("plant_id", fk=("plant", "plant_id")),
        C("tariff_version", "INTEGER", rule=">= 1"),
        C("window_name", allowed=("OFF_PEAK", "SHOULDER", "PEAK")),
        C("day_type", allowed=("WEEKDAY", "WEEKEND")),
        C("start_local_time", rule="HH:MM in tariff timezone"),
        C("end_local_time", rule="HH:MM; 24:00 allowed; windows tile the day exactly"),
        C("timezone_name", rule="must equal plant.timezone_name (else TARIFF_TIMEZONE_MISMATCH)"),
        C("utc_offset_minutes", "INTEGER", unit="minutes"),
        C("rate_per_kwh", "REAL", unit="currency/kWh", rule="> 0"),
        C("demand_charge_per_kw", "REAL", unit="currency/kW/month", rule=">= 0"),
        C("currency", allowed=("INR", "AED", "USD", "EUR")),
        C("effective_from", "DATE"),
        C("effective_to", "DATE", required=False, rule="NULL = open-ended; >= effective_from"),
        C("status", allowed=("ACTIVE", "SUPERSEDED")),
    ), relationships="N:1 plant"),

    T("weather_observation", ("weather_id",), "Hourly ambient weather per plant (synthetic).", (
        C("weather_id", unique=True, pattern=r"^WX-P\d{2}-\d{10}$", rule="WX-<plant>-<yyyymmddhh UTC>"),
        C("plant_id", fk=("plant", "plant_id")),
        C("observation_ts_utc", "TIMESTAMP", rule="top of the UTC hour"),
        C("temperature_c", "REAL", unit="degC", rule="-30..60"),
        C("relative_humidity_pct", "REAL", unit="%", rule="0..100"),
        C("source", allowed=("SYNTHETIC",)),
    ), relationships="N:1 plant"),

    T("production_batch", ("batch_id",), "Production batch (output). Runs on 1..n machines (batch_machine).", (
        C("batch_id", unique=True, pattern=r"^BATCH-\d{4}-\d{6}$", rule="BATCH-<year>-<nnnnnn>, ordered by start"),
        C("plant_id", fk=("plant", "plant_id")),
        C("product_code", allowed=("PRD-A", "PRD-B", "PRD-C", "PRD-D", "PRD-E")),
        C("shift_id", fk=("shift", "shift_id"), rule="shift in which the batch starts"),
        C("planned_units", "INTEGER", unit="units", rule="> 0"),
        C("produced_units", "INTEGER", unit="units", rule="0 < produced <= planned"),
        C("start_ts_utc", "TIMESTAMP", rule="15-minute grid"),
        C("end_ts_utc", "TIMESTAMP", rule="> start; 15-minute grid"),
        C("status", allowed=("COMPLETED", "ABORTED"), rule="final state; history in batch_status_event"),
    ), relationships="N:1 plant, shift; N:M machine via batch_machine; 1:N batch_status_event"),

    T("batch_machine", ("batch_machine_id",), "Bridge: machines used by a batch (N:M).", (
        C("batch_machine_id", unique=True, pattern=r"^BM-BATCH-\d{4}-\d{6}-\d{2}$"),
        C("batch_id", fk=("production_batch", "batch_id")),
        C("machine_id", fk=("machine", "machine_id"), rule="same plant as batch; no time overlap per machine"),
        C("sequence_no", "INTEGER", rule="1..n"),
    ), relationships="N:M production_batch<->machine"),

    T("batch_status_event", ("batch_event_id",), "Append-only batch lifecycle history.", (
        C("batch_event_id", unique=True, pattern=r"^BEV-\d{4}-\d{6}$"),
        C("batch_id", fk=("production_batch", "batch_id")),
        C("event_seq", "INTEGER", rule="1..n per batch"),
        C("status", allowed=("PLANNED", "RUNNING", "COMPLETED", "ABORTED"),
          rule="PLANNED->RUNNING->COMPLETED|ABORTED"),
        C("event_ts_utc", "TIMESTAMP", rule="non-decreasing per batch"),
    ), relationships="N:1 production_batch"),

    T("energy_reading", ("reading_id", "ingest_seq"), "15-minute interval meter reading (the raw feed; may contain labelled anomalies).", (
        C("reading_id", pattern=r"^ENG-MTR-P\d{2}-\d{3}-\d{8}T\d{6}Z$", rule="ENG-<meter-id>-<interval start UTC compact>"),
        C("ingest_seq", "INTEGER", rule="1 = first delivery; >1 = re-delivery (duplicate)"),
        C("meter_id", fk=("meter", "meter_id")),
        C("machine_id", required=False, fk=("machine", "machine_id"),
          rule="NULL for MAIN meters; for SUB must be the machine assigned to the meter"),
        C("interval_start_utc", "TIMESTAMP", rule="15-minute grid"),
        C("interval_minutes", "INTEGER", allowed=("15",), unit="minutes"),
        C("energy_kwh", "REAL", unit="kWh", rule=">= 0; = avg_kw * interval_minutes/60 (+-0.002)"),
        C("avg_kw", "REAL", unit="kW", rule=">= 0"),
        C("peak_kw", "REAL", unit="kW", rule=">= avg_kw"),
    ), relationships="N:1 meter; N:1 machine (attribution)"),

    T("meter_daily_total", ("total_id",), "Meter register total per plant-local day (independent register feed).", (
        C("total_id", unique=True, pattern=r"^MDT-MTR-P\d{2}-\d{3}-\d{8}$"),
        C("meter_id", fk=("meter", "meter_id")),
        C("total_date", "DATE", rule="plant-local date"),
        C("register_kwh", "REAL", unit="kWh", rule=">= 0; should equal the sum of that day's true intervals"),
    ), relationships="N:1 meter"),

    T("machine_state_event", ("state_event_id",), "Machine operating-state history (periods; never overwritten).", (
        C("state_event_id", unique=True, pattern=r"^MSE-\d{4}-\d{6}$"),
        C("machine_id", fk=("machine", "machine_id")),
        C("state", allowed=("RUN", "IDLE", "STANDBY", "MAINTENANCE")),
        C("start_ts_utc", "TIMESTAMP"),
        C("end_ts_utc", "TIMESTAMP", rule="> start; contiguous with the previous period"),
        C("batch_id", required=False, fk=("production_batch", "batch_id"), rule="set iff state = RUN"),
    ), relationships="N:1 machine; N:1 production_batch (optional)"),

    T("maintenance_event", ("maintenance_event_id",), "Maintenance work-order lifecycle history (one row per state change).", (
        C("maintenance_event_id", unique=True, pattern=r"^MNT-\d{4}-\d{6}$"),
        C("work_order_id", pattern=r"^WO-\d{4}-\d{4}$"),
        C("machine_id", fk=("machine", "machine_id")),
        C("maintenance_type", allowed=("PREVENTIVE", "CORRECTIVE", "INSPECTION")),
        C("event_seq", "INTEGER", rule="1..n per work order"),
        C("status", allowed=("SCHEDULED", "IN_PROGRESS", "COMPLETED", "CANCELLED"),
          rule="SCHEDULED->IN_PROGRESS->COMPLETED | SCHEDULED->CANCELLED"),
        C("event_ts_utc", "TIMESTAMP", rule="non-decreasing per work order"),
        C("reason_code", allowed=("ROUTINE", "CORRECTIVE_REPAIR", "INSPECTION_REQUEST")),
    ), relationships="N:1 machine"),

    T("recommendation", ("recommendation_id",), "Energy-saving recommendation citing a measurable baseline and expected saving.", (
        C("recommendation_id", unique=True, pattern=r"^REC-\d{4}-\d{6}$"),
        C("plant_id", fk=("plant", "plant_id")),
        C("meter_id", fk=("meter", "meter_id")),
        C("machine_id", required=False, fk=("machine", "machine_id"), rule="NULL for plant-level (peak) recommendations"),
        C("recommendation_type", allowed=("REDUCE_IDLE_LOAD", "INSPECT_ENERGY_INTENSITY", "SHIFT_PEAK_LOAD")),
        C("rule_id", allowed=("REC-R1-IDLE", "REC-R2-INTENSITY", "REC-R3-PEAK")),
        C("status", allowed=("OPEN", "ACCEPTED", "REJECTED", "IMPLEMENTED"), rule="latest state; history in recommendation_status_event"),
        C("created_ts_utc", "TIMESTAMP"),
        C("baseline_metric", allowed=("offshift_excess_kwh_per_day", "kwh_per_unit", "peak_main_kw")),
        C("baseline_value", "REAL", rule="> 0, measured from readings"),
        C("baseline_unit", allowed=("kWh/day", "kWh/unit", "kW")),
        C("baseline_start_utc", "TIMESTAMP"),
        C("baseline_end_utc", "TIMESTAMP", rule="> baseline_start_utc"),
        C("observed_value", "REAL", rule="current value of the same metric"),
        C("expected_saving_kwh", "REAL", unit="kWh per 30 days", rule=">= 0"),
        C("expected_saving_kw", "REAL", required=False, unit="kW", rule=">= 0; peak recommendations"),
        C("expected_saving_cost", "REAL", unit="currency per 30 days", rule=">= 0; priced with the tariff of the baseline period"),
        C("currency", allowed=("INR", "AED", "USD", "EUR")),
        C("expected_saving_pct", "REAL", unit="%", rule="saving / baseline consumption"),
        C("related_maintenance_event_id", required=False, fk=("maintenance_event", "maintenance_event_id"),
          rule="COMPLETED maintenance that resolved an intensity issue"),
    ), relationships="N:1 plant, meter, machine; 1:N recommendation_status_event"),

    T("recommendation_status_event", ("rec_event_id",), "Append-only recommendation lifecycle history.", (
        C("rec_event_id", unique=True, pattern=r"^REV-\d{4}-\d{6}$"),
        C("recommendation_id", fk=("recommendation", "recommendation_id")),
        C("event_seq", "INTEGER"),
        C("status", allowed=("OPEN", "ACCEPTED", "REJECTED", "IMPLEMENTED"),
          rule="OPEN->ACCEPTED|REJECTED; ACCEPTED->IMPLEMENTED"),
        C("event_ts_utc", "TIMESTAMP", rule="non-decreasing per recommendation"),
    ), relationships="N:1 recommendation"),

    # ---------------- derived (computed from source tables, never generated independently) ----------------
    T("interval_cost", ("reading_id",), "DERIVED. Cleaned interval energy priced with the tariff window that applies in plant-local time.", (
        C("reading_id"), C("meter_id"), C("plant_id"), C("interval_start_utc", "TIMESTAMP"),
        C("energy_kwh", "REAL", unit="kWh", rule="after de-duplication and unit repair"),
        C("tariff_id"), C("window_name"), C("rate_per_kwh", "REAL"), C("cost", "REAL", unit="currency"),
    ), kind="derived"),
    T("meter_daily_reconciliation", ("meter_id", "total_date"), "DERIVED. Register total vs sum of intervals per meter-day.", (
        C("meter_id"), C("total_date", "DATE"), C("interval_count", "INTEGER"), C("missing_intervals", "INTEGER"),
        C("interval_sum_kwh", "REAL"), C("register_kwh", "REAL"), C("diff_kwh", "REAL"), C("diff_pct", "REAL"),
        C("status", allowed=("RECONCILED", "GAP_EXPLAINED", "MISMATCH")),
    ), kind="derived"),
    T("batch_energy_summary", ("batch_id", "machine_id"), "DERIVED. Energy and intensity (kWh/unit) per batch and machine.", (
        C("batch_id"), C("machine_id"), C("meter_id"), C("plant_id"), C("product_code"), C("units_produced", "INTEGER"),
        C("intervals_expected", "INTEGER"), C("intervals_found", "INTEGER"), C("energy_kwh", "REAL"),
        C("energy_cost", "REAL"), C("kwh_per_unit", "REAL", unit="kWh/unit"),
        C("completeness", allowed=("COMPLETE", "INCOMPLETE")),
    ), kind="derived"),
    T("peak_demand_daily", ("plant_id", "local_date", "window_name"), "DERIVED. Max MAIN-meter demand per plant, day and tariff window.", (
        C("plant_id"), C("local_date", "DATE"), C("window_name"), C("max_kw", "REAL", unit="kW"),
        C("max_kw_ts_utc", "TIMESTAMP"), C("contract_demand_kw", "REAL", unit="kW"),
        C("exceeded", allowed=("Y", "N")), C("exceed_kw", "REAL", unit="kW"),
    ), kind="derived"),
    T("data_quality_findings", ("finding_type", "entity_id"), "DERIVED. Anomalies the pipeline detected from the data alone (no ground truth).", (
        C("finding_type"), C("entity_id"), C("plant_id", required=False), C("detail"),
    ), kind="derived"),
]

BY_NAME: Dict[str, Table] = {t.name: t for t in TABLES}
SOURCE_TABLES = [t for t in TABLES if t.kind == "source"]
DERIVED_TABLES = [t for t in TABLES if t.kind == "derived"]
# load order = parents first
LOAD_ORDER = ["plant", "meter", "machine", "meter_machine_assignment", "shift", "tariff", "weather_observation",
              "production_batch", "batch_machine", "batch_status_event", "energy_reading", "meter_daily_total",
              "machine_state_event", "maintenance_event", "recommendation", "recommendation_status_event"]

GROUND_TRUTH_COLUMNS = ["truth_id", "issue_type", "entity_type", "entity_id", "plant_id", "meter_id", "machine_id",
                        "start_ts_utc", "end_ts_utc", "expected_decision", "magnitude", "magnitude_unit",
                        "rule_id", "details"]

ISSUE_TYPES = {
    "HIGH_IDLE_CONSUMPTION": ("R-IDLE-01", "RECOMMEND_SHUTDOWN_OFF_SHIFT"),
    "INTENSITY_SHIFT": ("R-INT-01", "RECOMMEND_MAINTENANCE_INSPECTION"),
    "PEAK_DEMAND_SPIKE": ("R-PEAK-01", "RECOMMEND_STAGGER_STARTS"),
    "METER_GAP": ("R-GAP-01", "FLAG_MISSING_INTERVALS_EXCLUDE_FROM_INTENSITY"),
    "METER_TOTAL_MISMATCH": ("R-TOT-01", "TRUST_INTERVAL_SUM_FLAG_REGISTER"),
    "KW_KWH_CONFUSION": ("R-UNIT-01", "RECOMPUTE_ENERGY_FROM_AVG_KW"),
    "TARIFF_TIMEZONE_MISMATCH": ("R-TZ-01", "CONVERT_TO_PLANT_LOCAL_TIME"),
    "READING_DUPLICATE": ("R-DUP-01", "DROP_LATER_DELIVERY_KEEP_INGEST_SEQ_1"),
    "MACHINE_OUTSIDE_HIERARCHY": ("R-HIER-01", "REASSIGN_TO_METER_MACHINE"),
}


def ddl_sql(tables=None) -> str:
    """CREATE TABLE statements (SQLite/ANSI compatible) for the source tables."""
    sqltype = {"TEXT": "TEXT", "INTEGER": "INTEGER", "REAL": "REAL", "DATE": "TEXT", "TIMESTAMP": "TEXT"}
    out = []
    for name in LOAD_ORDER:
        t = BY_NAME[name]
        lines = []
        for c in t.cols:
            line = f"  {c.name} {sqltype[c.type]}" + (" NOT NULL" if c.required else "")
            if c.allowed:
                vals = ", ".join("'" + a + "'" for a in c.allowed)
                if c.type == "INTEGER":
                    vals = ", ".join(c.allowed)
                line += f" CHECK ({c.name} IN ({vals}))"
            lines.append(line)
        lines.append(f"  PRIMARY KEY ({', '.join(t.pk)})")
        for c in t.cols:
            if c.fk:
                lines.append(f"  FOREIGN KEY ({c.name}) REFERENCES {c.fk[0]}({c.fk[1]})")
        out.append(f"-- {t.description}\nCREATE TABLE {t.name} (\n" + ",\n".join(lines) + "\n);")
    return "\n\n".join(out) + "\n"
