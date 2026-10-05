"""Pure PI calculation with independent state; no scheduling or MQTT."""

from dataclasses import dataclass
import math

from .config import Configuration, PIConfig


def _finite(value: float, name: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


@dataclass(frozen=True)
class PIResult:
    error_c: float
    proportional_percent: float
    integral_percent: float
    opening_percent: float
    saturated: bool


class PIController:
    """One instance per room; elapsed time is supplied by the caller.

    Conditional integration prevents further windup at output limits. Integration
    is allowed when it moves existing saturation toward the permitted range.
    The room lifecycle must reset this controller when control is suspended.
    """

    def __init__(self, parameters: PIConfig, target_temperature_c: float):
        for name in ("kp", "ki", "integral_min", "integral_max"):
            _finite(getattr(parameters, name), name)
        if parameters.kp < 0 or parameters.ki < 0:
            raise ValueError("PI gains must be nonnegative")
        if not 0 <= parameters.integral_min <= parameters.integral_max <= 100:
            raise ValueError("Integral bounds must satisfy 0 <= min <= max <= 100")
        self._parameters = parameters
        self.target_temperature_c = target_temperature_c
        self.reset()

    @property
    def target_temperature_c(self) -> float:
        return self._target_temperature_c

    @target_temperature_c.setter
    def target_temperature_c(self, value: float) -> None:
        _finite(value, "target_temperature_c")
        self._target_temperature_c = float(value)

    @property
    def integral_percent(self) -> float:
        return self._integral_percent

    def reset(self) -> None:
        """Clear stored integration; the next active step reapplies its bounds."""
        self._integral_percent = 0.0

    def step(self, measured_temperature_c: float, elapsed_seconds: float) -> PIResult:
        _finite(measured_temperature_c, "measured_temperature_c")
        _finite(elapsed_seconds, "elapsed_seconds")
        if elapsed_seconds <= 0:
            raise ValueError("elapsed_seconds must be positive")
        return self._calculate(measured_temperature_c, elapsed_seconds)

    def output(self, measured_temperature_c: float) -> PIResult:
        """Evaluate without advancing integration, e.g. when becoming active."""
        _finite(measured_temperature_c, "measured_temperature_c")
        return self._calculate(measured_temperature_c, 0.0)

    def _calculate(self, measured_temperature_c: float, elapsed_seconds: float) -> PIResult:
        parameters = self._parameters
        error = self.target_temperature_c - measured_temperature_c
        proportional = parameters.kp * error
        delta = parameters.ki * error * elapsed_seconds
        for value in (error, proportional, delta):
            _finite(value, "PI calculation")
        previous = min(parameters.integral_max, max(parameters.integral_min, self._integral_percent))
        candidate = min(parameters.integral_max, max(parameters.integral_min, previous + delta))
        total = proportional + candidate
        _finite(total, "PI output")
        # Block positive windup at full opening. The nonnegative integral floor
        # prevents negative windup; always allow stored heating demand to unwind.
        if total > 100 and candidate > previous:
            candidate = previous
        total = proportional + candidate
        self._integral_percent = candidate
        return PIResult(error, proportional, candidate, min(100.0, max(0.0, total)), total < 0 or total > 100)


def create_room_controllers(config: Configuration) -> dict[str, PIController]:
    """Instantiate one independent controller for each configured room.

    Disabled rooms also receive an instance; the lifecycle controls whether it
    runs. Construction performs no calculation or device interaction.
    """
    return {
        room.id: PIController(room.pi, room.target_temperature_c)
        for room in config.rooms
    }
