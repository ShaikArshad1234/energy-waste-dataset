"""Validation: PK uniqueness, FK integrity, enums, date ordering, arithmetic reconciliation, lifecycles, duplicates.

Every violation is classified as *labelled* (the ground truth says it was injected on purpose) or *unexplained*.
The dataset passes only when there are zero unexplained violations. Ground truth is used here for classification
only -- never by the generator's derived tables or the detectors.
"""
import re
from collections import defaultdict
from datetime import date

from energygen.schema import BY_NAME, LOAD_ORDER, SOURCE_TABLES
from energygen.tariff import TariffIndex
from energygen.timeutil import parse_iso

_NUM = {"INTEGER": re.compile(r"^-?\d+$"), "REAL": re.compile(r"^-?\d+(\.\d+)?$")}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, group, violations, explained_ids=None, note=""):
        v = list(violations)
        ex = explained_ids or set()
        labelled = [x for x in v if x in ex]
        unexplained = [x for x in v if x not in ex]
        self.checks.append({"check": name, "group": group, "violations": len(v), "labelled_by_ground_truth": len(labelled),
                            "unexplained": len(unexplained), "status": "PASS" if not unexplained else "FAIL",
                            "sample_unexplained": [str(x) for x in unexplained[:5]], "note": note})

    def summary(self):
        failed = [c for c in self.checks if c["status"] == "FAIL"]
        return {"overall": "PASS" if not failed else "FAIL", "checks_total": len(self.checks),
                "checks_passed": len(self.checks) - len(failed), "failed": [c["check"] for c in failed],
                "labelled_violations_total": sum(c["labelled_by_ground_truth"] for c in self.checks),
                "unexplained_violations_total": sum(c["unexplained"] for c in self.checks), "checks": self.checks}


def _gt_sets(gt):
    s = defaultdict(set)
    for g in gt or []:
        t = g["issue_type"]
        if t == "TARIFF_TIMEZONE_MISMATCH":
            s[t].update(g["entity_id"].split(","))
        elif t in ("METER_GAP", "PEAK_DEMAND_SPIKE"):
            s[t].add(f"{g['meter_id']}@{g['start_ts_utc']}")
        else:
            s[t].add(g["entity_id"])
    return s


def validate(tables, derived, gt=None):
    R = Report()
    G = _gt_sets(gt)
    pks = {n: set() for n in tables}

    # ---------- generic: required, types, enums, patterns, PK uniqueness ----------
    for t in SOURCE_TABLES:
        rows = tables[t.name]
        bad_req, bad_type, bad_enum, bad_pat, dup_pk = [], [], [], [], []
        seen = set()
        pats = {c.name: re.compile(c.pattern) for c in t.cols if c.pattern}
        for i, r in enumerate(rows, 1):
            for c in t.cols:
                v = r.get(c.name, "")
                if v == "":
                    if c.required:
                        bad_req.append(f"{t.name}#{i}.{c.name}")
                    continue
                if c.type in _NUM and not _NUM[c.type].match(v):
                    bad_type.append(f"{t.name}#{i}.{c.name}={v}")
                elif c.type == "DATE" and not _DATE.match(v):
                    bad_type.append(f"{t.name}#{i}.{c.name}={v}")
                elif c.type == "TIMESTAMP" and not _TS.match(v):
                    bad_type.append(f"{t.name}#{i}.{c.name}={v}")
                if c.allowed and v not in c.allowed:
                    bad_enum.append(f"{t.name}#{i}.{c.name}={v}")
                if c.name in pats and not pats[c.name].match(v):
                    bad_pat.append(f"{t.name}#{i}.{c.name}={v}")
            key = tuple(r[k] for k in t.pk)
            if key in seen:
                dup_pk.append(key)
            seen.add(key)
        pks[t.name] = seen
        R.add(f"{t.name}: required fields present", "schema", bad_req)
        R.add(f"{t.name}: data types", "schema", bad_type)
        R.add(f"{t.name}: allowed values (enums)", "enum", bad_enum)
        R.add(f"{t.name}: id pattern", "id", bad_pat)
        R.add(f"{t.name}: primary key unique", "pk", dup_pk)

    # ---------- foreign keys ----------
    for t in SOURCE_TABLES:
        for c in t.cols:
            if not c.fk:
                continue
            target = {r[c.fk[1]] for r in tables[c.fk[0]]}
            bad = [f"{t.name}.{c.name}={r[c.name]}" for r in tables[t.name] if r[c.name] != "" and r[c.name] not in target]
            R.add(f"FK {t.name}.{c.name} -> {c.fk[0]}.{c.fk[1]}", "fk", bad)

    plants = {r["plant_id"]: r for r in tables["plant"]}
    meters = {r["meter_id"]: r for r in tables["meter"]}
    machines = {r["machine_id"]: r for r in tables["machine"]}
    batches = {r["batch_id"]: r for r in tables["production_batch"]}

    # ---------- hierarchy ----------
    bad = [m["meter_id"] for m in meters.values() if (m["meter_type"] == "MAIN") != (m["parent_meter_id"] == "")]
    bad += [m["meter_id"] for m in meters.values() if m["parent_meter_id"] and
            (meters[m["parent_meter_id"]]["meter_type"] != "MAIN" or meters[m["parent_meter_id"]]["plant_id"] != m["plant_id"])]
    R.add("meter hierarchy: SUB -> MAIN of same plant, MAIN has no parent", "hierarchy", bad)
    R.add("exactly one MAIN meter per plant", "hierarchy",
          [p for p in plants if sum(1 for m in meters.values() if m["plant_id"] == p and m["meter_type"] == "MAIN") != 1])
    asg = defaultdict(list)
    bad = []
    for a in tables["meter_machine_assignment"]:
        asg[a["machine_id"]].append(a)
        if meters[a["meter_id"]]["meter_type"] != "SUB" or meters[a["meter_id"]]["plant_id"] != machines[a["machine_id"]]["plant_id"]:
            bad.append(a["assignment_id"])
        if a["effective_to"] and a["effective_to"] < a["effective_from"]:
            bad.append(a["assignment_id"])
    R.add("assignment: SUB meter, same plant, effective_from <= effective_to", "hierarchy", bad)
    R.add("assignment: every machine has exactly one open assignment; no SUB meter shared", "hierarchy",
          [m for m in machines if sum(1 for a in asg[m] if not a["effective_to"]) != 1] +
          [k for k, v in defaultdict(int, {a["meter_id"]: 0 for a in tables["meter_machine_assignment"]}).items()
           if sum(1 for a in tables["meter_machine_assignment"] if a["meter_id"] == k and not a["effective_to"]) > 1])
    meter_machine = {a["meter_id"]: a["machine_id"] for a in tables["meter_machine_assignment"] if not a["effective_to"]}

    # ---------- readings ----------
    first_seen, dups, hier, unit, grid = {}, [], [], [], []
    for r in tables["energy_reading"]:
        rid = r["reading_id"]
        if rid in first_seen:
            dups.append(rid)
        else:
            first_seen[rid] = r
        if r["machine_id"] != meter_machine.get(r["meter_id"], ""):
            hier.append(rid)
        e, a = float(r["energy_kwh"]), float(r["avg_kw"])
        if abs(e - a * 0.25) > 0.002 or float(r["peak_kw"]) + 0.0005 < a or e < 0:
            unit.append(rid)
        ts = parse_iso(r["interval_start_utc"])
        if ts % 900 != 0 or not rid.endswith(r["interval_start_utc"].replace("-", "").replace(":", "")):
            grid.append(rid)
    R.add("energy_reading: no duplicate reading_id (extra deliveries)", "duplicate", dups, G["READING_DUPLICATE"])
    R.add("energy_reading: machine attribution inside meter hierarchy", "hierarchy", hier, G["MACHINE_OUTSIDE_HIERARCHY"])
    R.add("energy_reading: kWh = kW x 0.25 (kW/kWh not confused)", "arithmetic", unit, G["KW_KWH_CONFUSION"])
    R.add("energy_reading: 15-minute grid and id matches timestamp", "id", grid)
    conflicting = [r["reading_id"] for r in tables["energy_reading"] if r["ingest_seq"] != "1"
                   and (first_seen[r["reading_id"]]["avg_kw"] != r["avg_kw"])]
    R.add("energy_reading: re-deliveries carry identical values", "duplicate", conflicting, G["READING_DUPLICATE"])

    # ---------- reconciliation (energy totals) ----------
    findings = defaultdict(set)
    for f in derived["data_quality_findings"]:
        findings[f["finding_type"]].add(f["entity_id"])
    recon = derived["meter_daily_reconciliation"]
    R.add("meter totals: register = sum of intervals (gap days explained by missing intervals)", "reconciliation",
          [f"MDT-{r['meter_id']}-{r['total_date'].replace('-', '')}" for r in recon if r["status"] == "MISMATCH"],
          G["METER_TOTAL_MISMATCH"])
    gap_ids = {f for f in findings["METER_GAP"]}
    R.add("meters: no missing 15-minute intervals", "completeness", gap_ids, G["METER_GAP"])
    cost_total = sum(float(r["energy_kwh"]) for r in derived["interval_cost"])
    clean_total = sum(v[0] for s in derived["_ctx"]["cleaned_by_meter"].values() for v in s.values())
    R.add("cost table energy = cleaned interval energy (no rows lost)", "reconciliation",
          [] if abs(cost_total - clean_total) < 0.01 * max(1, len(derived["interval_cost"])) * 0.001 + 0.5 else ["total"])
    # parent >= sum of children (per interval, only where every child reading exists)
    cb, bad_par = derived["_ctx"]["cleaned_by_meter"], []
    for pid in plants:
        main = cb[f"MTR-{pid}-001"]
        subs = [cb[m] for m, x in meters.items() if x["plant_id"] == pid and x["meter_type"] == "SUB"]
        for ts, (_e, _c, a) in main.items():
            if all(ts in s for s in subs) and a + 0.01 < sum(s[ts][2] for s in subs):
                bad_par.append(f"{pid}@{ts}")
    R.add("MAIN kW >= sum of SUB kW (every interval with complete children)", "reconciliation", bad_par)
    bad = [f"{r['reading_id']}" for r in derived["interval_cost"]
           if abs(float(r["cost"]) - float(r["energy_kwh"]) * float(r["rate_per_kwh"])) > 0.0006
           or r["tariff_id"] not in pks["tariff"] and (r["tariff_id"],) not in pks["tariff"]]
    R.add("interval cost = energy x rate and a tariff window was found", "reconciliation", bad)
    bad = [f"{r['batch_id']}/{r['machine_id']}" for r in derived["batch_energy_summary"]
           if r["kwh_per_unit"] and abs(float(r["kwh_per_unit"]) - float(r["energy_kwh"]) / int(r["units_produced"])) > 0.0002]
    R.add("intensity = energy / produced units", "reconciliation", bad)

    # independent cost check (different implementation: linear scan of tariff rows, plant-local clock)
    plant_off = {p: int(r["utc_offset_minutes"]) for p, r in plants.items()}
    bad, rows = [], derived["interval_cost"]
    step = max(1, len(rows) // 3000)
    for r in rows[::step]:
        ts = parse_iso(r["interval_start_utc"]) + plant_off[r["plant_id"]] * 60
        d = date.fromordinal(ts // 86400 + 719163)
        mins, wd = (ts % 86400) // 60, d.weekday()
        hits = [t["tariff_id"] for t in tables["tariff"]
                if t["plant_id"] == r["plant_id"] and t["day_type"] == ("WEEKDAY" if wd < 5 else "WEEKEND")
                and t["effective_from"] <= d.isoformat() and (not t["effective_to"] or d.isoformat() <= t["effective_to"])
                and int(t["start_local_time"][:2]) * 60 + int(t["start_local_time"][3:]) <= mins
                < int(t["end_local_time"][:2]) * 60 + int(t["end_local_time"][3:])]
        if hits != [r["tariff_id"]]:
            bad.append(r["reading_id"])
    R.add("cost: tariff interval chosen by an independent plant-local-time scan (sampled)", "reconciliation", bad)

    # ---------- tariffs ----------
    bad_tz = [t["tariff_id"] for t in tables["tariff"] if t["timezone_name"] != plants[t["plant_id"]]["timezone_name"]
              or int(t["utc_offset_minutes"]) != plant_off[t["plant_id"]]]
    R.add("tariff timezone = plant timezone", "tariff", bad_tz, G["TARIFF_TIMEZONE_MISMATCH"])
    groups, bad = defaultdict(list), []
    for t in tables["tariff"]:
        groups[(t["plant_id"], t["tariff_version"], t["day_type"])].append(t)
        if t["effective_to"] and t["effective_to"] < t["effective_from"]:
            bad.append(t["tariff_id"])
        if float(t["rate_per_kwh"]) <= 0:
            bad.append(t["tariff_id"])
    R.add("tariff: effective dates ordered, rates > 0", "date-order", bad)
    bad = []
    for k, rows in groups.items():
        rows = sorted(rows, key=lambda t: t["start_local_time"])
        cur = "00:00"
        for t in rows:
            if t["start_local_time"] != cur:
                bad.append(str(k))
            cur = t["end_local_time"]
        if cur != "24:00":
            bad.append(str(k))
    R.add("tariff windows tile the 24 hours exactly (no gap/overlap)", "tariff", bad)
    ver = defaultdict(dict)
    for t in tables["tariff"]:
        ver[t["plant_id"]][t["tariff_version"]] = (t["effective_from"], t["effective_to"])
    bad = []
    for pid, v in ver.items():
        keys = sorted(v, key=int)
        for a, b in zip(keys, keys[1:]):
            if not v[a][1] or v[a][1] >= v[b][0]:
                bad.append(pid)
    R.add("tariff versions do not overlap per plant", "tariff", bad)

    # ---------- shifts, batches ----------
    shifts = {s["shift_id"]: s for s in tables["shift"]}
    R.add("shift: start < end", "date-order", [s["shift_id"] for s in shifts.values() if s["start_ts_utc"] >= s["end_ts_utc"]])
    bad, overlap, per_machine = [], [], defaultdict(list)
    bm_by_batch = defaultdict(list)
    for bm in tables["batch_machine"]:
        bm_by_batch[bm["batch_id"]].append(bm["machine_id"])
    for b in batches.values():
        s, e = parse_iso(b["start_ts_utc"]), parse_iso(b["end_ts_utc"])
        sh = shifts[b["shift_id"]]
        if not (e > s and s % 900 == 0 and e % 900 == 0 and 0 < int(b["produced_units"]) <= int(b["planned_units"])):
            bad.append(b["batch_id"])
        if not (sh["start_ts_utc"] <= b["start_ts_utc"] < sh["end_ts_utc"]) or sh["plant_id"] != b["plant_id"] or sh["staffed"] != "Y":
            bad.append(b["batch_id"])
        for m in bm_by_batch[b["batch_id"]]:
            if machines[m]["plant_id"] != b["plant_id"]:
                bad.append(b["batch_id"])
            per_machine[m].append((s, e, b["batch_id"]))
    R.add("batch: start<end, grid, 0<produced<=planned, starts inside a staffed shift of its plant, machines in same plant",
          "date-order", bad)
    for m, iv in per_machine.items():
        iv.sort()
        overlap += [f"{m}:{a[2]}/{b[2]}" for a, b in zip(iv, iv[1:]) if b[0] < a[1]]
    R.add("batch: a machine never runs two batches at once", "lifecycle", overlap)
    R.add("batch: every batch has >= 1 machine", "fk", [b for b in batches if not bm_by_batch[b]])

    def lifecycle(table, owner, allowed, name):
        evs = defaultdict(list)
        for r in tables[table]:
            evs[r[owner]].append(r)
        bad = []
        for k, rows in evs.items():
            rows.sort(key=lambda r: int(r["event_seq"]))
            if [int(r["event_seq"]) for r in rows] != list(range(1, len(rows) + 1)):
                bad.append(k)
            for a, b in zip(rows, rows[1:]):
                if (a["status"], b["status"]) not in allowed or b["event_ts_utc"] < a["event_ts_utc"]:
                    bad.append(k)
            if rows and rows[0]["status"] not in {x[0] for x in allowed if x[0] in ("PLANNED", "SCHEDULED", "OPEN")}:
                bad.append(k)
        R.add(name, "lifecycle", sorted(set(bad)))
        return evs

    lifecycle("batch_status_event", "batch_id", {("PLANNED", "RUNNING"), ("RUNNING", "COMPLETED"), ("RUNNING", "ABORTED")},
              "batch lifecycle PLANNED->RUNNING->COMPLETED|ABORTED, seq and time ordered")
    bev = defaultdict(list)
    for r in tables["batch_status_event"]:
        bev[r["batch_id"]].append(r)
    R.add("batch final status = last lifecycle event; RUNNING event = batch start", "lifecycle",
          [b for b, rows in bev.items() if max(rows, key=lambda r: int(r["event_seq"]))["status"] != batches[b]["status"]
           or next((r["event_ts_utc"] for r in rows if r["status"] == "RUNNING"), None) != batches[b]["start_ts_utc"]])
    lifecycle("maintenance_event", "work_order_id",
              {("SCHEDULED", "IN_PROGRESS"), ("IN_PROGRESS", "COMPLETED"), ("SCHEDULED", "CANCELLED")},
              "maintenance lifecycle SCHEDULED->IN_PROGRESS->COMPLETED | CANCELLED")
    recev = lifecycle("recommendation_status_event", "recommendation_id",
                      {("OPEN", "ACCEPTED"), ("OPEN", "REJECTED"), ("ACCEPTED", "IMPLEMENTED")},
                      "recommendation lifecycle OPEN->ACCEPTED|REJECTED, ACCEPTED->IMPLEMENTED")
    R.add("recommendation.status = last lifecycle event", "lifecycle",
          [r["recommendation_id"] for r in tables["recommendation"]
           if max(recev[r["recommendation_id"]], key=lambda e: int(e["event_seq"]))["status"] != r["status"]])
    R.add("recommendation: first event at created_ts", "lifecycle",
          [r["recommendation_id"] for r in tables["recommendation"]
           if min(recev[r["recommendation_id"]], key=lambda e: int(e["event_seq"]))["event_ts_utc"] != r["created_ts_utc"]])
    bad = []
    mev = {}
    for r in tables["maintenance_event"]:
        mev[r["maintenance_event_id"]] = r
    for r in tables["recommendation"]:
        if (not r["baseline_end_utc"] > r["baseline_start_utc"] or float(r["baseline_value"]) <= 0
                or float(r["expected_saving_kwh"]) < 0 or float(r["expected_saving_cost"]) < 0):
            bad.append(r["recommendation_id"])
        if r["related_maintenance_event_id"] and (mev[r["related_maintenance_event_id"]]["status"] != "COMPLETED"
                                                  or mev[r["related_maintenance_event_id"]]["machine_id"] != r["machine_id"]):
            bad.append(r["recommendation_id"])
    R.add("recommendation: measurable baseline (>0, ordered period) and non-negative expected saving", "recommendation", bad)

    # ---------- machine state history ----------
    bad, per = [], defaultdict(list)
    for r in tables["machine_state_event"]:
        per[r["machine_id"]].append(r)
        if (r["state"] == "RUN") != (r["batch_id"] != "") or r["start_ts_utc"] >= r["end_ts_utc"]:
            bad.append(r["state_event_id"])
    for m, rows in per.items():
        rows.sort(key=lambda r: r["start_ts_utc"])
        bad += [b["state_event_id"] for a, b in zip(rows, rows[1:]) if a["end_ts_utc"] != b["start_ts_utc"]]
    R.add("machine state history: contiguous, non-overlapping, RUN <=> batch_id", "lifecycle", bad)
    R.add("plant: effective_from <= first reading date", "date-order",
          [p["plant_id"] for p in plants.values()
           if p["effective_from"] > min(r["interval_start_utc"][:10] for r in tables["energy_reading"])])
    return R.summary()
