from contextlib import closing
from dataclasses import replace
from datetime import datetime, time, timezone
import json
from pathlib import Path
import sqlite3
from threading import Event
import tempfile
import unittest
from unittest.mock import patch

import yaml

from heating_controller.application import main, run_application
from heating_controller.actuation import ActuatorCoordinator, TransportState, ValveReport, ReportField
from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.config import ConfigError, RecordingConfig, SupplyInterval, parse_config
from heating_controller.measurements import MeasurementStore
from heating_controller.recording import Reference, SQLiteRecorder, config_snapshot
from heating_controller.room import RoomOutput
from heating_controller.runtime import HeatingRuntime
from test_actuation import configuration
from test_application import FakeClient
from test_config import EXAMPLE


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.sqlite3'
        config = configuration()
        full_day = (SupplyInterval(time(0), time(12)), SupplyInterval(time(12), time(0)))
        self.config = replace(config, rooms=(config.rooms[0],), general=replace(
            config.general, recording=RecordingConfig(enabled=True, path=str(self.path), flush_interval_seconds=0.01),
            supply_intervals=full_day, heating_intervals=full_day,
            control=replace(config.general.control, period_seconds=0.25),
            actuator=replace(config.general.actuator, report_timeout_seconds=0.25)))
        self.now = 0.0
        self.utc = 1800000000.0
        metadata = patch.object(SQLiteRecorder, '_metadata', return_value=('test', 'abc123', True))
        metadata.start()
        self.addCleanup(metadata.stop)

    def recorder(self, config=None):
        recorder = SQLiteRecorder(config or self.config, clock=lambda: self.now, utc_clock=lambda: self.utc)
        self.addCleanup(recorder.close)
        return recorder

    def rows(self, table):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            return list(db.execute(f'SELECT * FROM {table} ORDER BY id'))

    def test_config_defaults_validation_and_snapshot(self):
        document = yaml.safe_load(EXAMPLE.read_text())
        del document['general']['recording']
        self.assertFalse(parse_config(document).general.recording.enabled)
        for invalid in [None, {'enabled': 1}, {'path': ''}, {'queue_capacity': True},
                        {'queue_capacity': 0}, {'retention_days': -1}, {'retention_days': 1.5},
                        {'flush_interval_seconds': float('inf')}, {'flush_interval_seconds': 0}, {'typo': 1}]:
            with self.subTest(invalid=invalid):
                document['general']['recording'] = invalid
                with self.assertRaises(ConfigError):
                    parse_config(document)
        config = replace(self.config, general=replace(self.config.general, mqtt=replace(
            self.config.general.mqtt, username_env='PRIVATE_USERNAME', password_env='PRIVATE_PASSWORD')))
        snapshot = config_snapshot(config)
        self.assertNotIn('PRIVATE', snapshot)
        self.assertNotIn('password', snapshot)
        self.assertIn('Europe/Rome', snapshot)
        self.assertTrue(json.loads(snapshot)['general']['dry_run'])

    def test_check_config_does_not_create_database(self):
        document = yaml.safe_load(EXAMPLE.read_text())
        document['general']['recording'] = {'enabled': True, 'path': str(self.path)}
        config_path = Path(self.directory.name) / 'config.yaml'
        config_path.write_text(yaml.safe_dump(document))
        with patch('heating_controller.application.logging.basicConfig'):
            main([str(config_path), '--target-temperature', '21', '--check-config'])
        self.assertFalse(self.path.exists())

    def test_sensor_arrivals_links_monotonic_timing_and_annotations(self):
        recorder = self.recorder()
        store = MeasurementStore(self.config, clock=lambda: self.now, recorder=recorder)
        topic = 'zigbee2mqtt/bedroom_thermometer'
        store.receive(topic, '{"temperature":19}')
        self.now = 10
        self.utc -= 100  # Wall-clock rollback must not alter elapsed timing.
        store.receive(topic, '{"temperature":19}')
        store.receive(topic, '{"temperature":20}', retained=True)
        store.receive(topic, '{"humidity":40}')
        client = FakeClient()
        transport = ActuatorMQTT(self.config, store, client=client, clock=lambda: self.now)
        transport.recorder = recorder
        runtime = HeatingRuntime(self.config, store, transport, recorder=recorder)
        runtime.tick(self.now, datetime(2026, 10, 8, 8, tzinfo=timezone.utc))
        recorder.annotate('Artificial sensor heating', room_id='bedroom', start_at_utc=recorder.utc_ms())
        self.assertTrue(recorder.close())
        readings = self.rows('temperature_readings')
        self.assertEqual([r['value_changed'] for r in readings], [1, 0])
        self.assertEqual([r['elapsed_seconds'] for r in readings], [0, 10])
        self.assertLess(readings[1]['recorded_at_utc'], readings[0]['recorded_at_utc'])
        sample = self.rows('control_samples')[0]
        self.assertEqual(sample['temperature_reading_id'], readings[1]['id'])
        self.assertEqual(sample['temperature_c'], 19)
        self.assertEqual(sample['measurement_age_seconds'], 0)
        self.assertEqual(sample['actuator_phase'], 'unknown')
        self.assertTrue(any(r['event_type'] == 'annotation' for r in self.rows('events')))
        run = self.rows('runs')[0]
        self.assertIsNotNone(run['ended_at_utc'])
        self.assertEqual(run['git_revision'], 'abc123')
        self.assertEqual(run['working_tree_dirty'], 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)

    def run_simulated_application(self, *, dry_run):
        config = replace(self.config, general=replace(self.config.general, dry_run=dry_run))
        client = FakeClient()
        def factory(config, store):
            return ActuatorMQTT(config, store, client=client, clock=lambda: self.now)
        def wait(seconds):
            self.now += seconds
            client.deliver()
        code = run_application(config, stop=Event(), force_stop=Event(), run_seconds=2,
                               shutdown_timeout=1, transport_factory=factory,
                               clock=lambda: self.now, wait=wait)
        self.assertEqual(code, 0)
        return client

    def test_live_simulation_records_command_attempt_and_confirmation_chain(self):
        client = self.run_simulated_application(dry_run=False)
        self.assertTrue(client.published)
        commands = self.rows('actuator_commands')
        self.assertTrue(commands)
        self.assertTrue(all(r['outcome'] == 'confirmed' for r in commands))
        reports = {r['id']: r for r in self.rows('valve_reports')}
        for command in commands:
            report = reports[command['confirmation_report_id']]
            self.assertTrue(report['accepted'])
            self.assertFalse(report['retained'])
            self.assertIsNotNone(command['completed_at_utc'])
        attempts = self.rows('command_attempts')
        self.assertEqual(len(commands), len(attempts))
        self.assertTrue(all(a['mqtt_return_code'] == 0 and a['send_result'] == 'sent' for a in attempts))
        self.assertTrue(any(c['control_sample_id'] is not None for c in commands))
        self.assertIsNone(commands[-1]['control_sample_id'])  # Shutdown command.
        self.assertFalse(self.rows('runs')[0]['dry_run'])

    def test_dry_run_is_explicit_and_never_fabricates_reports(self):
        client = self.run_simulated_application(dry_run=True)
        self.assertEqual(client.published, [])
        self.assertEqual(self.rows('valve_reports'), [])
        self.assertTrue(all(r['outcome'] == 'simulated' and r['confirmation_report_id'] is None
                            for r in self.rows('actuator_commands')))
        self.assertTrue(all(r['send_result'] == 'dry_run' and r['mqtt_return_code'] is None
                            for r in self.rows('command_attempts')))
        self.assertTrue(self.rows('runs')[0]['dry_run'])

    def test_partial_retained_invalid_reports_preserve_original_payloads(self):
        recorder = self.recorder()
        store = MeasurementStore(self.config)
        client = FakeClient()
        transport = ActuatorMQTT(self.config, store, client=client)
        transport.recorder = recorder
        client.message('zigbee2mqtt/bedroom_valve', {'system_mode':'heat'})
        client.message('zigbee2mqtt/bedroom_valve', {'valve_opening_degree':42})
        from types import SimpleNamespace
        client.on_message(client, None, SimpleNamespace(topic='zigbee2mqtt/bedroom_valve',
                          payload=b'{"system_mode":"off"}', retain=True))
        client.on_message(client, None, SimpleNamespace(topic='zigbee2mqtt/bedroom_valve',
                          payload=b'invalid', retain=False))
        recorder.close()
        reports = self.rows('valve_reports')
        self.assertEqual(len(reports), 4)
        self.assertIsNone(reports[0]['opening_setting_percent'])
        self.assertIsNone(reports[1]['system_mode'])
        self.assertEqual(reports[1]['opening_setting_percent'], 42)
        self.assertFalse(reports[2]['accepted'])
        self.assertEqual(reports[2]['rejection_reason'], 'retained')
        self.assertEqual(reports[3]['payload_json'], 'invalid')
        self.assertFalse(reports[3]['accepted'])

    def test_retry_failure_has_one_command_and_three_attempts(self):
        recorder = self.recorder()
        coordinator = ActuatorCoordinator(self.config, recorder=recorder)
        transport = TransportState(True, 1, {})
        output = RoomOutput('bedroom', 'active', 21, 19, 0, 40, None)
        for now in (0, 0.25, 0.5, 0.75):
            self.now = now
            coordinator.step((output,), now, transport, lambda c, g: 'sent')
        recorder.close()
        commands = self.rows('actuator_commands')
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]['outcome'], 'failed')
        self.assertEqual([r['attempt_number'] for r in self.rows('command_attempts')], [1, 2, 3])

    def test_changed_retry_target_and_reconnect_supersede_without_false_confirmation(self):
        recorder = self.recorder()
        coordinator = ActuatorCoordinator(self.config, recorder=recorder)
        adapter = coordinator.adapters['bedroom']
        reports = {}
        sequence = 0
        def report(command):
            nonlocal sequence
            sequence += 1
            previous = reports.get('bedroom', ValveReport())
            fields = dict(previous.fields)
            fields.update({k: ReportField(v, sequence, self.now) for k, v in command.payload.items()})
            reports['bedroom'] = ValveReport(fields, sequence)
        def step(opening=40, generation=1):
            output = RoomOutput('bedroom', 'active', 21, 19, 0, opening, None)
            return coordinator.step((output,), self.now, TransportState(True, generation, reports),
                                    lambda c, g: 'sent')
        step()
        report(adapter.close())
        self.now = 1
        step()
        report(adapter.prepare(19))
        self.now = 2
        step()
        # Opening is retried with a different current demand before any matching report.
        self.now = 2.25
        step(opening=60)
        coordinator.set_target('bedroom', 22)
        self.now = 3
        step()
        self.now = 4
        step(generation=2)
        coordinator.cancel_pending('test ended')
        recorder.close()
        commands = self.rows('actuator_commands')
        openings = [c for c in commands if c['reason'] == 'opening']
        self.assertEqual([json.loads(c['payload_json'])['valve_opening_degree'] for c in openings], [40, 60])
        self.assertEqual([c['outcome'] for c in openings], ['superseded', 'superseded'])
        self.assertEqual(openings[0]['failure_reason'], 'Retry demand changed')
        self.assertEqual(openings[1]['failure_reason'], 'Target changed')
        self.assertTrue(any(c['failure_reason'] == 'Transport generation changed' for c in commands))
        opening_ids = {c['id'] for c in openings}
        self.assertEqual([a['attempt_number'] for a in self.rows('command_attempts')
                          if a['command_id'] in opening_ids], [1, 1])

    def test_disk_write_failure_does_not_stop_application(self):
        with patch.object(SQLiteRecorder, '_write', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertLogs('heating_controller.recording', level='ERROR'):
                client = self.run_simulated_application(dry_run=True)
        self.assertEqual(client.published, [])
        self.assertEqual(self.rows('runs')[0]['stop_reason'], 'recording_failure')
        self.assertGreater(self.rows('runs')[0]['dropped_records'], 0)
        self.assertIsNone(self.rows('runs')[0]['ended_at_utc'])

    def test_wal_reader_can_query_during_recording_and_crash_is_not_marked_clean(self):
        recorder = self.recorder()
        self.assertTrue(recorder.ready.wait(2))
        with closing(sqlite3.connect(self.path)) as reader:
            reader.execute('BEGIN')
            self.assertIsNone(reader.execute('SELECT ended_at_utc FROM runs').fetchone()[0])
            recorder.event('test', 'written while reader is active')
            self.assertTrue(recorder.close())
            reader.rollback()
            self.assertEqual(reader.execute('SELECT count(*) FROM events').fetchone()[0], 1)

    def test_queue_overflow_is_nonblocking_visible_and_persisted(self):
        release = Event()
        entered = Event()
        original = SQLiteRecorder._connect
        def blocked(recorder):
            entered.set()
            release.wait(2)
            return original(recorder)
        config = replace(self.config, general=replace(self.config.general, recording=replace(
            self.config.general.recording, queue_capacity=2)))
        with patch.object(SQLiteRecorder, '_connect', blocked):
            recorder = self.recorder(config)
            self.assertTrue(entered.wait(1))
            try:
                self.assertIsNotNone(recorder.event('test', 'one'))
                self.assertIsNotNone(recorder.event('test', 'two'))
                with self.assertLogs('heating_controller.recording', level='ERROR'):
                    for _ in range(3):
                        self.assertIsNone(recorder.event('test', 'dropped'))
                self.assertEqual(recorder.status['recording_dropped_records'], 3)
            finally:
                release.set()
            self.assertTrue(recorder.close())
        self.assertEqual(self.rows('runs')[0]['dropped_records'], 3)
        self.assertTrue(any(e['event_type'] == 'recording_gap' for e in self.rows('events')))

    def test_missing_reference_stays_null_and_runs_do_not_cross_link(self):
        for _ in range(2):
            recorder = self.recorder()
            recorder.record('command_attempts', room_id='bedroom', command_id=Reference('actuator_commands', 999),
                            attempt_number=1, connection_generation=1, send_result='rejected')
            recorder.close()
        self.assertEqual(len(self.rows('runs')), 2)
        attempts = self.rows('command_attempts')
        self.assertTrue(all(a['command_id'] is None for a in attempts))
        self.assertNotEqual(attempts[0]['run_id'], attempts[1]['run_id'])

    def test_database_failure_and_unknown_schema_do_not_change_control(self):
        with patch.object(SQLiteRecorder, '_connect', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertLogs('heating_controller.recording', level='ERROR'):
                recorder = self.recorder()
                self.assertTrue(recorder.ready.wait(2))
            self.assertEqual(recorder.status['recording_status'], 'fault')
            self.assertIsNone(recorder.event('test', 'dropped'))
            recorder.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=999')
        with self.assertLogs('heating_controller.recording', level='ERROR'):
            recorder = self.recorder()
            self.assertTrue(recorder.ready.wait(2))
        self.assertEqual(recorder.status['recording_status'], 'fault')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 999)

    def test_retention_removes_complete_old_runs_but_preserves_incomplete_runs(self):
        recorder = self.recorder()
        recorder.event('test', 'old completed run')
        recorder.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO runs (started_at_utc, dry_run, config_json) VALUES (?,1,?)', (self.utc*1000, '{}'))
        self.utc += 91 * 86400
        recorder = self.recorder()
        recorder.event('test', 'new run')
        recorder.close()
        self.assertEqual(len(self.rows('runs')), 2)
        self.assertEqual([e['message'] for e in self.rows('events')], ['new run'])
        self.assertTrue(any(r['ended_at_utc'] is None for r in self.rows('runs')))


if __name__ == '__main__':
    unittest.main()
