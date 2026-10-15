from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from queue import Empty
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from heating_app import create_app
from heating_app.config import Room, Settings
from heating_app.history import History, HistoryUnavailable, parse_time
from heating_app.live import LiveState, decode


PAYLOAD = {'temperature_c': 23.4, 'target_temperature_c': 20.0,
           'opening_percent': 0, 'status': 'active', 'measurement_age_seconds': 12,
           'proportional_percent': -34, 'integral_percent': 0,
           'actuator_status': 'closed', 'fault': 'none', 'dry_run': False,
           'hvac_action': 'idle'}
START = '2026-10-10T12:00:00Z'
END = '2026-10-10T13:00:00Z'


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'history.sqlite3'
        self.settings = Settings((Room('bedroom', 'Bedroom', True), Room('bathroom', 'Bathroom', True),
                                  Room('kitchen', 'Kitchen', False), Room('living_room', 'Livingroom', True)),
                                 'fake-broker', 1883, 'test/controller', self.path, 30)
        self.now = 100.0
        self.live = LiveState(self.settings, clock=lambda: self.now, wall_clock=lambda: 1791633600 + self.now)
        self.client = Mock()
        self.client.publish.return_value = SimpleNamespace(rc=0)
        self.client.subscribe.return_value = (0, 1)
        self.app = create_app(self.settings, mqtt_client=self.client, live=self.live)
        self.app.testing = True
        self.http = self.app.test_client()

    def online(self, room='bedroom'):
        self.live.connection(True)
        self.live.receive('test/controller/availability', b'online', True)
        self.live.receive(f'test/controller/{room}/state', json.dumps(PAYLOAD))

    def drain(self, sub):
        messages = []
        while True:
            try:
                messages.append(sub.queue.get_nowait())
            except Empty:
                return messages

    def test_factory_has_no_network_side_effect_and_lists_rooms(self):
        self.client.connect_async.assert_not_called()
        result = self.http.get('/api/v1/rooms').json['rooms']
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]['target_max_c'], 30)
        self.assertFalse(result[2]['enabled'])
        self.assertEqual(self.http.get('/api/v1/rooms/nope/state').status_code, 404)
        self.assertFalse(self.http.get('/api/v1/rooms/bedroom/state').json['available'])

    def test_target_only_publishes_valid_online_nonretained_controller_command(self):
        self.online()
        url = '/api/v1/rooms/bedroom/target'
        result = self.http.put(url, json={'temperature_c': 21.5})
        self.assertEqual(result.status_code, 202)
        self.assertFalse(result.json['confirmed'])
        self.client.publish.assert_called_once_with('test/controller/bedroom/target_temperature/set',
                                                    '21.5', qos=0, retain=False)
        # Sending a command cannot make the reported target change optimistically.
        self.assertIsNone(self.live.snapshot('bedroom')['reported_target'])
        for bad in [True, None, '22', 3, 30.1, float('nan'), float('inf'), {}, []]:
            with self.subTest(bad=bad):
                self.assertEqual(self.http.put(url, json={'temperature_c': bad}).status_code, 400)
        self.assertEqual(self.client.publish.call_count, 1)
        self.assertEqual(self.http.put(url, json={'temperature_c': 20, 'extra': 1}).status_code, 400)
        self.assertEqual(self.http.put(url, data='oops', content_type='application/json').status_code, 400)
        self.assertEqual(self.http.put('/api/v1/rooms/kitchen/target', json={'temperature_c': 20}).status_code, 400)
        self.assertEqual(self.http.put('/api/v1/rooms/nope/target', json={'temperature_c': 20}).status_code, 404)

    def test_target_rejects_disconnected_offline_stale_and_publish_failure(self):
        url = '/api/v1/rooms/bedroom/target'
        self.assertEqual(self.http.put(url, json={'temperature_c': 20}).status_code, 503)
        self.online()
        self.now += 91
        self.assertEqual(self.http.put(url, json={'temperature_c': 20}).status_code, 503)
        self.online()
        self.client.publish.return_value.rc = 4
        self.assertEqual(self.http.put(url, json={'temperature_c': 20}).status_code, 503)
        self.client.publish.side_effect = OSError('failed')
        self.assertEqual(self.http.put(url, json={'temperature_c': 20}).status_code, 503)
        self.client.publish.reset_mock()
        self.live.receive('test/controller/availability', b'offline')
        self.assertEqual(self.http.put(url, json={'temperature_c': 20}).status_code, 503)
        self.client.publish.assert_not_called()

    def test_untrusted_browser_origin_is_rejected(self):
        self.online()
        result = self.http.put('/api/v1/rooms/bedroom/target', json={'temperature_c': 22},
                               headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(result.status_code, 403)
        self.client.publish.assert_not_called()
        self.assertEqual(self.http.get('/api/v1/rooms', headers={'Origin': 'http://localhost'}).status_code, 200)

    def test_subscriptions_switch_snapshot_targets_and_isolation(self):
        self.online()
        first, second = self.live.add_subscription(), self.live.add_subscription()
        self.live.subscribe(first, 'bedroom')
        self.live.subscribe(second, 'bathroom')
        events = self.drain(first)
        self.assertEqual([e['type'] for e in events], ['system.state', 'room.state'])
        self.assertTrue(events[-1]['data']['available'])
        self.drain(second)
        self.live.receive('test/controller/bedroom/target_temperature', b'26', retained=True)
        event = self.drain(first)[0]
        self.assertEqual(event['type'], 'room.target')
        self.assertTrue(event['data']['retained'])
        self.assertEqual(self.drain(second), [])
        self.live.receive('test/controller/bedroom/state', json.dumps(PAYLOAD))
        self.live.subscribe(first, 'bathroom')
        self.assertEqual(self.drain(first)[-1]['room_id'], 'bathroom')
        self.live.receive('test/controller/bedroom/state', json.dumps(PAYLOAD))
        self.assertEqual(self.drain(first), [])
        self.live.remove_subscription(first)
        self.assertNotIn(first, self.live.subscriptions)

    def test_retained_and_malformed_telemetry_cannot_initialize_or_refresh_state(self):
        self.live.connection(True)
        self.live.receive('test/controller/availability', 'online', True)
        self.live.receive('test/controller/bedroom/state', json.dumps(PAYLOAD), True)
        self.assertFalse(self.live.snapshot('bedroom')['available'])
        self.online()
        before = self.live.snapshot('bedroom')['received_at']
        self.now += 10
        for bad in ['null', '[]', '{}', '{', json.dumps({**PAYLOAD, 'opening_percent': True}),
                    json.dumps({**PAYLOAD, 'opening_percent': float('nan')}),
                    json.dumps({**PAYLOAD, 'temperature_c': 1e309})]:
            self.live.receive('test/controller/bedroom/state', bad)
        self.assertEqual(self.live.snapshot('bedroom')['received_at'], before)

    def test_expiry_and_reconnect_need_new_telemetry(self):
        self.online()
        sub = self.live.add_subscription()
        self.live.subscribe(sub, 'bedroom')
        self.drain(sub)
        self.now += 91
        self.live.expire()
        self.assertTrue(self.drain(sub)[0]['data']['stale'])
        self.online()
        self.live.connection(False)
        self.live.connection(True)
        self.live.receive('test/controller/availability', 'online', True)
        self.assertFalse(self.live.snapshot('bedroom')['available'])
        self.live.receive('test/controller/bedroom/state', json.dumps(PAYLOAD))
        self.assertTrue(self.live.snapshot('bedroom')['available'])

    def test_slow_consumer_is_marked_for_disconnect(self):
        self.online()
        sub = self.live.add_subscription()
        self.live.subscribe(sub, 'bedroom')
        for _ in range(70):
            self.live.receive('test/controller/bedroom/state', json.dumps(PAYLOAD))
        self.assertTrue(sub.overflow)
        self.assertEqual(sub.queue.qsize(), 64)

    def test_mqtt_reconnect_subscribes_only_to_controller_topics(self):
        self.client.on_connect(self.client, None, None, 0, None)
        topics = self.client.subscribe.call_args.args[0]
        self.assertEqual(len(topics), 10)
        self.assertTrue(all(t.startswith('test/controller/') and not t.endswith('/set') for t, qos in topics))
        self.client.on_disconnect(self.client, None, None, 1, None)
        self.assertFalse(self.live.system()['mqtt_connected'])

    def fixture(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''PRAGMA user_version=1;
                PRAGMA journal_mode=WAL;
                CREATE TABLE runs(id INTEGER PRIMARY KEY,started_at_utc INTEGER,ended_at_utc INTEGER,
                                  dry_run INTEGER,config_json TEXT);
                CREATE TABLE control_samples(id INTEGER PRIMARY KEY,run_id INTEGER,room_id TEXT,
                    recorded_at_utc INTEGER,temperature_c REAL,target_temperature_c REAL,
                    measurement_age_seconds REAL,supply_available INTEGER,heating_requested INTEGER,
                    control_status TEXT,proportional_percent REAL,integral_percent REAL,
                    requested_opening_percent REAL,saturated INTEGER,actuator_phase TEXT,actuator_fault TEXT);
                CREATE INDEX control_samples_room_time ON control_samples(room_id,recorded_at_utc);
                CREATE TABLE temperature_readings(id INTEGER PRIMARY KEY,run_id INTEGER,room_id TEXT,
                    recorded_at_utc INTEGER,temperature_c REAL,value_changed INTEGER);
                CREATE TABLE actuator_commands(id INTEGER PRIMARY KEY,run_id INTEGER,room_id TEXT,
                    recorded_at_utc INTEGER,reason TEXT,payload_json TEXT,outcome TEXT,
                    completed_at_utc INTEGER,failure_reason TEXT);
                CREATE TABLE valve_reports(id INTEGER PRIMARY KEY,run_id INTEGER,room_id TEXT,
                    recorded_at_utc INTEGER,accepted INTEGER,retained INTEGER,
                    opening_setting_percent REAL,closing_setting_percent REAL,system_mode TEXT,
                    running_state TEXT,external_temperature_c REAL,setpoint_c REAL);
            ''')
            config = {'general': {'timezone': 'Europe/Rome', 'control': {'period_seconds': 10},
                                 'supply_intervals': [], 'heating_intervals': [],
                                 'mqtt': {'password_env': 'DO_NOT_EXPOSE'}},
                      'rooms': [{'id': 'bedroom', 'pi': {'kp': 10}}]}
            for run in (1, 2):
                db.execute('INSERT INTO runs VALUES (?,?,?,?,?)',
                           (run, parse_time(START), None, run == 1, json.dumps(config)))
            for id_, run, seconds, room in [(1,1,0,'bedroom'), (2,1,10,'bedroom'),
                                           (3,1,100,'bedroom'), (4,2,110,'bedroom'),
                                           (5,2,115,'bathroom')]:
                db.execute('INSERT INTO control_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (id_,run,room,parse_time(START)+seconds*1000,23,20,1,1,1,'active',-30,0,0,0,'closed',None))
            db.execute('INSERT INTO temperature_readings VALUES (1,1,?,?,23,1)', ('bedroom',parse_time(START)))
            db.execute('INSERT INTO actuator_commands VALUES (1,1,?,?,?, ?,?,?,NULL)',
                       ('bedroom',parse_time(START),'opening','{"valve_opening_degree":14}',
                        'confirmed',parse_time(START)+1000))
            db.execute('INSERT INTO valve_reports VALUES (1,1,?,?,1,0,14,100,?,?,24,31)',
                       ('bedroom',parse_time(START),'heat','heat'))

    def test_history_pagination_gaps_run_boundaries_and_metadata(self):
        self.fixture()
        url = '/api/v1/rooms/bedroom/history'
        args = {'from': START, 'to': END, 'limit': 2}
        first = self.http.get(url, query_string=args)
        self.assertEqual(first.status_code, 200)
        self.assertEqual([r['id'] for r in first.json['samples']], [1,2])
        self.assertEqual([r['break_before'] for r in first.json['samples']], [True,False])
        self.assertNotIn('DO_NOT_EXPOSE', first.text)
        second = self.http.get(url, query_string={**args,'cursor': first.json['next_cursor']}).json
        self.assertEqual([r['id'] for r in second['samples']], [3,4])
        self.assertTrue(all(r['break_before'] for r in second['samples']))
        self.assertIsNone(second['next_cursor'])
        self.assertEqual([r['dry_run'] for r in second['runs']], [True,False])
        for series in ('temperature','commands','reports'):
            response = self.http.get(url, query_string={**args,'series':series})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json['samples']),1)

    def test_sensor_history_pagination_and_isolation(self):
        from dataclasses import replace
        self.fixture()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""PRAGMA user_version=3;
                CREATE TABLE humidity_readings(id INTEGER PRIMARY KEY,run_id INTEGER,
                    room_id TEXT,recorded_at_utc INTEGER,humidity_percent REAL);
                CREATE TABLE outdoor_temperature_readings(id INTEGER PRIMARY KEY,run_id INTEGER,
                    room_id TEXT,recorded_at_utc INTEGER,topic TEXT,temperature_c REAL);""")
            for id_, run, room, topic in [(1,1,'bedroom','outside'), (2,1,'bedroom','outside'),
                                          (3,2,'bedroom','outside'), (4,2,'bathroom','other')]:
                db.execute('INSERT INTO humidity_readings VALUES (?,?,?,?,?)',
                           (id_,run,room,parse_time(START)+id_*1000,40+id_))
                db.execute('INSERT INTO outdoor_temperature_readings VALUES (?,?,?,?,?,?)',
                           (id_,run,'',parse_time(START)+id_*1000,topic,-id_))
        http = create_app(replace(self.settings, outdoor_topic='outside'), mqtt_client=Mock()).test_client()
        for url, series, field in [('/api/v1/rooms/bedroom/history','humidity','humidity_percent'),
                                   ('/api/v1/outdoor/history','outdoor','temperature_c')]:
            args = {'from': START, 'to': END, 'series': series, 'limit': 1}
            samples = []
            while True:
                response = http.get(url, query_string=args)
                self.assertEqual(response.status_code, 200)
                samples.extend(response.json['samples'])
                if not response.json['next_cursor']:
                    break
                args['cursor'] = response.json['next_cursor']
            self.assertEqual([r['id'] for r in samples], [1,2,3])
            self.assertEqual([r['break_before'] for r in samples], [True,False,True])
            self.assertIn(field, samples[0])
        self.assertEqual(http.get('/api/v1/outdoor/history', query_string={'from': END, 'to': START}).status_code, 400)

    def test_sensor_history_on_older_schema(self):
        self.fixture()
        args = {'from': START, 'to': END, 'series': 'humidity'}
        self.assertEqual(self.http.get('/api/v1/rooms/bedroom/history', query_string=args).json['samples'], [])
        self.assertEqual(History(self.path).query(None, {**args, 'series': 'outdoor'}, outdoor_topic='outside')['samples'], [])

    def test_history_read_only_and_range_validation(self):
        args = {'from': START, 'to': END}
        url = '/api/v1/rooms/bedroom/history'
        self.assertEqual(self.http.get(url, query_string=args).status_code, 503)
        self.assertFalse(self.path.exists())
        self.fixture()
        for change in [{'from':'2026-10-10'}, {'to':START}, {'limit':5001}, {'limit':'nan'},
                       {'series':'runs; DROP TABLE runs'}, {'cursor':'oops'}, {'resolution':'1m'},
                       {'to':'2027-10-10T13:00:00Z'}]:
            self.assertEqual(self.http.get(url, query_string={**args,**change}).status_code, 400)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=4')
        self.assertEqual(self.http.get(url, query_string=args).status_code, 503)

    def test_history_wal_reads_committed_data_while_writer_active(self):
        self.fixture()
        with closing(sqlite3.connect(self.path)) as writer, writer:
            writer.execute('UPDATE control_samples SET temperature_c=29 WHERE id=1')
            result = History(self.path).query('bedroom',{'from':START,'to':END})
            self.assertEqual(result['samples'][0]['temperature_c'],23)
            writer.commit()
            result = History(self.path).query('bedroom',{'from':START,'to':END})
            self.assertEqual(result['samples'][0]['temperature_c'],29)

    def test_dst_instants_are_distinct_and_utc_output(self):
        a=parse_time('2026-10-25T02:30:00+02:00')
        b=parse_time('2026-10-25T02:30:00+01:00')
        self.assertEqual(b-a,3600000)
        self.assertEqual(a,parse_time('2026-10-25T00:30:00Z'))

    def test_actual_websocket_snapshot_update_switch_errors_and_disconnect(self):
        from werkzeug.serving import make_server
        from websocket import create_connection
        server = make_server('127.0.0.1', 0, self.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.online()
        ws = create_connection(f'ws://127.0.0.1:{server.server_port}/ws', timeout=3,
                               http_proxy_host=None)
        try:
            self.assertEqual(json.loads(ws.recv())['type'],'system.state')
            ws.send(json.dumps({'type':'subscribe','room_id':'bedroom'}))
            self.assertEqual(json.loads(ws.recv())['type'],'system.state')
            event=json.loads(ws.recv())
            self.assertEqual(event['room_id'],'bedroom')
            self.assertTrue(event['data']['available'])
            self.live.receive('test/controller/bedroom/state',json.dumps({**PAYLOAD,'temperature_c':25}))
            self.assertEqual(json.loads(ws.recv())['data']['telemetry']['temperature_c'],25)
            ws.send('{bad')
            self.assertEqual(json.loads(ws.recv())['type'],'error')
            ws.send(json.dumps({'type':'subscribe','room_id':'bathroom'}))
            self.assertEqual(json.loads(ws.recv())['type'],'system.state')
            self.assertEqual(json.loads(ws.recv())['room_id'],'bathroom')
        finally:
            ws.close()


class ConfigurationTests(unittest.TestCase):
    def test_load_subset_without_controller_dependency(self):
        import yaml
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'controller.yaml'
            document={'version':1,'general':{'mqtt':{'host':'mosquitto','port':1883},
                      'actuator':{'trv_setpoint_margin_c':5},'control':{'period_seconds':10}},
                      'rooms':[{'id':'bedroom','name':'Bedroom'}]}
            path.write_text(yaml.safe_dump(document))
            with patch.dict(os.environ, {'HEATING_CONTROLLER_CONFIG':str(path),
                                        'HEATING_HISTORY_DB':str(Path(directory)/'db'),
                                        'HEATING_MQTT_HOST':'localhost'},clear=True):
                settings=Settings.from_env()
                self.assertEqual(settings.max_target,30)
                self.assertEqual(settings.mqtt_host,'localhost')
                document['rooms'][0]['id']='bad/+'
                path.write_text(yaml.safe_dump(document))
                with self.assertRaises(ValueError): Settings.from_env()


if __name__ == '__main__':
    unittest.main()
