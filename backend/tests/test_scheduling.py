from datetime import UTC, datetime

import pytest

from backend.app.workflows.scheduling import ScheduleSpecError, build_spec, next_slot, parse_cron, upcoming_slots

MSK = "Europe/Moscow"  # UTC+3, no daylight saving


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_daily_runs_today_or_tomorrow():
    spec = build_spec(frequency="daily", timezone=MSK, time_text="09:00")
    assert next_slot(spec, utc(2026, 11, 20, 5, 0)) == utc(2026, 11, 20, 6, 0)
    assert next_slot(spec, utc(2026, 11, 20, 6, 0)) == utc(2026, 11, 21, 6, 0)


def test_weekly_picks_selected_weekdays():
    # 2026-11-20 is a Friday; Monday = 0, Thursday = 3.
    spec = build_spec(frequency="weekly", timezone=MSK, time_text="10:00", weekdays=[3, 0])
    slots = upcoming_slots(spec, utc(2026, 11, 20, 12, 0), 3)
    assert slots == [utc(2026, 11, 23, 7, 0), utc(2026, 11, 26, 7, 0), utc(2026, 11, 30, 7, 0)]


def test_monthly_clamps_to_last_day_of_short_month():
    spec = build_spec(frequency="monthly", timezone=MSK, time_text="03:00", day_of_month=31)
    slots = upcoming_slots(spec, utc(2027, 1, 31, 12, 0), 3)
    assert slots == [utc(2027, 2, 28, 0, 0), utc(2027, 3, 31, 0, 0), utc(2027, 4, 30, 0, 0)]


def test_cron_weekdays_range_and_steps():
    spec = build_spec(frequency="cron", timezone=MSK, cron="*/30 9 * * 1-5")
    slots = upcoming_slots(spec, utc(2026, 11, 20, 6, 10), 4)  # Friday 09:10 MSK
    assert slots == [utc(2026, 11, 20, 6, 30), utc(2026, 11, 23, 6, 0), utc(2026, 11, 23, 6, 30), utc(2026, 11, 24, 6, 0)]


def test_cron_day_of_month_or_weekday_and_names():
    cron = parse_cron("0 0 1 * sun")
    assert cron.days_restricted and cron.weekdays_restricted
    spec = build_spec(frequency="cron", timezone="UTC", cron="0 0 1 * SUN")
    # 2026-11-22 is a Sunday, 2026-12-01 is the first day of the month.
    assert upcoming_slots(spec, utc(2026, 11, 20), 3) == [utc(2026, 11, 22), utc(2026, 11, 29), utc(2026, 12, 1)]


def test_daylight_saving_gap_is_skipped():
    # 02:30 does not exist in Berlin on 2026-03-29.
    spec = build_spec(frequency="daily", timezone="Europe/Berlin", time_text="02:30")
    assert next_slot(spec, utc(2026, 3, 28, 12, 0)) == utc(2026, 3, 30, 0, 30)


@pytest.mark.parametrize("kwargs, code, field", [
    ({"frequency": "daily", "timezone": "Mars/Base"}, "INVALID_TIMEZONE", "timezone"),
    ({"frequency": "daily", "timezone": MSK}, "SCHEDULE_SETTINGS_REQUIRED", "time"),
    ({"frequency": "weekly", "timezone": MSK, "time_text": "09:00", "weekdays": []}, "SCHEDULE_SETTINGS_REQUIRED", "weekdays"),
    ({"frequency": "monthly", "timezone": MSK, "time_text": "09:00"}, "SCHEDULE_SETTINGS_REQUIRED", "day_of_month"),
    ({"frequency": "cron", "timezone": MSK, "cron": "0 9 * *"}, "INVALID_CRON", "cron"),
    ({"frequency": "cron", "timezone": MSK, "cron": "61 9 * * *"}, "INVALID_CRON", "cron"),
    ({"frequency": "cron", "timezone": MSK, "cron": "0 0 30 2 *"}, "SCHEDULE_NEVER_RUNS", "cron"),
])
def test_invalid_specs_are_rejected(kwargs, code, field):
    with pytest.raises(ScheduleSpecError) as error:
        build_spec(**kwargs)
    assert error.value.code == code and error.value.field == field
