"""Per-room command sequencing independent of MQTT and PI calculations."""

from dataclasses import dataclass, field
import logging
import math
from typing import Callable, Literal

from .adapters.trvzb import TRVZBAdapter, ValveCommand
from .config import Configuration
from .room import RoomOutput


SendResult = Literal["sent", "dry_run", "rejected"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportField:
    value: object
    sequence: int
    received_at: float


@dataclass(frozen=True)
class ValveReport:
    fields: dict[str, ReportField] = field(default_factory=dict)
    sequence: int = 0

    def matches(self, values: dict[str, object], after: int = -1) -> bool:
        return all(key in self.fields and self.fields[key].sequence > after
                   and self.fields[key].value == value for key, value in values.items())


@dataclass(frozen=True)
class TransportState:
    connected: bool
    generation: int
    reports: dict[str, ValveReport]
    unavailable: frozenset[str] = frozenset()


@dataclass
class PendingCommand:
    command: ValveCommand
    attempted_at: float
    after_sequence: int
    attempts: int
    accepted: bool


@dataclass
class ValveProgress:
    phase: str = "unknown"
    desired_opening: float = 0.0
    last_sent: ValveCommand | None = None
    pending: PendingCommand | None = None
    fault: str | None = None
    last_opening: float = 0.0
    last_opening_at: float = -math.inf
    last_temperature: float | None = None
    last_temperature_at: float = -math.inf
    simulated: bool = False


class ActuatorCoordinator:
    """Call from one control thread with a fresh RoomOutput for every room.

    The caller must serialize report sampling and command issue (HeatingRuntime
    does this). Live progress requires matching post-command reports. Dry-run advances only
    a simulated plan; it never fabricates reported device state. A fault latches
    until a new transport generation and prevents further opening.
    """

    def __init__(self, config: Configuration):
        self.config = config
        self.adapters = {room.id: TRVZBAdapter(room, config.general.actuator,
                         config.general.zigbee2mqtt.base_topic) for room in config.rooms}
        self.progress = {room.id: ValveProgress() for room in config.rooms}
        self._generation: int | None = None
        self._last_now = -math.inf

    def step(self, outputs: tuple[RoomOutput, ...], now: float, transport: TransportState,
             send: Callable[[ValveCommand, int], SendResult]) -> tuple[ValveCommand, ...]:
        if not math.isfinite(now) or now < self._last_now:
            raise ValueError("Actuator clock must be finite and monotonic")
        self._last_now = now
        if transport.generation != self._generation:
            self.progress = {room_id: ValveProgress() for room_id in self.adapters}
            self._generation = transport.generation
        if not transport.connected:
            return ()
        by_room = {output.room_id: output for output in outputs}
        commands = []
        for room_id, adapter in self.adapters.items():
            if room_id in transport.unavailable:
                self.progress[room_id] = ValveProgress()
                continue
            state = self.progress[room_id]
            report = transport.reports.get(room_id, ValveReport())
            output = by_room.get(room_id)
            opening, temperature = 0.0, None
            if output is not None and output.status == "active":
                try:
                    # Reject inconsistent active outputs as well as out-of-range inputs.
                    if (output.measurement_age_seconds is None
                            or not 0 <= output.measurement_age_seconds <= self.config.general.control.measurement_max_age_seconds):
                        raise ValueError("Missing or stale measurement age")
                    temperature = adapter.temperature(output.temperature_c).payload["external_temperature_input"]
                    opening = adapter.opening(output.opening_percent).payload["valve_opening_degree"]
                except ValueError as exc:
                    state.fault = str(exc)
            state.desired_opening = opening
            if (opening > 0 and state.phase in ("prepared", "active") and not state.simulated
                    and (state.pending is None or state.pending.command.reason in ("temperature", "opening"))):
                if not self._healthy(state, adapter, report, now):
                    state.fault = "Valve state stale or changed unexpectedly"
            if state.fault:
                opening = 0

            pending = state.pending
            if opening == 0 and pending is not None and pending.command.reason != "close":
                state.pending = None  # A closure supersedes an in-flight opening or temperature.
                state.phase = "unknown"
                pending = None
            if pending is not None:
                if pending.accepted and report.matches(pending.command.payload, pending.after_sequence):
                    logger.info("%s: matching report for %s", room_id, pending.command.reason)
                    self._complete(state, pending.command)
                    state.pending = None
                    if (opening > 0 and state.phase in ("prepared", "active")
                            and not self._healthy(state, adapter, report, now)):
                        state.fault = "Valve state stale or changed unexpectedly"
                        opening = 0
                elif now - pending.attempted_at < self.config.general.actuator.report_timeout_seconds:
                    continue
                elif pending.attempts >= self.config.general.actuator.max_command_attempts:
                    state.fault = f"No matching report for {pending.command.reason}"
                    state.pending = None
                    if pending.command.reason == "close":
                        state.phase = "fault"
                        logger.error("%s: closure unconfirmed after %d attempts", room_id, pending.attempts)
                        continue
                    state.phase = "unknown"
                    opening = 0
                else:
                    # Recompute current values instead of replaying obsolete demand.
                    command = pending.command
                    if command.reason == "opening":
                        command = adapter.opening(opening)
                    elif command.reason == "temperature":
                        command = adapter.temperature(temperature)
                    elif command.reason == "prepare":
                        command = adapter.prepare(temperature)
                    self._issue(state, command, now, report, transport, send, commands,
                                attempts=pending.attempts + 1)
                    continue

            if state.phase == "fault":
                continue
            command = None
            if state.phase == "unknown" or (opening == 0 and state.phase != "closed"):
                command = adapter.close()
            elif opening == 0:
                # Reassert closure if a later report contradicts it.
                if not state.simulated and not report.matches(adapter.close().payload):
                    command = adapter.close()
            elif state.phase == "closed":
                command = (adapter.prepare(temperature) if state.simulated or report.matches(adapter.close().payload)
                           else adapter.close())
            elif state.phase == "prepared":
                command = adapter.opening(opening)
            elif state.phase == "active":
                elapsed = now - state.last_temperature_at
                settings = self.config.general.actuator
                if (elapsed >= settings.temperature_refresh_seconds
                        or (temperature != state.last_temperature and elapsed >= settings.temperature_min_interval_seconds)):
                    command = adapter.temperature(temperature)
                elif (opening != state.last_opening
                      and abs(opening - state.last_opening) >= self.config.general.control.opening_change_threshold_percent
                      and now - state.last_opening_at >= self.config.general.control.command_min_interval_seconds):
                    command = adapter.opening(opening)
            if command is not None:
                self._issue(state, command, now, report, transport, send, commands)
        return tuple(commands)

    def _healthy(self, state: ValveProgress, adapter: TRVZBAdapter,
                 report: ValveReport, now: float) -> bool:
        expected = {"temperature_sensor_select": adapter.sensor_mode,
                    "occupied_heating_setpoint": adapter.setpoint,
                    "smart_temperature_control": False, "valve_closing_degree": 100}
        if state.phase == "active":
            expected["system_mode"] = "heat"
            if state.pending is None:
                expected["valve_opening_degree"] = state.last_opening
        elif state.phase == "prepared":
            # Observed on the bathroom TRVZB: setting the elevated setpoint
            # switches mode to heat. Both endpoints must still request closure
            # before we issue an opening; off is not a setup invariant.
            mode = report.fields.get("system_mode")
            if mode is None or mode.value not in ("off", "heat"):
                logger.warning("%s: unexpected setup system_mode=%r", adapter.room_id,
                               mode.value if mode is not None else None)
                return False
            expected["system_mode"] = mode.value
            if state.pending is None:
                expected["valve_opening_degree"] = 0
        return report.matches(expected) and all(
            now - report.fields[key].received_at <= self.config.general.actuator.report_max_age_seconds
            for key in expected)

    def _issue(self, state: ValveProgress, command: ValveCommand, now: float,
               report: ValveReport, transport: TransportState,
               send: Callable[[ValveCommand, int], SendResult], commands: list[ValveCommand],
               attempts: int = 1) -> None:
        if command.reason == "close" and state.fault:
            logger.warning("%s: %s; requesting closure", command.room_id, state.fault)
        result = send(command, transport.generation)
        commands.append(command)
        state.pending = PendingCommand(command, now, report.sequence, attempts, result == "sent")
        if result in ("sent", "dry_run"):
            state.last_sent = command
            if command.reason in ("opening", "close"):
                state.last_opening_at = now
                state.last_opening = command.payload["valve_opening_degree"]
            if "external_temperature_input" in command.payload:
                state.last_temperature_at = now
                state.last_temperature = command.payload["external_temperature_input"]
        if result == "dry_run":
            state.simulated = True
            self._complete(state, command)
            state.pending = None

    @staticmethod
    def _complete(state: ValveProgress, command: ValveCommand) -> None:
        phase = {"close": "closed", "prepare": "prepared", "opening": "active"}
        state.phase = phase.get(command.reason, state.phase)
