# Energy waste & peak-demand synthetic dataset generator

Correlates **machine telemetry, production output, time-of-use tariffs and weather** to expose **energy waste**
(idle load, efficiency drift) and **peak demand**. A single deterministic generator produces a normalised dataset
(16 source tables), derived values computed *from* that data, labelled edge cases, a **separate ground truth**,
validation + detection-quality reports, SQL/CSV/JSON exports and a reproducibility proof.

* Python 3.10+, **standard library only** (`pytest` for tests). No external data, no secrets.
* Fixed seed **20261022**; the same generator makes the small starter set and the full set (parameters only differ).

## Quick start (clean machine)
```bash
git clone <this-repo> && cd <repo>
python3 -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt                           # installs pytest only
python -m pytest                                          # 48 tests (~35 s)

python -m energygen generate --preset starter --out data/starter    # 8 meters, 14 days, 80 batches (~2 s)
python -m energygen generate --preset full --out out/full           # 20 meters, 90 days, 2,000 batches (~30 s)
python -m energygen repro-check --preset full                       # generate twice, compare row counts + sha256
```
Exact commands used for the committed data are in `data/*/generator_command.txt`.
`make test | starter | full | repro | dictionary` are shortcuts.

| command | purpose |
|---|---|
| `generate --out DIR [--preset starter\|full] [--seed --plants --meters --days --batches --start-date --formats csv,json,sql]` | generate, validate, evaluate, export |
| `validate --data DIR` | reload CSVs from disk, re-derive everything, re-validate |
| `repro-check [--preset ...] [--out file.json]` | Step 10: two runs, same seed, compare row counts + checksums |
| `dictionary --out docs/DATA_DICTIONARY.md` | regenerate the data dictionary from the schema |

Infeasible parameters fail loudly (e.g. more batches than the machines can physically run).

## What is in the repository
```
energygen/          generator, schema (single source of truth), derive, recommend, validate, evaluate, export, cli
tests/              48 tests
data/starter/       small dataset: source/ derived/ ground_truth/ json/ sql/ reports/ source_manifest.json
data/full/          full dataset (CSV): 171k readings, 2,000 batches, ... + reports + reproducibility.json
docs/               DATA_DICTIONARY.md  DESIGN.md  AI_USAGE.md  PROMPT_HISTORY.md  test_results.txt
```
Inside each dataset folder: `source/*.csv` (inputs) · `derived/*.csv` (computed from inputs) ·
`ground_truth/ground_truth.{csv,json}` (**never an input**) · `sql/schema.sql + seed.sql` · `json/` ·
`reports/validation_summary.{md,json}`, `evaluation.json`, `reproducibility.json` · `source_manifest.json`
(provenance, parameters, per-file rows + sha256) · `generator_command.txt`.

## Entities and relationships
```
plant 1─N meter(MAIN|SUB) ──self 1:N── (MAIN → SUB)
plant 1─N machine        meter N─M machine  via meter_machine_assignment (effective-dated; 1:1 at any instant)
plant 1─N shift          plant 1─N tariff (windows, versioned, effective-dated)     plant 1─N weather_observation
plant 1─N production_batch ─N:M─ machine  via batch_machine      production_batch 1─N batch_status_event
meter 1─N energy_reading (15 min)   meter 1─N meter_daily_total (register)
machine 1─N machine_state_event     machine 1─N maintenance_event (one row per state change of a work order)
recommendation N─1 plant/meter/machine; 1─N recommendation_status_event; optional link to a COMPLETED maintenance_event
```
Full field-level dictionary (type, required, allowed values, units, uniqueness, validation rule, FK) is in
`docs/DATA_DICTIONARY.md`, generated from `energygen/schema.py` which also produces the DDL and drives validation.

## How the data is generated (rules, not independent randomness)
1. **Master data first**: 2 plants (UTC+5:30 and UTC+4, no DST) → 1 MAIN meter + SUB meters per plant → one machine per SUB meter,
   effective-dated assignment bridge. 20 meters = 2 mains + 18 submeters/machines.
2. **Shift calendar** (A 06-14 Mon-Sat, B 14-22 Mon-Fri, C 22-06 starting Mon-Wed nights). `staffed` decides whether idling is expected.
3. **Weather** (hourly; diurnal + trend + AR noise). Weather raises machine load (`weather_sensitivity_pct_per_c`, compressors/chillers most)
   and the unmetered facility/HVAC load.
4. **Maintenance** work orders are planned first and block machines; batches are then placed in staffed time with no machine double-booking.
   Peak "stacking" days start several batches together at 10:00 local.
5. **Telemetry**: per 15-minute interval each machine is `RUN` (load factor x product x weather x noise x degradation, 1.35x start-up spike),
   `IDLE` (staffed, no batch), `STANDBY` (unstaffed) or `MAINTENANCE` (0 kW). SUB meters report the machine; the MAIN meter reports
   Σ sub-meters + unmetered facility load. `energy_kwh = avg_kw x 0.25`.
6. **Waste scenarios (labelled in ground truth):**
   * `HIGH_IDLE_CONSUMPTION` (rule R-IDLE-01): selected machines stay idling instead of going to standby while unstaffed, for a date range.
   * `INTENSITY_SHIFT` (R-INT-01): selected machines draw 1.18-1.40x per unit of output from a date until corrective maintenance completes
     (aborted batches are also more likely while degraded).
   * `PEAK_DEMAND_SPIKE` (R-PEAK-01): MAIN-meter interval demand above the plant's `contract_demand_kw`
     (= 1.02 x the 99.7th percentile of normal operation, rounded up to 5 kW).
7. **Edge cases injected on distinct rows (label, never silent corruption):**

| issue_type | what is injected | expected decision |
|---|---|---|
| METER_TOTAL_MISMATCH | register daily total differs ±4-12% from the true interval sum (days without gaps) | trust interval sum, flag register |
| KW_KWH_CONFUSION | the kW value is written into `energy_kwh` | recompute energy from `avg_kw` |
| TARIFF_TIMEZONE_MISMATCH | last plant's tariff v2 declared in `UTC` instead of the plant timezone | convert to plant-local time |
| READING_DUPLICATE | re-delivery of an interval (`ingest_seq=2`), 1 in 6 with conflicting values | keep `ingest_seq=1` |
| MACHINE_OUTSIDE_HIERARCHY | reading attributed to a machine not under that meter | reassign to the meter's machine |
| METER_GAP | missing 15-minute intervals (comms outage); the register still counts the energy | flag, exclude from intensity |

## Derived values (computed from source tables only — `energygen/derive.py`)
* **Cleaning**: drop later deliveries, repair kW/kWh confusion, attribute by assignment bridge.
* **Cost** = cleaned kWh x rate of the tariff window found in **plant-local** time with the version chosen by plant-local date (`interval_cost`).
* **Meter reconciliation**: register vs interval sum → `RECONCILED | GAP_EXPLAINED | MISMATCH`.
* **Intensity** (`batch_energy_summary`): kWh per produced unit per batch/machine; `INCOMPLETE` when intervals are missing.
* **Peak demand** per plant/day/tariff window vs contract demand; **idle excess** per machine-day above standby.
* **Recommendations** (`energygen/recommend.py`) cite a measured baseline and a 30-day expected saving (90% capture):
  `REC-R1-IDLE` (≥3 days of off-shift excess ≥3 kWh), `REC-R2-INTENSITY` (weekly median kWh/unit index >1.12 vs the machine+product baseline),
  `REC-R3-PEAK` (≥2 days above contract demand; saving = excess kW x demand charge). Status history is stored as events.

## Validation (137 checks) and evidence
`reports/validation_summary.md` per dataset. Covers primary-key uniqueness, FK integrity (all 30+ relationships), enums, ID patterns,
required fields/types, date ordering, tariff tiling, batch/machine overlap, lifecycle transitions (batch, maintenance, recommendation,
machine state), energy reconciliation (register vs intervals, MAIN ≥ Σ SUB, cost = energy x rate with an **independent** plant-local tariff scan,
intensity = energy/units) and duplicates. Each violation is classified **labelled** (matches a ground-truth row) or **unexplained**;
the dataset passes only with **0 unexplained**. Full dataset: 137/137 checks, 230 labelled violations, 0 unexplained.

Detection quality on the full dataset (pipeline sees only the inputs; ground truth is used only for scoring):

| issue_type | granularity | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| HIGH_IDLE_CONSUMPTION | machine-day | 131 | 0 | 3 | 1.00 | 0.98 |
| INTENSITY_SHIFT | machine-week | 10 | 0 | 0 | 1.00 | 1.00 |
| PEAK_DEMAND_SPIKE | episode | 46 | 0 | 0 | 1.00 | 1.00 |
| METER_GAP / DUPLICATE / KW_KWH / HIERARCHY / TOTAL / TARIFF-TZ | record/episode | all found | 0 | 0 | 1.00 | 1.00 |

Recommendations: 10 (4 idle, 4 intensity, 2 peak); median absolute error of the claimed saving vs ground truth —
idle 16%, intensity 31%, peak 0%.

**Reproducibility (Step 10):** `python -m energygen repro-check` generates twice and compares row counts and SHA-256 of every CSV —
IDENTICAL for starter and full (`reports/reproducibility.json`). Randomness uses only Mersenne-Twister `random()` through per-component
streams derived from `sha256(seed:label)`, and outputs contain no timestamps, so results do not depend on the Python version or host.

## Provenance and lawful use
100% synthetic. No external, public, personal or confidential data was used (`source_manifest.json → provenance`).
Schema design was informed by common smart-meter/MES concepts only. Code and generated data: MIT.

## Assumptions and limitations
* Detection scores are very high because the pipeline's rules are the same documented rules that create the labels
  (the task asks for labels from documented rules). They demonstrate internal consistency, not accuracy on real plants.
* Fixed UTC offsets (no DST); two plants; hourly weather; one machine per sub-meter; machine-level power model is simple (no power factor, phases or harmonics).
* Weekly shift calendar is deliberately simple; no public holidays; tariffs have no seasonal or demand-ratchet logic.
* Contract demand is derived from simulated baseline operation (documented), then used as the exceedance threshold.
* Intensity recommendation savings are noisier (31% median error) because the first flagged week is a small sample.
* Next steps: holidays/DST, more plants, noisy baseline drift, an ML detector benchmarked on the same ground truth, streaming ingestion.

See `docs/DESIGN.md` for design decisions and trade-offs, and `docs/AI_USAGE.md` / `docs/PROMPT_HISTORY.md` for the AI disclosure.
