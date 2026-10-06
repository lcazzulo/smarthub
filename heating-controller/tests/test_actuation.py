from dataclasses import replace
from pathlib import Path
import unittest

from heating_controller import load_config
from heating_controller.actuation import ActuatorCoordinator, ReportField, TransportState, ValveReport
from heating_controller.adapters.trvzb import TRVZBAdapter
from heating_controller.room import RoomOutput


def configuration():
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    return replace(config, rooms=tuple(replace(room, target_temperature_c=21) for room in config.rooms))


class ActuationTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.coordinator = ActuatorCoordinator(self.config)
        self.adapter = self.coordinator.adapters["bedroom"]
        self.reports = {}
        self.generation = 1
        self.connected = True
        self.sent = []
        self.result = "sent"

    def send(self, command, generation):
        self.sent.append(command)
        self.assertEqual(generation, self.generation)
        return self.result

    def step(self, now, opening=40, temperature=19, status="active", age=0):
        output = RoomOutput("bedroom", status, 21, temperature, age, opening, None)
        return self.coordinator.step((output,), now,
            TransportState(self.connected, self.generation, dict(self.reports)), self.send)

    def report(self, values, now, room="bedroom"):
        previous = self.reports.get(room, ValveReport())
        sequence = previous.sequence + 1
        fields = dict(previous.fields)
        fields.update({key: ReportField(value, sequence, now) for key, value in values.items()})
        self.reports[room] = ValveReport(fields, sequence)

    def acknowledge(self, now, room="bedroom"):
        command = self.coordinator.progress[room].pending.command
        self.report(command.payload, now, room)

    def activate(self):
        self.step(0)
        self.acknowledge(1)
        self.step(1)
        self.acknowledge(2)
        self.step(2)
        self.acknowledge(3)
        self.step(3)
        self.assertEqual(self.coordinator.progress["bedroom"].phase, "active")

    def bedroom_commands(self, commands):
        return [command for command in commands if command.room_id == "bedroom"]

    def test_staged_startup_needs_post_command_reports(self):
        self.report(self.adapter.close().payload, -1)
        self.assertEqual(self.bedroom_commands(self.step(0))[0].reason, "close")
        self.assertEqual(self.bedroom_commands(self.step(1)), [])
        self.report({"system_mode": "off"}, 2)
        self.assertEqual(self.bedroom_commands(self.step(2)), [])
        self.report({"valve_opening_degree": 0, "valve_closing_degree": 100}, 3)
        prepare = self.bedroom_commands(self.step(3))[0]
        self.assertEqual(prepare.payload["external_temperature_input"], 19)
        self.assertEqual(prepare.payload["temperature_sensor_select"], "external")
        self.assertFalse(prepare.payload["smart_temperature_control"])
        self.acknowledge(4)
        opening = self.bedroom_commands(self.step(4))[0]
        self.assertEqual(opening.payload, {"valve_opening_degree": 40, "system_mode": "heat"})

    def test_latest_opening_limits_and_threshold(self):
        self.activate()
        self.assertEqual(self.bedroom_commands(self.step(20, opening=60)), [])
        self.assertEqual(self.bedroom_commands(self.step(62, opening=42)), [])
        command = self.bedroom_commands(self.step(63, opening=70))[0]
        self.assertEqual(command.payload["valve_opening_degree"], 70)

    def test_closure_preempts_pending_opening_and_rate_limits(self):
        self.activate()
        self.step(62, opening=60)
        command = self.bedroom_commands(self.step(63, status="stale_temperature"))[0]
        self.assertEqual(command.reason, "close")
        self.assertEqual(command.payload["valve_closing_degree"], 100)
        self.assertEqual(self.bedroom_commands(self.step(64, status="stale_temperature")), [])

    def test_all_inhibited_statuses_and_zero_demand_close(self):
        for status in ("stale_temperature", "waiting_for_temperature", "outside_supply", "disabled", "active"):
            with self.subTest(status=status):
                self.setUp()
                self.activate()
                command = self.bedroom_commands(self.step(4, status=status, opening=0))[0]
                self.assertEqual(command.reason, "close")

    def test_temperature_forwarding_has_separate_interval_and_refresh(self):
        self.activate()
        self.assertEqual(self.bedroom_commands(self.step(30, temperature=19.5)), [])
        command = self.bedroom_commands(self.step(61, temperature=19.5))[0]
        self.assertEqual(command.payload, {"external_temperature_input": 19.5})
        self.acknowledge(62)
        self.step(62, temperature=19.5)
        command = self.bedroom_commands(self.step(361, temperature=19.5))[0]
        self.assertEqual(command.reason, "temperature")
        command = self.bedroom_commands(self.step(362, temperature=19.5, status="stale_temperature"))[0]
        self.assertEqual(command.reason, "close")

    def test_reconnect_discards_pending_demand_and_restarts_closed(self):
        self.activate()
        self.step(62, opening=70)
        self.generation += 1
        self.reports.clear()
        command = self.bedroom_commands(self.step(63, status="waiting_for_temperature"))[0]
        self.assertEqual(command.reason, "close")
        self.connected = False
        self.assertEqual(self.step(64), ())

    def test_no_reports_causes_bounded_closure_attempts_and_fault(self):
        for now in (0, 60, 120):
            self.step(now)
        self.assertEqual(len([c for c in self.sent if c.room_id == "bedroom"]), 3)
        self.step(180)
        self.step(240)
        self.assertEqual(self.coordinator.progress["bedroom"].phase, "fault")
        self.assertEqual(len([c for c in self.sent if c.room_id == "bedroom"]), 3)

    def test_unconfirmed_opening_faults_then_attempts_closure(self):
        self.activate()
        for now in (62, 122, 182):
            self.step(now, opening=70)
        command = self.bedroom_commands(self.step(242, opening=70))[0]
        self.assertEqual(command.reason, "close")
        self.assertIsNotNone(self.coordinator.progress["bedroom"].fault)
        self.acknowledge(243)
        self.assertEqual(self.bedroom_commands(self.step(243)), [])

    def test_failed_publish_is_not_last_sent_or_confirmed(self):
        self.result = "rejected"
        self.step(0)
        self.acknowledge(1)
        self.step(1)
        state = self.coordinator.progress["bedroom"]
        self.assertIsNone(state.last_sent)
        self.assertEqual(state.phase, "unknown")

    def test_drift_and_stale_reports_close_and_latch_fault(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                self.setUp()
                self.activate()
                if not stale:
                    self.report({"temperature_sensor_select": "internal"}, 4)
                commands = self.step(902 if stale else 4)
                self.assertEqual(self.bedroom_commands(commands)[0].reason, "close")
                self.assertIsNotNone(self.coordinator.progress["bedroom"].fault)

    def test_invalid_active_temperature_requests_closure(self):
        self.activate()
        command = self.bedroom_commands(self.step(4, temperature=-1))[0]
        self.assertEqual(command.reason, "close")

    def test_dry_run_simulates_progress_without_device_reports(self):
        self.result = "dry_run"
        reasons = [self.bedroom_commands(self.step(now))[0].reason for now in (0, 1, 2)]
        self.assertEqual(reasons, ["close", "prepare", "opening"])
        self.assertEqual(self.reports, {})
        self.assertTrue(self.coordinator.progress["bedroom"].simulated)
        self.assertIsNone(self.coordinator.progress["bedroom"].pending)

    def test_adapter_ranges_and_mode_mapping(self):
        for percent in (-1, 101, float("nan"), True):
            with self.assertRaises(ValueError):
                self.adapter.opening(percent)
        for value in (-0.1, 100, float("inf"), None):
            with self.assertRaises(ValueError):
                self.adapter.temperature(value)
        self.assertEqual(self.adapter.opening(24.7).payload["valve_opening_degree"], 25)
        settings = replace(self.config.general.actuator, external_sensor_mode="remote_temperature")
        adapter = TRVZBAdapter(self.config.rooms[0], settings, "test")
        self.assertEqual(adapter.prepare(18.27).payload["temperature_sensor_select"], "remote_temperature")
        self.assertEqual(adapter.temperature(18.27).payload["external_temperature_input"], 18.3)
        with self.assertRaisesRegex(ValueError, "target plus TRV margin"):
            ActuatorCoordinator(load_config(Path(__file__).resolve().parents[1] / "config.example.yaml"))

    def test_invalid_reports(self):
        for payload in ('[]', 'invalid'):
            with self.assertRaises(ValueError):
                TRVZBAdapter.parse_report(payload)

    def test_invalid_fields_do_not_discard_valid_closure_settings(self):
        import json
        for invalid in (None, "OFF", "false", 0, 1, {}, []):
            with self.subTest(invalid=invalid):
                payload = {**self.adapter.close().payload, "smart_temperature_control": invalid}
                parsed = TRVZBAdapter.parse_report(json.dumps(payload))
                self.assertIsNone(parsed.pop("smart_temperature_control"))
                self.assertEqual(parsed, self.adapter.close().payload)
        for payload, key in (('{"valve_opening_degree":true}', "valve_opening_degree"),
                             ('{"external_temperature_input":NaN}', "external_temperature_input")):
            self.assertIsNone(TRVZBAdapter.parse_report(payload)[key])

    def test_unknown_smart_state_allows_closure_but_cannot_confirm_setup(self):
        self.step(0)
        self.report({**self.adapter.close().payload, "smart_temperature_control": None}, 1)
        self.assertEqual(self.bedroom_commands(self.step(1))[0].reason, "prepare")
        setup = self.coordinator.progress["bedroom"].pending.command.payload
        self.report({**setup, "smart_temperature_control": None}, 2)
        self.assertEqual(self.bedroom_commands(self.step(2)), [])
        self.assertNotEqual(self.coordinator.progress["bedroom"].phase, "active")

    def test_fallback_preempts_pending_temperature(self):
        self.activate()
        self.step(61, temperature=19.5)
        self.report({"temperature_sensor_select": "internal"}, 62)
        command = self.bedroom_commands(self.step(62, temperature=19.5))[0]
        self.assertEqual(command.reason, "close")

    def test_setup_ack_with_unsafe_closing_endpoint_cannot_open(self):
        self.step(0)
        self.acknowledge(1)
        self.step(1)
        self.acknowledge(2)
        self.report({"valve_closing_degree": 0}, 2)
        command = self.bedroom_commands(self.step(2))[0]
        self.assertEqual(command.reason, "close")

    def test_setup_ack_requires_valve_still_reported_closed(self):
        self.step(0)
        self.acknowledge(1)
        self.step(1)
        self.acknowledge(2)
        self.report({"system_mode": "heat", "valve_opening_degree": 100}, 2)
        command = self.bedroom_commands(self.step(2))[0]
        self.assertEqual(command.reason, "close")

    def test_setup_can_switch_to_heat_while_endpoints_remain_closed(self):
        self.step(0, opening=5)
        self.acknowledge(1)
        self.step(1, opening=5)
        # Reports can arrive incrementally, as in the live bathroom run.
        self.report({"system_mode": "heat", "occupied_heating_setpoint": self.adapter.setpoint}, 2)
        self.assertEqual(self.bedroom_commands(self.step(2, opening=5)), [])
        self.acknowledge(3)
        command = self.bedroom_commands(self.step(3, opening=5))[0]
        self.assertEqual(command.payload, {"valve_opening_degree": 5, "system_mode": "heat"})
        self.assertIsNone(self.coordinator.progress["bedroom"].fault)
        self.acknowledge(4)
        self.step(4, opening=5)
        self.assertEqual(self.coordinator.progress["bedroom"].phase, "active")

    def test_setup_still_rejects_auto_unknown_and_stale_mode(self):
        for mode in ("auto", None, "stale"):
            with self.subTest(mode=mode):
                self.setUp()
                self.step(0)
                self.acknowledge(1)
                self.step(1)
                self.acknowledge(2)
                self.report({"system_mode": "heat" if mode == "stale" else mode},
                            -1000 if mode == "stale" else 2)
                command = self.bedroom_commands(self.step(2))[0]
                self.assertEqual(command.reason, "close")
                self.assertIsNotNone(self.coordinator.progress["bedroom"].fault)

    def test_retry_uses_latest_demand(self):
        self.activate()
        self.step(62, opening=70)
        command = self.bedroom_commands(self.step(122, opening=20))[0]
        self.assertEqual(command.payload["valve_opening_degree"], 20)

    def test_one_valve_can_progress_while_other_waits_for_reports(self):
        self.activate()
        self.assertEqual(self.coordinator.progress["bathroom"].phase, "unknown")
        self.assertEqual(self.coordinator.progress["bathroom"].pending.command.reason, "close")
