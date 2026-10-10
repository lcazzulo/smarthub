from dataclasses import replace
from datetime import time
import json
from pathlib import Path
import signal
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.application import build_parser, main, resolve_configuration, run_application
from heating_controller.config import ConfigError, SupplyInterval
from test_actuation import configuration


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        # CLI tests should not reconfigure logging for the rest of the suite.
        logging_setup = patch("heating_controller.application.logging.basicConfig")
        logging_setup.start()
        self.addCleanup(logging_setup.stop)
        self.now = 0.0
        self.stop = Event()
        self.force = Event()
        config = configuration()
        general = replace(config.general, dry_run=False,
                          supply_intervals=(SupplyInterval(time(0), time(12)), SupplyInterval(time(12), time(0))),
                          heating_intervals=(SupplyInterval(time(0), time(12)), SupplyInterval(time(12), time(0))),
                          control=replace(config.general.control, period_seconds=0.25),
                          actuator=replace(config.general.actuator, report_timeout_seconds=0.25))
        self.config = replace(config, general=general, rooms=(config.rooms[0],))
        self.client = FakeClient()
        self.disconnect_at = None

    def factory(self, config, store):
        return ActuatorMQTT(config, store, client=self.client, clock=lambda: self.now)

    def wait(self, seconds):
        self.now += seconds
        self.client.deliver()
        if self.disconnect_at is not None and self.now >= self.disconnect_at:
            self.client.on_disconnect(self.client, None, None, 1, None)
            self.stop.set()

    def run_app(self, **kwargs):
        return run_application(self.config, stop=self.stop, force_stop=self.force,
                               transport_factory=self.factory, clock=lambda: self.now,
                               wait=self.wait, **kwargs)

    def parse(self, *extra):
        return build_parser().parse_args([str(EXAMPLE), *extra])

    def test_default_dry_run_room_selection_and_overrides(self):
        config = resolve_configuration(self.parse("--room", "bedroom", "--trv-setpoint-margin", "4.5",
                                                  "--mqtt-host", "localhost"))
        self.assertTrue(config.general.dry_run)
        self.assertEqual([room.id for room in config.rooms], ["bedroom"])
        self.assertEqual(config.general.mqtt.host, "localhost")
        self.assertEqual(config.general.actuator.trv_setpoint_margin_c, 4.5)
        # Even an existing live YAML requires --live on the CLI.
        with patch("heating_controller.application.load_config", return_value=self.config):
            self.assertTrue(resolve_configuration(self.parse()).general.dry_run)
            self.assertFalse(resolve_configuration(self.parse("--live")).general.dry_run)

    def test_invalid_selection_and_setpoint_fail_before_network_creation(self):
        with self.assertRaises(ConfigError):
            resolve_configuration(self.parse("--room", "not-a-room"))
        with self.assertRaisesRegex(ValueError, "target plus TRV margin"):
            resolve_configuration(self.parse())
        with patch("heating_controller.application.ActuatorMQTT") as transport:
            with self.assertRaises(SystemExit) as raised:
                main([str(EXAMPLE)])
            self.assertEqual(raised.exception.code, 1)
            transport.assert_not_called()

    def test_bathroom_target_override_and_trv_margin(self):
        config = resolve_configuration(self.parse("--room", "bathroom", "--target-temperature", "26", "--live"))
        self.assertEqual([room.id for room in config.rooms], ["bathroom"])
        self.assertEqual(config.rooms[0].target_temperature_c, 26)
        from heating_controller.actuation import ActuatorCoordinator
        from heating_controller.room import RoomController
        from heating_controller.measurements import TemperatureMeasurement

        coordinator = ActuatorCoordinator(config)
        self.assertEqual(coordinator.adapters["bathroom"].setpoint, 31)
        room = RoomController(config.rooms[0], config.general.control.sensor_message_timeout_seconds)
        output = room.evaluate(TemperatureMeasurement(25, 0, 0), 0, True)
        self.assertEqual(output.opening_percent, 10)
        with self.assertRaisesRegex(ValueError, "target plus TRV margin"):
            resolve_configuration(self.parse("--room", "bathroom", "--target-temperature", "31"))

    def test_check_config_does_not_start_application(self):
        with patch("heating_controller.application.run_application") as run:
            main([str(EXAMPLE), "--trv-setpoint-margin", "4.5", "--check-config"])
            run.assert_not_called()

    def test_timed_live_run_full_sequence_then_confirmed_shutdown(self):
        self.assertEqual(self.run_app(run_seconds=2, shutdown_timeout=1), 0)
        commands = [payload for _, payload in self.client.published]
        self.assertEqual(commands[0]["valve_opening_degree"], 0)
        self.assertEqual(commands[1]["external_temperature_input"], 19)
        self.assertEqual(commands[1]["temperature_sensor_select"], "external")
        self.assertTrue(any(c.get("system_mode") == "heat" for c in commands))
        self.assertEqual(commands[-1], {"valve_opening_degree": 0, "valve_closing_degree": 100, "system_mode": "off"})
        self.assertTrue(all(topic == "zigbee2mqtt/bedroom_valve/set" for topic, _ in self.client.published))
        self.assertEqual(self.client.starts, 1)
        self.assertEqual(self.client.stops, 1)

    def test_dry_run_including_shutdown_never_publishes(self):
        self.config = replace(self.config, general=replace(self.config.general, dry_run=True))
        self.assertEqual(self.run_app(run_seconds=2, shutdown_timeout=1), 0)
        self.assertEqual(self.client.published, [])

    def test_live_setup_mode_change_then_open_and_shutdown(self):
        self.client.setpoint_switches_to_heat = True
        self.assertEqual(self.run_app(run_seconds=2, shutdown_timeout=1), 0)
        commands = [payload for _, payload in self.client.published]
        self.assertTrue(any(payload.get("valve_opening_degree", 0) > 0 for payload in commands))
        self.assertEqual(commands[-1]["valve_opening_degree"], 0)
        self.assertEqual(commands[-1]["system_mode"], "off")

    def test_disconnected_shutdown_exits_with_unconfirmed_closure(self):
        self.disconnect_at = 1
        with self.assertLogs("heating_controller.application", level="ERROR") as logs:
            self.assertEqual(self.run_app(run_seconds=2, shutdown_timeout=0.5), 1)
        self.assertTrue(any("closure unconfirmed" in line for line in logs.output))
        self.assertLess(self.now, 2)
        self.assertEqual(self.client.stops, 1)

    def test_report_failure_stops_application_and_shutdown_is_bounded(self):
        self.client.report_commands = False
        self.assertEqual(self.run_app(run_seconds=10, shutdown_timeout=0.6), 1)
        self.assertLess(self.now, 2)
        self.assertFalse(any(payload.get("system_mode") == "heat" for _, payload in self.client.published))
        self.assertEqual(self.client.stops, 1)

    def test_outside_supply_never_opens(self):
        self.config = replace(self.config, general=replace(self.config.general, supply_intervals=()))
        self.assertEqual(self.run_app(run_seconds=1, shutdown_timeout=1), 0)
        self.assertTrue(self.client.published)
        self.assertTrue(all(payload.get("system_mode") == "off" for _, payload in self.client.published))

    def test_forced_stop_skips_shutdown_wait(self):
        self.stop.set()
        self.force.set()
        self.assertEqual(self.run_app(run_seconds=1, shutdown_timeout=30), 1)
        self.assertEqual(self.now, 0)
        self.assertEqual(self.client.stops, 1)

    def test_sigterm_requests_stop_and_second_signal_forces_exit(self):
        original = signal.getsignal(signal.SIGTERM)

        def run(config, *, stop, force_stop, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(stop.is_set())
            self.assertFalse(force_stop.is_set())
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            self.assertTrue(force_stop.is_set())
            return 0

        with patch("heating_controller.application.run_application", side_effect=run):
            with self.assertRaises(SystemExit) as raised:
                main([str(EXAMPLE), "--trv-setpoint-margin", "4.5"])
            self.assertEqual(raised.exception.code, 0)
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)


class FakeClient:
    """Queue device reports for delivery outside the control-session lock."""

    def __init__(self):
        self.published = []
        self.target_states = []
        self.pending = []
        self.settings = {}
        self.report_commands = True
        self.setpoint_switches_to_heat = False
        self.starts = 0
        self.stops = 0

    def reconnect_delay_set(self, **kwargs):
        pass

    def connect_async(self, host, port):
        pass

    def loop_start(self):
        self.starts += 1
        self.on_connect(self, None, None, 0, None)
        self.message("zigbee2mqtt/bedroom_thermometer", {"temperature": 19})

    def subscribe(self, topics):
        return 0, 1

    def publish(self, topic, payload, *, qos, retain):
        if topic.startswith("heating-controller/"):
            assert qos == 0 and retain is True
            self.target_states.append((topic, json.loads(payload)))
            return SimpleNamespace(rc=0)
        assert qos == 0 and retain is False
        values = json.loads(payload)
        self.published.append((topic, values))
        self.settings.update(values)
        if self.setpoint_switches_to_heat and "occupied_heating_setpoint" in values:
            self.settings["system_mode"] = "heat"
        if self.report_commands:
            self.pending.append((topic.removesuffix("/set"), dict(self.settings)))
        return SimpleNamespace(rc=0)

    def deliver(self):
        pending, self.pending = self.pending, []
        for topic, payload in pending:
            self.message(topic, payload)

    def message(self, topic, payload):
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=json.dumps(payload), retain=False))

    def disconnect(self):
        self.on_disconnect(self, None, None, 0, None)

    def loop_stop(self):
        self.stops += 1
