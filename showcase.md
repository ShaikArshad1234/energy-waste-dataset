# Dataset showcase

## 1. Dataset size (rows per table)

```
table                       | rows  
----------------------------+-------
batch_energy_summary        | 3800  
batch_machine               | 3800  
batch_status_event          | 6000  
data_quality_findings       | 408   
energy_reading              | 171362
ground_truth                | 343   
interval_cost               | 171293
machine                     | 18    
machine_state_event         | 7214  
maintenance_event           | 64    
meter                       | 20    
meter_daily_reconciliation  | 1800  
meter_daily_total           | 1800  
meter_machine_assignment    | 18    
peak_demand_daily           | 488   
plant                       | 2     
production_batch            | 2000  
recommendation              | 10    
recommendation_status_event | 26    
shift                       | 542   
tariff                      | 36    
weather_observation         | 4323  
```

## 2. Recommendations: count, status, expected saving

```
rule_id          | status      | n | saving_kwh | saving_kw | currency | saving_cost
-----------------+-------------+---+------------+-----------+----------+------------
REC-R1-IDLE      | ACCEPTED    | 1 | 2767.0     | 0.0       | AED      | 894.0      
REC-R1-IDLE      | ACCEPTED    | 1 | 1237.0     | 0.0       | INR      | 8203.0     
REC-R1-IDLE      | IMPLEMENTED | 1 | 1366.0     | 0.0       | AED      | 441.0      
REC-R1-IDLE      | IMPLEMENTED | 1 | 1068.0     | 0.0       | INR      | 6946.0     
REC-R2-INTENSITY | IMPLEMENTED | 2 | 7858.0     | 0.0       | AED      | 3223.0     
REC-R2-INTENSITY | IMPLEMENTED | 2 | 2421.0     | 0.0       | INR      | 20294.0    
REC-R3-PEAK      | ACCEPTED    | 1 | 0.0        | 172.5     | AED      | 2070.0     
REC-R3-PEAK      | ACCEPTED    | 1 | 0.0        | 139.7     | INR      | 48907.0    
```

## 3. Anomalies injected (ground truth) by type

```
issue_type                | n 
--------------------------+---
HIGH_IDLE_CONSUMPTION     | 82
METER_GAP                 | 71
READING_DUPLICATE         | 69
PEAK_DEMAND_SPIKE         | 46
KW_KWH_CONFUSION          | 35
MACHINE_OUTSIDE_HIERARCHY | 26
METER_TOTAL_MISMATCH      | 9 
INTENSITY_SHIFT           | 4 
TARIFF_TIMEZONE_MISMATCH  | 1 
```

## 4. Findings the pipeline detected from source data only

```
finding_type              | n  
--------------------------+----
HIGH_IDLE_CONSUMPTION     | 131
METER_GAP                 | 71 
READING_DUPLICATE         | 69 
PEAK_DEMAND_SPIKE         | 46 
KW_KWH_CONFUSION          | 35 
MACHINE_OUTSIDE_HIERARCHY | 26 
INTENSITY_SHIFT           | 12 
TARIFF_TIMEZONE_MISMATCH  | 9  
METER_TOTAL_MISMATCH      | 9  
```

## 5. Meter daily reconciliation: largest gaps between interval sum and register (kWh)

```
plant_id | meter_id    | total_date | interval_kwh | register_kwh | diff_kwh | status       
---------+-------------+------------+--------------+--------------+----------+--------------
P02      | MTR-P02-001 | 2026-08-13 | 5526.7       | 9698.4       | 4171.7   | GAP_EXPLAINED
P02      | MTR-P02-001 | 2026-09-09 | 11327.5      | 13286.3      | 1958.8   | GAP_EXPLAINED
P02      | MTR-P02-001 | 2026-09-22 | 10734.5      | 12686.9      | 1952.4   | GAP_EXPLAINED
P01      | MTR-P01-001 | 2026-09-11 | 4886.2       | 6480.3       | 1594.2   | GAP_EXPLAINED
P02      | MTR-P02-002 | 2026-09-01 | 1430.2       | 2218.6       | 788.4    | GAP_EXPLAINED
P02      | MTR-P02-009 | 2026-07-13 | 907.7        | 1417.7       | 509.9    | GAP_EXPLAINED
P01      | MTR-P01-005 | 2026-09-01 | 872.5        | 1380.4       | 507.9    | GAP_EXPLAINED
P02      | MTR-P02-007 | 2026-09-01 | 210.1        | 671.7        | 461.5    | GAP_EXPLAINED
```

## 6. Energy cost by tariff window (P01 = INR, P02 = AED; plant-local windows)

```
plant_id | window_name | kwh      | avg_rate | cost     
---------+-------------+----------+----------+----------
P01      | OFF_PEAK    | 232184.0 | 5.67     | 1316247.0
P01      | SHOULDER    | 424698.0 | 8.24     | 3498725.0
P01      | PEAK        | 340306.0 | 11.83    | 4030485.0
P02      | OFF_PEAK    | 311294.0 | 0.29     | 89768.0  
P02      | SHOULDER    | 571470.0 | 0.39     | 223765.0 
P02      | PEAK        | 450238.0 | 0.54     | 241085.0 
```

## 7. Peak-demand days above contract demand

```
plant_id | local_date | window_name | max_kw   | contract_demand_kw | exceed_kw
---------+------------+-------------+----------+--------------------+----------
P02      | 2026-07-02 | PEAK        | 1007.524 | 835.0              | 172.524  
P01      | 2026-08-06 | PEAK        | 749.735  | 610.0              | 139.735  
P02      | 2026-08-27 | PEAK        | 956.288  | 835.0              | 121.288  
P01      | 2026-09-08 | PEAK        | 715.763  | 610.0              | 105.763  
P02      | 2026-08-24 | SHOULDER    | 936.725  | 835.0              | 101.725  
P02      | 2026-09-02 | PEAK        | 935.989  | 835.0              | 100.989  
P01      | 2026-08-20 | PEAK        | 709.536  | 610.0              | 99.536   
P01      | 2026-07-09 | PEAK        | 700.368  | 610.0              | 90.368   
```

## 8. Worst energy intensity: batches ranked by kWh per unit (COMPLETE batches)

```
batch_id          | machine_id  | product_code | units_produced | kwh   | kwh_per_unit
------------------+-------------+--------------+----------------+-------+-------------
BATCH-2026-001573 | MCH-P02-001 | PRD-C        | 167.0          | 311.9 | 1.8676      
BATCH-2026-000567 | MCH-P02-001 | PRD-C        | 114.0          | 212.7 | 1.8656      
BATCH-2026-000843 | MCH-P02-001 | PRD-C        | 94.0           | 174.3 | 1.8546      
BATCH-2026-001691 | MCH-P02-001 | PRD-C        | 97.0           | 178.1 | 1.8358      
BATCH-2026-000601 | MCH-P02-001 | PRD-C        | 391.0          | 713.4 | 1.8245      
BATCH-2026-000286 | MCH-P02-001 | PRD-C        | 96.0           | 174.2 | 1.8146      
BATCH-2026-001901 | MCH-P02-001 | PRD-C        | 223.0          | 402.9 | 1.807       
BATCH-2026-000686 | MCH-P02-001 | PRD-C        | 96.0           | 173.1 | 1.8027      
```

## 9. Intensity by machine and product vs the batch average (drift shows as a high ratio)

```
machine_id  | product_code | batches | min_kwh_unit | avg_kwh_unit | max_kwh_unit | max_over_avg
------------+--------------+---------+--------------+--------------+--------------+-------------
MCH-P02-002 | PRD-A        | 56      | 0.6201       | 0.6905       | 0.9412       | 1.36        
MCH-P02-005 | PRD-C        | 39      | 0.5635       | 0.6172       | 0.8339       | 1.35        
MCH-P02-005 | PRD-D        | 32      | 0.0933       | 0.104        | 0.1399       | 1.35        
MCH-P02-005 | PRD-A        | 64      | 0.3355       | 0.3842       | 0.5117       | 1.33        
MCH-P01-006 | PRD-E        | 44      | 0.0925       | 0.1029       | 0.1358       | 1.32        
MCH-P02-002 | PRD-C        | 26      | 0.8892       | 1.003        | 1.3173       | 1.31        
MCH-P02-002 | PRD-D        | 26      | 0.232        | 0.2669       | 0.3509       | 1.31        
MCH-P01-006 | PRD-C        | 32      | 0.2291       | 0.2636       | 0.341        | 1.29        
```

## 10. Idle waste by machine (off-shift excess kWh)

```
machine_id  | idle_days | excess_kwh
------------+-----------+-----------
MCH-P02-004 | 54        | 7808.2    
MCH-P01-005 | 45        | 3234.3    
MCH-P02-009 | 20        | 1420.4    
MCH-P01-008 | 12        | 824.2     
```
