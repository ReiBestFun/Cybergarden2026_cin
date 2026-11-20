from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_daily_slot(local_time: time, zone: ZoneInfo, now: datetime) -> datetime:
    local_date = now.astimezone(zone).date()
    while True:
        local = datetime.combine(local_date, local_time)
        slot = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
        # The round trip rejects times skipped by a daylight-saving transition.
        if slot > now and slot.astimezone(zone).replace(tzinfo=None) == local:
            return slot
        local_date += timedelta(days=1)
