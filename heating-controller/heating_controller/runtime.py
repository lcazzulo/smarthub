"""Wire PI/lifecycle outputs to the actuator on a single caller-owned loop."""

from datetime import datetime
import logging

from .actuation import ActuatorCoordinator
from .actuator_mqtt import ActuatorMQTT
from .config import Configuration
from .measurements import MeasurementStore
from .room import ControlLoop
from .recording import Reference
from .schedule import heating_requested, supply_available


class HeatingRuntime:
    def __init__(self, config: Configuration, measurements: MeasurementStore,
                 transport: ActuatorMQTT, recorder=None):
        self.config = config
        self.recorder = recorder
        self._measurements = measurements
        self._last_recorded_status = {}
        self.control = ControlLoop(config, measurements)
        self.actuators = ActuatorCoordinator(config, recorder=recorder)
        self.transport = transport
        transport.enable_targets()
        self.last_outputs = ()
        self._shutdown: ActuatorCoordinator | None = None

    def tick(self, now: float, wall_time: datetime):
        if self._shutdown is not None:
            raise RuntimeError("Normal control cannot resume after shutdown has begun")
        # Hold the callback lock through snapshot/evaluation/send so a report
        # received before publication cannot masquerade as its confirmation.
        with self.transport.control_session() as snapshot:
            for room_id, target in self.transport.take_target_updates().items():
                old_target = self.control.rooms[room_id].pi.target_temperature_c
                if self.recorder and target != old_target:
                    self.recorder.event("target_change", "Room target changed", room_id=room_id,
                                        source="mqtt", now=now, previous=old_target, target=target)
                    self.recorder.event("integration_reset", "Target changed", room_id=room_id, now=now)
                self.actuators.set_target(room_id, target)
                self.control.rooms[room_id].set_target(target)
                logging.getLogger(__name__).info("%s target temperature set to %.1f", room_id, target)
            self.transport.publish_targets({room_id: room.pi.target_temperature_c
                                            for room_id, room in self.control.rooms.items()})
            previous_integrals = {room_id: room.pi.integral_percent for room_id, room in self.control.rooms.items()}
            outputs = self.control.tick(now, wall_time)
            if not outputs:
                return ()
            self.last_outputs = outputs
            self.actuators.sample_keys = self._record_samples(outputs, now, wall_time)
            commands = self.actuators.step(outputs, now, snapshot, self.transport.send)
            # A latched actuator fault should not accumulate PI demand.
            for room_id, state in self.actuators.progress.items():
                if state.fault or state.phase != "active":
                    self.control.rooms[room_id].reset()
            if self.recorder:
                for output in outputs:
                    state = self.actuators.progress[output.room_id]
                    self.recorder.update("control_samples", self.actuators.sample_keys.get(output.room_id),
                                         actuator_phase=state.phase, actuator_fault=state.fault)
                    signature = (output.status, state.phase, state.fault)
                    if signature != self._last_recorded_status.get(output.room_id):
                        self.recorder.event("room_transition", "Room control/actuator state changed",
                                            room_id=output.room_id, now=now,
                                            severity="error" if state.fault else "info",
                                            control_status=output.status, actuator_phase=state.phase,
                                            actuator_fault=state.fault)
                        self._last_recorded_status[output.room_id] = signature
                    if previous_integrals[output.room_id] != 0 and self.control.rooms[output.room_id].pi.integral_percent == 0:
                        self.recorder.event("integration_zeroed", "Integral returned to zero",
                                            room_id=output.room_id, now=now, control_status=output.status,
                                            actuator_phase=state.phase)
            self.transport.publish_room_states(outputs, self.actuators.progress, now)
            return commands

    def _record_samples(self, outputs, now, wall_time):
        if self.recorder is None:
            return {}
        result = {}
        for output in outputs:
            measurement = self._measurements.get(output.room_id)
            pi = output.pi
            result[output.room_id] = self.recorder.record(
                "control_samples", now=now, room_id=output.room_id,
                temperature_reading_id=Reference("temperature_readings", measurement.recording_key if measurement else None),
                temperature_c=output.temperature_c, target_temperature_c=output.target_temperature_c,
                measurement_age_seconds=output.measurement_age_seconds,
                supply_available=supply_available(self.config.general, wall_time),
                heating_requested=heating_requested(self.config.general, wall_time),
                control_status=output.status, proportional_percent=pi.proportional_percent if pi else None,
                integral_percent=pi.integral_percent if pi else None,
                requested_opening_percent=output.opening_percent, saturated=pi.saturated if pi else None)
        return result

    def finish_recording(self):
        """Finalize unresolved command outcomes once transport has stopped."""
        if self.recorder:
            self.actuators.cancel_pending("Application stopped before confirmation", outcome="failed")
            if self._shutdown:
                self._shutdown.cancel_pending("Shutdown ended without confirmation", outcome="failed")

    def shutdown_tick(self, now: float) -> bool:
        """Request closure only; return whether every valve has reported it.

        A fresh coordinator permits a final bounded closure attempt even after
        an operational fault. The caller must also impose a wall-clock deadline.
        Dry-run completes simulated closure without claiming device confirmation.
        """
        if self._shutdown is None:
            self.actuators.cancel_pending("Shutdown started")
            if self.recorder:
                self.recorder.event("shutdown", "Shutdown closure requested", now=now)
            self._shutdown = ActuatorCoordinator(self.config, recorder=self.recorder)
            for room in self.control.rooms.values():
                room.reset()
        with self.transport.control_session() as snapshot:
            self._shutdown.step((), now, snapshot, self.transport.send)
            return (snapshot.connected and not snapshot.unavailable
                    and all(state.phase == "closed" and state.pending is None
                            for state in self._shutdown.progress.values()))
