-- Manufacturing site. Parent of meters, machines, shifts, tariffs, weather, batches.
CREATE TABLE plant (
  plant_id TEXT NOT NULL,
  plant_name TEXT NOT NULL,
  country_code TEXT NOT NULL,
  timezone_name TEXT NOT NULL,
  utc_offset_minutes INTEGER NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('INR', 'AED', 'USD', 'EUR')),
  contract_demand_kw REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
  effective_from TEXT NOT NULL,
  PRIMARY KEY (plant_id)
);

-- Energy meter. One MAIN meter per plant; SUB meters hang under it.
CREATE TABLE meter (
  meter_id TEXT NOT NULL,
  plant_id TEXT NOT NULL,
  meter_name TEXT NOT NULL,
  meter_type TEXT NOT NULL CHECK (meter_type IN ('MAIN', 'SUB')),
  parent_meter_id TEXT,
  energy_unit TEXT NOT NULL CHECK (energy_unit IN ('kWh')),
  demand_unit TEXT NOT NULL CHECK (demand_unit IN ('kW')),
  sampling_interval_minutes INTEGER NOT NULL CHECK (sampling_interval_minutes IN (15)),
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
  installed_date TEXT NOT NULL,
  rated_capacity_kw REAL NOT NULL,
  PRIMARY KEY (meter_id),
  FOREIGN KEY (plant_id) REFERENCES plant(plant_id),
  FOREIGN KEY (parent_meter_id) REFERENCES meter(meter_id)
);

-- Production machine. Metered by exactly one SUB meter at a time.
CREATE TABLE machine (
  machine_id TEXT NOT NULL,
  plant_id TEXT NOT NULL,
  machine_name TEXT NOT NULL,
  machine_type TEXT NOT NULL CHECK (machine_type IN ('CNC_MILL', 'INJECTION_MOLDER', 'AIR_COMPRESSOR', 'INDUSTRIAL_OVEN', 'HYDRAULIC_PRESS', 'CONVEYOR_LINE', 'CHILLER')),
  rated_kw REAL NOT NULL,
  idle_kw REAL NOT NULL,
  standby_kw REAL NOT NULL,
  weather_sensitivity_pct_per_c REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
  commissioned_date TEXT NOT NULL,
  PRIMARY KEY (machine_id),
  FOREIGN KEY (plant_id) REFERENCES plant(plant_id)
);

-- Bridge: which SUB meter measures which machine, effective-dated.
CREATE TABLE meter_machine_assignment (
  assignment_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'ENDED')),
  PRIMARY KEY (assignment_id),
  FOREIGN KEY (meter_id) REFERENCES meter(meter_id),
  FOREIGN KEY (machine_id) REFERENCES machine(machine_id)
);

-- Shift calendar. Three shifts per local day; staffed flag drives idle-vs-standby expectation.
CREATE TABLE shift (
  shift_id TEXT NOT NULL,
  plant_id TEXT NOT NULL,
  shift_date TEXT NOT NULL,
  shift_name TEXT NOT NULL CHECK (shift_name IN ('A', 'B', 'C')),
  start_ts_utc TEXT NOT NULL,
  end_ts_utc TEXT NOT NULL,
  staffed TEXT NOT NULL CHECK (staffed IN ('Y', 'N')),
  PRIMARY KEY (shift_id),
  FOREIGN KEY (plant_id) REFERENCES plant(plant_id)
);

-- Time-of-use tariff window, effective-dated (one row per window/day-type/version).
CREATE TABLE tariff (
  tariff_id TEXT NOT NULL,
  plant_id TEXT NOT NULL,
  tariff_version INTEGER NOT NULL,
  window_name TEXT NOT NULL CHECK (window_name IN ('OFF_PEAK', 'SHOULDER', 'PEAK')),
  day_type TEXT NOT NULL CHECK (day_type IN ('WEEKDAY', 'WEEKEND')),
  start_local_time TEXT NOT NULL,
  end_local_time TEXT NOT NULL,
  timezone_name TEXT NOT NULL,
  utc_offset_minutes INTEGER NOT NULL,
  rate_per_kwh REAL NOT NULL,
  demand_charge_per_kw REAL NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('INR', 'AED', 'USD', 'EUR')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'SUPERSEDED')),
  PRIMARY KEY (tariff_id),
  FOREIGN KEY (plant_id) REFERENCES plant(plant_id)
);

-- Hourly ambient weather per plant (synthetic).
CREATE TABLE weather_observation (
  weather_id TEXT NOT NULL,
  plant_id TEXT NOT NULL,
  observation_ts_utc TEXT NOT NULL,
  temperature_c REAL NOT NULL,
  relative_humidity_pct REAL NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('SYNTHETIC')),
  PRIMARY KEY (weather_id),
  FOREIGN KEY (plant_id) REFERENCES plant(plant_id)
);

-- Production batch (output). Runs on 1..n machines (batch_machine).
CREATE TABLE production_batch (
  batch_id TEXT NOT NULL,
  plant_id TEXT NOT NULL,
  product_code TEXT NOT NULL CHECK (product_code IN ('PRD-A', 'PRD-B', 'PRD-C', 'PRD-D', 'PRD-E')),
  shift_id TEXT NOT NULL,
  planned_units INTEGER NOT NULL,
  produced_units INTEGER NOT NULL,
  start_ts_utc TEXT NOT NULL,
  end_ts_utc TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'ABORTED')),
  PRIMARY KEY (batch_id),
  FOREIGN KEY (plant_id) REFERENCES plant(plant_id),
  FOREIGN KEY (shift_id) REFERENCES shift(shift_id)
);

-- Bridge: machines used by a batch (N:M).
CREATE TABLE batch_machine (
  batch_machine_id TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  sequence_no INTEGER NOT NULL,
  PRIMARY KEY (batch_machine_id),
  FOREIGN KEY (batch_id) REFERENCES production_batch(batch_id),
  FOREIGN KEY (machine_id) REFERENCES machine(machine_id)
);

-- Append-only batch lifecycle history.
CREATE TABLE batch_status_event (
  batch_event_id TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  event_seq INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PLANNED', 'RUNNING', 'COMPLETED', 'ABORTED')),
  event_ts_utc TEXT NOT NULL,
  PRIMARY KEY (batch_event_id),
  FOREIGN KEY (batch_id) REFERENCES production_batch(batch_id)
);

-- 15-minute interval meter reading (the raw feed; may contain labelled anomalies).
CREATE TABLE energy_reading (
  reading_id TEXT NOT NULL,
  ingest_seq INTEGER NOT NULL,
  meter_id TEXT NOT NULL,
  machine_id TEXT,
  interval_start_utc TEXT NOT NULL,
  interval_minutes INTEGER NOT NULL CHECK (interval_minutes IN (15)),
  energy_kwh REAL NOT NULL,
  avg_kw REAL NOT NULL,
  peak_kw REAL NOT NULL,
  PRIMARY KEY (reading_id, ingest_seq),
  FOREIGN KEY (meter_id) REFERENCES meter(meter_id),
  FOREIGN KEY (machine_id) REFERENCES machine(machine_id)
);

-- Meter register total per plant-local day (independent register feed).
CREATE TABLE meter_daily_total (
  total_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  total_date TEXT NOT NULL,
  register_kwh REAL NOT NULL,
  PRIMARY KEY (total_id),
  FOREIGN KEY (meter_id) REFERENCES meter(meter_id)
);

-- Machine operating-state history (periods; never overwritten).
CREATE TABLE machine_state_event (
  state_event_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('RUN', 'IDLE', 'STANDBY', 'MAINTENANCE')),
  start_ts_utc TEXT NOT NULL,
  end_ts_utc TEXT NOT NULL,
  batch_id TEXT,
  PRIMARY KEY (state_event_id),
  FOREIGN KEY (machine_id) REFERENCES machine(machine_id),
  FOREIGN KEY (batch_id) REFERENCES production_batch(batch_id)
);

-- Maintenance work-order lifecycle history (one row per state change).
CREATE TABLE maintenance_event (
  maintenance_event_id TEXT NOT NULL,
  work_order_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  maintenance_type TEXT NOT NULL CHECK (maintenance_type IN ('PREVENTIVE', 'CORRECTIVE', 'INSPECTION')),
  event_seq INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('SCHEDULED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
  event_ts_utc TEXT NOT NULL,
  reason_code TEXT NOT NULL CHECK (reason_code IN ('ROUTINE', 'CORRECTIVE_REPAIR', 'INSPECTION_REQUEST')),
  PRIMARY KEY (maintenance_event_id),
  FOREIGN KEY (machine_id) REFERENCES machine(machine_id)
);

-- Energy-saving recommendation citing a measurable baseline and expected saving.
CREATE TABLE recommendation (
  recommendation_id TEXT NOT NULL,
  plant_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  machine_id TEXT,
  recommendation_type TEXT NOT NULL CHECK (recommendation_type IN ('REDUCE_IDLE_LOAD', 'INSPECT_ENERGY_INTENSITY', 'SHIFT_PEAK_LOAD')),
  rule_id TEXT NOT NULL CHECK (rule_id IN ('REC-R1-IDLE', 'REC-R2-INTENSITY', 'REC-R3-PEAK')),
  status TEXT NOT NULL CHECK (status IN ('OPEN', 'ACCEPTED', 'REJECTED', 'IMPLEMENTED')),
  created_ts_utc TEXT NOT NULL,
  baseline_metric TEXT NOT NULL CHECK (baseline_metric IN ('offshift_excess_kwh_per_day', 'kwh_per_unit', 'peak_main_kw')),
  baseline_value REAL NOT NULL,
  baseline_unit TEXT NOT NULL CHECK (baseline_unit IN ('kWh/day', 'kWh/unit', 'kW')),
  baseline_start_utc TEXT NOT NULL,
  baseline_end_utc TEXT NOT NULL,
  observed_value REAL NOT NULL,
  expected_saving_kwh REAL NOT NULL,
  expected_saving_kw REAL,
  expected_saving_cost REAL NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('INR', 'AED', 'USD', 'EUR')),
  expected_saving_pct REAL NOT NULL,
  related_maintenance_event_id TEXT,
  PRIMARY KEY (recommendation_id),
  FOREIGN KEY (plant_id) REFERENCES plant(plant_id),
  FOREIGN KEY (meter_id) REFERENCES meter(meter_id),
  FOREIGN KEY (machine_id) REFERENCES machine(machine_id),
  FOREIGN KEY (related_maintenance_event_id) REFERENCES maintenance_event(maintenance_event_id)
);

-- Append-only recommendation lifecycle history.
CREATE TABLE recommendation_status_event (
  rec_event_id TEXT NOT NULL,
  recommendation_id TEXT NOT NULL,
  event_seq INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('OPEN', 'ACCEPTED', 'REJECTED', 'IMPLEMENTED')),
  event_ts_utc TEXT NOT NULL,
  PRIMARY KEY (rec_event_id),
  FOREIGN KEY (recommendation_id) REFERENCES recommendation(recommendation_id)
);
