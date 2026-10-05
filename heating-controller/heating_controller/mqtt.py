"""One shared, subscription-only MQTT connection for room thermometers."""

import logging
import os
from typing import Callable

from .config import Configuration
from .measurements import MeasurementError, MeasurementStore


logger = logging.getLogger(__name__)


class TemperatureSubscriber:
    """Acquire temperatures without publishing device commands or telemetry.

    Paho's network thread updates the thread-safe store. The future control loop
    reads snapshots on its own schedule. A client can be injected for tests.
    """

    def __init__(self, config: Configuration, store: MeasurementStore, *, client=None,
                 on_temperature: Callable[[str, float], None] | None = None):
        if client is None:
            from paho.mqtt.client import CallbackAPIVersion, Client

            client = Client(callback_api_version=CallbackAPIVersion.VERSION2, clean_session=True)
        self._config = config.general.mqtt
        self._store = store
        self._on_temperature = on_temperature
        base = config.general.zigbee2mqtt.base_topic
        self._rooms_by_topic = {
            room.thermometer.state_topic(base): room.id for room in config.rooms
        }
        self._client = client
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Subscriber is already started")
        credentials = []
        for name in (self._config.username_env, self._config.password_env):
            if name is not None and name not in os.environ:
                raise ValueError(f"Missing MQTT credential environment variable: {name}")
            credentials.append(os.environ[name] if name is not None else None)
        username, password = credentials
        if username is not None:
            self._client.username_pw_set(username, password)
        elif password is not None:
            raise ValueError("MQTT password requires a username")
        self._store.clear()
        self._client.connect_async(self._config.host, self._config.port)
        self._client.loop_start()
        self._started = True

    def stop(self) -> None:
        if self._started:
            self._client.disconnect()
            self._client.loop_stop()
            self._started = False
        self._store.clear()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self._store.clear()
        if reason_code != 0:
            logger.warning("MQTT connection rejected: %s", reason_code)
            return
        result, _ = client.subscribe([(topic, 0) for topic in self._store.topics])
        if result != 0:
            logger.error("MQTT subscription request failed: %s", result)
        else:
            logger.info("Subscribed to %d thermometer topics", len(self._store.topics))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._store.clear()
        logger.info("MQTT disconnected: %s; measurements invalidated", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            stored = self._store.receive(message.topic, message.payload, retained=message.retain)
        except MeasurementError as exc:
            logger.warning("Ignoring invalid measurement on %s: %s", message.topic, exc)
            return
        if stored and self._on_temperature is not None:
            room_id = self._rooms_by_topic[message.topic]
            measurement = self._store.get(room_id)
            if measurement is not None:
                # Hooks run on the network thread: keep them short (e.g. enqueue).
                try:
                    self._on_temperature(room_id, measurement.temperature_c)
                except Exception:
                    logger.exception("Temperature notification failed for %s", room_id)
