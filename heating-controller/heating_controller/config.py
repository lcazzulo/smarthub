"""Immutable configuration and strict YAML parsing; no device interaction."""

from dataclasses import dataclass
from datetime import time
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class ConfigError(ValueError):
    """Invalid configuration, with a field path where possible."""


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ConfigError("YAML mapping keys must be strings")
        if key in result:
            raise ConfigError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class MQTTConfig:
    host: str
    port: int
    username_env: str | None
    password_env: str | None
    control_base_topic: str = "heating-controller"


@dataclass(frozen=True)
class Zigbee2MQTTConfig:
    base_topic: str


@dataclass(frozen=True)
class SupplyInterval:
    start: time
    end: time


@dataclass(frozen=True)
class ControlConfig:
    period_seconds: float
    sensor_message_timeout_seconds: float
    command_min_interval_seconds: float
    opening_change_threshold_percent: float


@dataclass(frozen=True)
class PIConfig:
    kp: float
    ki: float
    integral_min: float
    integral_max: float


@dataclass(frozen=True)
class ActuatorConfig:
    external_sensor_mode: str
    trv_setpoint_margin_c: float
    temperature_min_interval_seconds: float = 60.0
    temperature_refresh_seconds: float = 300.0
    report_timeout_seconds: float = 60.0
    report_max_age_seconds: float = 900.0
    max_command_attempts: int = 3


@dataclass(frozen=True)
class DeviceConfig:
    model: str
    friendly_name: str

    def state_topic(self, base_topic: str) -> str:
        return f"{base_topic}/{self.friendly_name}"

    def command_topic(self, base_topic: str) -> str:
        return f"{self.state_topic(base_topic)}/set"


@dataclass(frozen=True)
class RoomConfig:
    id: str
    name: str
    enabled: bool
    target_temperature_c: float
    thermometer: DeviceConfig
    valve: DeviceConfig
    pi: PIConfig  # Effective parameters after applying room overrides.


@dataclass(frozen=True)
class HomeAssistantConfig:
    enabled: bool = False
    discovery_prefix: str = "homeassistant"


@dataclass(frozen=True)
class RecordingConfig:
    enabled: bool = False
    path: str = "recordings/heating.sqlite3"
    queue_capacity: int = 10000
    flush_interval_seconds: float = 1.0
    retention_days: int = 90


@dataclass(frozen=True)
class GeneralConfig:
    dry_run: bool
    timezone: ZoneInfo
    mqtt: MQTTConfig
    zigbee2mqtt: Zigbee2MQTTConfig
    supply_intervals: tuple[SupplyInterval, ...]
    control: ControlConfig
    pi_defaults: PIConfig
    actuator: ActuatorConfig
    heating_intervals: tuple[SupplyInterval, ...]
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    recording: RecordingConfig = RecordingConfig()


@dataclass(frozen=True)
class Configuration:
    version: int
    general: GeneralConfig
    rooms: tuple[RoomConfig, ...]


def _object(value, path, required, optional=()):
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected an object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ConfigError(f"{path}: missing fields {sorted(missing)}; unknown fields {sorted(unknown)}")
    return value


def _text(value, path, topic=False):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConfigError(f"{path}: expected a nonempty string without surrounding whitespace")
    if "\x00" in value or (topic and (any(c in value for c in "+#") or any(not p for p in value.split("/")))):
        raise ConfigError(f"{path}: invalid MQTT topic/name")
    return value


def _number(value, path, minimum=None, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ConfigError(f"{path}: expected a finite number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ConfigError(f"{path}: outside permitted range")
    return float(value)


def _positive(value, path):
    value = _number(value, path, 0)
    if value == 0:
        raise ConfigError(f"{path}: must be greater than zero")
    return value


def _bool(value, path):
    if type(value) is not bool:
        raise ConfigError(f"{path}: expected a boolean")
    return value


def _pi(value, path, defaults=None):
    fields = ("kp", "ki", "integral_min", "integral_max")
    value = _object(value, path, fields if defaults is None else (), fields if defaults else ())
    values = {k: _number(value[k], f"{path}.{k}", 0) if k in value else getattr(defaults, k) for k in fields}
    if not 0 <= values["integral_min"] <= values["integral_max"] <= 100:
        raise ConfigError(f"{path}: integral bounds must satisfy 0 <= min <= max <= 100")
    return PIConfig(**values)


def _device(value, path, model):
    value = _object(value, path, ("model", "friendly_name"))
    if value["model"] != model:
        raise ConfigError(f"{path}.model: expected {model}")
    return DeviceConfig(model, _text(value["friendly_name"], f"{path}.friendly_name", topic=True))


def _intervals(value, path="general.supply_intervals"):
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected an array")
    intervals, segments = [], []
    for i, entry in enumerate(value):
        item_path = f"{path}[{i}]"
        entry = _object(entry, item_path, ("start", "end"))
        times = []
        for key in ("start", "end"):
            text = entry[key]
            if not isinstance(text, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
                raise ConfigError(f"{item_path}.{key}: expected quoted HH:MM")
            times.append(time.fromisoformat(text))
        start, end = (t.hour * 60 + t.minute for t in times)
        if start == end:
            raise ConfigError(f"{item_path}: equal endpoints are ambiguous")
        intervals.append(SupplyInterval(*times))
        segments.extend([(start, end)] if start < end else [(start, 1440), (0, end)])
    segments.sort()
    if any(b[0] < a[1] for a, b in zip(segments, segments[1:])):
        raise ConfigError(f"{path}: intervals overlap")
    return tuple(intervals)


def _interval_minutes(interval: SupplyInterval) -> set[int]:
    start = interval.start.hour * 60 + interval.start.minute
    end = interval.end.hour * 60 + interval.end.minute
    if start < end:
        return set(range(start, end))
    return set(range(start, 1440)) | set(range(end))


def _validate_heating_intervals(supply, heating):
    # Minute resolution matches the required HH:MM configuration format.
    available = set().union(*(_interval_minutes(interval) for interval in supply))
    for index, interval in enumerate(heating):
        if not _interval_minutes(interval) <= available:
            raise ConfigError(f"general.heating_intervals[{index}]: must be fully covered by supply_intervals")


def parse_config(value) -> Configuration:
    """Validate a decoded document and resolve per-room PI overrides."""
    root = _object(value, "config", ("version", "general", "rooms"))
    if type(root["version"]) is not int or root["version"] != 1:
        raise ConfigError("version: only integer version 1 is supported")
    g = _object(root["general"], "general", ("timezone", "mqtt", "zigbee2mqtt", "supply_intervals", "control", "pi_defaults", "actuator"), ("dry_run", "heating_intervals", "home_assistant", "recording"))
    try:
        timezone = ZoneInfo(_text(g["timezone"], "general.timezone"))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("general.timezone: unknown or invalid timezone") from exc
    m = _object(g["mqtt"], "general.mqtt", ("host", "port"), ("username_env", "password_env", "control_base_topic"))
    if type(m["port"]) is not int or not 1 <= m["port"] <= 65535:
        raise ConfigError("general.mqtt.port: expected integer from 1 to 65535")
    credentials = []
    for key in ("username_env", "password_env"):
        item = m.get(key)
        if item is not None and (not isinstance(item, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item)):
            raise ConfigError(f"general.mqtt.{key}: expected an environment variable name or null")
        credentials.append(item)
    mqtt = MQTTConfig(_text(m["host"], "general.mqtt.host"), m["port"], *credentials,
                      _text(m.get("control_base_topic", "heating-controller"),
                            "general.mqtt.control_base_topic", topic=True))
    z = _object(g["zigbee2mqtt"], "general.zigbee2mqtt", ("base_topic",))
    zigbee = Zigbee2MQTTConfig(_text(z["base_topic"], "general.zigbee2mqtt.base_topic", topic=True))
    fields = ("period_seconds", "sensor_message_timeout_seconds", "command_min_interval_seconds", "opening_change_threshold_percent")
    c = _object(g["control"], "general.control", fields)
    control = ControlConfig(*[_positive(c[k], f"general.control.{k}") for k in fields[:3]], _number(c[fields[3]], f"general.control.{fields[3]}", 0, 100))
    pi = _pi(g["pi_defaults"], "general.pi_defaults")
    timing_fields = ("temperature_min_interval_seconds", "temperature_refresh_seconds",
                     "report_timeout_seconds", "report_max_age_seconds")
    a = _object(g["actuator"], "general.actuator", ("external_sensor_mode", "trv_setpoint_margin_c"),
                (*timing_fields, "max_command_attempts"))
    if a["external_sensor_mode"] not in ("external", "external_2", "external_3", "remote_temperature"):
        raise ConfigError("general.actuator.external_sensor_mode: invalid external sensor mode")
    timings = {key: _positive(a.get(key, getattr(ActuatorConfig, key)), f"general.actuator.{key}")
               for key in timing_fields}
    if timings["temperature_refresh_seconds"] < timings["temperature_min_interval_seconds"]:
        raise ConfigError("general.actuator: temperature refresh must be >= minimum interval")
    attempts = a.get("max_command_attempts", 3)
    if type(attempts) is not int or attempts < 1:
        raise ConfigError("general.actuator.max_command_attempts: expected a positive integer")
    actuator = ActuatorConfig(a["external_sensor_mode"],
                             _positive(a["trv_setpoint_margin_c"], "general.actuator.trv_setpoint_margin_c"),
                             **timings, max_command_attempts=attempts)
    supply_intervals = _intervals(g["supply_intervals"])
    heating_intervals = _intervals(g.get("heating_intervals", g["supply_intervals"]),
                                   "general.heating_intervals")
    _validate_heating_intervals(supply_intervals, heating_intervals)
    ha = _object(g.get("home_assistant", {}), "general.home_assistant", (),
                 ("enabled", "discovery_prefix"))
    home_assistant = HomeAssistantConfig(
        _bool(ha.get("enabled", False), "general.home_assistant.enabled"),
        _text(ha.get("discovery_prefix", "homeassistant"),
              "general.home_assistant.discovery_prefix", topic=True))
    r = _object(g.get("recording", {}), "general.recording", (),
                ("enabled", "path", "queue_capacity", "flush_interval_seconds", "retention_days"))
    integers = {}
    for key in ("queue_capacity", "retention_days"):
        value = r.get(key, getattr(RecordingConfig, key))
        if type(value) is not int or value <= 0:
            raise ConfigError(f"general.recording.{key}: expected a positive integer")
        integers[key] = value
    recording = RecordingConfig(
        enabled=_bool(r.get("enabled", False), "general.recording.enabled"),
        path=_text(r.get("path", RecordingConfig.path), "general.recording.path"),
        flush_interval_seconds=_positive(r.get("flush_interval_seconds", 1.0),
                                         "general.recording.flush_interval_seconds"), **integers)
    general = GeneralConfig(_bool(g.get("dry_run", True), "general.dry_run"), timezone, mqtt, zigbee, supply_intervals, control, pi, actuator, heating_intervals, home_assistant, recording)
    if not isinstance(root["rooms"], list) or not root["rooms"]:
        raise ConfigError("rooms: expected a nonempty array")
    rooms, ids, devices = [], set(), set()
    for i, value in enumerate(root["rooms"]):
        path = f"rooms[{i}]"
        r = _object(value, path, ("id", "name", "target_temperature_c", "thermometer", "valve"), ("enabled", "pi"))
        room_id = _text(r["id"], f"{path}.id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", room_id) or room_id in ids:
            raise ConfigError(f"{path}.id: expected a unique lowercase slug")
        ids.add(room_id)
        thermometer = _device(r["thermometer"], f"{path}.thermometer", "SNZB-02D")
        valve = _device(r["valve"], f"{path}.valve", "TRVZB")
        for device in (thermometer, valve):
            if device.friendly_name in devices:
                raise ConfigError(f"{path}: duplicate device assignment {device.friendly_name}")
            devices.add(device.friendly_name)
        rooms.append(RoomConfig(room_id, _text(r["name"], f"{path}.name"), _bool(r.get("enabled", True), f"{path}.enabled"), _number(r["target_temperature_c"], f"{path}.target_temperature_c"), thermometer, valve, _pi(r.get("pi", {}), f"{path}.pi", pi)))
    return Configuration(1, general, tuple(rooms))


def load_config(path: str | Path) -> Configuration:
    """Read YAML safely. Credentials are names only; no environment is read."""
    try:
        value = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_Loader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
    return parse_config(value)
