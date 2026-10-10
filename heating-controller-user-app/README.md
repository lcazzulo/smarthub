# Heating controller user app

A room-focused web application for inspecting and controlling the heating system.
Home Assistant remains available for everyday control; this app adds detailed
history and explanations of controller decisions.

This directory is a sibling of `heating-controller/` in the `smarthub` repository.
The application is currently in the design stage: only the directory structure
and this document exist. No application service has been deployed.

## Directory structure

```text
heating-controller-user-app/
├── backend/       # Flask API, WebSocket connections and MQTT integration
├── ui/            # React + TypeScript SPA, built with Vite
└── README.md
```

Backend dependencies and frontend dependencies will be managed separately.
Use npm for the UI, commit its package-lock.json, and use npm ci in image builds.
A Dockerfile and service configuration will be added during implementation.

## Room view

Display one room at a time, selected through four tabs: Bathroom, Bedroom,
Kitchen and Living room. Preserve the selected time range when switching rooms.
The page should explain what is happening now, why, and how it changed over time.

### Current state

A compact panel shows:

- External measured temperature, prominently displayed.
- Room setpoint, adjustable with minus/plus buttons and numeric entry.
- Requested opening percentage.
- Text status: idle, heating requested, outside supply, outside heating schedule,
  waiting for temperature, stale temperature, disabled or fault.
- Time since the latest temperature receipt.

Keep Live / Dry-run mode and connection state visible. Controls should show
pending changes until the controller reports the accepted target. A lost
connection must not leave old values looking current.

Reuse the HA colours alongside explicit labels: red for actuator faults, purple
for missing/stale measurements, blue-grey outside supply hours, blue outside
heating hours, amber for active dry-run, orange for live heating demand and grey
for disabled/idle states. Fault indications take priority.

### History

Use vertically aligned charts with a shared time axis and synchronized cursor:

| Chart | Series |
| --- | --- |
| Temperature | External temperature and room setpoint |
| Valve control | Requested opening, commanded opening and valve-reported opening setting |
| PI calculation | Proportional and integral contributions |

Shade periods outside supply or desired heating hours. Mark target changes,
faults and controller restarts. Display gaps for missing data; do not draw
continuous operation through outages or across incompatible runs.

Provide 1h, 6h, 24h, 7d and custom ranges, plus a Follow live toggle. Incoming
WebSocket events must not move the time range while the user explores history.
Use aggregation for longer ranges while preserving meaningful transitions and
extremes. Show timestamps in Europe/Rome with unambiguous dates/offsets around
 daylight-saving transitions; exchange timestamps with the API in UTC.

Requested demand, sent commands and reported settings are separate series.
Reported opening is a device setting, not a measurement of physical valve
position, flow or delivered heat. Likewise, heating requested does not establish
that the shared boiler or hot-water supply is operating.

### Event timeline

An expandable timeline connects target changes, command attempts,
acknowledgements, faults and controller lifecycle events. For example:

```text
19:08:22  Target changed: 21 → 26°C
19:08:39  External-sensor setup sent
19:08:49  Setup acknowledged; opening 14% requested
19:08:59  Opening acknowledged
19:11:29  Closure acknowledged
```

These times illustrate a sequence, not live state. Expand an event to inspect
its payload, attempts and associated report. Preserve the distinction between
sent, confirmed, superseded and failed commands.

### Diagnostics

Keep diagnostics collapsed by default. Include sensor selection, last forwarded
temperature, valve setpoint, smart temperature control, actuator phase, pending
command, faults and recording health. Timestamp reported device values so stale
reports are identifiable.

## Architecture

```text
Browser: React SPA
    │ HTTPS / WSS
Host Nginx: existing certificates and TLS termination
    ├── /       → compiled React assets served by the app
    ├── /api/   → Flask HTTP API
    └── /ws     → backend WebSocket endpoint
                     ├── MQTT ↔ heating controller
                     └── read-only SQLite history queries
```

The paths above are the intended API layout; the WebSocket library and endpoint
contract remain to be selected. Flask communicates with the controller through
MQTT. React does not connect directly to the broker or the database.

### Live data and commands

Use WebSockets for live updates rather than browser polling. The backend
subscribes to controller MQTT telemetry and forwards relevant updates to clients.
Send an initial snapshot on connection/reconnection, explicitly mark stale or
unavailable values, and recover historical gaps through the HTTP API.

Setpoint changes pass through Flask to the existing controller topic:

```text
heating-controller/<room_id>/target_temperature/set
```

Commands are non-retained JSON numbers. Reported accepted targets come from:

```text
heating-controller/<room_id>/target_temperature
```

Room telemetry is published on `heating-controller/<room_id>/state`. These topics
use the deployment's configured control base topic; do not hardcode a different
namespace. Existing telemetry does not expose every diagnostic or command event.
Additional controller event publications may be needed for the full live timeline.
Do not treat the current telemetry payload as a complete live API.

The heating controller owns PI calculations, schedules, validation and valve
commands. The web backend must not command Zigbee2MQTT valves directly or write
targets into the history database. Heating continues if the web app is offline.
Current MQTT target changes are runtime-only; they revert to controller YAML
values on restart. Persistent target editing, schedule editing and PI tuning
require separate design and controller support.

Keep MQTT subscriptions and WebSocket connection management separate from HTTP
handlers. Choose an explicit worker/subscriber architecture to avoid duplicate
subscriptions or inconsistent client state when deploying multiple workers.
Authentication and authorization must cover both API and WebSocket access before
exposing control outside a trusted environment. HTTPS alone is not access control.

### Historical data

The existing database is on the SSD at:

```text
/srv/data/heating_controller/heating.sqlite3
```

It records runs, temperature readings, control samples, actuator commands,
command attempts, valve reports and events. See the controller's
[recording documentation](../heating-controller/docs/recording.md) for schema,
correlation and retention details.

Use read-only SQLite connections, bounded queries and short transactions. The
controller remains the database writer. SQLite uses WAL; the app must have access
to the database and relevant WAL/SHM sidecars on the same host. Validate read-only
mount behaviour during deployment; do not use SQLite immutable mode against the
actively written database or copy only its main file for live queries.

Historical interpretation should use each run's recorded configuration, including
schedules and dry-run mode. Do not apply today's settings retroactively. Preserve
PI sample semantics and distinguish controller sample time from report time.

Humidity and outside temperature are not currently recorded by the controller.
Historical views for those measurements require additional data collection.

## Deployment plan

Run the user app as a separate Docker Compose service alongside the controller.
Use a Node build stage to compile Vite assets, then copy them into the Python app
image. Node and the Vite development server are not production services.

Use a production Flask server compatible with the chosen WebSocket library.
Publish its port on 127.0.0.1 only; select an available port during implementation
(5055 is a candidate, not reserved). Host Nginx will proxy HTTP and WebSocket
upgrades with appropriate timeouts. Use the existing Compose network to reach
mosquitto:1883. Do not change or restart the controller when deploying UI changes.

During development, Vite can proxy API and WebSocket traffic to Flask. Keep local
configuration, credentials, runtime data, node_modules and build output out of Git.
No installation or run commands are provided yet because neither app is scaffolded.

## Implementation milestones

1. Scaffold Flask and React/TypeScript with Vite; define API and event contracts,
   connection lifecycle and deployment boundaries.
2. Build room tabs, current state, temperature/opening charts and the expandable
   event timeline. Connect recorded history and live MQTT updates through the backend.
3. Add setpoint controls with validation and accepted-state feedback; verify
   reconnect behaviour and unavailable states using simulated MQTT transports.
4. Add the PI chart, richer diagnostics and history export.
5. Add a consistency report for multi-day observations: unexpected opening above
   target, stale measurements, unconfirmed commands, faults and recording gaps.

Tests must use simulated transports and isolated database fixtures; they must not
actuate real valves. Validate timeline correlations, timezones and daylight-saving
boundaries, reconnection, command rejection, and historical/live data consistency.
