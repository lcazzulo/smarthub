"""Flask application factory. Importing this module does not connect to MQTT."""
import atexit
from dataclasses import asdict
import json
from queue import Empty
from uuid import uuid4

from flask import Flask, jsonify, request
from flask_sock import Sock
from simple_websocket import ConnectionClosed
from werkzeug.exceptions import HTTPException

from .config import Settings
from .history import History, HistoryUnavailable
from .live import LiveState, decode
from .mqtt import MQTTBridge, ControllerUnavailable


def create_app(settings=None, *, mqtt_client=None, start_mqtt=False, live=None):
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=4096,
                      SOCK_SERVER_OPTIONS={'ping_interval': 25, 'max_message_size': 4096})
    live = live or LiveState(settings)
    bridge = MQTTBridge(settings, live, client=mqtt_client)
    history = History(settings.database)
    app.extensions.update(heating_live=live, heating_mqtt=bridge, heating_history=history)

    @app.before_request
    def restrict_origin():
        # Browser cross-origin control is denied unless explicitly configured.
        # Authentication is delegated to the deployment's reverse proxy for now.
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') not in {request.host_url.rstrip('/'), *settings.allowed_origins}:
            return jsonify(error={'code': 'origin_denied', 'message': 'Origin is not allowed'}), 403

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error={'code': exc.name, 'message': exc.description}), exc.code

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error={'code': 'invalid_request', 'message': str(exc)}), 400

    @app.errorhandler(ControllerUnavailable)
    @app.errorhandler(HistoryUnavailable)
    def unavailable(exc):
        return jsonify(error={'code': 'unavailable', 'message': str(exc)}), 503

    def require_room(room_id):
        if room_id not in live.rooms:
            from flask import abort
            abort(404, description='Unknown room')

    @app.get('/api/v1/rooms')
    def rooms():
        return jsonify(rooms=[dict(asdict(r), target_min_c=4, target_max_c=settings.max_target,
                                   target_step_c=0.5) for r in settings.rooms])

    @app.get('/api/v1/rooms/<room_id>/state')
    def state(room_id):
        require_room(room_id)
        return jsonify(live.snapshot(room_id))

    @app.get('/api/v1/rooms/<room_id>/history')
    def room_history(room_id):
        require_room(room_id)
        return jsonify(history.query(room_id, request.args))

    @app.put('/api/v1/rooms/<room_id>/target')
    def target(room_id):
        require_room(room_id)
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {'temperature_c'}:
            raise ValueError('Expected only temperature_c')
        value = bridge.set_target(room_id, body['temperature_c'])
        return jsonify(room_id=room_id, request_id=str(uuid4()), temperature_c=value,
                       status='submitted', confirmed=False), 202

    @app.get('/api/v1/health')
    def health():
        return jsonify(live.system())

    sock = Sock(app)

    @sock.route('/ws')
    def websocket(ws):
        try:
            sub = live.add_subscription()
        except RuntimeError:
            ws.close(reason=1013, message='Connection limit reached')
            return
        try:
            while ws.connected:
                if sub.overflow:
                    ws.close(reason=1013, message='Slow consumer; reconnect and reload history')
                    break
                # This timeout multiplexes incoming socket commands and an in-memory
                # event queue; it does not poll MQTT, SQLite or HTTP for updates.
                raw = ws.receive(timeout=0.2)
                if raw is not None:
                    try:
                        message = decode(raw)
                        if not isinstance(message, dict) or set(message) != {'type', 'room_id'} or message['type'] != 'subscribe':
                            raise ValueError('Expected {"type":"subscribe","room_id":"..."}')
                        if not isinstance(message['room_id'], str):
                            raise ValueError('room_id must be a string')
                        live.subscribe(sub, message['room_id'])
                    except (ValueError, KeyError, TypeError, UnicodeError):
                        live.error(sub, 'Invalid subscription or unknown room')
                for _ in range(64):
                    try:
                        event = sub.queue.get_nowait()
                    except Empty:
                        break
                    ws.send(json.dumps(event, allow_nan=False))
        except ConnectionClosed:
            pass
        finally:
            live.remove_subscription(sub)

    if start_mqtt:
        bridge.start()
        atexit.register(bridge.stop)
    return app
