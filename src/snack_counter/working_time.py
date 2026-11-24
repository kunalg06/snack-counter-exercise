"""Working-time arithmetic per office: Mon–Fri 09:00–17:00 local time, minus that office's holidays.

Timers (accept an offer, buy by a deadline) only count working time, so an offer sent
on Friday at 16:30 doesn't expire over the weekend.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snack_counter.model import Office

WORK_START = time(9, 0)
WORK_END = time(17, 0)


def is_working_day(office: Office, day: date) -> bool:
    return day.weekday() < 5 and day not in office.holidays


def local_date(moment: datetime, office: Office) -> date:
    return moment.astimezone(office.tz).date()


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def add_working_hours(start: datetime, hours: float, office: Office) -> datetime:
    """Return the moment `hours` of working time after `start`, in the office's time zone."""
    tz = office.tz
    current = start.astimezone(tz)
    remaining = timedelta(hours=hours)
    while True:
        day = current.date()
        day_start = datetime.combine(day, WORK_START, tz)
        day_end = datetime.combine(day, WORK_END, tz)
        if not is_working_day(office, day) or current >= day_end:
            current = datetime.combine(day + timedelta(days=1), WORK_START, tz)
            continue
        if current < day_start:
            current = day_start
        available = day_end - current
        if remaining <= available:
            return current + remaining
        remaining -= available
        current = datetime.combine(day + timedelta(days=1), WORK_START, tz)


def end_of_next_working_day(start: datetime, office: Office) -> datetime:
    """17:00 local time on the first working day after `start`'s local date."""
    day = local_date(start, office) + timedelta(days=1)
    while not is_working_day(office, day):
        day += timedelta(days=1)
    return datetime.combine(day, WORK_END, office.tz)
