"""Schedule specifications and computation of the next run slots.

Supported frequencies: daily, weekly (selected weekdays), monthly (day of month,
clamped to the last day of shorter months) and a standard five-field cron expression.
All times are local to an IANA time zone; slots are returned in UTC.
"""

import calendar
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Frequency = Literal["daily", "weekly", "monthly", "cron"]

# How far ahead to look for a matching day; covers cron expressions like "0 0 29 2 *".
_SEARCH_DAYS = 366 * 8


class ScheduleSpecError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


# ---------- cron ----------

_MONTH_NAMES = {name.lower(): index for index, name in enumerate(calendar.month_abbr) if name}
_DAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_FIELDS = (
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, _MONTH_NAMES),
    ("day of week", 0, 7, _DAY_NAMES),
)


@dataclass(frozen=True)
class CronExpression:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # cron numbering: 0 = Sunday
    days_restricted: bool
    weekdays_restricted: bool

    def matches_day(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.weekday() + 1) % 7 in self.weekdays
        # Standard cron: when both fields are restricted, either one may match.
        if self.days_restricted and self.weekdays_restricted:
            return dom or dow
        return dom and dow


def _parse_value(token: str, low: int, high: int, names: dict[str, int], label: str) -> int:
    value = names.get(token.lower()) if names else None
    if value is None:
        if not token.isdigit():
            raise ScheduleSpecError("INVALID_CRON", f"Неверное значение «{token}» в поле {label}.", "cron")
        value = int(token)
    if not low <= value <= high:
        raise ScheduleSpecError("INVALID_CRON", f"Значение {value} вне диапазона {low}–{high} в поле {label}.", "cron")
    return value


def _parse_field(text: str, low: int, high: int, names: dict[str, int], label: str) -> tuple[set[int], bool]:
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise ScheduleSpecError("INVALID_CRON", f"Неверный шаг «{step_text}» в поле {label}.", "cron")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start = _parse_value(first, low, high, names, label)
            end = _parse_value(last, low, high, names, label)
            if start > end:
                raise ScheduleSpecError("INVALID_CRON", f"Неверный диапазон «{base}» в поле {label}.", "cron")
        else:
            start = _parse_value(base, low, high, names, label)
            end = high if step_text else start
        values.update(range(start, end + 1, step))
    return values, text != "*"


def parse_cron(expression: str) -> CronExpression:
    parts = expression.split()
    if len(parts) != 5:
        raise ScheduleSpecError("INVALID_CRON", "Cron-выражение должно состоять из 5 полей: минута час день месяц день_недели.", "cron")
    parsed = [_parse_field(text, low, high, names, label) for text, (label, low, high, names) in zip(parts, _FIELDS)]
    (minutes, _), (hours, _), (days, days_restricted), (months, _), (weekdays, weekdays_restricted) = parsed
    if 7 in weekdays:
        weekdays = (weekdays - {7}) | {0}
    return CronExpression(
        tuple(sorted(minutes)), tuple(sorted(hours)), frozenset(days), frozenset(months),
        frozenset(weekdays), days_restricted, weekdays_restricted,
    )


# ---------- schedule specification ----------

_TIME_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


@dataclass(frozen=True)
class ScheduleSpec:
    frequency: Frequency
    zone: ZoneInfo
    time: time | None = None
    weekdays: tuple[int, ...] = ()  # 0 = Monday … 6 = Sunday
    day_of_month: int | None = None
    cron: CronExpression | None = field(default=None, compare=False)
    cron_text: str | None = None

    def matches_day(self, day: date) -> bool:
        if self.frequency == "weekly":
            return day.weekday() in self.weekdays
        if self.frequency == "monthly":
            assert self.day_of_month is not None
            last = calendar.monthrange(day.year, day.month)[1]
            return day.day == min(self.day_of_month, last)
        if self.frequency == "cron":
            assert self.cron is not None
            return self.cron.matches_day(day)
        return True

    def times(self) -> Iterator[time]:
        if self.frequency == "cron":
            assert self.cron is not None
            for hour in self.cron.hours:
                for minute in self.cron.minutes:
                    yield time(hour, minute)
        else:
            assert self.time is not None
            yield self.time


def build_spec(
    *, frequency: str = "daily", timezone: str | None, time_text: str | None = None,
    weekdays: list[int] | None = None, day_of_month: int | None = None, cron: str | None = None,
) -> ScheduleSpec:
    if not timezone:
        raise ScheduleSpecError("SCHEDULE_SETTINGS_REQUIRED", "Укажите часовой пояс.", "timezone")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleSpecError("INVALID_TIMEZONE", "Укажите часовой пояс IANA, например Europe/Moscow.", "timezone") from None

    if frequency == "cron":
        text = " ".join((cron or "").split())
        if not text:
            raise ScheduleSpecError("SCHEDULE_SETTINGS_REQUIRED", "Введите cron-выражение.", "cron")
        spec = ScheduleSpec("cron", zone, cron=parse_cron(text), cron_text=text)
    else:
        if frequency not in ("daily", "weekly", "monthly"):
            raise ScheduleSpecError("INVALID_FREQUENCY", "Неизвестная периодичность.", "frequency")
        if not time_text or not _TIME_PATTERN.match(time_text):
            raise ScheduleSpecError("SCHEDULE_SETTINGS_REQUIRED", "Укажите время запуска в формате ЧЧ:ММ.", "time")
        local_time = time.fromisoformat(time_text)
        if frequency == "weekly":
            days = tuple(sorted(set(weekdays or ())))
            if not days or any(not 0 <= d <= 6 for d in days):
                raise ScheduleSpecError("SCHEDULE_SETTINGS_REQUIRED", "Выберите хотя бы один день недели.", "weekdays")
            spec = ScheduleSpec("weekly", zone, local_time, weekdays=days)
        elif frequency == "monthly":
            if day_of_month is None or not 1 <= day_of_month <= 31:
                raise ScheduleSpecError("SCHEDULE_SETTINGS_REQUIRED", "Выберите день месяца от 1 до 31.", "day_of_month")
            spec = ScheduleSpec("monthly", zone, local_time, day_of_month=day_of_month)
        else:
            spec = ScheduleSpec("daily", zone, local_time)

    if next_slot(spec, datetime.now(UTC)) is None:
        raise ScheduleSpecError("SCHEDULE_NEVER_RUNS", "По этому расписанию запуск никогда не произойдёт.", "cron")
    return spec


# ---------- slot computation ----------

def _local_slot(day: date, local_time: time, zone: ZoneInfo) -> datetime | None:
    local = datetime.combine(day, local_time)
    slot = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
    # The round trip rejects times skipped by a daylight-saving transition.
    return slot if slot.astimezone(zone).replace(tzinfo=None) == local else None


def next_slot(spec: ScheduleSpec, now: datetime) -> datetime | None:
    day = now.astimezone(spec.zone).date()
    for _ in range(_SEARCH_DAYS):
        if spec.matches_day(day):
            for local_time in spec.times():
                slot = _local_slot(day, local_time, spec.zone)
                if slot is not None and slot > now:
                    return slot
        day += timedelta(days=1)
    return None


def upcoming_slots(spec: ScheduleSpec, now: datetime, count: int) -> list[datetime]:
    slots: list[datetime] = []
    cursor = now
    while len(slots) < count and (slot := next_slot(spec, cursor)) is not None:
        slots.append(slot)
        cursor = slot
    return slots


def next_daily_slot(local_time: time, zone: ZoneInfo, now: datetime) -> datetime:
    slot = next_slot(ScheduleSpec("daily", zone, local_time), now)
    assert slot is not None
    return slot
