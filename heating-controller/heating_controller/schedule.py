"""Daily hot-water supply intervals in the configured local timezone."""

from datetime import datetime, time

from .config import GeneralConfig, SupplyInterval


def supply_available(config: GeneralConfig, now: datetime) -> bool:
    """Evaluate wall-clock intervals, start inclusive and end exclusive.

    Both occurrences of a repeated local time have the same availability;
    nonexistent times are skipped naturally when converting actual instants.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Supply evaluation requires a timezone-aware datetime")
    local_time = now.astimezone(config.timezone).time()
    return _contains(config.supply_intervals, local_time)


def heating_requested(config: GeneralConfig, now: datetime) -> bool:
    """Evaluate the desired heating schedule in the same local timezone."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Heating evaluation requires a timezone-aware datetime")
    return _contains(config.heating_intervals, now.astimezone(config.timezone).time())


def _contains(intervals: tuple[SupplyInterval, ...], local_time: time) -> bool:
    for interval in intervals:
        if interval.start < interval.end:
            if interval.start <= local_time < interval.end:
                return True
        elif local_time >= interval.start or local_time < interval.end:
            return True
    return False
