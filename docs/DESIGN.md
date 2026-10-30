# Design and trade-off note

| decision | alternative | why |
|---|---|---|
| One generator, `Params` scale (starter vs full) | separate scripts | identical logic, so starter data is a faithful sample of the full set |
| `schema.py` is the single source of truth (DDL, dictionary, validation) | hand-written docs | dictionary and validator can never drift from the data |
| Simulate state per interval (RUN/IDLE/STANDBY/MAINT) and *derive* totals, costs, intensity from rows | draw totals independently | spec step 6: derived values must reconcile by construction; any mismatch is a labelled injection |
| Ground-truth labels created by the same rules that shape the data (idle while unstaffed, degradation multiplier, kW above contract) | random labels | spec: outcomes from documented rules, not independent random assignment |
| Anomalies injected on distinct, labelled rows only | corrupt rows silently | other rows stay clean; tests assert this |
| Duplicates carry `ingest_seq` (PK = reading_id + ingest_seq) | duplicate PKs | the SQL seed loads cleanly with FKs on; duplicates are still detectable |
| Tariff tz defect is a *master-data* defect, cost always uses plant-local time | wrong cost rows | acceptance rule "cost uses correct tariff interval"; the ground truth records the cost impact of not fixing it |
| Gaps are removed rows while the register keeps counting | store NULLs | models a real comms outage; reconciliation explains the difference |
| Contract demand from simulated baseline (p99.7 x 1.02) | magic number | scale-independent; natural exceedances stay rare; stacking days create real peaks |
| Infeasible parameters raise `ValueError` | silently place fewer batches | found during development: the first starter dataset silently produced 96 of 120 batches |
| stdlib only, own RNG wrapper | numpy/faker | portable, byte-reproducible across Python versions |

## Quality checks that found real problems during development
* weather IDs were truncated (caught by the ID-pattern + PK checks);
* batch count silently short when the calendar was too crowded (now an error);
* a valid gap day was classed as mismatch because the explanation bound used the observed maximum instead of the meter's rated capacity.

## Known limits
See README "Assumptions and limitations".
