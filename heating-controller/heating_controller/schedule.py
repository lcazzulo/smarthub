"""Daily hot-water supply intervals in the configured local timezone."""

from datetime import datetime

from .config import GeneralConfig


def supply_available(config: GeneralConfig, now: datetime) -> bool:
    """Evaluate wall-clock intervals, start inclusive and end exclusive.

    Both occurrences of a repeated local time have the same availability;
    nonexistent times are skipped naturally when converting actual instants.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Supply evaluation requires a timezone-aware datetime")
    local_time = now.astimezone(config.timezone).time()
    for interval in config.supply_intervals:
        if interval.start < interval.end:
            if interval.start <= local_time < interval.end:
                return True
        elif local_time >= interval.start or local_time < interval.end:
            return True
    return False
