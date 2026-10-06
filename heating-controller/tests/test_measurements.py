from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from heating_controller import load_config
from heating_controller.measurements import MeasurementError, MeasurementStore
from heating_controller.mqtt import TemperatureSubscriber


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
        self.now = 100.0
        self.store = MeasurementStore(self.config, clock=lambda: self.now)
        self.topic = "zigbee2mqtt/bedroom_thermometer"

    def receive(self, temperature):
        return self.store.receive(self.topic, '{"temperature": %s}' % temperature)

    def test_repeated_values_do_not_refresh_change_age(self):
        self.assertIsNone(self.store.fresh_temperature("bedroom"))
        self.receive(18.5)
        snapshot = self.store.get("bedroom")
        self.now = 1000
        self.receive(18.5)
        self.assertEqual(self.store.get("bedroom").last_received_at, 1000)
        self.assertEqual(self.store.get("bedroom").last_changed_at, 100)
        self.assertEqual(self.store.fresh_temperature("bedroom"), 18.5)
        self.now = 1000.001
        self.assertIsNone(self.store.fresh_temperature("bedroom"))
        self.receive(18.5)
        self.assertIsNone(self.store.fresh_temperature("bedroom"))
        self.receive(18.6)
        self.assertEqual(self.store.fresh_temperature("bedroom"), 18.6)
        self.assertEqual(snapshot.temperature_c, 18.5)
        self.assertEqual(snapshot.last_received_at, 100)

    def test_rooms_are_independent(self):
        self.receive(18)
        self.now = 500
        self.store.receive("zigbee2mqtt/bathroom_thermometer", b'{"temperature": 19}')
        self.now = 1100
        self.assertIsNone(self.store.fresh_temperature("bedroom"))
        self.assertEqual(self.store.fresh_temperature("bathroom"), 19)

    def test_ignored_and_invalid_messages_do_not_refresh(self):
        self.receive(18)
        snapshot = self.store.get("bedroom")
        self.now = 200
        self.assertFalse(self.store.receive(self.topic, '{"temperature": 20}', retained=True))
        self.assertFalse(self.store.receive("other/topic", "invalid"))
        self.assertFalse(self.store.receive(self.topic, '{"humidity": 50}'))
        for payload in ["invalid", "[]", '{"temperature": null}', '{"temperature": true}', '{"temperature": "18"}', '{"temperature": NaN}', '{"temperature": 1e999}', '{"temperature": 18, "temperature_units": "fahrenheit"}', b'\xff']:
            with self.subTest(payload=payload):
                with self.assertRaises(MeasurementError):
                    self.store.receive(self.topic, payload)
                self.assertEqual(self.store.get("bedroom"), snapshot)

    def test_retained_startup_and_clear(self):
        self.store.receive(self.topic, '{"temperature": 18}', retained=True)
        self.assertIsNone(self.store.get("bedroom"))
        self.receive(18)
        self.store.clear()
        self.assertIsNone(self.store.fresh_temperature("bedroom"))
        self.now = 500
        self.receive(18)
        self.assertEqual(self.store.get("bedroom").last_changed_at, 500)

    def test_shared_subscriber_reconnect_and_no_publish(self):
        client = Mock()
        client.subscribe.return_value = (0, 1)
        notify = Mock()
        subscriber = TemperatureSubscriber(self.config, self.store, client=client, on_temperature=notify)
        subscriber.start()
        client.connect_async.assert_called_once_with("mosquitto", 1883)
        client.username_pw_set.assert_not_called()
        client.on_connect(client, None, None, 0, None)
        client.subscribe.assert_called_once_with([
            ("zigbee2mqtt/bedroom_thermometer", 0),
            ("zigbee2mqtt/bathroom_thermometer", 0),
            ("zigbee2mqtt/kitchen_thermometer", 0),
            ("zigbee2mqtt/livingroom_thermometer", 0),
        ])
        message = SimpleNamespace(topic=self.topic, payload=b'{"temperature": 18.5}', retain=False)
        client.on_message(client, None, message)
        self.assertEqual(self.store.fresh_temperature("bedroom"), 18.5)
        notify.assert_called_once_with("bedroom", 18.5)
        client.on_message(client, None, message)
        self.assertEqual(notify.call_count, 2)  # Repeated temperatures are printed too.
        client.on_message(client, None, SimpleNamespace(topic=self.topic, payload=message.payload, retain=True))
        client.on_message(client, None, SimpleNamespace(topic=self.topic, payload=b'{"humidity": 50}', retain=False))
        client.on_message(client, None, SimpleNamespace(topic=self.topic, payload=b"invalid", retain=False))
        self.assertEqual(notify.call_count, 2)
        client.on_disconnect(client, None, None, 1, None)
        self.assertIsNone(self.store.get("bedroom"))
        client.on_connect(client, None, None, 0, None)
        self.assertEqual(client.subscribe.call_count, 2)
        subscriber.stop()
        client.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
