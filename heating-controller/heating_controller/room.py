"""Room lifecycle and timed calculations; no device command transport."""

from dataclasses import dataclass
from datetime import datetime
import math

from .config import Configuration, RoomConfig
from .measurements import MeasurementStore, TemperatureMeasurement
from .pi import PIController, PIResult
from .schedule import supply_available


@dataclass(frozen=True)
class RoomOutput:
    room_id: str
    status: str
    target_temperature_c: float
    temperature_c: float | None
    measurement_age_seconds: float | None
    opening_percent: float
    pi: PIResult | None


class RoomController:
    """Own a PI instance and reset it whenever room control is inhibited."""

    def __init__(self, room: RoomConfig, max_age_seconds: float):
        self.room = room
        self.pi = PIController(room.pi, room.target_temperature_c)
        self._max_age = max_age_seconds
        self._last_active_at: float | None = None
        self._generation: int | None = None

    def set_target(self, temperature_c: float) -> None:
        if temperature_c != self.pi.target_temperature_c:
            self.pi.target_temperature_c = temperature_c
            self.reset()

    def reset(self) -> None:
        """Suspend integration until the next active evaluation."""
        self.pi.reset()
        self._last_active_at = None
        self._generation = None

    def evaluate(self, measurement: TemperatureMeasurement | None, now: float,
                 available: bool) -> RoomOutput:
        temperature = measurement.temperature_c if measurement else None
        age = max(0.0, now - measurement.last_changed_at) if measurement else None
        if not self.room.enabled:
            status = "disabled"
        elif not available:
            status = "outside_supply"
        elif measurement is None:
            status = "waiting_for_temperature"
        elif measurement.is_stale(now, self._max_age):
            status = "stale_temperature"
        else:
            status = "active"
        if status != "active":
            self.reset()
            return RoomOutput(self.room.id, status, self.pi.target_temperature_c,
                              temperature, age, 0.0, None)
        # A disconnect/reconnect can occur entirely between two control ticks.
        if measurement.generation != self._generation:
            self.pi.reset()
            self._last_active_at = None
        self._generation = measurement.generation
        if self._last_active_at is None:
            result = self.pi.output(temperature)
        else:
            result = self.pi.step(temperature, now - self._last_active_at)
        self._last_active_at = now
        return RoomOutput(self.room.id, status, self.pi.target_temperature_c,
                          temperature, age, result.opening_percent, result)


class ControlLoop:
    """Caller polls tick; calculations run on monotonic control deadlines.

    Delayed polls perform one evaluation using elapsed time, without catch-up
    bursts. Wall time controls supply availability only, never integration.
    """

    def __init__(self, config: Configuration, measurements: MeasurementStore):
        self._config = config
        self._measurements = measurements
        self.rooms = {
            room.id: RoomController(room, config.general.control.measurement_max_age_seconds)
            for room in config.rooms
        }
        self._next_due: float | None = None
        self._last_poll: float | None = None

    def tick(self, now: float, wall_time: datetime) -> tuple[RoomOutput, ...]:
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("Control clock must be finite")
        if self._last_poll is not None and now < self._last_poll:
            raise ValueError("Control clock must be monotonic")
        if self._next_due is not None and now < self._next_due:
            self._last_poll = now
            return ()
        available = supply_available(self._config.general, wall_time)
        outputs = tuple(
            room.evaluate(self._measurements.get(room_id), now, available)
            for room_id, room in self.rooms.items()
        )
        self._last_poll = now
        self._next_due = now + self._config.general.control.period_seconds
        return outputs
