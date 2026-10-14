from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
import json
import sqlite3

from heating_app import create_app
from heating_app.config import Room, Settings
from heating_app.demand import daily_demand
from heating_app.history import HistoryUnavailable


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class DemandTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''PRAGMA user_version=3;
                CREATE TABLE runs(id INTEGER PRIMARY KEY,dry_run INTEGER,ended_at_utc INTEGER,config_json TEXT);
                CREATE TABLE control_samples(id INTEGER PRIMARY KEY,run_id INTEGER,room_id TEXT,
                    recorded_at_utc INTEGER,requested_opening_percent REAL);
                CREATE INDEX control_room_time ON control_samples(room_id,recorded_at_utc);''')
            for run, dry in [(1, 0), (2, 1), (3, 0)]:
                db.execute('INSERT INTO runs VALUES (?,?,NULL,?)', (run, dry, json.dumps({'general': {'control': {'period_seconds': 3600}}})))

    def add(self, at, opening, run=1, room='bedroom'):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO control_samples(run_id,room_id,recorded_at_utc,requested_opening_percent) VALUES (?,?,?,?)', (run, room, stamp(at)*1000, opening))

    def query(self, first='2026-01-10', last='2026-01-10', now='2026-11-01T00:00:00Z'):
        return daily_demand(self.path, 'bedroom', {'from': first, 'to': last}, now=stamp(now))['days']

    def test_weights_durations_not_sample_count_and_excludes_closed_valve(self):
        self.add('2026-01-10T00:00:00+01:00', 25)
        self.add('2026-01-10T02:00:00+01:00', 75)
        self.add('2026-01-10T03:00:00+01:00', 0)
        self.add('2026-01-10T04:00:00+01:00', 100)
        self.add('2026-01-10T01:00:00+01:00', 100, room='bathroom')
        day = self.query()[0]
        self.assertEqual(day['bands_seconds'], [7200, 0, 3600, 0])
        self.assertEqual(day['active_seconds'], 10800)
        self.assertEqual(day['coverage_seconds'], 14400)
        self.assertEqual(day['equivalent_seconds'], 4500)
        self.assertAlmostEqual(day['average_opening_percent'], 100*4500/10800)
        self.assertEqual(day['unknown_seconds'], 20*3600)

    def test_midnight_split_and_boundary_neighbors(self):
        self.add('2026-01-09T23:30:00+01:00', 50)
        self.add('2026-01-10T00:30:00+01:00', 0)
        self.add('2026-01-10T23:30:00+01:00', 100)
        self.add('2026-01-11T00:30:00+01:00', 0)
        days = self.query('2026-01-10', '2026-01-11')
        self.assertEqual(days[0]['bands_seconds'], [0, 1800, 0, 1800])
        self.assertEqual(days[1]['bands_seconds'], [0, 0, 0, 1800])
        # A single-day query still uses the following sample to clip midnight.
        self.assertEqual(self.query()[0]['bands_seconds'], days[0]['bands_seconds'])

    def test_dry_runs_restarts_gaps_invalid_values_and_duplicate_timestamps(self):
        self.add('2026-01-10T00:00:00+01:00', 100, run=2)
        self.add('2026-01-10T01:00:00+01:00', 100, run=2)
        self.add('2026-01-10T02:00:00+01:00', 100, run=1)
        self.add('2026-01-10T06:00:00+01:00', None, run=1)
        self.add('2026-01-10T07:00:00+01:00', 101, run=1)
        self.add('2026-01-10T08:00:00+01:00', 25, run=1)
        self.add('2026-01-10T08:00:00+01:00', 50, run=1)
        self.add('2026-01-10T09:00:00+01:00', 75, run=1)
        day = self.query()[0]
        self.assertEqual(day['simulated_seconds'], 3600)
        self.assertEqual(day['coverage_seconds'], 3600)
        self.assertEqual(day['bands_seconds'], [0, 3600, 0, 0])
        self.assertEqual(day['unknown_seconds'], 22*3600)

    def test_dst_days_are_23_and_25_hours(self):
        self.assertEqual(self.query('2026-03-29', '2026-03-29')[0]['day_seconds'], 23*3600)
        self.assertEqual(self.query('2026-10-25', '2026-10-25')[0]['day_seconds'], 25*3600)
        self.add('2026-10-25T02:00:00+02:00', 100)
        self.add('2026-10-25T02:00:00+01:00', 0)
        self.assertEqual(self.query('2026-10-25', '2026-10-25')[0]['active_seconds'], 3600)

    def test_partial_day_and_run_end(self):
        self.add('2026-01-10T00:00:00+01:00', 100)
        self.add('2026-01-10T01:00:00+01:00', 100)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE runs SET ended_at_utc=? WHERE id=1', (stamp('2026-01-10T00:30:00+01:00')*1000,))
        day = self.query(now='2026-01-10T02:00:00+01:00')[0]
        self.assertTrue(day['partial'])
        self.assertEqual(day['elapsed_seconds'], 7200)
        self.assertEqual(day['coverage_seconds'], 1800)
        self.assertEqual(day['unknown_seconds'], 5400)

    def test_timeline_preserves_active_closed_missing_and_simulated_time(self):
        self.add('2026-01-10T00:00:00+01:00', 20)
        self.add('2026-01-10T00:01:00+01:00', 80)
        self.add('2026-01-10T00:02:00+01:00', 0)
        self.add('2026-01-10T00:03:00+01:00', 100, run=2)
        self.add('2026-01-10T00:04:00+01:00', 100, run=2)
        day = self.query()[0]
        cell = day['timeline'][0]
        self.assertEqual(len(day['timeline']), 288)
        self.assertEqual(cell['minute'], 0)
        self.assertEqual(cell['opening_percent'], 50)
        self.assertEqual(cell['active_seconds'], 120)
        self.assertEqual(cell['coverage_seconds'], 120)
        self.assertEqual(cell['simulated_seconds'], 60)
        self.assertEqual(cell['unknown_seconds'], 120)
        for key in ('coverage_seconds', 'active_seconds', 'simulated_seconds', 'unknown_seconds'):
            self.assertAlmostEqual(sum(c[key] for c in day['timeline']), day[key])
        self.add('2026-01-10T00:05:00+01:00', 0, run=3)
        self.add('2026-01-10T00:10:00+01:00', 0, run=3)
        closed = self.query()[0]['timeline'][1]
        self.assertEqual(closed['coverage_seconds'], 300)
        self.assertEqual(closed['unknown_seconds'], 0)
        self.assertEqual(closed['active_seconds'], 0)
        self.assertIsNone(closed['opening_percent'])

    def test_timeline_handles_dst_and_future_slots(self):
        spring = self.query('2026-03-29', '2026-03-29')[0]['timeline']
        self.assertEqual(len(spring), 276)
        self.assertFalse(any(120 <= c['minute'] < 180 for c in spring))
        self.add('2026-10-25T02:00:00+02:00', 25)
        self.add('2026-10-25T02:00:00+01:00', 100)
        self.add('2026-10-25T03:00:00+01:00', 0)
        autumn = self.query('2026-10-25', '2026-10-25')[0]['timeline']
        self.assertEqual(len(autumn), 300)
        repeated = [c for c in autumn if c['minute'] == 120]
        self.assertEqual([c['fold'] for c in repeated], [0, 1])
        self.assertEqual([c['offset'] for c in repeated], ['+0200', '+0100'])
        self.assertEqual([c['opening_percent'] for c in repeated], [25, 100])
        self.assertTrue(all(c['repeated'] for c in repeated))
        partial = self.query(now='2026-01-10T00:02:00+01:00')[0]['timeline']
        self.assertEqual(partial[0]['elapsed_seconds'], 120)
        self.assertEqual(partial[0]['unknown_seconds'], 120)
        self.assertEqual(partial[1]['elapsed_seconds'], 0)
        self.assertEqual(partial[1]['unknown_seconds'], 0)

    def test_endpoint_validation_empty_days_and_unavailable_database(self):
        settings = Settings((Room('bedroom', 'Bedroom', True),), 'broker', 1883, 'controller', self.path, 30)
        http = create_app(settings, mqtt_client=Mock()).test_client()
        url = '/api/v1/rooms/bedroom/demand'
        response = http.get(url, query_string={'from': '2025-01-01', 'to': '2025-01-02'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['days']), 2)
        self.assertIsNone(response.json['days'][0]['average_opening_percent'])
        for args in [{}, {'from': 'bad', 'to': '2025-01-01'}, {'from': '2025-01-02', 'to': '2025-01-01'}, {'from': '2025-01-01', 'to': '2025-02-01'}, {'from': '2099-01-01', 'to': '2099-01-01'}]:
            self.assertEqual(http.get(url, query_string=args).status_code, 400)
        self.assertEqual(http.get('/api/v1/rooms/unknown/demand').status_code, 404)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(HistoryUnavailable):
            self.query()
