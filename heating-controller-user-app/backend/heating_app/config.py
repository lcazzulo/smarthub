"""Read only the user-app contract from an existing controller configuration."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re

import yaml


def number(value, name, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name}: expected a finite number from {minimum} to {maximum}")
    return float(value)


@dataclass(frozen=True)
class Room:
    id: str
    name: str
    enabled: bool


@dataclass(frozen=True)
class Settings:
    rooms: tuple[Room, ...]
    mqtt_host: str
    mqtt_port: int
    base_topic: str
    database: Path
    max_target: float
    stale_seconds: float = 90
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls):
        path = os.environ.get('HEATING_CONTROLLER_CONFIG')
        database = os.environ.get('HEATING_HISTORY_DB')
        if not path or not database:
            raise ValueError('HEATING_CONTROLLER_CONFIG and HEATING_HISTORY_DB are required')
        document = yaml.safe_load(Path(path).read_text())
        if not isinstance(document, dict) or document.get('version') != 1:
            raise ValueError('Expected controller configuration version 1')
        try:
            general = document['general']
            broker = general['mqtt']
            rooms = tuple(Room(r['id'], r['name'], r.get('enabled', True)) for r in document['rooms'])
            margin = number(general['actuator']['trv_setpoint_margin_c'], 'TRV margin', 0.000001, 31)
            period = number(general['control']['period_seconds'], 'Control period', 0.000001, 86400)
            base = broker.get('control_base_topic', 'heating-controller')
            host = os.environ.get('HEATING_MQTT_HOST', broker['host'])
            port = int(os.environ.get('HEATING_MQTT_PORT', broker['port']))
        except (KeyError, TypeError) as exc:
            raise ValueError('Incomplete controller configuration') from exc
        if not rooms or len({r.id for r in rooms}) != len(rooms):
            raise ValueError('Room IDs must be nonempty and unique')
        if any(not isinstance(r.id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', r.id)
               or not isinstance(r.name, str) or not r.name or type(r.enabled) is not bool for r in rooms):
            raise ValueError('Invalid room definition')
        if (not isinstance(base, str) or not base or any(c in base for c in '+#\0')
                or base.startswith('/') or base.endswith('/')):
            raise ValueError('Invalid control base topic')
        if not host or not 1 <= port <= 65535:
            raise ValueError('Invalid MQTT address')
        def credential(key):
            name = broker.get(key)
            if name is None:
                return None
            if name not in os.environ:
                raise ValueError(f'Missing credential variable: {name}')
            return os.environ[name]
        username, password = credential('username_env'), credential('password_env')
        if password is not None and username is None:
            raise ValueError('MQTT password requires a username')
        return cls(rooms, host, port, base, Path(database).resolve(), 35 - margin,
                   max(90, period * 3), username, password,
                   tuple(x.strip().rstrip('/') for x in os.environ.get('HEATING_ALLOWED_ORIGINS', '').split(',') if x.strip()))
