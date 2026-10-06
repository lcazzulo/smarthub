"""Wire PI/lifecycle outputs to the actuator on a single caller-owned loop."""

from datetime import datetime
import logging

from .actuation import ActuatorCoordinator
from .actuator_mqtt import ActuatorMQTT
from .config import Configuration
from .measurements import MeasurementStore
from .room import ControlLoop


class HeatingRuntime:
    def __init__(self, config: Configuration, measurements: MeasurementStore,
                 transport: ActuatorMQTT):
        self.config = config
        self.control = ControlLoop(config, measurements)
        self.actuators = ActuatorCoordinator(config)
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
                self.actuators.set_target(room_id, target)
                self.control.rooms[room_id].set_target(target)
                logging.getLogger(__name__).info("%s target temperature set to %.1f", room_id, target)
            self.transport.publish_targets({room_id: room.pi.target_temperature_c
                                            for room_id, room in self.control.rooms.items()})
            outputs = self.control.tick(now, wall_time)
            if not outputs:
                return ()
            self.last_outputs = outputs
            commands = self.actuators.step(outputs, now, snapshot, self.transport.send)
            # A latched actuator fault should not accumulate PI demand.
            for room_id, state in self.actuators.progress.items():
                if state.fault or state.phase != "active":
                    self.control.rooms[room_id].reset()
            return commands

    def shutdown_tick(self, now: float) -> bool:
        """Request closure only; return whether every valve has reported it.

        A fresh coordinator permits a final bounded closure attempt even after
        an operational fault. The caller must also impose a wall-clock deadline.
        Dry-run completes simulated closure without claiming device confirmation.
        """
        if self._shutdown is None:
            self._shutdown = ActuatorCoordinator(self.config)
            for room in self.control.rooms.values():
                room.reset()
        with self.transport.control_session() as snapshot:
            self._shutdown.step((), now, snapshot, self.transport.send)
            return (snapshot.connected and not snapshot.unavailable
                    and all(state.phase == "closed" and state.pending is None
                            for state in self._shutdown.progress.values()))
