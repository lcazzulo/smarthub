"""Thread-safe MQTT cache and bounded per-browser event queues. No database polling."""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
from queue import Full, Queue
from threading import RLock
import time
from uuid import uuid4

from .config import number

logger = logging.getLogger(__name__)


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def decode(payload):
    def invalid(value):
        raise ValueError('Nonfinite JSON value')
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Nonfinite JSON value')
        return result
    return json.loads(payload, parse_constant=invalid, parse_float=finite_float)


@dataclass(eq=False)
class Subscription:
    room_id: str | None = None
    queue: Queue = field(default_factory=lambda: Queue(maxsize=64))
    overflow: bool = False


class LiveState:
    def __init__(self, settings, *, clock=time.monotonic, wall_clock=time.time):
        self.settings = settings
        self.rooms = {r.id: r for r in settings.rooms}
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = RLock()
        self.stream_id = str(uuid4())
        self.sequence = 0
        self.connected = False
        self.online = False
        self.states = {}
        self.targets = {}
        self.subscriptions = set()
        self._freshness = {}

    def _message(self, kind, data, room_id=None):
        self.sequence += 1
        return {'version': 1, 'type': kind, 'stream_id': self.stream_id,
                'sequence': self.sequence, 'sent_at': timestamp(self.wall_clock()),
                'room_id': room_id, 'data': deepcopy(data)}

    @staticmethod
    def _enqueue(sub, message):
        try:
            sub.queue.put_nowait(message)
        except Full:
            sub.overflow = True  # Disconnect; client must resubscribe and recover history.

    def _broadcast(self, kind, data, room_id=None):
        message = self._message(kind, data, room_id)
        for sub in self.subscriptions:
            if room_id is None or sub.room_id == room_id:
                self._enqueue(sub, message)

    def system(self):
        with self.lock:
            return {'mqtt_connected': self.connected, 'controller_online': self.connected and self.online}

    def snapshot(self, room_id):
        with self.lock:
            if room_id not in self.rooms:
                raise KeyError(room_id)
            item = self.states.get(room_id)
            age = max(0, self.clock() - item['monotonic']) if item else None
            stale = age is None or age > self.settings.stale_seconds
            available = self.connected and self.online and not stale and item['current_connection']
            return {'room_id': room_id, 'available': bool(available), 'stale': not bool(available),
                    'received_at': item['received_at'] if item else None,
                    'telemetry_age_seconds': age,
                    'telemetry': deepcopy(item['payload']) if item else None,
                    'reported_target': deepcopy(self.targets.get(room_id))}

    def add_subscription(self):
        with self.lock:
            if len(self.subscriptions) >= 16:
                raise RuntimeError('Too many WebSocket connections')
            sub = Subscription()
            self.subscriptions.add(sub)
            self._enqueue(sub, self._message('system.state', self.system()))
            return sub

    def subscribe(self, sub, room_id):
        with self.lock:
            snapshot = self.snapshot(room_id)
            while not sub.queue.empty():
                sub.queue.get_nowait()
            sub.room_id = room_id
            self._enqueue(sub, self._message('system.state', self.system()))
            self._enqueue(sub, self._message('room.state', snapshot, room_id))

    def remove_subscription(self, sub):
        with self.lock:
            self.subscriptions.discard(sub)

    def error(self, sub, message):
        with self.lock:
            self._enqueue(sub, self._message('error', {'message': message}))

    def connection(self, connected):
        with self.lock:
            self.connected = connected
            self.online = False
            self.targets.clear()
            for item in self.states.values():
                item['current_connection'] = False
            self._broadcast('system.state', self.system())
            self._push_all_states()

    def _push_all_states(self):
        for room_id in self.rooms:
            snapshot = self.snapshot(room_id)
            self._freshness[room_id] = snapshot['available']
            self._broadcast('room.state', snapshot, room_id)

    def expire(self):
        """Clock-only watchdog: push availability transitions without querying data."""
        with self.lock:
            for room_id in self.rooms:
                snapshot = self.snapshot(room_id)
                if self._freshness.get(room_id) != snapshot['available']:
                    self._freshness[room_id] = snapshot['available']
                    self._broadcast('room.state', snapshot, room_id)

    def receive(self, topic, payload, retained=False):
        if len(payload) > 65536:
            return
        with self.lock:
            if not self.connected:
                return
            try:
                if topic == f'{self.settings.base_topic}/availability':
                    value = payload.decode() if isinstance(payload, bytes) else payload
                    if value not in ('online', 'offline'):
                        return
                    changed = self.online != (value == 'online')
                    self.online = value == 'online'
                    if not self.online:
                        for item in self.states.values():
                            item['current_connection'] = False
                    if changed:
                        self._broadcast('system.state', self.system())
                        self._push_all_states()
                    return
                for room_id in self.rooms:
                    base = f'{self.settings.base_topic}/{room_id}'
                    if topic == f'{base}/target_temperature':
                        value = number(decode(payload), 'Reported target', 4, 35)
                        target = {'temperature_c': value, 'received_at': timestamp(self.wall_clock()),
                                  'retained': retained}
                        self.targets[room_id] = target
                        self._broadcast('room.target', target, room_id)
                        return
                    if topic == f'{base}/state' and not retained:
                        value = self._validate_state(decode(payload))
                        self.states[room_id] = {'payload': value, 'monotonic': self.clock(),
                                               'received_at': timestamp(self.wall_clock()),
                                               'current_connection': True}
                        snapshot = self.snapshot(room_id)
                        self._freshness[room_id] = snapshot['available']
                        self._broadcast('room.state', snapshot, room_id)
                        return
            except (ValueError, TypeError, UnicodeError, OverflowError):
                logger.warning('Ignoring malformed controller message on %s', topic)

    @staticmethod
    def _validate_state(value):
        if not isinstance(value, dict):
            raise ValueError('Expected object')
        for key in ('status', 'actuator_status', 'fault', 'hvac_action'):
            if not isinstance(value.get(key), str):
                raise ValueError(f'Invalid {key}')
        if type(value.get('dry_run')) is not bool:
            raise ValueError('Invalid dry_run')
        for key, minimum, maximum, optional in (
            ('temperature_c', -100, 150, True), ('target_temperature_c', 4, 35, False),
            ('opening_percent', 0, 100, False), ('measurement_age_seconds', 0, 1e12, True),
            ('proportional_percent', -1e12, 1e12, True), ('integral_percent', -1e12, 1e12, True),
        ):
            if optional and value.get(key) is None:
                continue
            number(value.get(key), key, minimum, maximum)
        # Preserve the controller's existing schema; never manufacture device reports.
        return value
