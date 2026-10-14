"""Daily, duration-weighted requested valve demand from recorded control samples."""
from contextlib import closing
from datetime import date, datetime, time as day_time, timedelta
import json
import math
import sqlite3
import time
from zoneinfo import ZoneInfo

from .history import HistoryUnavailable

ZONE = ZoneInfo('Europe/Rome')


def daily_demand(path, room, args, *, now=None):
    try:
        first, last = (date.fromisoformat(args[key]) for key in ('from', 'to'))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('from and to must be calendar dates (YYYY-MM-DD)') from exc
    if not 0 <= (last - first).days < 31:
        raise ValueError('Select between 1 and 31 calendar days')
    now = time.time() if now is None else now
    if last > datetime.fromtimestamp(now, ZONE).date():
        raise ValueError('Dates cannot be in the future')
    days = []
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        start = datetime.combine(day, day_time(), ZONE).timestamp()
        end = datetime.combine(day + timedelta(days=1), day_time(), ZONE).timestamp()
        days.append(dict(date=day.isoformat(), start=start, end=end, day_seconds=end-start,
                         elapsed_seconds=max(0, min(end, now)-start), partial=now < end,
                         bands_seconds=[0., 0., 0., 0.], coverage_seconds=0., simulated_seconds=0.,
                         equivalent_seconds=0.))
    start, end = days[0]['start'] * 1000, min(days[-1]['end'], now) * 1000
    deadline = time.monotonic() + 5
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if db.execute('PRAGMA user_version').fetchone()[0] not in (1, 2, 3):
                raise HistoryUnavailable('Unsupported recording schema')
            db.execute('BEGIN')
            runs = {}
            def metadata(run_id):
                if run_id not in runs:
                    row = db.execute('SELECT dry_run,ended_at_utc,config_json FROM runs WHERE id=?', (run_id,)).fetchone()
                    period = float(json.loads(row['config_json'])['general']['control']['period_seconds'])
                    if not math.isfinite(period) or period <= 0:
                        raise ValueError('Invalid recording period')
                    runs[run_id] = (bool(row['dry_run']), period * 3000, row['ended_at_utc'])
                return runs[run_id]
            columns = 'id,run_id,recorded_at_utc,requested_opening_percent'
            previous = db.execute(f'SELECT {columns} FROM control_samples WHERE room_id=? AND recorded_at_utc<? ORDER BY recorded_at_utc DESC,id DESC LIMIT 1', (room, start)).fetchone()
            rows = db.execute(f'SELECT {columns} FROM control_samples WHERE room_id=? AND recorded_at_utc>=? AND recorded_at_utc<=? ORDER BY recorded_at_utc,id', (room, start, end))
            day_index = 0
            count = 0
            def add_interval(a, b):
                nonlocal day_index
                if a is None or a['run_id'] != b['run_id']:
                    return
                dry_run, max_gap, run_end = metadata(a['run_id'])
                delta = b['recorded_at_utc'] - a['recorded_at_utc']
                value = a['requested_opening_percent']
                if delta <= 0 or delta > max_gap or value is None or not math.isfinite(value) or not 0 <= value <= 100:
                    return
                left = max(start, a['recorded_at_utc']) / 1000
                right = min(end, b['recorded_at_utc'], run_end if run_end is not None else end) / 1000
                while day_index < len(days) and days[day_index]['end'] <= left:
                    day_index += 1
                index = day_index
                while index < len(days) and days[index]['start'] < right:
                    day = days[index]
                    duration = max(0, min(right, day['end']) - max(left, day['start']))
                    if dry_run:
                        day['simulated_seconds'] += duration
                    else:
                        day['coverage_seconds'] += duration
                        if value > 0:
                            band = min(3, math.ceil(value / 25) - 1)
                            day['bands_seconds'][band] += duration
                            day['equivalent_seconds'] += duration * value / 100
                    index += 1
            for row in rows:
                count += 1
                if count > 1000000 or time.monotonic() > deadline:
                    raise HistoryUnavailable('Too many samples; select a shorter date range')
                add_interval(previous, row)
                previous = row
            # A following sample allows exact clipping at the requested end.
            following = db.execute(f'SELECT {columns} FROM control_samples WHERE room_id=? AND recorded_at_utc>? AND recorded_at_utc<=? ORDER BY recorded_at_utc,id LIMIT 1', (room, end, now * 1000)).fetchone()
            if following is not None:
                add_interval(previous, following)
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HistoryUnavailable('Demand history unavailable; try a shorter range or retry') from exc
    for day in days:
        day.pop('start'); day.pop('end')
        active = sum(day['bands_seconds'])
        day['active_seconds'] = active
        day['average_opening_percent'] = day['equivalent_seconds'] * 100 / active if active else None
        day['unknown_seconds'] = max(0, day['elapsed_seconds'] - day['coverage_seconds'] - day['simulated_seconds'])
    return dict(room_id=room, timezone='Europe/Rome', days=days)
