"""Pure TRVZB translation; reported settings do not prove physical position."""

from dataclasses import dataclass
import json
import logging
import math

from ..config import ActuatorConfig, RoomConfig


logger = logging.getLogger(__name__)


def number(value: object, minimum: float, maximum: float, name: str) -> float:
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError(f"{name}: expected a finite number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}: expected {minimum} to {maximum}")
    return float(value)


@dataclass(frozen=True)
class ValveCommand:
    room_id: str
    topic: str
    values: tuple[tuple[str, object], ...]
    reason: str

    @property
    def payload(self) -> dict[str, object]:
        return dict(self.values)


class TRVZBAdapter:
    def __init__(self, room: RoomConfig, settings: ActuatorConfig, base_topic: str):
        self.room_id = room.id
        self.topic = room.valve.command_topic(base_topic)
        if settings.external_sensor_mode not in ("external", "remote_temperature"):
            raise ValueError("TRVZB actuation requires external or remote_temperature; "
                             "verify the installed Zigbee2MQTT exposes")
        self.sensor_mode = settings.external_sensor_mode
        self.setpoint = number(room.target_temperature_c + settings.trv_setpoint_margin_c,
                               4, 35, f"{room.id}: target plus TRV margin")

    def command(self, reason: str, **values: object) -> ValveCommand:
        return ValveCommand(self.room_id, self.topic, tuple(values.items()), reason)

    def close(self) -> ValveCommand:
        return self.command("close", valve_opening_degree=0, valve_closing_degree=100,
                            system_mode="off")

    def prepare(self, temperature_c: float) -> ValveCommand:
        return self.command("prepare", **self.temperature(temperature_c).payload,
                            temperature_sensor_select=self.sensor_mode,
                            occupied_heating_setpoint=self.setpoint,
                            smart_temperature_control=False)

    def opening(self, percent: float) -> ValveCommand:
        percent = int(number(percent, 0, 100, "opening percent") + 0.5)
        return self.close() if percent == 0 else self.command(
            "opening", valve_opening_degree=percent, system_mode="heat")

    def temperature(self, temperature_c: float) -> ValveCommand:
        value = round(number(temperature_c, 0, 99.9, "external temperature"), 1)
        return self.command("temperature", external_temperature_input=value)

    @staticmethod
    def parse_report(payload: bytes | str) -> dict[str, object]:
        """Validate fields independently; None invalidates an unusable field.

        Keeping explicit invalidation prevents an older valid value from being
        reused as confirmation when a newer report contains unknown state.
        """
        try:
            document = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid valve JSON") from exc
        if not isinstance(document, dict):
            raise ValueError("Valve report must be an object")
        values = {}
        limits = {"valve_opening_degree": (0, 100), "valve_closing_degree": (0, 100),
                  "occupied_heating_setpoint": (4, 35), "external_temperature_input": (0, 99.9)}
        enums = {"system_mode": ("off", "auto", "heat"),
                 "temperature_sensor_select": ("internal", "external", "external_2", "external_3",
                                               "local_temperature", "remote_temperature", "remote_source_offline")}
        for key, value in document.items():
            try:
                if key in limits:
                    values[key] = number(value, *limits[key], key)
                elif key in enums:
                    if value not in enums[key]:
                        raise ValueError(f"Invalid {key}")
                    values[key] = value
                elif key == "smart_temperature_control":
                    if type(value) is not bool:
                        raise ValueError("smart_temperature_control must be boolean")
                    values[key] = value
            except (ValueError, OverflowError):
                values[key] = None
                logger.warning("Invalid valve field %s=%r (%s); marking this field unknown",
                               key, value, type(value).__name__)
        return values
