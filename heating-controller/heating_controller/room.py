"""Room lifecycle and timed calculations; no device command transport."""

from dataclasses import dataclass
from datetime import datetime
import math

from .config import Configuration, RoomConfig
from .measurements import MeasurementStore, TemperatureMeasurement
from .pi import PIController, PIResult
from .schedule import heating_requested, supply_available


@dataclass(frozen=True)
class RoomOutput:
    room_id: str
    status: str
    target_temperature_c: float
    temperature_c: float | None
    measurement_age_seconds: float | None
    opening_percent: float
    pi: PIResult | None
    humidity_percent: float | None = None


class RoomController:
    """Own a PI instance and reset it whenever room control is inhibited."""

    def __init__(self, room: RoomConfig, max_age_seconds: float):
        self.room = room
        self.pi = PIController(room.pi, room.target_temperature_c)
        self._max_age = max_age_seconds
        self._last_active_at: float | None = None
        self._generation: int | None = None
        self._warmup_checked = False
        self._warmup_started_at: float | None = None

    def set_target(self, temperature_c: float) -> None:
        if temperature_c != self.pi.target_temperature_c:
            self.pi.target_temperature_c = temperature_c
            self.reset()

    def reset(self, *, preserve_warmup: bool = False) -> None:
        """Suspend PI and normally cancel boost, without rearming this period.

        Valve preparation may preserve boost and its original deadline.
        Keep the measurement generation so reconnects remain detectable.
        """
        self.pi.reset()
        self._last_active_at = None
        if not preserve_warmup:
            self._warmup_started_at = None

    def evaluate(self, measurement: TemperatureMeasurement | None, now: float,
                 available: bool, requested: bool = True) -> RoomOutput:
        temperature = measurement.temperature_c if measurement else None
        age = max(0.0, now - measurement.last_received_at) if measurement else None
        if not self.room.enabled:
            status = "disabled"
        elif not available:
            status = "outside_supply"
        elif not requested:
            status = "outside_heating_schedule"
        elif measurement is None:
            status = "waiting_for_temperature"
        elif measurement.is_stale(now, self._max_age):
            status = "stale_temperature"
        else:
            status = "active"
        if status != "active":
            self.reset()
            if status in ("outside_supply", "outside_heating_schedule"):
                self._warmup_checked = False
            return RoomOutput(self.room.id, status, self.pi.target_temperature_c,
                              temperature, age, 0.0, None)
        # A disconnect/reconnect can occur entirely between two control ticks.
        if measurement.generation != self._generation:
            self.reset()
        self._generation = measurement.generation
        warmup = self.room.warmup
        error = self.pi.target_temperature_c - temperature
        if not self._warmup_checked:
            self._warmup_checked = True
            if warmup is not None and error >= warmup.entry_error_c:
                self._warmup_started_at = now
        if self._warmup_started_at is not None:
            if (error > warmup.exit_error_c
                    and (warmup.max_duration_seconds is None
                         or now - self._warmup_started_at < warmup.max_duration_seconds)):
                return RoomOutput(self.room.id, "warming_up", self.pi.target_temperature_c,
                                  temperature, age, warmup.opening_percent, None)
            # Start PI without integrating the time spent under overridden demand.
            self.reset()
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
    bursts. Wall time controls both schedules, never integration.
    """

    def __init__(self, config: Configuration, measurements: MeasurementStore):
        self._config = config
        self._measurements = measurements
        self.rooms = {
            room.id: RoomController(room, config.general.control.sensor_message_timeout_seconds)
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
        requested = heating_requested(self._config.general, wall_time)
        outputs = tuple(
            room.evaluate(self._measurements.get(room_id), now, available, requested)
            for room_id, room in self.rooms.items()
        )
        self._last_poll = now
        self._next_due = now + self._config.general.control.period_seconds
        return outputs
