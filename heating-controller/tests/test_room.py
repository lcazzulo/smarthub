from dataclasses import replace
from datetime import datetime, time, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock

from heating_controller import load_config
from heating_controller.config import SupplyInterval
from heating_controller.measurements import MeasurementStore
from heating_controller.mqtt import TemperatureSubscriber
from heating_controller.room import ControlLoop
from heating_controller.schedule import supply_available


class ControlLoopTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
        self.config = replace(self.config, rooms=tuple(
            replace(room, target_temperature_c=18.5) for room in self.config.rooms
        ))
        self.now = 100.0
        self.store = MeasurementStore(self.config, clock=lambda: self.now)
        self.loop = ControlLoop(self.config, self.store)
        self.wall = datetime(2026, 10, 5, 8, tzinfo=self.config.general.timezone)

    def receive(self, room, temperature):
        self.store.receive(f"zigbee2mqtt/{room}_thermometer", f'{{"temperature": {temperature}}}')

    def tick(self, now, wall=None):
        self.now = now
        return self.loop.tick(now, wall or self.wall)

    def test_independent_timing_and_room_integrals(self):
        self.receive("bedroom", 17.5)
        self.receive("bathroom", 18)
        first = self.tick(100)
        self.assertEqual([o.pi.integral_percent for o in first], [0, 0])
        self.assertEqual([o.opening_percent for o in first], [10, 5])
        self.now = 105
        self.receive("bedroom", 17.4)
        self.assertEqual(self.tick(105), ())
        second = self.tick(113)  # Actual elapsed time, not assumed 10 seconds.
        self.assertAlmostEqual(second[0].pi.integral_percent, 0.01 * 1.1 * 13)
        self.assertAlmostEqual(second[1].pi.integral_percent, 0.01 * 0.5 * 13)
        self.assertEqual(self.tick(114), ())

    def test_missing_stale_reset_and_recovery(self):
        missing = self.tick(100)
        self.assertEqual(missing[0].status, "waiting_for_temperature")
        self.receive("bedroom", 17.5)
        self.tick(110)
        self.assertGreater(self.tick(120)[0].pi.integral_percent, 0)
        self.now = 1001
        self.receive("bedroom", 17.5)  # Repeated temperature does not refresh freshness.
        output = self.tick(1001)[0]
        self.assertEqual(output.status, "stale_temperature")
        self.assertEqual(output.opening_percent, 0)
        self.assertEqual(self.loop.rooms["bedroom"].pi.integral_percent, 0)
        self.now = 1011
        self.receive("bedroom", 17.6)
        recovered = self.tick(1011)[0]
        self.assertEqual(recovered.status, "active")
        self.assertEqual(recovered.pi.integral_percent, 0)

    def test_supply_closure_and_disabled_room(self):
        self.receive("bedroom", 17.5)
        self.tick(100)
        self.tick(110)
        outside = datetime(2026, 10, 5, 12, tzinfo=self.config.general.timezone)
        output = self.tick(120, outside)[0]
        self.assertEqual(output.status, "outside_supply")
        self.assertEqual(output.opening_percent, 0)
        self.assertEqual(self.loop.rooms["bedroom"].pi.integral_percent, 0)
        self.assertEqual(self.tick(130)[0].pi.integral_percent, 0)
        disabled = replace(self.config, rooms=(replace(self.config.rooms[0], enabled=False),))
        output = ControlLoop(disabled, self.store).tick(130, self.wall)[0]
        self.assertEqual(output.status, "disabled")
        self.assertEqual(output.opening_percent, 0)

    def test_reconnect_between_ticks_resets_integration(self):
        self.receive("bedroom", 17.5)
        self.tick(100)
        self.tick(110)
        self.assertGreater(self.loop.rooms["bedroom"].pi.integral_percent, 0)
        self.store.clear()
        self.now = 115
        self.receive("bedroom", 17.5)
        self.assertEqual(self.tick(120)[0].pi.integral_percent, 0)

    def test_preview_does_not_publish(self):
        client = Mock()
        subscriber = TemperatureSubscriber(self.config, self.store, client=client)
        subscriber.start()
        self.receive("bedroom", 17.5)
        self.tick(100)
        self.tick(110)
        subscriber.stop()
        client.publish.assert_not_called()

    def test_invalid_clock(self):
        self.tick(100)
        for now in (99, float("nan"), True):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.tick(now)


class SupplyScheduleTests(unittest.TestCase):
    def setUp(self):
        self.general = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml").general

    def test_boundaries_and_empty_schedule(self):
        for hour, minute, expected in [(5, 59, False), (6, 0, True), (11, 59, True), (12, 0, False), (16, 0, True), (22, 0, False)]:
            with self.subTest(hour=hour, minute=minute):
                now = datetime(2026, 10, 5, hour, minute, tzinfo=self.general.timezone)
                self.assertEqual(supply_available(self.general, now), expected)
        self.assertFalse(supply_available(replace(self.general, supply_intervals=()), now))
        with self.assertRaises(ValueError):
            supply_available(self.general, datetime(2026, 10, 5))

    def test_overnight_and_utc_conversion(self):
        general = replace(self.general, supply_intervals=(SupplyInterval(time(22), time(6)),))
        for hour, expected in [(22, True), (0, True), (5, True), (6, False), (12, False)]:
            now = datetime(2026, 10, 5, hour, tzinfo=general.timezone)
            self.assertEqual(supply_available(general, now), expected)
        self.assertTrue(supply_available(general, datetime(2026, 10, 5, 20, tzinfo=timezone.utc)))

    def test_dst_repeated_and_skipped_times(self):
        general = replace(self.general, supply_intervals=(SupplyInterval(time(2, 30), time(3, 30)),))
        for utc_hour in (0, 1):
            now = datetime(2026, 10, 25, utc_hour, 45, tzinfo=timezone.utc)
            self.assertTrue(supply_available(general, now))  # Both local 02:45 occurrences.
        self.assertFalse(supply_available(general, datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)))
        self.assertTrue(supply_available(general, datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)))
        self.assertFalse(supply_available(general, datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
