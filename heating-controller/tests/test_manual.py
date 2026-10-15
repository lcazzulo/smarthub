from dataclasses import replace
from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.measurements import MeasurementStore
from heating_controller.runtime import HeatingRuntime
from test_actuation import configuration


class ManualTests(unittest.TestCase):
    def setup_runtime(self, dry_run=True):
        config = configuration()
        self.config = replace(config, general=replace(config.general, dry_run=dry_run))
        self.now = 0
        self.client = Mock()
        self.client.subscribe.return_value = (0, 1)
        self.client.publish.return_value = SimpleNamespace(rc=0)
        self.store = MeasurementStore(self.config, clock=lambda: self.now)
        self.transport = ActuatorMQTT(self.config, self.store, client=self.client, clock=lambda: self.now)
        self.runtime = HeatingRuntime(self.config, self.store, self.transport)
        self.wall = datetime(2026, 10, 6, 8, tzinfo=self.config.general.timezone)
        self.transport._on_connect(self.client, None, None, 0, None)
        self.store.receive('zigbee2mqtt/bedroom_thermometer', '{"temperature":19}')
        self.tick(0)

    def tick(self, now):
        self.now = now
        commands = self.runtime.tick(now, self.wall)
        # Fake valve reports: no actual broker or hardware.
        for command in commands:
            self.transport._on_message(self.client, None, SimpleNamespace(
                topic=command.topic.removesuffix('/set'), payload=json.dumps(command.payload), retain=False))
        return [c for c in commands if c.room_id == 'bedroom']

    def request(self, enabled=True, opening=0, token=None, retained=False):
        values = dict.fromkeys(self.runtime.manual.openings, 0)
        values['bedroom'] = opening
        document = {'token': token or self.runtime.manual.token, 'enabled': enabled, 'openings': values}
        self.transport._on_message(self.client, None, SimpleNamespace(
            topic='heating-controller/manual/set', payload=json.dumps(document), retain=retained))

    def test_manual_live_sequence_suppresses_pi_and_resumes_automatic(self):
        self.setup_runtime(False)
        self.request()
        self.assertEqual(self.tick(10)[0].reason, 'close')
        self.request(opening=67)
        self.assertEqual(self.tick(20)[0].reason, 'prepare')
        opening = self.tick(30)[0]
        self.assertEqual(opening.payload['valve_opening_degree'], 67)
        self.assertNotEqual(self.runtime._manual_preview['bedroom'], 67)
        self.assertIsNone(self.runtime.last_outputs[0].pi)
        self.request(enabled=False)
        self.assertEqual(self.tick(40)[0].reason, 'close')
        self.assertFalse(self.runtime.manual.enabled)
        self.assertEqual(self.runtime.last_outputs[0].pi.integral_percent, 0)

    def test_dry_run_manual_and_shutdown_never_publish_device_commands(self):
        self.setup_runtime()
        self.request()
        self.tick(10)
        self.request(opening=100)
        self.tick(20)
        self.tick(30)
        self.runtime.shutdown_tick(40)
        self.assertEqual(self.runtime.last_outputs[0].opening_percent, 100)
        self.assertTrue(all(not c.args[0].startswith('zigbee2mqtt/')
                            for c in self.client.publish.call_args_list))

    def test_invalid_retained_stale_and_mode_change_opening_are_rejected(self):
        self.setup_runtime()
        old_token = self.runtime.manual.token
        self.request(opening=20)
        self.tick(1)
        self.assertFalse(self.runtime.manual.enabled)
        self.request(retained=True)
        self.tick(2)
        self.assertFalse(self.runtime.manual.enabled)
        self.request()
        self.tick(3)
        self.assertTrue(self.runtime.manual.enabled)
        self.request(enabled=False, token=old_token)
        self.tick(4)
        self.assertTrue(self.runtime.manual.enabled)
        for now, value in enumerate([True, '20', -1, 101, None, float('nan'), float('inf')], 5):
            self.request(opening=value)
            self.tick(now)
            self.assertEqual(self.runtime.manual.openings['bedroom'], 0)

    def test_reconnect_clears_openings_and_old_commands_without_resuming_auto(self):
        self.setup_runtime()
        self.request()
        self.tick(10)
        self.request(opening=50)
        self.tick(20)
        old_token = self.runtime.manual.token
        self.request(opening=75)
        self.transport._on_disconnect(self.client, None, None, 1, None)
        self.transport._on_connect(self.client, None, None, 0, None)
        self.tick(30)
        self.assertTrue(self.runtime.manual.enabled)
        self.assertEqual(self.runtime.manual.openings['bedroom'], 0)
        self.assertNotEqual(old_token, self.runtime.manual.token)
        self.request(opening=80, token=old_token)
        self.tick(40)
        self.assertEqual(self.runtime.manual.openings['bedroom'], 0)

    def test_supply_end_clears_demand_and_requires_new_request(self):
        self.setup_runtime()
        self.request()
        self.tick(10)
        self.request(opening=50)
        self.tick(20)
        self.tick(30)
        self.wall = self.wall.replace(hour=1)
        self.assertEqual(self.tick(40)[0].reason, 'close')
        self.assertEqual(self.runtime.manual.openings['bedroom'], 0)
        self.assertEqual(self.runtime.last_outputs[0].status, 'outside_supply')
        self.wall = self.wall.replace(hour=8)
        self.tick(50)
        self.assertEqual(self.runtime.last_outputs[0].opening_percent, 0)

    def test_mode_change_preserves_fault_and_supersedes_pending(self):
        self.setup_runtime(False)
        self.runtime.actuators.progress['bedroom'].fault = 'test fault'
        self.request()
        self.tick(10)
        self.assertEqual(self.runtime.actuators.progress['bedroom'].fault, 'test fault')
        self.assertEqual(self.runtime.actuators.progress['bedroom'].pending.command.reason, 'close')

    def test_stale_sensor_closes_and_does_not_restore_manual_demand(self):
        self.setup_runtime()
        self.request()
        self.tick(10)
        self.request(opening=50)
        self.tick(20)
        self.tick(30)
        commands = self.tick(self.config.general.control.sensor_message_timeout_seconds + 1)
        self.assertEqual(commands[0].reason, 'close')
        self.assertEqual(self.runtime.last_outputs[0].status, 'stale_temperature')
        self.assertEqual(self.runtime.manual.openings['bedroom'], 0)
        self.store.receive('zigbee2mqtt/bedroom_thermometer', '{"temperature":19}')
        self.tick(self.now + 10)
        self.assertEqual(self.runtime.last_outputs[0].opening_percent, 0)

    def test_single_percent_adjustments_retain_command_interval(self):
        self.setup_runtime(False)
        self.request()
        self.tick(10)
        self.request(opening=50)
        self.tick(20)
        self.assertEqual(self.tick(30)[0].payload['valve_opening_degree'], 50)
        self.request(opening=51)
        self.assertFalse(any(c.reason == 'opening' for c in self.tick(40)))
        commands = self.tick(90)
        self.assertEqual(commands[0].payload['valve_opening_degree'], 51)
        self.request(opening=0)
        self.assertEqual(self.tick(100)[0].reason, 'close')
