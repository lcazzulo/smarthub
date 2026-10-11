"""Bounded, read-only access to version 1 controller recordings."""
from contextlib import closing
from datetime import datetime
import json
import sqlite3
import time

from .live import timestamp


class HistoryUnavailable(Exception):
    pass


SERIES = {
    'control': ('control_samples', 'temperature_c,target_temperature_c,measurement_age_seconds,'
                'supply_available,heating_requested,control_status,proportional_percent,integral_percent,'
                'requested_opening_percent,saturated,actuator_phase,actuator_fault'),
    'temperature': ('temperature_readings', 'temperature_c,value_changed'),
    'commands': ('actuator_commands', 'reason,payload_json,outcome,completed_at_utc,failure_reason'),
    'reports': ('valve_reports', 'accepted,retained,opening_setting_percent,closing_setting_percent,'
                'system_mode,running_state,external_temperature_c,setpoint_c'),
}


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError('from and to are required ISO-8601 timestamps with timezone')
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError()
        return int(date.timestamp() * 1000)
    except (ValueError, OverflowError) as exc:
        raise ValueError('Expected an ISO-8601 timestamp with timezone') from exc


class History:
    def __init__(self, path):
        self.path = path.resolve()

    def query(self, room_id, args):
        start, end = parse_time(args.get('from')), parse_time(args.get('to'))
        if not 0 < end - start <= 31 * 86400000:
            raise ValueError('Time range must be positive and at most 31 days')
        series = args.get('series', 'control')
        if series not in SERIES:
            raise ValueError('series must be control, temperature, commands or reports')
        if args.get('resolution', 'raw') != 'raw':
            raise ValueError('Only resolution=raw is currently supported; follow next_cursor for all samples')
        try:
            limit = int(args.get('limit', 1000))
        except (TypeError, ValueError) as exc:
            raise ValueError('limit must be an integer') from exc
        if not 1 <= limit <= 5000:
            raise ValueError('limit must be between 1 and 5000')
        cursor = None
        if args.get('cursor'):
            try:
                cursor = tuple(int(v) for v in args['cursor'].split(':'))
                if len(cursor) != 3 or not start <= cursor[0] < end or min(cursor[1:]) <= 0 or max(cursor[1:]) >= 2**63:
                    raise ValueError()
            except (ValueError, AttributeError) as exc:
                raise ValueError('Invalid cursor; keep the same room, series and time range across pages') from exc
        table, columns = SERIES[series]
        params = [room_id, start, end]
        after = ''
        if cursor:
            after = ' AND (recorded_at_utc, id) > (?, ?)'
            params.extend(cursor[:2])
        params.append(limit + 1)
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                deadline = time.monotonic() + 3
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                    raise HistoryUnavailable('Unsupported recording schema; expected version 1')
                db.execute('BEGIN')
                rows = db.execute(f'SELECT id,run_id,recorded_at_utc,{columns} FROM {table} '
                                  'WHERE room_id=? AND recorded_at_utc>=? AND recorded_at_utc<?'
                                  + after + ' ORDER BY recorded_at_utc,id LIMIT ?', params).fetchall()
                more = len(rows) > limit
                rows = [dict(r) for r in rows[:limit]]
                runs = {}
                for run_id in sorted({r['run_id'] for r in rows}):
                    run = db.execute('SELECT id,started_at_utc,ended_at_utc,dry_run,config_json '
                                     'FROM runs WHERE id=?', (run_id,)).fetchone()
                    config = json.loads(run['config_json'])
                    general = config['general']
                    room = next((r for r in config['rooms'] if r['id'] == room_id), {})
                    runs[run_id] = {'id': run_id, 'started_at': timestamp(run['started_at_utc'] / 1000),
                                    'ended_at': timestamp(run['ended_at_utc'] / 1000) if run['ended_at_utc'] else None,
                                    'dry_run': bool(run['dry_run']),
                                    'timezone': general.get('timezone'),
                                    'supply_intervals': general.get('supply_intervals'),
                                    'heating_intervals': general.get('heating_intervals', general.get('supply_intervals')),
                                    'control_period_seconds': general['control']['period_seconds'],
                                    'pi': room.get('pi')}
        except (sqlite3.Error, OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
            raise HistoryUnavailable('Recording database unavailable or incompatible') from exc
        next_cursor = None
        if more:
            last = rows[-1]
            next_cursor = f"{last['recorded_at_utc']}:{last['id']}:{last['run_id']}"
        previous = cursor
        for row in rows:
            stamp = row.pop('recorded_at_utc')
            row['at'] = timestamp(stamp / 1000)
            if series == 'control':
                period = runs[row['run_id']]['control_period_seconds']
                row['break_before'] = (previous is None or previous[2] != row['run_id']
                                       or stamp - previous[0] > period * 3000)
            previous = (stamp, row['id'], row['run_id'])
            if 'payload_json' in row:
                row['payload'] = json.loads(row.pop('payload_json'))
            if 'completed_at_utc' in row:
                value = row.pop('completed_at_utc')
                row['completed_at'] = timestamp(value / 1000) if value else None
        return {'room_id': room_id, 'series': series, 'resolution': 'raw',
                'from': timestamp(start / 1000), 'to': timestamp(end / 1000),
                'samples': rows, 'runs': list(runs.values()), 'next_cursor': next_cursor}
