# Validation summary: **PASS**

* checks: 137/137 passed
* violations explained by ground-truth labels (deliberate anomalies): 31
* **unexplained violations: 0**

| group | check | violations | labelled | unexplained | status |
|---|---|---|---|---|---|
| hierarchy | meter hierarchy: SUB -> MAIN of same plant, MAIN has no parent | 0 | 0 | 0 | PASS |
| hierarchy | exactly one MAIN meter per plant | 0 | 0 | 0 | PASS |
| hierarchy | assignment: SUB meter, same plant, effective_from <= effective_to | 0 | 0 | 0 | PASS |
| hierarchy | assignment: every machine has exactly one open assignment; no SUB meter shared | 0 | 0 | 0 | PASS |
| duplicate | energy_reading: no duplicate reading_id (extra deliveries) | 4 | 4 | 0 | PASS |
| hierarchy | energy_reading: machine attribution inside meter hierarchy | 2 | 2 | 0 | PASS |
| arithmetic | energy_reading: kWh = kW x 0.25 (kW/kWh not confused) | 2 | 2 | 0 | PASS |
| reconciliation | meter totals: register = sum of intervals (gap days explained by missing intervals) | 2 | 2 | 0 | PASS |
| completeness | meters: no missing 15-minute intervals | 12 | 12 | 0 | PASS |
| reconciliation | cost table energy = cleaned interval energy (no rows lost) | 0 | 0 | 0 | PASS |
| reconciliation | MAIN kW >= sum of SUB kW (every interval with complete children) | 0 | 0 | 0 | PASS |
| reconciliation | interval cost = energy x rate and a tariff window was found | 0 | 0 | 0 | PASS |
| reconciliation | intensity = energy / produced units | 0 | 0 | 0 | PASS |
| reconciliation | cost: tariff interval chosen by an independent plant-local-time scan (sampled) | 0 | 0 | 0 | PASS |
| tariff | tariff timezone = plant timezone | 9 | 9 | 0 | PASS |
| lifecycle | batch: a machine never runs two batches at once | 0 | 0 | 0 | PASS |
| lifecycle | batch lifecycle PLANNED->RUNNING->COMPLETED|ABORTED, seq and time ordered | 0 | 0 | 0 | PASS |
| lifecycle | batch final status = last lifecycle event; RUNNING event = batch start | 0 | 0 | 0 | PASS |
| lifecycle | maintenance lifecycle SCHEDULED->IN_PROGRESS->COMPLETED | CANCELLED | 0 | 0 | 0 | PASS |
| lifecycle | recommendation lifecycle OPEN->ACCEPTED|REJECTED, ACCEPTED->IMPLEMENTED | 0 | 0 | 0 | PASS |
| lifecycle | recommendation.status = last lifecycle event | 0 | 0 | 0 | PASS |
| lifecycle | recommendation: first event at created_ts | 0 | 0 | 0 | PASS |
| lifecycle | machine state history: contiguous, non-overlapping, RUN <=> batch_id | 0 | 0 | 0 | PASS |

## Detection quality (pipeline from data only vs ground truth)

| issue_type | granularity | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|---|
| HIGH_IDLE_CONSUMPTION | machine-day | 5 | 0 | 1 | 1.0 | 0.8333 | 0.9091 |
| INTENSITY_SHIFT | machine-week (>=4 degraded days) | 1 | 0 | 1 | 1.0 | 0.5 | 0.6667 |
| KW_KWH_CONFUSION | record | 2 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| MACHINE_OUTSIDE_HIERARCHY | record | 2 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| METER_GAP | gap episode | 12 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| METER_TOTAL_MISMATCH | record | 2 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| PEAK_DEMAND_SPIKE | exceedance episode | 6 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| READING_DUPLICATE | record | 4 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| TARIFF_TIMEZONE_MISMATCH | tariff row | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 |

## Recommendations

```json
{
  "idle": {
    "tp": 1,
    "fp": 0,
    "fn": 1,
    "precision": 1.0,
    "recall": 0.5,
    "f1": 0.6667
  },
  "intensity": {
    "tp": 1,
    "fp": 0,
    "fn": 1,
    "precision": 1.0,
    "recall": 0.5,
    "f1": 0.6667
  },
  "intensity_saving_median_abs_pct_error": 64.7,
  "peak_saving_kw_median_abs_pct_error": 0.0,
  "recommendations_total": 4,
  "idle_saving_median_abs_pct_error": 18.5
}
```
