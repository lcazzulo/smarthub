"""Thread-safe room measurements, with change-based temperature freshness."""

from dataclasses import dataclass
import json
import math
from threading import Lock
from time import monotonic
from typing import Callable

from .config import Configuration


class MeasurementError(ValueError):
    """A sensor payload cannot be used as a temperature reading."""


@dataclass(frozen=True)
class TemperatureMeasurement:
    temperature_c: float
    last_received_at: float
    last_changed_at: float
    generation: int = 0

    def is_stale(self, now: float, max_age_seconds: float) -> bool:
        """Age uses a local monotonic clock, not wall time or message frequency."""
        return now - self.last_changed_at > max_age_seconds


class MeasurementStore:
    """MQTT writes and independently scheduled control reads immutable snapshots.

    The first non-retained value initializes the change timestamp. Identical
    values update receipt time only. This deliberately treats constant readings
    as stale, even when the sensor is working. It cannot prove measurement age.
    """

    def __init__(self, config: Configuration, clock: Callable[[], float] = monotonic):
        base = config.general.zigbee2mqtt.base_topic
        self.topics = tuple(room.thermometer.state_topic(base) for room in config.rooms)
        self._rooms_by_topic = dict(zip(self.topics, (room.id for room in config.rooms)))
        self._measurements: dict[str, TemperatureMeasurement] = {}
        self._max_age = config.general.control.measurement_max_age_seconds
        self._clock = clock
        self._lock = Lock()
        self._generation = 0

    def receive(self, topic: str, payload: bytes | str, retained: bool = False) -> bool:
        """Return whether a valid reading was stored; ignore unrelated/retained data.

        Missing temperature (e.g. humidity-only payload) is ignored. Malformed
        readings raise MeasurementError without refreshing either timestamp.
        """
        room_id = self._rooms_by_topic.get(topic)
        if room_id is None or retained:
            return False
        try:
            document = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise MeasurementError("Invalid sensor JSON") from exc
        if not isinstance(document, dict):
            raise MeasurementError("Sensor payload must be a JSON object")
        if "temperature" not in document:
            return False
        value = document["temperature"]
        if type(value) not in (int, float):
            raise MeasurementError("Temperature must be a finite numeric Celsius value")
        try:
            value = float(value)
        except OverflowError as exc:
            raise MeasurementError("Temperature is outside numeric range") from exc
        if not math.isfinite(value):
            raise MeasurementError("Temperature must be finite")
        if document.get("temperature_units", "celsius") != "celsius":
            raise MeasurementError("Temperature units must be celsius")
        with self._lock:
            now = self._clock()
            previous = self._measurements.get(room_id)
            changed_at = now if previous is None or previous.temperature_c != value else previous.last_changed_at
            self._measurements[room_id] = TemperatureMeasurement(value, now, changed_at, self._generation)
        return True

    def get(self, room_id: str) -> TemperatureMeasurement | None:
        with self._lock:
            return self._measurements.get(room_id)

    def fresh_temperature(self, room_id: str) -> float | None:
        """Return None for missing or stale data; no PI calculation is triggered."""
        with self._lock:
            measurement = self._measurements.get(room_id)
            if measurement is None or measurement.is_stale(self._clock(), self._max_age):
                return None
            return measurement.temperature_c

    def clear(self) -> None:
        """Invalidate readings across a transport disconnect/reconnect."""
        with self._lock:
            self._measurements.clear()
            self._generation += 1
