from dataclasses import replace
from datetime import datetime
import unittest

import yaml

from heating_controller.config import ConfigError, WarmupConfig, parse_config
from heating_controller.measurements import MeasurementStore, TemperatureMeasurement
from heating_controller.room import RoomController
from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.runtime import HeatingRuntime
from test_actuation import configuration
from test_application import FakeClient
from test_config import EXAMPLE


class WarmupTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.room_config = replace(self.config.rooms[0], warmup=WarmupConfig(80, 1, 0.5, 300))
        self.room = RoomController(self.room_config, 60)

    def evaluate(self, now, temperature=19, **kwargs):
        return self.room.evaluate(TemperatureMeasurement(temperature, now, now), now, True, **kwargs)

    def test_entry_freezes_integral_and_handoff_does_not_integrate_boost_time(self):
        self.assertEqual(self.evaluate(0).opening_percent, 80)
        self.assertEqual(self.evaluate(100).status, 'warming_up')
        self.assertEqual(self.room.pi.integral_percent, 0)
        handoff = self.evaluate(110, 20.5)
        self.assertEqual(handoff.status, 'active')
        self.assertEqual(handoff.pi.integral_percent, 0)
        self.assertEqual(handoff.opening_percent, 5)
        self.assertAlmostEqual(self.evaluate(120, 20.5).pi.integral_percent, 0.05)
        self.assertEqual(self.evaluate(130, 19).status, 'active')

    def test_entry_threshold_checked_only_once(self):
        for temperature, expected in [(20, 'warming_up'), (20.1, 'active'), (21, 'active'), (22, 'active')]:
            with self.subTest(temperature=temperature):
                self.room = RoomController(self.room_config, 60)
                self.assertEqual(self.evaluate(0, temperature).status, expected)
                if expected == 'active':
                    self.assertEqual(self.evaluate(10, 19).status, 'active')

    def test_deadline_and_delayed_ticks(self):
        for elapsed in (300, 900):
            with self.subTest(elapsed=elapsed):
                self.room = RoomController(self.room_config, 60)
                self.evaluate(0)
                self.assertEqual(self.evaluate(299).status, 'warming_up')
                result = self.evaluate(elapsed)
                self.assertEqual(result.status, 'active')
                self.assertEqual(result.pi.integral_percent, 0)
                self.assertEqual(self.evaluate(elapsed + 10).status, 'active')

    def test_unlimited_boost_hands_off_on_temperature_without_integrating_elapsed_time(self):
        self.room = RoomController(
            replace(self.room_config, warmup=WarmupConfig(80, 1, 0.5)), 60)
        for now in (0, 300, 900, 86400):
            self.assertEqual(self.evaluate(now).status, 'warming_up')
            self.assertEqual(self.room.pi.integral_percent, 0)
        result = self.evaluate(86410, 20.5)
        self.assertEqual(result.status, 'active')
        self.assertEqual(result.pi.integral_percent, 0)
        self.assertEqual(self.evaluate(86420).status, 'active')

    def test_unlimited_boost_preserves_cancellation_and_rearming_rules(self):
        self.room_config = replace(self.room_config, warmup=WarmupConfig(80, 1, 0.5))
        for check in (self.test_schedule_closure_rearms_but_stale_recovery_does_not,
                      self.test_reconnect_and_target_change_cancel_without_rearming,
                      self.test_missing_measurement_waits_and_disabled_room_never_boosts):
            with self.subTest(check=check.__name__):
                self.room = RoomController(self.room_config, 60)
                check()

    def test_schedule_closure_rearms_but_stale_recovery_does_not(self):
        self.evaluate(0)
        stale = self.room.evaluate(TemperatureMeasurement(19, 0, 0), 61, True)
        self.assertEqual((stale.status, stale.opening_percent), ('stale_temperature', 0))
        self.assertEqual(self.evaluate(70).status, 'active')
        for available, requested in ((False, True), (True, False)):
            closed = self.room.evaluate(TemperatureMeasurement(19, 80, 80), 80, available, requested)
            self.assertEqual(closed.opening_percent, 0)
            self.assertEqual(self.evaluate(90).status, 'warming_up')

    def test_reconnect_and_target_change_cancel_without_rearming(self):
        self.evaluate(0)
        result = self.room.evaluate(TemperatureMeasurement(19, 10, 10, generation=1), 10, True)
        self.assertEqual(result.status, 'active')
        self.assertEqual(result.pi.integral_percent, 0)
        self.room.set_target(23)
        self.assertEqual(self.evaluate(20).status, 'active')

    def test_preparation_preserves_original_deadline_but_fault_reset_cancels(self):
        self.evaluate(0)
        self.room.reset(preserve_warmup=True)
        self.assertEqual(self.evaluate(100).status, 'warming_up')
        self.room.reset(preserve_warmup=True)
        self.assertEqual(self.evaluate(300).status, 'active')
        self.room = RoomController(self.room_config, 60)
        self.evaluate(0)
        self.room.reset()
        self.assertEqual(self.evaluate(10).status, 'active')

    def test_missing_measurement_waits_and_disabled_room_never_boosts(self):
        self.assertEqual(self.room.evaluate(None, 0, True).opening_percent, 0)
        self.assertEqual(self.evaluate(10).status, 'warming_up')
        disabled = RoomController(replace(self.room_config, enabled=False), 60)
        self.assertEqual(disabled.evaluate(TemperatureMeasurement(19, 0, 0), 0, True).opening_percent, 0)

    def test_runtime_preparation_live_and_dry_run(self):
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                config = replace(self.config, rooms=(self.room_config,),
                                 general=replace(self.config.general, dry_run=dry_run))
                now = 0
                store = MeasurementStore(config, clock=lambda: now)
                client = FakeClient()
                transport = ActuatorMQTT(config, store, client=client, clock=lambda: now)
                runtime = HeatingRuntime(config, store, transport)
                transport.start()
                wall = datetime(2026, 10, 6, 8, tzinfo=config.general.timezone)
                try:
                    for now in range(0, 100, 10):
                        client.deliver()
                        runtime.tick(now, wall)
                    self.assertEqual(runtime.last_outputs[0].status, 'warming_up')
                    self.assertEqual(runtime.actuators.progress['bedroom'].phase, 'active')
                    self.assertEqual(runtime.actuators.progress['bedroom'].desired_opening, 80)
                    if dry_run:
                        self.assertEqual(client.published, [])
                    else:
                        self.assertTrue(any(values.get('valve_opening_degree') == 80
                                            for _, values in client.published))
                    now = 100
                    runtime.tick(now, wall.replace(hour=9))
                    self.assertEqual(runtime.last_outputs[0].opening_percent, 0)
                    if dry_run:
                        self.assertEqual(client.published, [])
                    else:
                        self.assertEqual(client.published[-1][1]['valve_opening_degree'], 0)
                finally:
                    transport.stop()


class WarmupConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.document = yaml.safe_load(EXAMPLE.read_text())
        self.settings = dict(opening_percent=80, entry_error_c=1, exit_error_c=0.5,
                             max_duration_seconds=300)

    def test_opt_in_is_per_room(self):
        self.assertIsNone(parse_config(self.document).rooms[0].warmup)
        self.document['rooms'][0]['warmup'] = self.settings
        config = parse_config(self.document)
        self.assertEqual(config.rooms[0].warmup, WarmupConfig(80, 1, 0.5, 300))
        self.assertIsNone(config.rooms[1].warmup)
        self.document['rooms'][0]['warmup'] = None
        self.assertIsNone(parse_config(self.document).rooms[0].warmup)

    def test_invalid_values_missing_and_unknown_fields(self):
        cases = [{}, {'enabled': True}, {**self.settings, 'extra': 1}]
        for key in self.settings:
            for value in (True, '1', None, 0, -1, float('nan'), float('inf')):
                cases.append({**self.settings, key: value})
            if key != 'max_duration_seconds':
                cases.append({k: v for k, v in self.settings.items() if k != key})
        cases += [{**self.settings, 'opening_percent': 101},
                  {**self.settings, 'entry_error_c': 0.5},
                  {**self.settings, 'exit_error_c': 2}]
        for settings in cases:
            with self.subTest(settings=settings):
                self.document['rooms'][0]['warmup'] = settings
                with self.assertRaises(ConfigError):
                    parse_config(self.document)

    def test_omitted_duration_enables_unlimited_warmup(self):
        del self.settings['max_duration_seconds']
        self.document['rooms'][0]['warmup'] = self.settings
        self.assertEqual(parse_config(self.document).rooms[0].warmup,
                         WarmupConfig(80, 1, 0.5))
