from dataclasses import replace
from pathlib import Path
import unittest

from heating_controller import load_config
from heating_controller.config import PIConfig
from heating_controller.pi import PIController, create_room_controllers


class PIControllerTests(unittest.TestCase):
    def test_one_independent_controller_per_room(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
        bedroom = replace(config.rooms[0], pi=PIConfig(10, 0.1, 0, 100))
        controllers = create_room_controllers(replace(config, rooms=(bedroom, config.rooms[1])))
        self.assertEqual(set(controllers), {"bedroom", "bathroom"})
        self.assertIsNot(controllers["bedroom"], controllers["bathroom"])
        result = controllers["bedroom"].step(17.5, 10)
        self.assertEqual(result.integral_percent, 1)
        self.assertEqual(controllers["bathroom"].integral_percent, 0)
        controllers["bedroom"].reset()
        self.assertEqual(controllers["bedroom"].integral_percent, 0)

    def test_elapsed_time_and_diagnostics(self):
        controller = PIController(PIConfig(10, 0.1, 0, 100), 18.5)
        result = controller.step(17.5, 10)
        self.assertEqual((result.error_c, result.proportional_percent, result.integral_percent, result.opening_percent), (1, 10, 1, 11))
        self.assertFalse(result.saturated)
        self.assertEqual(controller.step(17.5, 20).integral_percent, 3)
        self.assertEqual(controller.step(18.5, 10).opening_percent, 3)

    def test_upper_saturation_prevents_windup_and_recovers(self):
        controller = PIController(PIConfig(10, 1, 0, 100), 20)
        for _ in range(20):
            result = controller.step(0, 10)
            self.assertEqual(result.opening_percent, 100)
            self.assertEqual(result.integral_percent, 0)
            self.assertTrue(result.saturated)
        self.assertEqual(controller.step(19, 1).opening_percent, 11)

    def test_lower_saturation_can_unwind(self):
        controller = PIController(PIConfig(10, 1, 0, 100), 20)
        controller.step(19, 10)  # Store 10 percentage points.
        result = controller.step(22, 1)
        self.assertEqual(result.opening_percent, 0)
        self.assertEqual(result.integral_percent, 8)
        self.assertTrue(result.saturated)

    def test_integral_bounds_and_target_change(self):
        controller = PIController(PIConfig(0, 1, 0, 5), 20)
        self.assertEqual(controller.step(19, 10).integral_percent, 5)
        controller.target_temperature_c = 18
        self.assertEqual(controller.step(19, 10).integral_percent, 0)

    def test_invalid_inputs_do_not_mutate_state(self):
        controller = PIController(PIConfig(10, 1, 0, 100), 20)
        controller.step(19, 1)
        for measured, elapsed in [(float("nan"), 1), (19, 0), (19, -1), (19, True), (19, float("inf")), (1e308, 1e308)]:
            with self.subTest(measured=measured, elapsed=elapsed):
                with self.assertRaises(ValueError):
                    controller.step(measured, elapsed)
                self.assertEqual(controller.integral_percent, 1)
        with self.assertRaises(ValueError):
            controller.target_temperature_c = float("nan")
        with self.assertRaises(ValueError):
            PIController(PIConfig(-1, 1, 0, 100), 20)


if __name__ == "__main__":
    unittest.main()
