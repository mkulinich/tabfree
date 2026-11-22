"""Schedule specs and their next run time, in the schedule's own time zone (P6.1, ADR-013).

A spec is a small document, like Tableau's schedule settings::

    {"frequency": "hourly", "every": 4, "start": "06:00", "end": "18:00", "timezone": "Europe/London"}
    {"frequency": "daily", "at": "06:30", "timezone": "America/New_York"}
    {"frequency": "weekly", "at": "08:00", "weekdays": ["mon", "thu"], "timezone": "UTC"}
    {"frequency": "monthly", "at": "07:00", "days": [1, 15, -1], "timezone": "Asia/Tokyo"}  # -1: last day

Times are wall-clock times: "06:30 Europe/London" stays 06:30 across daylight-saving changes. A time
that does not exist on a given day (the spring-forward gap) runs at the first valid instant after it.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FREQUENCIES = ("hourly", "daily", "weekly", "monthly")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HORIZON_DAYS = 400


class ScheduleError(ValueError):
    pass


def _time(text: Any, what: str) -> dt.time:
    try:
        hours, minutes = str(text).split(":")
        return dt.time(int(hours), int(minutes))
    except (ValueError, TypeError):
        raise ScheduleError(f"{what} must be HH:MM, not {text!r}") from None


def _zone(name: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"unknown time zone {name!r}") from None


def validate(spec: Mapping[str, Any]) -> dict[str, Any]:
    """A normalised copy of the spec, or ScheduleError."""
    frequency = spec.get("frequency")
    if frequency not in FREQUENCIES:
        raise ScheduleError(f"frequency must be one of {', '.join(FREQUENCIES)}")
    out: dict[str, Any] = {"frequency": frequency, "timezone": str(spec.get("timezone") or "UTC")}
    _zone(out["timezone"])
    if frequency == "hourly":
        every = int(spec.get("every", 1))
        if every not in (1, 2, 4, 6, 8, 12):
            raise ScheduleError("hourly schedules run every 1, 2, 4, 6, 8 or 12 hours")
        out["every"] = every
        out["start"] = _time(spec.get("start", "00:00"), "start").strftime("%H:%M")
        out["end"] = _time(spec.get("end", "23:59"), "end").strftime("%H:%M")
    else:
        out["at"] = _time(spec.get("at", "00:00"), "at").strftime("%H:%M")
    if frequency in ("hourly", "weekly") and spec.get("weekdays"):
        days = [str(d).lower()[:3] for d in spec["weekdays"]]
        if not days or any(d not in WEEKDAYS for d in days):
            raise ScheduleError(f"weekdays are {', '.join(WEEKDAYS)}")
        out["weekdays"] = sorted(set(days), key=WEEKDAYS.index)
    elif frequency == "weekly":
        raise ScheduleError("weekly schedules need weekdays")
    if frequency == "monthly":
        month_days = [int(d) for d in spec.get("days", [1])]
        if not month_days or any(d == 0 or d < -1 or d > 31 for d in month_days):
            raise ScheduleError("monthly days are 1-31, or -1 for the last day")
        out["days"] = sorted(set(month_days))
    return out


def _local(day: dt.date, at: dt.time, zone: ZoneInfo) -> dt.datetime:
    """The instant of a wall-clock time; inside a DST gap, the first valid instant after it."""
    naive = dt.datetime.combine(day, at)
    aware = naive.replace(tzinfo=zone)
    roundtrip = aware.astimezone(dt.UTC).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != naive:  # nonexistent local time
        return aware.astimezone(dt.UTC).astimezone(zone)
    return aware


def _day_matches(spec: Mapping[str, Any], day: dt.date) -> bool:
    if spec.get("weekdays") and WEEKDAYS[day.weekday()] not in spec["weekdays"]:
        return False
    if spec["frequency"] == "monthly":
        last = calendar.monthrange(day.year, day.month)[1]
        return any(d == day.day or (d == -1 and day.day == last) for d in spec["days"])
    return True


def _times(spec: Mapping[str, Any]) -> list[dt.time]:
    if spec["frequency"] != "hourly":
        return [_time(spec["at"], "at")]
    start, end = _time(spec["start"], "start"), _time(spec["end"], "end")
    out = []
    minutes = start.hour * 60 + start.minute
    while minutes <= end.hour * 60 + end.minute:
        out.append(dt.time(minutes // 60, minutes % 60))
        minutes += 60 * int(spec["every"])
    return out


def next_run(spec: Mapping[str, Any], after: dt.datetime) -> dt.datetime:
    """The first run strictly after ``after`` (an aware datetime), in UTC."""
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    spec = validate(spec)
    zone = _zone(spec["timezone"])
    local_after = after.astimezone(zone)
    times = _times(spec)
    for offset in range(_HORIZON_DAYS):
        day = local_after.date() + dt.timedelta(days=offset)
        if not _day_matches(spec, day):
            continue
        for at in times:
            candidate = _local(day, at, zone)
            if candidate > local_after:
                return candidate.astimezone(dt.UTC)
    raise ScheduleError("the schedule never runs")


def describe(spec: Mapping[str, Any]) -> str:
    """A short description in plain words, for the portal."""
    spec = validate(spec)
    zone = spec["timezone"]
    if spec["frequency"] == "hourly":
        every = "every hour" if spec["every"] == 1 else f"every {spec['every']} hours"
        text = f"{every} from {spec['start']} to {spec['end']}"
    elif spec["frequency"] == "daily":
        text = f"daily at {spec['at']}"
    elif spec["frequency"] == "weekly":
        text = f"every {', '.join(d.capitalize() for d in spec['weekdays'])} at {spec['at']}"
    else:
        days = ", ".join("last day" if d == -1 else str(d) for d in spec["days"])
        text = f"monthly on day {days} at {spec['at']}"
    if spec.get("weekdays") and spec["frequency"] == "hourly":
        text += f" on {', '.join(d.capitalize() for d in spec['weekdays'])}"
    return f"{text} ({zone})"
