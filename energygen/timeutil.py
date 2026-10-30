"""Integer-based time helpers (UTC epoch seconds). No DST: plants use fixed UTC offsets."""
from datetime import date, datetime, timezone

EPOCH_ORD = date(1970, 1, 1).toordinal()
INTERVAL_SECONDS = 900
INTERVALS_PER_DAY = 96


def date_to_days(d: date) -> int:
    return d.toordinal() - EPOCH_ORD


def days_to_date(n: int) -> date:
    return date.fromordinal(n + EPOCH_ORD)


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_iso(s: str) -> int:
    """'YYYY-MM-DDTHH:MM:SSZ' -> epoch seconds (fast, strict)."""
    d = date(int(s[0:4]), int(s[5:7]), int(s[8:10])).toordinal() - EPOCH_ORD
    return d * 86400 + int(s[11:13]) * 3600 + int(s[14:16]) * 60 + int(s[17:19])


def local_parts(ts: int, offset_minutes: int):
    """-> (local day number since epoch, seconds of local day, weekday Mon=0)."""
    ls = ts + offset_minutes * 60
    day = ls // 86400
    return day, ls % 86400, (day + 3) % 7
