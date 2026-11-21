"""Bridge between stored Schedule rows, API settings and slot computation."""

from datetime import UTC, datetime

from ..db.models import Schedule
from ..schemas.contracts import ScheduleRead, ScheduleSettings
from .scheduling import ScheduleSpec, ScheduleSpecError, build_spec, next_slot, upcoming_slots


def spec_from_settings(data: ScheduleSettings) -> ScheduleSpec:
    return build_spec(
        frequency=data.frequency, timezone=data.timezone, time_text=data.time,
        weekdays=data.weekdays, day_of_month=data.day_of_month, cron=data.cron,
    )


def spec_from_row(schedule: Schedule) -> ScheduleSpec:
    return build_spec(
        frequency=schedule.frequency, timezone=schedule.timezone,
        time_text=schedule.time.isoformat(timespec="minutes") if schedule.time else None,
        weekdays=schedule.weekdays, day_of_month=schedule.day_of_month, cron=schedule.cron,
    )


def apply_spec(schedule: Schedule, spec: ScheduleSpec) -> bool:
    """Store the specification; return True when the timing changed."""
    values = {
        "frequency": spec.frequency,
        "time": spec.time if spec.frequency != "cron" else None,
        "weekdays": list(spec.weekdays) if spec.frequency == "weekly" else None,
        "day_of_month": spec.day_of_month if spec.frequency == "monthly" else None,
        "cron": spec.cron_text if spec.frequency == "cron" else None,
        "timezone": spec.zone.key,
    }
    changed = any(getattr(schedule, key) != value for key, value in values.items())
    for key, value in values.items():
        setattr(schedule, key, value)
    return changed


def advance(schedule: Schedule, now: datetime) -> None:
    """Move next_run_at to the first slot after `now` (missed slots are not replayed)."""
    try:
        schedule.next_run_at = next_slot(spec_from_row(schedule), now)
    except ScheduleSpecError:
        schedule.next_run_at = None
    if schedule.next_run_at is None:
        schedule.enabled = False


def schedule_read(schedule: Schedule | None, *, upcoming: int = 3) -> ScheduleRead:
    if schedule is None:
        return ScheduleRead()
    slots: list[datetime] = []
    if schedule.enabled and schedule.next_run_at is not None:
        try:
            spec = spec_from_row(schedule)
            slots = [schedule.next_run_at, *upcoming_slots(spec, schedule.next_run_at, upcoming - 1)]
        except ScheduleSpecError:
            slots = [schedule.next_run_at]
    return ScheduleRead(
        enabled=schedule.enabled, frequency=schedule.frequency,
        time=schedule.time.isoformat(timespec="minutes") if schedule.time else None,
        weekdays=schedule.weekdays, day_of_month=schedule.day_of_month, cron=schedule.cron,
        timezone=schedule.timezone, next_run_at=schedule.next_run_at, upcoming=slots,
        last_enqueued_at=schedule.last_enqueued_at, last_error=schedule.last_error,
    )


def preview(data: ScheduleSettings, count: int = 5) -> list[datetime]:
    return upcoming_slots(spec_from_settings(data), datetime.now(UTC), count)
