# Flask backend

Implements room listing, MQTT-backed current state, recorded history, native
WebSocket subscriptions and target control. The heating controller is unchanged.
There is no dependency on the controller Python package and no direct valve API.

## Installation and startup

From this `backend/` directory, create an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
```

Set configuration explicitly. These paths describe the existing host deployment:

```sh
export HEATING_CONTROLLER_CONFIG=/home/luca/smarthub/heating-controller/config.local.yaml
export HEATING_HISTORY_DB=/srv/data/heating_controller/heating.sqlite3
export HEATING_MQTT_HOST=localhost
.venv/bin/gunicorn -c gunicorn.conf.py heating_app.wsgi:app
```

The default bind is `127.0.0.1:5055`; check availability before starting or supply
`--bind 127.0.0.1:ANOTHER_PORT`. This implementation has not been deployed, and
these commands have not been used to start a persistent service.

Use exactly one worker, without `--preload` or a development reloader: that worker
owns one MQTT client, cache and WebSocket subscriber registry. The supplied
Gunicorn configuration has 24 threads and permits up to 16 WebSockets, leaving
capacity for HTTP requests. Scaling to multiple workers requires a shared event
bus/cache and is not supported yet. MQTT starts only through `heating_app.wsgi`;
`create_app()` is inert by default for tests and tooling.

Configuration reads room names/enabled state, broker credentials via their named
environment variables, control base topic, TRV margin and control period from the
existing controller YAML. The file is never modified. Set `HEATING_MQTT_HOST` and
optionally `HEATING_MQTT_PORT` to override only network addressing; in Docker use
the existing `mosquitto` service. Database path is explicit because container and
host paths differ. Room/config changes require a backend restart.

Current controller telemetry requires its HA discovery option enabled. Current
state is not reconstructed from SQLite or YAML targets: before live telemetry it
is unavailable. History still works while MQTT is offline.

Keep this service bound to loopback. Authentication is not implemented in this
increment; before exposing it, configure authentication for both HTTP and WebSocket
access at Nginx (or implement app authentication). Do not expose its port directly.
Browser origins must match the request origin or be explicitly listed in the
comma-separated `HEATING_ALLOWED_ORIGINS` variable. Configure the external HTTPS
origin there when using Nginx. Origin checking is not authentication. No wildcard
CORS is enabled; the intended frontend uses a same-origin Vite/Nginx proxy.

## HTTP API

All responses are JSON, with `Cache-Control: no-store`. Errors use
`{"error":{"code":"...","message":"..."}}`.

| Method / path | Response |
| --- | --- |
| GET `/api/v1/rooms` | `rooms`: configured IDs, names, enabled state and target limits |
| GET `/api/v1/rooms/<id>/state` | Current snapshot, received timestamp, age, availability and telemetry |
| GET `/api/v1/rooms/<id>/history` | Paginated recorded series and associated run metadata |
| PUT `/api/v1/rooms/<id>/target` | Submit a target; 202 means submitted, not applied |
| GET `/api/v1/health` | MQTT connection and controller online flag |

Unknown room: 404. Invalid arguments: 400. Unsupported request content type: 415.
Missing/unusable database, disconnected/stale controller or failed publication:
503. Health's online flag is transport-level; room availability additionally
requires fresh telemetry.

### Setpoint control

```http
PUT /api/v1/rooms/bedroom/target
Content-Type: application/json

{"temperature_c":20.0}
```

```json
{
  "room_id": "bedroom",
  "request_id": "generated-backend-request-id",
  "temperature_c": 20.0,
  "status": "submitted",
  "confirmed": false
}
```

The value must be numeric, finite, at least 4°C and no higher than 35°C minus the
configured TRV margin. Disabled rooms and rooms without fresh online telemetry
reject commands. UI step is 0.5°C but the existing controller also accepts other
finite values within its range. Commands go only to
`<base>/<room>/target_temperature/set`, with QoS 0, no retention and no backend
retry. A successful publish return is not device or controller acknowledgement.
A connection race can still lose a command; wait for the controller's report.

React should show the requested value as pending until a subsequent `room.target`
message reports the matching value; use a timeout/unknown result if none arrives.
The local request ID is not sent to or echoed by the controller. Simultaneous HA
and app commands cannot be unambiguously correlated with the existing protocol.
Retained target reports on reconnect are snapshots, not acknowledgement of a new
request. Do not automatically retry commands on reconnect. Targets remain
runtime-only and reset to controller YAML values when the controller restarts.

### Current state

A snapshot contains:

- `room_id`, `available`, `stale`.
- `received_at`: backend receipt time of the last non-retained room telemetry.
- `telemetry_age_seconds`: age of that MQTT message at snapshot creation.
- `telemetry`: the validated existing controller state, or null before receipt.
- `reported_target`: latest target-topic value, receipt timestamp and retained flag,
  or null before receipt.

Telemetry retains the controller's names: `temperature_c`, `target_temperature_c`,
`opening_percent`, `status`, `measurement_age_seconds`, `proportional_percent`,
`integral_percent`, `actuator_status`, `fault`, `dry_run`, `hvac_action`, and optional
recording diagnostics. `fault: "none"` means no reported actuator fault.

`measurement_age_seconds` is the sensor age at the controller's evaluation, not
at browser render time. For a live age label, add elapsed backend/browser receipt
time and label it approximate. Do not replace this value with telemetry age.
`reported_target` can be newer than the target inside throttled telemetry; display
it preferentially when present. Neither timestamp is an exact controller sample
time: the existing MQTT state has no observation timestamp or run ID.

Telemetry expires after max(90 seconds, three configured control periods).
A broker disconnect or controller offline message immediately invalidates cached
state. Reconnection requires fresh non-retained telemetry, even if retained
availability says online. Invalid messages cannot refresh freshness. Last values
are retained for display with `available: false`; they must not look current.

### History

Example query:

```text
/api/v1/rooms/bedroom/history?from=2026-10-10T00:00:00Z&to=2026-10-11T00:00:00Z&series=control&limit=1000
```

`from` is inclusive, `to` exclusive; both require ISO-8601 timezone information.
The maximum range is 31 days. Results are sorted by timestamp then database ID.
`limit` defaults to 1000, maximum 5000. If `next_cursor` is non-null, repeat the
same room/range/series query with `cursor=<next_cursor>` until it is null. Cursors
are opaque to clients and only valid for those unchanged query parameters.

Supported series:

| Series | Data |
| --- | --- |
| `control` (default) | Temperature, target, demand, P/I, schedules, status and actuator phase/fault |
| `temperature` | Individual accepted temperature arrivals and change flag |
| `commands` | Command intent/payload, outcome, completion time and failure reason |
| `reports` | Valve-reported opening/closing settings, mode, running state, external temperature and setpoint, with accepted/retained flags |

Commands are intents; inspect outcomes before labeling one as executed. An
unconfirmed command is not evidence of actuation. Reports include rejected or
retained observations, explicitly flagged; they must not be used as fresh
acknowledgements. Database boolean columns are returned as 0/1 (or null where
unknown); run `dry_run` and `break_before` are JSON booleans.

Each response has `samples`, `runs`, `next_cursor`, `resolution`, room and range.
Samples include stable database `id`, `run_id` and UTC `at`. Run metadata includes
live/dry-run mode, timezone, schedules, control period and recorded room PI settings;
it excludes broker configuration and credentials. Control samples mark
`break_before` at the beginning, across run boundaries or when the gap exceeds
three recorded control periods. Use those breaks when drawing continuous lines.
The controller records PI contributions before any subsequent actuator-induced
integrator reset; these samples must be interpreted together with actuator state.

This increment provides lossless `resolution=raw` pagination only. Other
resolutions are rejected; chart aggregation is deferred rather than silently
subsampling and losing extrema or transitions. All pages are separate snapshots;
command outcomes and run end times may update between requests. Query history
again after live activity to reconcile it. There is no replayable WebSocket log.

SQLite is opened `mode=ro` with `query_only`, a three-second query budget and
bounded output. Missing databases are not created. Schema version 1 is required.
Each request closes its connection. Mount the database directory with its WAL/SHM
sidecars; never use immutable mode against the active writer.

## Native WebSocket `/ws`

This is the browser's standard WebSocket protocol, not Socket.IO. After connecting,
receive `system.state`, then subscribe to one room:

```json
{"type":"subscribe","room_id":"bedroom"}
```

The backend sends a `system.state` snapshot followed by `room.state`. Subscribing
to another room replaces the selection and discards queued messages for the old
room. Messages already in transit may still arrive; React should ignore events
whose room ID is no longer selected.

Every message has this envelope:

```json
{
  "version": 1,
  "type": "room.state",
  "stream_id": "backend-process-id",
  "sequence": 42,
  "sent_at": "2026-10-10T17:09:19.000Z",
  "room_id": "bedroom",
  "data": {}
}
```

`data` for `room.state` has the same shape as GET state. `room.target` carries
`temperature_c`, `received_at`, `retained`. `system.state` carries
`mqtt_connected`, `controller_online` and has a null room ID. `error` contains a
message about an invalid subscription. No target commands are accepted over WS.

Events come directly from MQTT callbacks. A clock-only watchdog pushes staleness
transitions, and protocol ping/pong frames every 25 seconds detect broken clients.
There is no HTTP/database polling for live data. Each socket loop multiplexes
incoming subscriptions and its in-memory outbound queue with a 200ms receive
timeout. State messages are complete snapshots, not patches.

Sequence values increase within a process, but gaps are normal because rooms are
filtered. `stream_id` changes on backend restart. There is no resume-by-sequence
API: reconnect with backoff, resubscribe, then query missed history through HTTP.
A slow consumer whose 64-message queue fills is closed with code 1013, requiring
reconnect and resynchronization. At most 16 simultaneous sockets are accepted.

Live `room.event`, physical valve diagnostics and exact controller observation
timestamps are not implemented because they are absent from existing telemetry.
The history endpoint provides recorded commands and reports without changing the
controller. Room humidity history is available with `series=humidity`.
`GET /api/v1/outdoor/history` uses the same range and pagination parameters and
returns readings for the configured outdoor sensor. Older recording schemas return
empty samples for sensor series they do not support.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use an injected fake MQTT client and temporary WAL databases. The socket
test opens a real temporary localhost WebSocket server with that same fake MQTT
client. No tests connect to the real broker, modify its topics or actuate valves.

### Outdoor temperature

When `general.recording.outdoor_thermometer` is configured, the shared MQTT client
subscribes to that sensor under `general.zigbee2mqtt.base_topic`. Valid non-retained
Celsius readings update the optional `system.state.outdoor_temperature` object
(`temperature_c`, `received_at`) for all browser subscriptions. At startup the
backend restores the latest matching reading from `outdoor_temperature_readings`
when available. The timestamp remains the original receipt time across reconnects.
The UI shows the value and date/time above the room tabs.

### Daily heating demand

`GET /api/v1/rooms/<id>/demand?from=YYYY-MM-DD&to=YYYY-MM-DD` returns
`days` for 1–31 inclusive Europe/Rome calendar dates. Future dates are rejected.
Each day includes four `bands_seconds` (>0–25%, >25–50%, >50–75%, >75–100%),
`active_seconds`, duration-weighted `average_opening_percent`,
`equivalent_seconds` (opening fraction × time), `coverage_seconds`,
`simulated_seconds`, `unknown_seconds`, `elapsed_seconds`, `day_seconds`, and
`partial`. Zero demand with recording coverage differs from unknown data.

Aggregation holds each sample's requested opening until the next sample only
within the same run and within three configured control periods. Invalid values,
run boundaries, longer gaps and the unconfirmed final tail are not extrapolated.
Intervals are clipped to the requested dates, now, and recorded run end; boundary
samples outside the range allow exact midnight clipping. Dry-run intervals are
reported separately and excluded from demand and coverage. DST days retain their
actual 23/25-hour lengths. Reads use a consistent read-only transaction, a five
second deadline and a one-million-sample cap; larger queries fail explicitly.
This represents requested valve demand, not measured heating or energy use.
