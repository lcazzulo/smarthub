"""Shared thermometer/valve MQTT transport with a guarded publishing boundary."""

import json
import logging
from contextlib import contextmanager
from threading import RLock
from time import monotonic
from typing import Callable

from .actuation import ReportField, SendOutcome, SendResult, TransportState, ValveReport
from .adapters.trvzb import TRVZBAdapter, ValveCommand, number
from .config import Configuration
from .discovery import RoomDiscovery
from .measurements import MeasurementStore
from .mqtt import TemperatureSubscriber


logger = logging.getLogger(__name__)


class ActuatorMQTT(TemperatureSubscriber):
    """Explicit actuation transport; existing preview subscribers remain read-only.

    QoS 0 prevents application-level offline replay; failed writes are retried by
    the coordinator using current demand. A publish return is not a device ack.
    """

    def __init__(self, config: Configuration, store: MeasurementStore, *, client=None,
                 clock: Callable[[], float] = monotonic):
        super().__init__(config, store, client=client)
        base = config.general.zigbee2mqtt.base_topic
        self.recorder = None
        self._valves = {room.valve.state_topic(base): room.id for room in config.rooms}
        self._command_topics = {room.id: room.valve.command_topic(base) for room in config.rooms}
        self._availability = {f"{topic}/availability": room for topic, room in self._valves.items()}
        self._target_topics = {
            f"{config.general.mqtt.control_base_topic}/{room.id}/target_temperature/set": room.id
            for room in config.rooms
        }
        self._targets = {room.id: room.target_temperature_c for room in config.rooms}
        self._target_updates: dict[str, float] = {}
        self._target_dirty: set[str] = set()
        self._target_enabled = False
        self._target_margin = config.general.actuator.trv_setpoint_margin_c
        self._dry_run = config.general.dry_run
        self._clock = clock
        self._lock = RLock()
        self._connected = False
        self._generation = 0
        self._reports: dict[str, ValveReport] = {}
        self._unavailable: set[str] = set()
        self._client.on_connect_fail = self._on_connect_fail
        self._discovery = RoomDiscovery(config, self._client) if config.general.home_assistant.enabled else None

    def enable_targets(self) -> None:
        """Enable the application API before starting the shared connection."""
        self._target_enabled = True

    def take_target_updates(self) -> dict[str, float]:
        with self._lock:
            updates = self._target_updates
            self._target_updates = {}
            return updates

    def publish_targets(self, targets: dict[str, float]) -> None:
        with self._lock:
            for room_id, target in targets.items():
                if self._targets[room_id] != target:
                    self._targets[room_id] = target
                    self._target_dirty.add(room_id)
            if not self._connected:
                return
            for room_id in tuple(self._target_dirty):
                topic = f"{self._config.control_base_topic}/{room_id}/target_temperature"
                try:
                    result = self._client.publish(topic, json.dumps(self._targets[room_id]),
                                                  qos=0, retain=True)
                    if result.rc == 0:
                        self._target_dirty.remove(room_id)
                except (ValueError, OSError):
                    logger.exception("Target state publication failed for %s", room_id)

    def publish_room_states(self, outputs, progress, now) -> None:
        with self._lock:
            if self._connected and self._discovery:
                self._discovery.update(outputs, progress, now, recording_status=self.recorder.status if self.recorder else None)

    def _on_connect_fail(self, client, userdata):
        logger.warning("MQTT connection failed; retrying %s:%s", self._config.host, self._config.port)

    @contextmanager
    def control_session(self):
        """Serialize report sampling and command issue with network callbacks."""
        with self._lock:
            yield self.snapshot()

    def snapshot(self) -> TransportState:
        with self._lock:
            return TransportState(self._connected, self._generation,
                                  {room: ValveReport(dict(report.fields), report.sequence, report.recording_key)
                                   for room, report in self._reports.items()},
                                  frozenset(self._unavailable))

    def send(self, command: ValveCommand, generation: int) -> SendResult:
        with self._lock:
            if command.topic != self._command_topics.get(command.room_id):
                raise ValueError("Command topic does not match configured valve")
            if (not self._connected or generation != self._generation
                    or command.room_id in self._unavailable):
                return SendOutcome("rejected", error_message="Disconnected, generation changed, or valve unavailable")
            payload = json.dumps(command.payload, allow_nan=False)
            if self._dry_run:
                logger.info("DRY RUN %s %s: %s", command.reason, command.topic, payload)
                return SendOutcome("dry_run")
            try:
                result = self._client.publish(command.topic, payload, qos=0, retain=False)
            except (ValueError, OSError) as exc:
                logger.exception("Valve command publication failed")
                return SendOutcome("rejected", error_message=str(exc))
            if result.rc != 0:
                logger.warning("Valve command rejected: rc=%s", result.rc)
                return SendOutcome("rejected", mqtt_return_code=int(result.rc), error_message="MQTT client rejected publication")
            logger.info("Sent %s to %s: %s", command.reason, command.room_id, payload)
            return SendOutcome("sent", mqtt_return_code=int(result.rc))

    def _invalidate(self):
        self._connected = False
        if self._discovery:
            self._discovery.reset()
        self._generation += 1
        self._target_updates.clear()
        self._reports.clear()
        self._unavailable.clear()
        self._store.clear()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        with self._lock:
            self._invalidate()
            if reason_code != 0:
                logger.warning("MQTT connection rejected: %s", reason_code)
                return
            topics = (*self._store.topics, *self._valves, *self._availability)
            if self._target_enabled:
                topics += tuple(self._target_topics)
            result, _ = client.subscribe([(topic, 0) for topic in topics])
            self._connected = result == 0
            if self.recorder:
                self.recorder.event("mqtt_connect", "MQTT connection/subscription result", source="mqtt",
                                    connected=self._connected, connection_generation=self._generation)
            if not self._connected:
                logger.error("MQTT subscription request failed: %s", result)
            else:
                logger.info("MQTT connected; requested temperature and valve subscriptions")
                if self._target_enabled:
                    self._target_dirty.update(self._targets)
                    self.publish_targets({})

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        with self._lock:
            self._invalidate()
        if self.recorder:
            self.recorder.event("mqtt_disconnect", "MQTT disconnected; inputs invalidated", source="mqtt",
                                reason=str(reason_code), connection_generation=self._generation)
        logger.info("MQTT disconnected; valve reports and measurements invalidated")

    def _on_message(self, client, userdata, message):
        with self._lock:
            if self._target_enabled and message.topic in self._target_topics:
                if message.retain or not self._connected:
                    logger.warning("Ignoring retained or disconnected target command on %s", message.topic)
                    return
                try:
                    target = number(json.loads(message.payload), 4, 35, "room target")
                    number(target + self._target_margin, 4, 35, "target plus TRV margin")
                except (ValueError, UnicodeError, OverflowError) as exc:
                    logger.warning("Ignoring invalid target on %s: %s", message.topic, exc)
                    return
                # Bounded mailbox: latest valid command wins for each selected room.
                self._target_updates[self._target_topics[message.topic]] = target
                return
            if message.topic in self._availability:
                try:
                    value = json.loads(message.payload)
                    status = value.get("state") if isinstance(value, dict) else value
                except (ValueError, UnicodeError):
                    status = message.payload.decode(errors="replace") if isinstance(message.payload, bytes) else message.payload
                room_id = self._availability[message.topic]
                if self.recorder and status in ("online", "offline"):
                    self.recorder.event("valve_availability", str(status), room_id=room_id, source="mqtt")
                if status == "offline":
                    if room_id not in self._unavailable:
                        # Invalidate commands planned before the offline event,
                        # including an offline/online cycle between control ticks.
                        self._generation += 1
                    self._unavailable.add(room_id)
                    self._reports.pop(room_id, None)
                elif status == "online":
                    self._unavailable.discard(room_id)
                return
            room_id = self._valves.get(message.topic)
            if room_id is None:
                super()._on_message(client, userdata, message)
                return
            if message.retain or room_id in self._unavailable:
                self._record_report(message, room_id, accepted=False,
                                    rejection_reason="retained" if message.retain else "valve_unavailable")
                return
            try:
                values = TRVZBAdapter.parse_report(message.payload)
            except ValueError as exc:
                self._record_report(message, room_id, accepted=False, rejection_reason=str(exc))
                logger.warning("Ignoring valve report for %s: %s", room_id, exc)
                return
            previous = self._reports.get(room_id, ValveReport())
            sequence = previous.sequence + 1
            fields = dict(previous.fields)
            fields.update({key: ReportField(value, sequence, self._clock()) for key, value in values.items()})
            recording_key = self._record_report(message, room_id, accepted=True, values=values, sequence=sequence)
            self._reports[room_id] = ValveReport(fields, sequence, recording_key)
            if values:
                logger.info("%s valve report: %s", room_id, values)

    def _record_report(self, message, room_id, *, accepted, rejection_reason=None, values=None, sequence=None):
        if self.recorder is None:
            return None
        raw = message.payload.decode(errors="replace") if isinstance(message.payload, bytes) else message.payload
        values = values or {}
        try:
            document = json.loads(raw)
            running_state = document.get("running_state") if isinstance(document, dict) else None
            if running_state not in ("idle", "heat"):
                running_state = None
        except (ValueError, TypeError):
            running_state = None
        return self.recorder.record(
            "valve_reports", now=self._clock(), room_id=room_id, topic=message.topic, payload_json=raw,
            retained=bool(message.retain), accepted=accepted, rejection_reason=rejection_reason,
            connection_generation=self._generation, report_sequence=sequence,
            opening_setting_percent=values.get("valve_opening_degree"),
            closing_setting_percent=values.get("valve_closing_degree"),
            system_mode=values.get("system_mode"), running_state=running_state,
            external_temperature_c=values.get("external_temperature_input"),
            setpoint_c=values.get("occupied_heating_setpoint"))

    def stop(self) -> None:
        with self._lock:
            if self._connected and self._discovery:
                self._discovery.offline()
            self._invalidate()
        super().stop()
