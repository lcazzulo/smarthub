"""One MQTT connection per app process; commands use only the controller API."""
from threading import Event, Thread

from paho.mqtt.client import CallbackAPIVersion, Client

from .config import number


class ControllerUnavailable(Exception):
    pass


class MQTTBridge:
    def __init__(self, settings, live, client=None):
        self.settings, self.live = settings, live
        self.client = client if client is not None else Client(CallbackAPIVersion.VERSION2, clean_session=True)
        self.client.on_connect = self._connect
        self.client.on_disconnect = lambda *args: self.live.connection(False)
        self.client.on_message = lambda client, userdata, message: self.live.receive(
            message.topic, message.payload, message.retain)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.stopped = Event()
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        if self.settings.username is not None:
            self.client.username_pw_set(self.settings.username, self.settings.password)
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port)
        self.client.loop_start()
        self.thread = Thread(target=self._watchdog, daemon=True, name='heating-state-expiry')
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.client.disconnect()
        self.client.loop_stop()
        if self.thread:
            self.thread.join(timeout=2)
        self.live.connection(False)

    def _watchdog(self):
        while not self.stopped.wait(1):
            self.live.expire()

    def _connect(self, client, userdata, flags, reason, properties):
        self.live.connection(False)
        if reason != 0:
            return
        topics = [(f'{self.settings.base_topic}/availability', 1)]
        for room in self.settings.rooms:
            topics.extend([(f'{self.settings.base_topic}/{room.id}/state', 0),
                           (f'{self.settings.base_topic}/{room.id}/target_temperature', 1)])
        result, _ = client.subscribe(topics)
        if result == 0:
            self.live.connection(True)

    def set_target(self, room_id, value):
        value = number(value, 'temperature_c', 4, self.settings.max_target)
        with self.live.lock:
            room = self.live.rooms[room_id]
            if not room.enabled:
                raise ValueError('Room is disabled')
            if not self.live.snapshot(room_id)['available']:
                raise ControllerUnavailable('Controller has no fresh online state for this room')
            try:
                # QoS 0 avoids storing an outgoing command for a later reconnect.
                result = self.client.publish(f'{self.settings.base_topic}/{room_id}/target_temperature/set',
                                             str(value), qos=0, retain=False)
            except (OSError, ValueError) as exc:
                raise ControllerUnavailable('MQTT publication failed') from exc
            if result.rc != 0:
                raise ControllerUnavailable('MQTT publication rejected')
        return value
