from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.measurements import MeasurementStore
from heating_controller.runtime import HeatingRuntime
from test_actuation import configuration


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.client = Mock()
        self.client.subscribe.return_value = (0, 1)
        self.client.publish.return_value = SimpleNamespace(rc=0)
        self.store = MeasurementStore(self.config, clock=lambda: 0)
        self.transport = ActuatorMQTT(self.config, self.store, client=self.client)
        self.runtime = HeatingRuntime(self.config, self.store, self.transport)
        self.wall = datetime(2026, 10, 6, 8, tzinfo=self.config.general.timezone)
        self.connect()

    def connect(self):
        self.client.on_connect(self.client, None, None, 0, None)

    def command(self, value, room="bedroom", retained=False):
        self.client.on_message(self.client, None, SimpleNamespace(
            topic=f"heating-controller/{room}/target_temperature/set",
            payload=json.dumps(value), retain=retained))

    def tick(self, now):
        return self.runtime.tick(now, self.wall)

    def test_target_changes_pi_and_adapter_and_publishes_applied_state(self):
        self.store.receive("zigbee2mqtt/bedroom_thermometer", '{"temperature":19}')
        for now in (0, 10, 20):
            self.tick(now)
        self.assertEqual(self.runtime.actuators.progress["bedroom"].phase, "active")
        self.command(23)
        self.assertEqual(self.runtime.control.rooms["bedroom"].pi.target_temperature_c, 21)
        commands = self.tick(30)
        self.assertEqual([c.reason for c in commands if c.room_id == "bedroom"], ["close"])
        self.assertEqual(self.runtime.last_outputs[0].target_temperature_c, 23)
        self.assertEqual(self.runtime.last_outputs[0].pi.integral_percent, 0)
        self.assertEqual(self.runtime.control.rooms["bathroom"].pi.target_temperature_c, 21)
        commands = self.tick(40)
        prepare = next(c for c in commands if c.room_id == "bedroom")
        self.assertEqual(prepare.payload["occupied_heating_setpoint"], 28)
        self.client.publish.assert_any_call("heating-controller/bedroom/target_temperature",
                                           "23.0", qos=0, retain=True)
        self.assertTrue(all(c.args[0].startswith("heating-controller/")
                            for c in self.client.publish.call_args_list))

    def test_rejects_invalid_retained_and_unknown_room_commands(self):
        for value in (True, None, {}, [], "22", float("nan"), float("inf"), 3, 31):
            self.command(value)
        self.command(24, retained=True)
        self.command(24, room="unknown")
        self.tick(0)
        self.assertEqual(self.runtime.control.rooms["bedroom"].pi.target_temperature_c, 21)

    def test_latest_valid_wins_reconnect_preserves_applied_target(self):
        self.command(22)
        self.command(23)
        self.command(False)
        self.tick(0)
        self.command(24)  # Unapplied commands are discarded on disconnection.
        self.client.on_disconnect(self.client, None, None, 1, None)
        self.connect()
        self.tick(10)
        self.assertEqual(self.runtime.control.rooms["bedroom"].pi.target_temperature_c, 23)
        self.client.publish.assert_any_call("heating-controller/bedroom/target_temperature",
                                           "23.0", qos=0, retain=True)

    def test_repeated_target_keeps_pending_and_fault_is_not_cleared(self):
        self.tick(0)
        state = self.runtime.actuators.progress["bedroom"]
        self.command(21)
        self.tick(1)
        self.assertIs(self.runtime.actuators.progress["bedroom"], state)
        state.fault = "test fault"
        self.command(22)
        self.tick(2)
        self.assertEqual(self.runtime.actuators.progress["bedroom"].fault, "test fault")

    def test_failed_state_publish_is_retried(self):
        self.client.publish.return_value = SimpleNamespace(rc=4)
        self.command(22)
        self.tick(0)
        self.client.publish.reset_mock()
        self.client.publish.return_value = SimpleNamespace(rc=0)
        self.tick(1)
        self.client.publish.assert_called_once_with(
            "heating-controller/bedroom/target_temperature", "22.0", qos=0, retain=True)

    def test_live_change_supersedes_pending_opening_and_reprepares(self):
        from dataclasses import replace

        config = replace(self.config, general=replace(self.config.general, dry_run=False))
        self.transport = ActuatorMQTT(config, self.store, client=self.client, clock=lambda: 0)
        self.runtime = HeatingRuntime(config, self.store, self.transport)
        self.connect()
        self.store.receive("zigbee2mqtt/bedroom_thermometer", '{"temperature":19}')
        settings = {}

        def report(command):
            settings.update(command.payload)
            self.client.on_message(self.client, None, SimpleNamespace(
                topic="zigbee2mqtt/bedroom_valve", payload=json.dumps(settings), retain=False))

        def bedroom_command(now):
            return next(c for c in self.tick(now) if c.room_id == "bedroom")

        report(bedroom_command(0))
        report(bedroom_command(10))
        opening = bedroom_command(20)
        self.assertEqual(opening.reason, "opening")
        self.command(24)
        closure = bedroom_command(30)
        self.assertEqual(closure.reason, "close")
        report(opening)  # Late old report must not confirm the new closure.
        self.assertFalse(any(c.room_id == "bedroom" for c in self.tick(40)))
        report(closure)
        prepare = bedroom_command(50)
        self.assertEqual(prepare.payload["occupied_heating_setpoint"], 29)
        report(prepare)
        report(bedroom_command(60))
        self.tick(70)
        state = self.runtime.actuators.progress["bedroom"]
        self.assertEqual(state.phase, "active")
        self.assertIsNone(state.fault)
