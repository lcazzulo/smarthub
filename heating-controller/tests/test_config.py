import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest

import yaml

from heating_controller.config import ConfigError, load_config, parse_config


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.document = yaml.safe_load(EXAMPLE.read_text())

    def test_example_and_topic_derivation(self):
        config = load_config(EXAMPLE)
        self.assertEqual([r.id for r in config.rooms], ["bedroom", "bathroom"])
        self.assertEqual(config.general.timezone.key, "Europe/Rome")
        self.assertTrue(config.general.dry_run)
        self.assertIsNone(config.general.mqtt.password_env)
        self.assertEqual(config.rooms[0].valve.command_topic(config.general.zigbee2mqtt.base_topic), "zigbee2mqtt/bedroom_valve/set")
        self.assertEqual(config.rooms[0].thermometer.state_topic("zigbee2mqtt"), "zigbee2mqtt/bedroom_thermometer")
        self.assertEqual(config.general.supply_intervals[0].start.hour, 6)
        with self.assertRaises(FrozenInstanceError):
            config.rooms[0].name = "Changed"

    def test_defaults_and_independent_pi_overrides(self):
        del self.document["general"]["dry_run"]
        self.document["rooms"][0]["pi"] = {"kp": 12.0}
        config = parse_config(self.document)
        self.assertTrue(config.general.dry_run)
        self.assertEqual(config.rooms[0].pi.kp, 12.0)
        self.assertEqual(config.rooms[0].pi.ki, config.general.pi_defaults.ki)
        self.assertEqual(config.rooms[1].pi.kp, 10.0)

    def test_invalid_fields(self):
        cases = [
            (("version",), True),
            (("general", "dry_run"), "false"),
            (("general", "timezone"), "Unknown/Nowhere"),
            (("general", "mqtt", "port"), 0),
            (("general", "mqtt", "port"), True),
            (("general", "control", "period_seconds"), 0),
            (("general", "control", "measurement_max_age_seconds"), None),
            (("general", "control", "opening_change_threshold_percent"), 101),
            (("general", "pi_defaults", "ki"), float("nan")),
            (("general", "pi_defaults", "integral_min"), 101),
            (("general", "actuator", "external_sensor_mode"), "internal"),
            (("general", "actuator", "temperature_min_interval_seconds"), 0),
            (("general", "actuator", "temperature_refresh_seconds"), 10),
            (("general", "actuator", "report_timeout_seconds"), -1),
            (("general", "actuator", "report_max_age_seconds"), float("nan")),
            (("general", "actuator", "max_command_attempts"), True),
            (("general", "actuator", "max_command_attempts"), 0),
            (("general", "mqtt", "password_env"), "not a variable"),
            (("rooms", 0, "valve", "friendly_name"), "device/#"),
            (("rooms", 0, "thermometer", "model"), "TRVZB"),
            (("rooms", 0, "target_temperature_c"), float("inf")),
            (("rooms", 0, "pi"), {"typo": 1}),
            (("rooms", 1, "id"), "bedroom"),
            (("rooms", 1, "valve", "friendly_name"), "bedroom_valve"),
            (("general", "typo"), 1),
            (("rooms",), []),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                document = copy.deepcopy(self.document)
                parent = document
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                with self.assertRaises(ConfigError):
                    parse_config(document)

    def test_schedule_validation(self):
        for intervals in [
            [{"start": "6:00", "end": "12:00"}],
            [{"start": "24:00", "end": "12:00"}],
            [{"start": "06:00", "end": "06:00"}],
            [{"start": "22:00", "end": "06:00"}, {"start": "05:00", "end": "07:00"}],
        ]:
            with self.subTest(intervals=intervals):
                self.document["general"]["supply_intervals"] = intervals
                with self.assertRaises(ConfigError):
                    parse_config(self.document)
        self.document["general"]["supply_intervals"] = [{"start": "22:00", "end": "06:00"}, {"start": "06:00", "end": "12:00"}]
        self.assertEqual(len(parse_config(self.document).general.supply_intervals), 2)
        self.document["general"]["supply_intervals"] = []
        self.assertEqual(parse_config(self.document).general.supply_intervals, ())

    def test_yaml_errors_duplicate_keys_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            for text in ("version: 1\nversion: 1\n", "a: [", "!!python/object:unsafe {}", "[]"):
                with self.subTest(text=text):
                    path.write_text(text)
                    with self.assertRaises(ConfigError):
                        load_config(path)
            with self.assertRaises(ConfigError):
                load_config(Path(directory) / "missing.yaml")


if __name__ == "__main__":
    unittest.main()
