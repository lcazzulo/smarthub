"""Home Assistant room discovery and telemetry, independent of valve commands."""

import hashlib
import json
import logging
import math

from .config import Configuration

logger = logging.getLogger(__name__)

# key, display name, unit, device class, diagnostic
SENSORS = (
    ("temperature_c", "Temperature", "°C", "temperature", False),
    ("target_temperature_c", "Target temperature", "°C", "temperature", False),
    ("opening_percent", "Requested opening", "%", None, False),
    ("status", "Control status", None, None, False),
    ("measurement_age_seconds", "Temperature message age", "s", "duration", True),
    ("proportional_percent", "P contribution", "%", None, True),
    ("integral_percent", "I contribution", "%", None, True),
    ("actuator_status", "Actuator status", None, None, True),
    ("fault", "Actuator fault", None, None, True),
)


class RoomDiscovery:
    def __init__(self, config: Configuration, client):
        self.config = config
        self.client = client
        self.base = config.general.mqtt.control_base_topic
        self.settings = config.general.home_assistant
        self.availability_topic = f"{self.base}/availability"
        self.pending = True
        self.last_sent = {}
        self.last_status = {}
        self.expiry = math.ceil(max(90, config.general.control.period_seconds * 3))
        client.will_set(self.availability_topic, "offline", qos=1, retain=True)

    def reset(self):
        self.pending = True
        self.last_sent.clear()
        self.last_status.clear()

    def publish(self, topic, payload, *, retain=False):
        try:
            result = self.client.publish(topic, payload, qos=1 if retain else 0, retain=retain)
            if result.rc == 0:
                return True
            logger.warning("Home Assistant publication rejected on %s: rc=%s", topic, result.rc)
        except (ValueError, OSError):
            logger.exception("Home Assistant publication failed on %s", topic)
        return False

    def configurations(self):
        namespace = hashlib.sha256(self.base.encode()).hexdigest()[:16]
        for room in self.config.rooms:
            device_id = f"heating_controller_{namespace}_{room.id}"
            common = {
                "state_topic": f"{self.base}/{room.id}/state",
                "availability_topic": self.availability_topic,
                "expire_after": self.expiry,
                "device": {"identifiers": [device_id], "name": f"{room.name} heating controller",
                           "manufacturer": "Heating controller", "model": "Room PI controller"},
            }
            sensors = SENSORS
            if self.config.general.recording.enabled:
                sensors += (("recording_status", "Recording status", None, None, True),
                            ("recording_fault", "Recording fault", None, None, True),
                            ("recording_dropped_records", "Dropped recording operations", None, None, True))
            for key, name, unit, device_class, diagnostic in sensors:
                payload = dict(common, name=name, unique_id=f"{device_id}_{key}",
                               value_template="{{ value_json." + key + " }}")
                if unit:
                    payload.update(unit_of_measurement=unit, state_class="measurement")
                if device_class:
                    payload["device_class"] = device_class
                if diagnostic:
                    payload["entity_category"] = "diagnostic"
                yield f"{self.settings.discovery_prefix}/sensor/{device_id}/{key}/config", payload
            yield f"{self.settings.discovery_prefix}/binary_sensor/{device_id}/dry_run/config", dict(
                common, name="Dry run", unique_id=f"{device_id}_dry_run", entity_category="diagnostic",
                value_template="{{ 'ON' if value_json.dry_run else 'OFF' }}")

    def update(self, outputs, progress, now, recording_status=None):
        if self.pending:
            successful = True
            for topic, payload in self.configurations():
                successful = self.publish(topic, json.dumps(payload), retain=True) and successful
            self.pending = not successful
        sent = False
        for output in outputs:
            state = progress[output.room_id]
            signature = (output.status, state.phase, state.fault, output.target_temperature_c,
                         tuple(sorted((recording_status or {}).items())))
            if (signature == self.last_status.get(output.room_id)
                    and now - self.last_sent.get(output.room_id, -math.inf) < 30):
                continue
            pi = output.pi
            payload = {
                "temperature_c": output.temperature_c,
                "target_temperature_c": output.target_temperature_c,
                "opening_percent": output.opening_percent,
                "status": output.status,
                "measurement_age_seconds": output.measurement_age_seconds,
                "proportional_percent": pi.proportional_percent if pi else None,
                "integral_percent": pi.integral_percent if pi else None,
                "actuator_status": state.phase,
                "fault": state.fault or "none",
                "dry_run": self.config.general.dry_run,
            }
            payload.update(recording_status or {})
            if self.publish(f"{self.base}/{output.room_id}/state", json.dumps(payload, allow_nan=False)):
                self.last_sent[output.room_id] = now
                self.last_status[output.room_id] = signature
                sent = True
        if sent:
            self.publish(self.availability_topic, "online", retain=True)

    def offline(self):
        self.publish(self.availability_topic, "offline", retain=True)
