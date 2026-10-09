from dataclasses import replace
from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from heating_controller.config import HomeAssistantConfig, RecordingConfig, ConfigError, parse_config
from heating_controller.discovery import RoomDiscovery
from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.measurements import MeasurementStore
from heating_controller.runtime import HeatingRuntime
from test_actuation import configuration
from test_config import EXAMPLE
import yaml


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        config = configuration()
        self.config = replace(config, general=replace(config.general,
                              home_assistant=HomeAssistantConfig(enabled=True)))
        self.client = Mock()
        self.client.publish.return_value = SimpleNamespace(rc=0)
        self.client.subscribe.return_value = (0, 1)
        self.store = MeasurementStore(self.config, clock=lambda: 0)
        self.transport = ActuatorMQTT(self.config, self.store, client=self.client, clock=lambda: 0)
        self.runtime = HeatingRuntime(self.config, self.store, self.transport)
        self.wall = datetime(2026, 10, 6, 8, tzinfo=self.config.general.timezone)

    def connect(self):
        self.client.on_connect(self.client, None, None, 0, None)

    def states(self):
        return [call for call in self.client.publish.call_args_list if call.args[0].endswith('/state')]

    def configs(self):
        return [call for call in self.client.publish.call_args_list if call.args[0].endswith('/config')]

    def test_config_validation_and_opt_in(self):
        document = yaml.safe_load(EXAMPLE.read_text())
        del document['general']['home_assistant']
        self.assertFalse(parse_config(document).general.home_assistant.enabled)
        for value in [None, {'enabled': 'true'}, {'discovery_prefix': 'bad/+'}, {'unknown': True}]:
            with self.subTest(value=value):
                document['general']['home_assistant'] = value
                with self.assertRaises(ConfigError):
                    parse_config(document)

    def test_room_grouping_stable_ids_units_expiry_and_nulls(self):
        self.connect()
        self.runtime.tick(0, self.wall)
        configs = self.configs()
        self.assertEqual(len(configs), 10 * len(self.config.rooms))
        payloads = [json.loads(c.args[1]) for c in configs]
        self.assertEqual(len({p['unique_id'] for p in payloads}), len(payloads))
        self.assertEqual(len({p['device']['identifiers'][0] for p in payloads}), len(self.config.rooms))
        self.assertTrue(all(c.kwargs['retain'] for c in configs))
        self.assertTrue(all(p['expire_after'] >= 90 for p in payloads))
        temperature = next(p for p in payloads if p['name'] == 'Temperature')
        self.assertEqual(temperature['state_class'], 'measurement')
        self.assertEqual(temperature['device_class'], 'temperature')
        self.assertIsNone(json.loads(self.states()[0].args[1])['temperature_c'])
        self.assertTrue(all(not c.kwargs['retain'] for c in self.states()))
        other = replace(self.config, general=replace(self.config.general, mqtt=replace(
            self.config.general.mqtt, control_base_topic='other/controller')))
        other_ids = {p['unique_id'] for _, p in RoomDiscovery(other, Mock()).configurations()}
        self.assertFalse(other_ids & {p['unique_id'] for p in payloads})

    def test_throttle_status_change_retry_and_reconnect(self):
        self.connect()
        self.runtime.tick(0, self.wall)
        self.client.publish.reset_mock()
        self.runtime.tick(10, self.wall)
        self.assertEqual(self.states(), [])
        self.runtime.tick(30, self.wall)
        self.assertEqual(len(self.states()), len(self.config.rooms))
        self.client.publish.reset_mock()
        self.runtime.tick(40, self.wall.replace(hour=12))
        self.assertTrue(self.states())
        self.client.on_disconnect(self.client, None, None, 1, None)
        self.client.publish.reset_mock()
        self.runtime.tick(50, self.wall)
        self.assertFalse(self.client.publish.called)
        self.connect()
        self.client.publish.return_value = SimpleNamespace(rc=4)
        self.runtime.tick(60, self.wall)
        self.assertTrue(self.transport._discovery.pending)
        self.client.publish.return_value = SimpleNamespace(rc=0)
        self.client.publish.reset_mock()
        self.runtime.tick(70, self.wall)
        self.assertTrue(self.configs())
        self.assertTrue(self.states())
        self.assertFalse(self.transport._discovery.pending)

    def test_dry_run_telemetry_never_sends_valve_commands_and_shutdown_offline(self):
        self.client.will_set.assert_called_once_with('heating-controller/availability',
                                                    'offline', qos=1, retain=True)
        self.transport.start()
        self.connect()
        self.store.receive('zigbee2mqtt/bedroom_thermometer', '{"temperature":19}')
        for now in (0, 10, 20, 30):
            self.runtime.tick(now, self.wall)
        self.assertTrue(self.states())
        self.assertTrue(json.loads(self.states()[0].args[1])['dry_run'])
        self.assertTrue(all(not c.args[0].startswith('zigbee2mqtt/')
                            for c in self.client.publish.call_args_list))
        self.transport.stop()
        self.client.publish.assert_called_with('heating-controller/availability', 'offline',
                                               qos=1, retain=True)

    def test_recording_health_is_discovered_and_fault_changes_publish_immediately(self):
        config = replace(self.config, general=replace(self.config.general,
                         recording=RecordingConfig(enabled=True)))
        discovery = RoomDiscovery(config, self.client)
        configs = list(discovery.configurations())
        self.assertEqual(len(configs), 13 * len(config.rooms))
        self.assertTrue(any(p['name'] == 'Recording fault' for _, p in configs))
        self.connect()
        self.runtime.tick(0, self.wall)
        status = {'recording_status': 'recording', 'recording_fault': 'none',
                  'recording_dropped_records': 0}
        discovery.update(self.runtime.last_outputs, self.runtime.actuators.progress, 0,
                         recording_status=status)
        self.client.publish.reset_mock()
        discovery.update(self.runtime.last_outputs, self.runtime.actuators.progress, 10,
                         recording_status={**status, 'recording_status': 'fault',
                                           'recording_fault': 'disk full', 'recording_dropped_records': 2})
        self.assertEqual(len(self.states()), len(config.rooms))
        self.assertEqual(json.loads(self.states()[0].args[1])['recording_dropped_records'], 2)

    def test_disabled_discovery_and_publish_exceptions(self):
        transport = ActuatorMQTT(configuration(), self.store, client=Mock())
        self.assertIsNone(transport._discovery)
        transport._client.will_set.assert_not_called()
        self.connect()
        self.client.publish.side_effect = OSError('connection lost')
        with self.assertLogs('heating_controller', level='ERROR'):
            self.runtime.tick(0, self.wall)
        self.assertTrue(self.transport._discovery.pending)
