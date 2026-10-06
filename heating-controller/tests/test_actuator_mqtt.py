from dataclasses import replace
from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.adapters.trvzb import TRVZBAdapter
from heating_controller.measurements import MeasurementStore
from heating_controller.runtime import HeatingRuntime
from test_actuation import configuration


class ActuatorMQTTTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.client = Mock()
        self.client.subscribe.return_value = (0, 1)
        self.client.publish.return_value = SimpleNamespace(rc=0)
        self.now = 0
        self.store = MeasurementStore(self.config, clock=lambda: self.now)
        self.transport = ActuatorMQTT(self.config, self.store, client=self.client,
                                     clock=lambda: self.now)
        self.adapter = TRVZBAdapter(self.config.rooms[0], self.config.general.actuator, "zigbee2mqtt")

    def connect(self):
        self.client.on_connect(self.client, None, None, 0, None)

    def message(self, topic, payload, retained=False):
        self.client.on_message(self.client, None, SimpleNamespace(
            topic=topic, payload=json.dumps(payload).encode(), retain=retained))

    def test_one_connection_routes_both_sensors_and_valves(self):
        self.transport.start()
        self.connect()
        topics = dict(self.client.subscribe.call_args.args[0])
        self.assertIn("zigbee2mqtt/bedroom_thermometer", topics)
        self.assertIn("zigbee2mqtt/bathroom_valve", topics)
        self.message("zigbee2mqtt/bedroom_thermometer", {"temperature": 19})
        self.message("zigbee2mqtt/bedroom_valve", self.adapter.close().payload)
        self.assertEqual(self.store.fresh_temperature("bedroom"), 19)
        self.assertTrue(self.transport.snapshot().reports["bedroom"].matches(self.adapter.close().payload))
        self.client.connect_async.assert_called_once()
        self.transport.stop()

    def test_retained_and_malformed_reports_cannot_confirm(self):
        self.connect()
        self.message("zigbee2mqtt/bedroom_valve", self.adapter.close().payload, retained=True)
        self.message("zigbee2mqtt/bedroom_valve", {"valve_opening_degree": "zero"})
        report = self.transport.snapshot().reports["bedroom"]
        self.assertIsNone(report.fields["valve_opening_degree"].value)
        self.assertFalse(report.matches(self.adapter.close().payload))

    def test_invalid_new_field_replaces_previous_valid_state(self):
        self.connect()
        topic = "zigbee2mqtt/bedroom_valve"
        self.message(topic, {"smart_temperature_control": False})
        self.message(topic, {**self.adapter.close().payload, "smart_temperature_control": None})
        report = self.transport.snapshot().reports["bedroom"]
        self.assertTrue(report.matches(self.adapter.close().payload))
        self.assertFalse(report.matches({"smart_temperature_control": False}))

    def test_partial_reports_preserve_field_receipt_times(self):
        self.connect()
        self.message("zigbee2mqtt/bedroom_valve", {"valve_opening_degree": 0})
        self.now = 5
        self.message("zigbee2mqtt/bedroom_valve", {"system_mode": "off"})
        fields = self.transport.snapshot().reports["bedroom"].fields
        self.assertEqual(fields["valve_opening_degree"].received_at, 0)
        self.assertEqual(fields["system_mode"].received_at, 5)

    def test_dry_run_never_publishes_even_temperature_or_closure(self):
        self.connect()
        generation = self.transport.snapshot().generation
        for command in (self.adapter.close(), self.adapter.prepare(19), self.adapter.opening(40),
                        self.adapter.temperature(19.5)):
            self.assertEqual(self.transport.send(command, generation), "dry_run")
        self.client.publish.assert_not_called()

    def test_live_publish_uses_nonretained_qos_zero_and_checks_generation(self):
        config = replace(self.config, general=replace(self.config.general, dry_run=False))
        self.transport = ActuatorMQTT(config, self.store, client=self.client)
        self.connect()
        generation = self.transport.snapshot().generation
        self.assertEqual(self.transport.send(self.adapter.close(), generation), "sent")
        self.assertEqual(self.client.publish.call_args.kwargs, {"qos": 0, "retain": False})
        self.client.on_disconnect(self.client, None, None, 1, None)
        self.assertEqual(self.transport.send(self.adapter.opening(40), generation), "rejected")
        self.connect()
        self.assertEqual(self.transport.send(self.adapter.opening(40), generation), "rejected")
        self.assertEqual(self.client.publish.call_count, 1)
        self.client.publish.return_value = SimpleNamespace(rc=4)
        self.assertEqual(self.transport.send(self.adapter.close(), self.transport.snapshot().generation), "rejected")

    def test_offline_cycle_invalidates_previously_planned_commands(self):
        self.connect()
        generation = self.transport.snapshot().generation
        self.message("zigbee2mqtt/bedroom_valve/availability", {"state": "offline"})
        self.assertIn("bedroom", self.transport.snapshot().unavailable)
        self.message("zigbee2mqtt/bedroom_valve/availability", {"state": "online"})
        self.assertEqual(self.transport.send(self.adapter.opening(40), generation), "rejected")

    def test_runtime_dry_run_and_stale_measurement_closure(self):
        runtime = HeatingRuntime(self.config, self.store, self.transport)
        self.connect()
        self.message("zigbee2mqtt/bedroom_thermometer", {"temperature": 19})
        wall = datetime(2026, 10, 6, 8, tzinfo=self.config.general.timezone)
        for now in (0, 10, 20):
            self.now = now
            runtime.tick(now, wall)
        self.assertEqual(runtime.actuators.progress["bedroom"].phase, "active")
        self.now = 901
        commands = runtime.tick(901, wall)
        self.assertEqual([c.reason for c in commands if c.room_id == "bedroom"], ["close"])
        self.assertEqual(runtime.control.rooms["bedroom"].pi.output(19).integral_percent, 0)
        self.assertTrue(self.client.publish.called)
        self.assertTrue(all(call.args[0].startswith("heating-controller/")
                            for call in self.client.publish.call_args_list))
