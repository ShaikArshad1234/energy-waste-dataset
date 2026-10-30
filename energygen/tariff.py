"""Tariff window lookup. Windows are expressed in the tariff's timezone; the *plant* timezone is authoritative."""
from datetime import date

from energygen.timeutil import date_to_days, local_parts


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class TariffIndex:
    """lookup(plant_id, ts) -> (tariff_id, window_name, rate_per_kwh, demand_charge) or None.

    use_declared=False (correct): the window is matched against plant-local time.
    use_declared=True: matched against the tariff row's own declared offset (what a naive engine would do).
    """

    def __init__(self, tariffs, plants, use_declared=False):
        self.plant_off = {p["plant_id"]: int(p["utc_offset_minutes"]) for p in plants}
        self.use_declared = use_declared
        groups = {}
        for t in tariffs:
            groups.setdefault((t["plant_id"], int(t["tariff_version"])), []).append(t)
        self.versions = {}
        for (pid, _ver), rows in sorted(groups.items()):
            tbl = {"WEEKDAY": [None] * 96, "WEEKEND": [None] * 96}
            for r in rows:
                for q in range(_mins(r["start_local_time"]) // 15, _mins(r["end_local_time"]) // 15):
                    tbl[r["day_type"]][q] = (r["tariff_id"], r["window_name"], float(r["rate_per_kwh"]),
                                             float(r["demand_charge_per_kw"]))
            eto = rows[0]["effective_to"]
            self.versions.setdefault(pid, []).append({
                "from": date_to_days(date.fromisoformat(rows[0]["effective_from"])),
                "to": date_to_days(date.fromisoformat(eto)) if eto else None,
                "off": int(rows[0]["utc_offset_minutes"]), "tbl": tbl})

    def lookup(self, plant_id, ts):
        day, _, _ = local_parts(ts, self.plant_off[plant_id])
        for v in self.versions.get(plant_id, ()):
            if v["from"] <= day and (v["to"] is None or day <= v["to"]):
                _, sod, wd = local_parts(ts, v["off"] if self.use_declared else self.plant_off[plant_id])
                return v["tbl"]["WEEKDAY" if wd < 5 else "WEEKEND"][sod // 900]
        return None
