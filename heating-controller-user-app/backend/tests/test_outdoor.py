from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import sqlite3

from heating_app import create_app
from heating_app.config import Room, Settings
from heating_app.live import LiveState


class OutdoorTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.sqlite3'
        self.settings = Settings((Room('bedroom', 'Bedroom', True),), 'broker', 1883,
                                 'controller', self.path, 30, outdoor_topic='z2m/outdoor')

    def test_restore_history_and_receive_global_updates_after_reconnect(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('PRAGMA user_version=3; CREATE TABLE outdoor_temperature_readings '
                             '(id INTEGER PRIMARY KEY, temperature_c REAL, recorded_at_utc INTEGER, topic TEXT);')
            db.execute('INSERT INTO outdoor_temperature_readings VALUES (1,-2,1700000000000,?)', ('z2m/outdoor',))
            db.execute('INSERT INTO outdoor_temperature_readings VALUES (2,30,1800000000000,?)', ('other',))
        live = LiveState(self.settings)
        client = Mock()
        client.subscribe.return_value = (0, 1)
        app = create_app(self.settings, live=live, mqtt_client=client)
        self.assertEqual(app.test_client().get('/api/v1/health').json['outdoor_temperature']['temperature_c'], -2)
        bridge = app.extensions['heating_mqtt']
        bridge._connect(client, None, None, 0, None)
        self.assertIn(('z2m/outdoor', 0), client.subscribe.call_args.args[0])
        sub = live.add_subscription()
        live.subscribe(sub, 'bedroom')
        while not sub.queue.empty():
            sub.queue.get_nowait()
        live.receive('z2m/outdoor', '{"temperature":0}')
        event = sub.queue.get_nowait()
        self.assertEqual(event['type'], 'system.state')
        self.assertEqual(event['data']['outdoor_temperature']['temperature_c'], 0)
        stamp = live.outdoor['received_at']
        for payload in ('{"temperature":true}', '{"temperature":NaN}', '{"humidity":50}',
                        '{"temperature":"2"}', '{"temperature":2,"temperature_units":"fahrenheit"}', '[]'):
            live.receive('z2m/outdoor', payload)
        live.receive('z2m/outdoor', '{"temperature":9}', retained=True)
        self.assertEqual(live.outdoor, {'temperature_c': 0, 'received_at': stamp})
        live.connection(False)
        bridge._connect(client, None, None, 0, None)
        self.assertEqual(client.subscribe.call_count, 2)
        self.assertEqual(live.outdoor['received_at'], stamp)
        client.publish.assert_not_called()

    def test_missing_database_still_allows_live_outdoor_readings(self):
        live = LiveState(self.settings)
        create_app(self.settings, live=live, mqtt_client=Mock())
        self.assertIsNone(live.outdoor)
        live.connection(True)
        live.receive('z2m/outdoor', '{"temperature":-5}')
        self.assertEqual(live.outdoor['temperature_c'], -5)

    def test_settings_reuse_configured_sensor_topic(self):
        config = Path(__file__).resolve().parents[3] / 'heating-controller/config.example.yaml'
        with patch.dict('os.environ', {'HEATING_CONTROLLER_CONFIG': str(config), 'HEATING_HISTORY_DB': str(self.path)}):
            settings = Settings.from_env()
        self.assertEqual(settings.outdoor_topic, 'zigbee2mqtt/external_thermometer')
