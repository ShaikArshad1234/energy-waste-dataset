# Data dictionary

Generated from `energygen/schema.py` (single source of truth for DDL, validation and this document).

Conventions: `snake_case`; timestamps ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`); dates `YYYY-MM-DD` (plant-local where stated); empty CSV cell = NULL; energy in **kWh** (interval energy), demand in **kW** (average over the interval), sampling cadence **15 minutes**, so `energy_kwh = avg_kw x 0.25`.

## ID conventions

| entity | pattern | example |
|---|---|---|
| plant | `P<nn>` | P01 |
| meter | `MTR-<plant-id>-<nnn>` | MTR-P01-001 |
| machine | `MCH-<plant-id>-<nnn>` | MCH-P01-003 |
| energy reading | `ENG-<meter-id>-<yyyymmddThhmmssZ>` | ENG-MTR-P01-001-20260701T001500Z |
| production batch | `BATCH-<year>-<nnnnnn>` (ordered by start) | BATCH-2026-000001 |
| tariff | `TAR-<year>-<nnn>` | TAR-2026-001 |
| recommendation | `REC-<year>-<nnnnnn>` | REC-2026-000001 |
| shift | `SHF-<plant>-<yyyymmdd>-<A/B/C>` | SHF-P01-20260701-A |
| other events | `BEV/MSE/MNT/REV-<year>-<nnnnnn>`, `WO-<year>-<nnnn>`, `ASG-<machine>-<nn>`, `BM-<batch>-<nn>`, `MDT-<meter>-<yyyymmdd>`, `WX-<plant>-<yyyymmddhh>` | |
| ground truth | `GT-<nnnnnn>` | GT-000001 |

IDs are deterministic (counters zero-padded, assigned in a fixed sort order) and immutable; foreign keys use the exact same strings.

## Source tables

### `plant`

Manufacturing site. Parent of meters, machines, shifts, tariffs, weather, batches.

* **Primary key:** `plant_id`
* **Relationships / cardinality:** 1:N meter, machine, shift, tariff, weather_observation, production_batch

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `plant_id` | TEXT | yes |  |  | yes | P<nn>; immutable; pattern `^P\d{2}$` |  |
| `plant_name` | TEXT | yes |  |  |  |  |  |
| `country_code` | TEXT | yes |  |  |  | ISO-3166 alpha-2 |  |
| `timezone_name` | TEXT | yes |  |  |  | IANA name; fixed UTC offset used (no DST) |  |
| `utc_offset_minutes` | INTEGER | yes |  | minutes |  | multiple of 15; matches timezone_name |  |
| `currency` | TEXT | yes | INR, AED, USD, EUR |  |  |  |  |
| `contract_demand_kw` | REAL | yes |  | kW |  | > 0; set from baseline operations (see README); peak-demand exceedance threshold |  |
| `status` | TEXT | yes | ACTIVE, INACTIVE |  |  |  |  |
| `effective_from` | DATE | yes |  |  |  | <= first reading date |  |

### `meter`

Energy meter. One MAIN meter per plant; SUB meters hang under it.

* **Primary key:** `meter_id`
* **Relationships / cardinality:** N:1 plant; self 1:N (main->sub); 1:1 machine via meter_machine_assignment (SUB)

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `meter_id` | TEXT | yes |  |  | yes | MTR-<plant-id>-<nnn>; pattern `^MTR-P\d{2}-\d{3}$` |  |
| `plant_id` | TEXT | yes |  |  |  |  | plant.plant_id |
| `meter_name` | TEXT | yes |  |  |  |  |  |
| `meter_type` | TEXT | yes | MAIN, SUB |  |  |  |  |
| `parent_meter_id` | TEXT | no |  |  |  | NULL for MAIN; the plant's MAIN meter for SUB | meter.meter_id |
| `energy_unit` | TEXT | yes | kWh | kWh |  | interval energy |  |
| `demand_unit` | TEXT | yes | kW | kW |  | average demand |  |
| `sampling_interval_minutes` | INTEGER | yes | 15 | minutes |  |  |  |
| `status` | TEXT | yes | ACTIVE, INACTIVE |  |  |  |  |
| `installed_date` | DATE | yes |  |  |  |  |  |
| `rated_capacity_kw` | REAL | yes |  | kW |  | > 0 |  |

### `machine`

Production machine. Metered by exactly one SUB meter at a time.

* **Primary key:** `machine_id`
* **Relationships / cardinality:** N:1 plant; N:M production_batch via batch_machine; 1:N maintenance_event

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `machine_id` | TEXT | yes |  |  | yes | MCH-<plant-id>-<nnn>; pattern `^MCH-P\d{2}-\d{3}$` |  |
| `plant_id` | TEXT | yes |  |  |  |  | plant.plant_id |
| `machine_name` | TEXT | yes |  |  |  |  |  |
| `machine_type` | TEXT | yes | CNC_MILL, INJECTION_MOLDER, AIR_COMPRESSOR, INDUSTRIAL_OVEN, HYDRAULIC_PRESS, CONVEYOR_LINE, CHILLER |  |  |  |  |
| `rated_kw` | REAL | yes |  | kW |  | > 0 |  |
| `idle_kw` | REAL | yes |  | kW |  | 0 < idle_kw < rated_kw |  |
| `standby_kw` | REAL | yes |  | kW |  | 0 < standby_kw < idle_kw |  |
| `weather_sensitivity_pct_per_c` | REAL | yes |  | % per degC above 22C |  | >= 0 |  |
| `status` | TEXT | yes | ACTIVE, INACTIVE |  |  |  |  |
| `commissioned_date` | DATE | yes |  |  |  |  |  |

### `meter_machine_assignment`

Bridge: which SUB meter measures which machine, effective-dated.

* **Primary key:** `assignment_id`
* **Relationships / cardinality:** N:M meter<->machine over time; at any instant 1:1

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `assignment_id` | TEXT | yes |  |  | yes | pattern `^ASG-MCH-P\d{2}-\d{3}-\d{2}$` |  |
| `meter_id` | TEXT | yes |  |  |  | meter_type = SUB | meter.meter_id |
| `machine_id` | TEXT | yes |  |  |  | same plant as meter | machine.machine_id |
| `effective_from` | DATE | yes |  |  |  |  |  |
| `effective_to` | DATE | no |  |  |  | NULL = open; > effective_from; no overlap per machine |  |
| `status` | TEXT | yes | ACTIVE, ENDED |  |  |  |  |

### `shift`

Shift calendar. Three shifts per local day; staffed flag drives idle-vs-standby expectation.

* **Primary key:** `shift_id`
* **Relationships / cardinality:** N:1 plant; 1:N production_batch (shift in which a batch starts)

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `shift_id` | TEXT | yes |  |  | yes | SHF-<plant>-<yyyymmdd>-<A/B/C>; pattern `^SHF-P\d{2}-\d{8}-[ABC]$` |  |
| `plant_id` | TEXT | yes |  |  |  |  | plant.plant_id |
| `shift_date` | DATE | yes |  |  |  | plant-local date the shift starts |  |
| `shift_name` | TEXT | yes | A, B, C |  |  | A 06-14, B 14-22, C 22-06 local |  |
| `start_ts_utc` | TIMESTAMP | yes |  |  |  |  |  |
| `end_ts_utc` | TIMESTAMP | yes |  |  |  | > start_ts_utc |  |
| `staffed` | TEXT | yes | Y, N |  |  | C shift not staffed on Sat/Sun nights |  |

### `tariff`

Time-of-use tariff window, effective-dated (one row per window/day-type/version).

* **Primary key:** `tariff_id`
* **Relationships / cardinality:** N:1 plant

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `tariff_id` | TEXT | yes |  |  | yes | TAR-<year>-<nnn>; pattern `^TAR-\d{4}-\d{3}$` |  |
| `plant_id` | TEXT | yes |  |  |  |  | plant.plant_id |
| `tariff_version` | INTEGER | yes |  |  |  | >= 1 |  |
| `window_name` | TEXT | yes | OFF_PEAK, SHOULDER, PEAK |  |  |  |  |
| `day_type` | TEXT | yes | WEEKDAY, WEEKEND |  |  |  |  |
| `start_local_time` | TEXT | yes |  |  |  | HH:MM in tariff timezone |  |
| `end_local_time` | TEXT | yes |  |  |  | HH:MM; 24:00 allowed; windows tile the day exactly |  |
| `timezone_name` | TEXT | yes |  |  |  | must equal plant.timezone_name (else TARIFF_TIMEZONE_MISMATCH) |  |
| `utc_offset_minutes` | INTEGER | yes |  | minutes |  |  |  |
| `rate_per_kwh` | REAL | yes |  | currency/kWh |  | > 0 |  |
| `demand_charge_per_kw` | REAL | yes |  | currency/kW/month |  | >= 0 |  |
| `currency` | TEXT | yes | INR, AED, USD, EUR |  |  |  |  |
| `effective_from` | DATE | yes |  |  |  |  |  |
| `effective_to` | DATE | no |  |  |  | NULL = open-ended; >= effective_from |  |
| `status` | TEXT | yes | ACTIVE, SUPERSEDED |  |  |  |  |

### `weather_observation`

Hourly ambient weather per plant (synthetic).

* **Primary key:** `weather_id`
* **Relationships / cardinality:** N:1 plant

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `weather_id` | TEXT | yes |  |  | yes | WX-<plant>-<yyyymmddhh UTC>; pattern `^WX-P\d{2}-\d{10}$` |  |
| `plant_id` | TEXT | yes |  |  |  |  | plant.plant_id |
| `observation_ts_utc` | TIMESTAMP | yes |  |  |  | top of the UTC hour |  |
| `temperature_c` | REAL | yes |  | degC |  | -30..60 |  |
| `relative_humidity_pct` | REAL | yes |  | % |  | 0..100 |  |
| `source` | TEXT | yes | SYNTHETIC |  |  |  |  |

### `production_batch`

Production batch (output). Runs on 1..n machines (batch_machine).

* **Primary key:** `batch_id`
* **Relationships / cardinality:** N:1 plant, shift; N:M machine via batch_machine; 1:N batch_status_event

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `batch_id` | TEXT | yes |  |  | yes | BATCH-<year>-<nnnnnn>, ordered by start; pattern `^BATCH-\d{4}-\d{6}$` |  |
| `plant_id` | TEXT | yes |  |  |  |  | plant.plant_id |
| `product_code` | TEXT | yes | PRD-A, PRD-B, PRD-C, PRD-D, PRD-E |  |  |  |  |
| `shift_id` | TEXT | yes |  |  |  | shift in which the batch starts | shift.shift_id |
| `planned_units` | INTEGER | yes |  | units |  | > 0 |  |
| `produced_units` | INTEGER | yes |  | units |  | 0 < produced <= planned |  |
| `start_ts_utc` | TIMESTAMP | yes |  |  |  | 15-minute grid |  |
| `end_ts_utc` | TIMESTAMP | yes |  |  |  | > start; 15-minute grid |  |
| `status` | TEXT | yes | COMPLETED, ABORTED |  |  | final state; history in batch_status_event |  |

### `batch_machine`

Bridge: machines used by a batch (N:M).

* **Primary key:** `batch_machine_id`
* **Relationships / cardinality:** N:M production_batch<->machine

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `batch_machine_id` | TEXT | yes |  |  | yes | pattern `^BM-BATCH-\d{4}-\d{6}-\d{2}$` |  |
| `batch_id` | TEXT | yes |  |  |  |  | production_batch.batch_id |
| `machine_id` | TEXT | yes |  |  |  | same plant as batch; no time overlap per machine | machine.machine_id |
| `sequence_no` | INTEGER | yes |  |  |  | 1..n |  |

### `batch_status_event`

Append-only batch lifecycle history.

* **Primary key:** `batch_event_id`
* **Relationships / cardinality:** N:1 production_batch

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `batch_event_id` | TEXT | yes |  |  | yes | pattern `^BEV-\d{4}-\d{6}$` |  |
| `batch_id` | TEXT | yes |  |  |  |  | production_batch.batch_id |
| `event_seq` | INTEGER | yes |  |  |  | 1..n per batch |  |
| `status` | TEXT | yes | PLANNED, RUNNING, COMPLETED, ABORTED |  |  | PLANNED->RUNNING->COMPLETED/ABORTED |  |
| `event_ts_utc` | TIMESTAMP | yes |  |  |  | non-decreasing per batch |  |

### `energy_reading`

15-minute interval meter reading (the raw feed; may contain labelled anomalies).

* **Primary key:** `reading_id, ingest_seq`
* **Relationships / cardinality:** N:1 meter; N:1 machine (attribution)

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `reading_id` | TEXT | yes |  |  | part of PK | ENG-<meter-id>-<interval start UTC compact>; pattern `^ENG-MTR-P\d{2}-\d{3}-\d{8}T\d{6}Z$` |  |
| `ingest_seq` | INTEGER | yes |  |  | part of PK | 1 = first delivery; >1 = re-delivery (duplicate) |  |
| `meter_id` | TEXT | yes |  |  |  |  | meter.meter_id |
| `machine_id` | TEXT | no |  |  |  | NULL for MAIN meters; for SUB must be the machine assigned to the meter | machine.machine_id |
| `interval_start_utc` | TIMESTAMP | yes |  |  |  | 15-minute grid |  |
| `interval_minutes` | INTEGER | yes | 15 | minutes |  |  |  |
| `energy_kwh` | REAL | yes |  | kWh |  | >= 0; = avg_kw * interval_minutes/60 (+-0.002) |  |
| `avg_kw` | REAL | yes |  | kW |  | >= 0 |  |
| `peak_kw` | REAL | yes |  | kW |  | >= avg_kw |  |

### `meter_daily_total`

Meter register total per plant-local day (independent register feed).

* **Primary key:** `total_id`
* **Relationships / cardinality:** N:1 meter

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `total_id` | TEXT | yes |  |  | yes | pattern `^MDT-MTR-P\d{2}-\d{3}-\d{8}$` |  |
| `meter_id` | TEXT | yes |  |  |  |  | meter.meter_id |
| `total_date` | DATE | yes |  |  |  | plant-local date |  |
| `register_kwh` | REAL | yes |  | kWh |  | >= 0; should equal the sum of that day's true intervals |  |

### `machine_state_event`

Machine operating-state history (periods; never overwritten).

* **Primary key:** `state_event_id`
* **Relationships / cardinality:** N:1 machine; N:1 production_batch (optional)

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `state_event_id` | TEXT | yes |  |  | yes | pattern `^MSE-\d{4}-\d{6}$` |  |
| `machine_id` | TEXT | yes |  |  |  |  | machine.machine_id |
| `state` | TEXT | yes | RUN, IDLE, STANDBY, MAINTENANCE |  |  |  |  |
| `start_ts_utc` | TIMESTAMP | yes |  |  |  |  |  |
| `end_ts_utc` | TIMESTAMP | yes |  |  |  | > start; contiguous with the previous period |  |
| `batch_id` | TEXT | no |  |  |  | set iff state = RUN | production_batch.batch_id |

### `maintenance_event`

Maintenance work-order lifecycle history (one row per state change).

* **Primary key:** `maintenance_event_id`
* **Relationships / cardinality:** N:1 machine

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `maintenance_event_id` | TEXT | yes |  |  | yes | pattern `^MNT-\d{4}-\d{6}$` |  |
| `work_order_id` | TEXT | yes |  |  |  | pattern `^WO-\d{4}-\d{4}$` |  |
| `machine_id` | TEXT | yes |  |  |  |  | machine.machine_id |
| `maintenance_type` | TEXT | yes | PREVENTIVE, CORRECTIVE, INSPECTION |  |  |  |  |
| `event_seq` | INTEGER | yes |  |  |  | 1..n per work order |  |
| `status` | TEXT | yes | SCHEDULED, IN_PROGRESS, COMPLETED, CANCELLED |  |  | SCHEDULED->IN_PROGRESS->COMPLETED / SCHEDULED->CANCELLED |  |
| `event_ts_utc` | TIMESTAMP | yes |  |  |  | non-decreasing per work order |  |
| `reason_code` | TEXT | yes | ROUTINE, CORRECTIVE_REPAIR, INSPECTION_REQUEST |  |  |  |  |

### `recommendation`

Energy-saving recommendation citing a measurable baseline and expected saving.

* **Primary key:** `recommendation_id`
* **Relationships / cardinality:** N:1 plant, meter, machine; 1:N recommendation_status_event

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `recommendation_id` | TEXT | yes |  |  | yes | pattern `^REC-\d{4}-\d{6}$` |  |
| `plant_id` | TEXT | yes |  |  |  |  | plant.plant_id |
| `meter_id` | TEXT | yes |  |  |  |  | meter.meter_id |
| `machine_id` | TEXT | no |  |  |  | NULL for plant-level (peak) recommendations | machine.machine_id |
| `recommendation_type` | TEXT | yes | REDUCE_IDLE_LOAD, INSPECT_ENERGY_INTENSITY, SHIFT_PEAK_LOAD |  |  |  |  |
| `rule_id` | TEXT | yes | REC-R1-IDLE, REC-R2-INTENSITY, REC-R3-PEAK |  |  |  |  |
| `status` | TEXT | yes | OPEN, ACCEPTED, REJECTED, IMPLEMENTED |  |  | latest state; history in recommendation_status_event |  |
| `created_ts_utc` | TIMESTAMP | yes |  |  |  |  |  |
| `baseline_metric` | TEXT | yes | offshift_excess_kwh_per_day, kwh_per_unit, peak_main_kw |  |  |  |  |
| `baseline_value` | REAL | yes |  |  |  | > 0, measured from readings |  |
| `baseline_unit` | TEXT | yes | kWh/day, kWh/unit, kW |  |  |  |  |
| `baseline_start_utc` | TIMESTAMP | yes |  |  |  |  |  |
| `baseline_end_utc` | TIMESTAMP | yes |  |  |  | > baseline_start_utc |  |
| `observed_value` | REAL | yes |  |  |  | current value of the same metric |  |
| `expected_saving_kwh` | REAL | yes |  | kWh per 30 days |  | >= 0 |  |
| `expected_saving_kw` | REAL | no |  | kW |  | >= 0; peak recommendations |  |
| `expected_saving_cost` | REAL | yes |  | currency per 30 days |  | >= 0; priced with the tariff of the baseline period |  |
| `currency` | TEXT | yes | INR, AED, USD, EUR |  |  |  |  |
| `expected_saving_pct` | REAL | yes |  | % |  | saving / baseline consumption |  |
| `related_maintenance_event_id` | TEXT | no |  |  |  | COMPLETED maintenance that resolved an intensity issue | maintenance_event.maintenance_event_id |

### `recommendation_status_event`

Append-only recommendation lifecycle history.

* **Primary key:** `rec_event_id`
* **Relationships / cardinality:** N:1 recommendation

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `rec_event_id` | TEXT | yes |  |  | yes | pattern `^REV-\d{4}-\d{6}$` |  |
| `recommendation_id` | TEXT | yes |  |  |  |  | recommendation.recommendation_id |
| `event_seq` | INTEGER | yes |  |  |  |  |  |
| `status` | TEXT | yes | OPEN, ACCEPTED, REJECTED, IMPLEMENTED |  |  | OPEN->ACCEPTED/REJECTED; ACCEPTED->IMPLEMENTED |  |
| `event_ts_utc` | TIMESTAMP | yes |  |  |  | non-decreasing per recommendation |  |

## Derived tables (computed from source tables)

### `interval_cost`

DERIVED. Cleaned interval energy priced with the tariff window that applies in plant-local time.

* **Primary key:** `reading_id`

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `reading_id` | TEXT | yes |  |  | part of PK |  |  |
| `meter_id` | TEXT | yes |  |  |  |  |  |
| `plant_id` | TEXT | yes |  |  |  |  |  |
| `interval_start_utc` | TIMESTAMP | yes |  |  |  |  |  |
| `energy_kwh` | REAL | yes |  | kWh |  | after de-duplication and unit repair |  |
| `tariff_id` | TEXT | yes |  |  |  |  |  |
| `window_name` | TEXT | yes |  |  |  |  |  |
| `rate_per_kwh` | REAL | yes |  |  |  |  |  |
| `cost` | REAL | yes |  | currency |  |  |  |

### `meter_daily_reconciliation`

DERIVED. Register total vs sum of intervals per meter-day.

* **Primary key:** `meter_id, total_date`

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `meter_id` | TEXT | yes |  |  | part of PK |  |  |
| `total_date` | DATE | yes |  |  | part of PK |  |  |
| `interval_count` | INTEGER | yes |  |  |  |  |  |
| `missing_intervals` | INTEGER | yes |  |  |  |  |  |
| `interval_sum_kwh` | REAL | yes |  |  |  |  |  |
| `register_kwh` | REAL | yes |  |  |  |  |  |
| `diff_kwh` | REAL | yes |  |  |  |  |  |
| `diff_pct` | REAL | yes |  |  |  |  |  |
| `status` | TEXT | yes | RECONCILED, GAP_EXPLAINED, MISMATCH |  |  |  |  |

### `batch_energy_summary`

DERIVED. Energy and intensity (kWh/unit) per batch and machine.

* **Primary key:** `batch_id, machine_id`

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `batch_id` | TEXT | yes |  |  | part of PK |  |  |
| `machine_id` | TEXT | yes |  |  | part of PK |  |  |
| `meter_id` | TEXT | yes |  |  |  |  |  |
| `plant_id` | TEXT | yes |  |  |  |  |  |
| `product_code` | TEXT | yes |  |  |  |  |  |
| `units_produced` | INTEGER | yes |  |  |  |  |  |
| `intervals_expected` | INTEGER | yes |  |  |  |  |  |
| `intervals_found` | INTEGER | yes |  |  |  |  |  |
| `energy_kwh` | REAL | yes |  |  |  |  |  |
| `energy_cost` | REAL | yes |  |  |  |  |  |
| `kwh_per_unit` | REAL | yes |  | kWh/unit |  |  |  |
| `completeness` | TEXT | yes | COMPLETE, INCOMPLETE |  |  |  |  |

### `peak_demand_daily`

DERIVED. Max MAIN-meter demand per plant, day and tariff window.

* **Primary key:** `plant_id, local_date, window_name`

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `plant_id` | TEXT | yes |  |  | part of PK |  |  |
| `local_date` | DATE | yes |  |  | part of PK |  |  |
| `window_name` | TEXT | yes |  |  | part of PK |  |  |
| `max_kw` | REAL | yes |  | kW |  |  |  |
| `max_kw_ts_utc` | TIMESTAMP | yes |  |  |  |  |  |
| `contract_demand_kw` | REAL | yes |  | kW |  |  |  |
| `exceeded` | TEXT | yes | Y, N |  |  |  |  |
| `exceed_kw` | REAL | yes |  | kW |  |  |  |

### `data_quality_findings`

DERIVED. Anomalies the pipeline detected from the data alone (no ground truth).

* **Primary key:** `finding_type, entity_id`

| field | type | required | allowed values | unit | unique | validation rule | references |
|---|---|---|---|---|---|---|---|
| `finding_type` | TEXT | yes |  |  | part of PK |  |  |
| `entity_id` | TEXT | yes |  |  | part of PK |  |  |
| `plant_id` | TEXT | no |  |  |  |  |  |
| `detail` | TEXT | yes |  |  |  |  |  |

## Ground truth (`ground_truth/ground_truth.csv|json`)

Kept in a separate folder; never an input feature. One row per labelled episode / record.

| field | meaning |
|---|---|
| `truth_id` | GT-<nnnnnn> |
| `issue_type` | see table below |
| `entity_type` | table/entity the label refers to |
| `entity_id` | key of the affected entity (comma list for tariff rows) |
| `plant_id` |  |
| `meter_id` |  |
| `machine_id` |  |
| `start_ts_utc` | episode start |
| `end_ts_utc` | episode end (exclusive) |
| `expected_decision` | what a correct pipeline should do |
| `magnitude` | size of the issue (kWh, kW or currency) |
| `magnitude_unit` |  |
| `rule_id` | documented rule that produced the label |
| `details` | JSON with rule parameters |

| issue_type | rule_id | expected_decision |
|---|---|---|
| HIGH_IDLE_CONSUMPTION | R-IDLE-01 | RECOMMEND_SHUTDOWN_OFF_SHIFT |
| INTENSITY_SHIFT | R-INT-01 | RECOMMEND_MAINTENANCE_INSPECTION |
| PEAK_DEMAND_SPIKE | R-PEAK-01 | RECOMMEND_STAGGER_STARTS |
| METER_GAP | R-GAP-01 | FLAG_MISSING_INTERVALS_EXCLUDE_FROM_INTENSITY |
| METER_TOTAL_MISMATCH | R-TOT-01 | TRUST_INTERVAL_SUM_FLAG_REGISTER |
| KW_KWH_CONFUSION | R-UNIT-01 | RECOMPUTE_ENERGY_FROM_AVG_KW |
| TARIFF_TIMEZONE_MISMATCH | R-TZ-01 | CONVERT_TO_PLANT_LOCAL_TIME |
| READING_DUPLICATE | R-DUP-01 | DROP_LATER_DELIVERY_KEEP_INGEST_SEQ_1 |
| MACHINE_OUTSIDE_HIERARCHY | R-HIER-01 | REASSIGN_TO_METER_MACHINE |
