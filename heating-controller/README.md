# Heating controller

The implementation provides immutable configuration classes, a strict YAML
parser, independent pure PI controllers, and a shared subscription-only MQTT
transport with per-room temperature storage, and a timed room lifecycle.
An initial TRVZB adapter, actuator coordinator, and optional shared actuation
transport are implemented and tested with simulated devices. Manual bathroom
tests also exercised external sensing, opening changes, closure requests, and
reopening, with motor movement observed. Physical water-flow closure, fallback,
and thermal regulation remain unverified.

From this directory, install the package in a Python 3.11+ virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

Load configuration without connecting to devices:

```python
from heating_controller import load_config

config = load_config("config.example.yaml")
bedroom = config.rooms[0]
topic = bedroom.valve.command_topic(config.general.zigbee2mqtt.base_topic)
```

Run the standalone configuration-reading example from this directory:

```sh
python -m heating_controller.examples.read_config config.example.yaml
```

It validates the file and prints a short summary without connecting to MQTT.

The parser rejects unknown/missing fields, duplicate YAML keys, invalid numeric
values, duplicate room/device assignments, and overlapping supply intervals.
Intervals use quoted `HH:MM` strings, allow overnight ranges, and have inclusive
starts and exclusive ends. Empty intervals mean no supply. Scheduling converts
actual instants to the configured timezone: repeated local times use the same
availability, and nonexistent local times are skipped.

`general.heating_intervals` is the desired daily heating schedule, using the
same `{start: "HH:MM", end: "HH:MM"}` format as `supply_intervals`.
At startup, every heating interval must be fully covered by the supply schedule;
partial overlap or crossing a gap in supply is rejected. Overnight intervals and
coverage by adjacent supply intervals are supported. Overlaps within either
array and equal endpoints are rejected. Both schedules use `Europe/Rome` in the
example, with the same daylight-saving behavior.

Omitting `heating_intervals` uses all supply hours for compatibility. Set it to
`[]` to disable scheduled heating. The example repeats the existing supply hours;
shorten those intervals to select the desired heating times. Control runs only
inside both schedules. Outside desired hours, status is
`outside_heating_schedule`, opening intent is zero, and integration resets;
outside supply hours, status remains `outside_supply`. With actuation enabled,
the controller requests closure when either schedule ends.

Room `pi` mappings override individual general PI defaults. Omitted `dry_run`
defaults to `true`. Credential fields contain optional environment variable
names; parsing does not read credentials from the environment.

The example contains untuned PI placeholders and provisional actuator settings.
Successful parsing does not validate hardware behavior or authorize actuation.

Instantiate one PI controller per room:

```python
from heating_controller import load_config, create_room_controllers

config = load_config("config.example.yaml")
controllers = create_room_controllers(config)
bedroom = controllers["bedroom"]
# Example calculation only; supply real measurements and elapsed time at runtime.
result = bedroom.step(measured_temperature_c=18.0, elapsed_seconds=10.0)
print(result.opening_percent)
```

Each instance stores its own integral and resolves gains from its room settings.
Output is bounded to 0–100%; conditional integration prevents windup and permits
unwinding. `reset()` clears integration. The room lifecycle gates calculations on
room enablement, fresh measurements, and supply availability, and resets
integration when suspended. Calculation alone sends no commands.

## PI output preview

On the Docker host, run:

```sh
python -m heating_controller.examples.watch_pi config.example.yaml --mqtt-host localhost
```

Omit the host override when using the shared Docker network. Stop with Ctrl+C.
One MQTT connection updates measurements independently of control ticks. Every
`control.period_seconds` (currently 10 seconds), the example prints each room's
status, temperature, target, receipt age, requested opening, P/I contributions,
and saturation. Missing/stale data, disabled rooms, and unavailable supply produce
zero opening intent and reset integration. The example never publishes commands,
even if `dry_run` is false.

The first active evaluation calculates proportional demand without integrating
time spent waiting. Subsequent active evaluations integrate actual monotonic
elapsed time. Reconnects reset integration even if they happen between ticks.
Delayed ticks run once, without catch-up bursts. Gains remain untuned placeholders;
opening is calculated demand, not measured heat output or physical valve position.

### Record and plot a run

In the activated virtual environment, install the optional plotting dependency:

```sh
python -m pip install -e ".[plot]"
mkdir -p recordings
python -m heating_controller.examples.watch_pi config.example.yaml --mqtt-host localhost --csv recordings/run.csv
```

Stop with Ctrl+C, then generate a plot:

```sh
python -m heating_controller.examples.plot_pi recordings/run.csv --output recordings/run.png
```

The plot has one column per room, showing temperature/target, opening/P/I,
measurement receipt age with its stale threshold, and control status. PNG, SVG,
and PDF outputs work without a desktop. Use `--max-age-seconds VALUE` if your
configured threshold differs from 7200 (two hours); `--timezone` defaults to Europe/Rome.
CSV recording preserves numeric precision, writes every tick, and flushes rows
immediately. Choose a new CSV filename for each run; existing files are preserved.
`recordings/` is ignored by Git. Plots help inspect arithmetic and state changes;
without actuation, they do not validate thermal response or tuned gains.

## Temperature acquisition

Run the example to load configuration and print each incoming temperature for
both rooms (including repeated values):

```sh
python -m heating_controller.examples.watch_temperatures config.example.yaml
```

Outside Docker, supply the broker's reachable hostname or IP using
`--mqtt-host HOST`. If running on the Docker host with port 1883 published:

```sh
python -m heating_controller.examples.watch_temperatures config.example.yaml --mqtt-host localhost
```

Install updated dependencies with `python -m pip install -e .` in your virtual
environment first. Stop with Ctrl+C. Retained, missing-temperature, and invalid
messages are ignored. This example only subscribes and prints; it does not run
PI or send valve commands. A bounded queue keeps console output off the MQTT
network thread; queue overflow drops console events but preserves stored values.

Record temperature arrivals for a few days with:

```sh
mkdir -p recordings
python -m heating_controller.examples.watch_temperatures config.example.yaml --mqtt-host localhost --csv recordings/temperatures.csv
```

Keep the process running; stop with Ctrl+C. The CSV contains `received_at_utc`,
`room_id`, and `temperature_c`. Receipt times are captured in the MQTT callback
in UTC (`+00:00`, equivalent to GMT), including microseconds. Every valid arrival,
including unchanged temperatures, is recorded and flushed immediately. Existing
files are preserved: choose a new filename for each run. Recording stops with an
error if the queue overflows, rather than silently losing arrivals. Pending queue
entries may be lost at shutdown. This command only subscribes; it sends no commands.

Compare arrival gaps and gaps between temperature changes separately for each
room to inform the stale threshold. The current policy uses time since receipt.
A few days give an initial baseline, not a guarantee: unchanged or cached values
do not prove fresh sensor measurements, and collector downtime also creates gaps.
No stale threshold is changed automatically.

```python
from heating_controller import load_config
from heating_controller.measurements import MeasurementStore
from heating_controller.mqtt import TemperatureSubscriber

config = load_config("config.example.yaml")
measurements = MeasurementStore(config)
subscriber = TemperatureSubscriber(config, measurements)
subscriber.start()
try:
    # Inside a separately scheduled control loop:
    temperature = measurements.fresh_temperature("bedroom")
    # None means missing or stale: the lifecycle must suspend/reset PI.
finally:
    subscriber.stop()
```

`start()` is asynchronous; readings arrive later, so keep the application alive
while acquiring. The `mosquitto` hostname requires the shared Docker network.
The subscriber uses one Paho client, subscribes again after reconnect, ignores
retained values, and invalidates readings on disconnect. It never calls publish,
even if `dry_run` is false. No live transport test is part of the unit tests.

Each reading has local monotonic `last_received_at` and `last_changed_at` times
(seconds, useful for elapsed time only). The first valid non-retained temperature
initializes both; identical values update only receipt time. Changed values
also update the diagnostic change timestamp. Data becomes stale when time since
the last valid temperature receipt is strictly greater than
`sensor_message_timeout_seconds` (7200, or two hours, in the example).
Repeated values keep the sensor fresh. This indicates message delivery, not proof
of genuine measurement freshness: payloads may contain cached temperatures.
When migrating an existing configuration, replace `measurement_max_age_seconds`
with `sensor_message_timeout_seconds: 7200`; the old setting is no longer accepted.
PI CSV `measurement_age_seconds` now records receipt age; older CSVs recorded
change age and should be interpreted accordingly.
Humidity-only and malformed payloads do not refresh temperature timestamps.
Thread-safe immutable snapshots keep acquisition independent of PI evaluation.

## TRVZB actuation components

`adapters/trvzb.py` translates commands and validates valve reports without I/O.
`actuation.py` owns independent command progress for each room. `actuator_mqtt.py`
shares one MQTT client for thermometers and valves, and `runtime.py` connects
room lifecycle outputs to the coordinator. The existing temperature recorder
and PI preview continue to use the subscription-only transport.

See the [class diagram](docs/diagrams/class-relationships.png) and
[timing diagram](docs/diagrams/timing-sequence.png) for component relationships
and the startup command sequence.

The staged sequence is:

1. Request opening degree 0, closing degree 100, and system mode `off`.
2. Wait for matching, non-retained reports received after the command.
3. While closed, send the real room temperature, configured external sensor mode,
   room target plus margin, and `smart_temperature_control: false`.
4. After matching setup reports, set system mode `heat` and the PI opening request.

The observed bathroom valve reports mode `heat` when its setpoint is changed
during setup. Setup therefore accepts `off` or `heat`, while still requiring
opening degree 0 and closing degree 100 before issuing a nonzero opening.
Unknown/auto mode, stale reports, or changed endpoints prevent opening.

Normal opening changes use the configured interval and percentage threshold.
Zero demand and inhibited room statuses bypass those limits to request closure.
Closure also supersedes a pending adjustment. Temperature forwarding has a
separate minimum interval and periodic refresh, rounds to 0.1°C, and runs only
while the room is active with positive opening demand. The latest fresh reading
is used; stale or out-of-range readings are never forwarded.

Each command waits for matching reported settings. Timeouts retry the current
intent, bounded by `max_command_attempts` including the first attempt. Failure
latches a fault and prevents reopening; a failed non-closure command starts a
bounded closure attempt. Unexpected sensor mode/setpoint changes and stale valve
state also fault and request closure. Integration resets while actuation is
suspended or faulted. MQTT reconnect resets progress and starts from closure;
an offline valve is blocked until online. An offline notification invalidates
the shared command generation, conservatively reconciling all rooms. Faults
clear when that generation changes or the runtime is restarted.

Commands use QoS 0 and `retain=False`; the coordinator handles failed publications
instead of keeping an offline command backlog. `HeatingRuntime.tick()` serializes
measurement evaluation and publishing with callbacks. A broker accepting a
publication is distinct from a matching device report. Even a matching report
can contain Zigbee2MQTT cached fields and does not prove motor movement or closure.
Retained valve reports cannot confirm commands. No automatic `/get` polling is
implemented; a device that does not report required fields will block progress.

Preview the command sequence with a configuration containing valid actuator
settings:

```sh
python -m heating_controller.examples.watch_actuators YOUR_CONFIG.yaml --mqtt-host localhost
```

This example **always forces dry-run**, including when the file says otherwise.
It logs planned payloads and advances simulated acknowledgements separately from
real reports. It does not validate device responsiveness. The library transport
can publish when explicitly constructed with `dry_run: false`. The application
entry point below provides explicit live mode and a bounded shutdown closure
sequence. Stopping the transport directly does not send or guarantee closure.

Before using the adapter:

- The current example target 30.5°C plus margin 5°C exceeds the TRV setpoint
  maximum of 35°C. Actuator construction rejects it; acquisition and PI-only
  examples still accept it. Choose the intended target/margin before previewing.
- Select `external` or `remote_temperature` to match the installed Zigbee2MQTT
  exposes. Older `external_2`/`external_3` configuration values remain readable
  for existing acquisition setups but are rejected for actuation.
- Verify firmware/exposes support for opening/closing degrees and disabling
  `smart_temperature_control`. The adapter requires reported setup confirmation;
  it does not automatically detect firmware capabilities or change the enum.
- All new timing defaults in `config.example.yaml` are provisional. In particular,
  the temperature refresh interval is not a verified external-sensor timeout.

The implementation follows the [Zigbee2MQTT TRVZB documentation](https://www.zigbee2mqtt.io/devices/TRVZB.html).
Opening degree applies when the thermostat calls for heat. Artificial sensor
cooling/warming tests demonstrated the elevated-setpoint command sequence and
reopening after closure, but not its thermal behavior. `off` includes frost
protection. Physical closure, external-temperature fallback, and recovery after
communication failure remain unverified; an offline controller cannot guarantee
valve closure. Unit tests use simulated transports and never actuate devices.

## Run the application

From this directory, use `python -m heating_controller` with the virtual
environment activated. Installing again with `python -m pip install -e .` also
adds the equivalent `heating-controller` console command.

The CLI defaults to **dry-run even if YAML contains `dry_run: false`**. Pass
`--live` to publish device commands. `--room` selects the only room to control
and can be repeated; omit it to control all configured rooms. Other rooms are
not subscribed to or commanded by that application instance. The separate
temperature recorder can continue running.

Validate a bedroom test configuration without connecting:

```sh
python -m heating_controller config.example.yaml --room bedroom --trv-setpoint-margin 4.5 --check-config
```

The margin override makes this example's TRV setpoint exactly 35°C
(30.5 + 4.5). It applies to this process only and is a test setting, not a tuned
control parameter. No configuration file is rewritten. `--check-config` validates
local values; it does not verify the firmware or installed Zigbee2MQTT exposes.

Preview the complete application for five minutes:

```sh
python -m heating_controller config.example.yaml --mqtt-host localhost --room bedroom --trv-setpoint-margin 4.5 --run-seconds 300
```

To run the same test with real valve commands:

```sh
python -m heating_controller config.example.yaml --mqtt-host localhost --room bedroom --trv-setpoint-margin 4.5 --run-seconds 300 --live
```

Replace `bedroom` with `bathroom` if that is the valve you want to test. Use
`--external-sensor-mode remote_temperature` if that is the external source enum
exposed by your installed Zigbee2MQTT; otherwise the configured `external` value
is used. The setup must be reported back before the application permits opening.
There is no automatic firmware capability detection.

For a bathroom-only movement test with a room target of 26°C:

```sh
python -m heating_controller config.example.yaml --mqtt-host localhost --room bathroom --target-temperature 26 --run-seconds 300 --live
```

`--target-temperature` overrides the PI target for selected rooms only, without
rewriting YAML. With the configured 5°C margin, the valve's own setpoint is 31°C.
At a measured 25°C, the example gains initially request 10% opening; integration
can increase demand while the temperature stays below target. The actual room
sensor reading is still forwarded to the valve. Without heat, this tests demand
and valve commands rather than a thermal response.

Supply intervals still apply: the current example allows opening from 06:00–12:00
and 16:00–22:00 Europe/Rome. Fresh room measurements and positive PI demand are
also required. With no actual heating supply, this run can check MQTT exchange
and observable motor movement, but cannot validate thermal control or water-flow
closure. The application does not bypass the supply schedule for a movement test.

UTC logs show room status, temperature, demand, actuator phase, pending commands,
outgoing payloads, and matching reports. `outside_supply` or
`waiting_for_temperature` explains why a valve is kept closed. An actuator fault
stops normal control and begins shutdown.

Ctrl+C, SIGTERM, or the run-duration limit starts a closure-only shutdown for all
selected valves, with up to 30 seconds to receive matching reports. Change this
budget with `--shutdown-timeout SECONDS`. The normal bounded retry interval still
applies, so a 30-second shutdown budget permits only one attempt with the default
60-second report timeout. A second stop signal skips the remaining wait. The
application exits with status 1 on a control error or unconfirmed shutdown
closure; matching reports do not prove physical closure. A crash, forced kill,
or offline broker/device can leave valves open. Omitting `--run-seconds` keeps
normal control running until a stop signal or fault.

### Changing room targets over MQTT

The application exposes targets on the shared MQTT connection. Set
`general.mqtt.control_base_topic` to choose the namespace (default:
`heating-controller`). Only rooms selected for this application run are exposed.

| Topic | Payload | Purpose |
| --- | --- | --- |
| `heating-controller/<room_id>/target_temperature/set` | JSON number, e.g. `22.5` | Non-retained target command |
| `heating-controller/<room_id>/target_temperature` | JSON number | Retained current application target |

For the bathroom test using the local broker:

```bash
mosquitto_pub -h localhost -t heating-controller/bathroom/target_temperature/set -m '26'
mosquitto_sub -h localhost -t heating-controller/+/target_temperature -v
```

Do not use `-r` for commands. Retained commands received on subscription are
ignored. Targets must be finite JSON numbers between 4 and 35°C, and target plus
configured TRV margin must not exceed 35°C (with a 5°C margin the maximum target
is 30°C). Invalid commands are logged and leave the current target unchanged.

The control loop applies the latest valid command per room and publishes the
accepted target. Changing it resets that room's PI integral and starts the
existing close → prepare → open sequence with the new TRV setpoint. This also
supersedes pending commands without clearing latched faults. Repeating the same
target does not restart the sequence. Supply, freshness, disabled-room gates,
and report confirmation still apply; setting a target does not enable a room.
State publication confirms the application target, not physical valve movement.

A broker reconnect preserves the applied target and republishes it; unapplied
commands are discarded on disconnect. An application restart restores YAML
`target_temperature_c` (or the `--target-temperature` startup override). Targets
are not written back to YAML or restored from retained state. Dry-run accepts
target commands and publishes target state but never publishes device commands.
Read-only temperature/PI preview tools do not expose this API.

## Home Assistant room discovery

Enable discovery in the controller configuration (disabled by default):

```yaml
general:
  home_assistant:
    enabled: true
    discovery_prefix: homeassistant
```

Merge this block into the existing `general` mapping. Home Assistant's MQTT
integration must connect to the same broker and use the same discovery prefix.
The main controller application publishes discovery; the subscription-only
`watch_temperatures` and `watch_pi` examples remain read-only.

Each configured room, including disabled rooms, appears as a separate
“<room name> heating controller” device with these read-only entities:

- Temperature, target temperature, and requested opening percentage.
- Control status, including disabled and outside-schedule states.
- Diagnostic temperature message age, P and I contributions, actuator status,
  actuator fault (`none` when clear), and a dry-run binary sensor.

Numeric entities have units and `state_class: measurement` for Home Assistant
statistics. Missing numeric values are sent as null/unknown, never zero.
Requested opening is calculated demand, not measured position, flow, or proof
that a command reached a valve. Actuator status is simulated in dry-run; the
separate dry-run entity makes this visible. Valve-reported opening and target
controls are not part of this initial discovery implementation.

Retained discovery configurations are published under
`<discovery_prefix>/<component>/<device_id>/<entity>/config`. Stable IDs derive
from `control_base_topic` and room ID, so room display-name changes preserve
identity. Separate controller instances must have distinct control base topics.
Changing a base topic or room ID creates new entities. Removing a room or
turning discovery off does not delete its retained discovery configurations;
remove those configurations explicitly when retiring entities.

Live JSON state uses `<control_base_topic>/<room_id>/state` without retention.
It updates every 30 seconds, or on the next control tick after a control status,
actuator phase, fault, or target change. Cadence cannot exceed the control loop's
configured tick frequency. Discovery is republished after reconnect; failed
publications are retried on subsequent control ticks. Retained discovery also
allows Home Assistant to rediscover rooms after its own restart.

The shared connection sets a retained offline Last Will at
`<control_base_topic>/availability`, publishes online with live room updates,
and publishes offline on normal shutdown. Sensor expiry is the larger of
90 seconds and three control periods, rounded up to whole seconds, so a stalled
control loop does not leave old readings available indefinitely. Availability
indicates controller communication, not sensor health or physical valve closure;
room status and temperature message age indicate missing sensor readings.

Telemetry and discovery are allowed in dry-run; valve commands remain blocked.
All integration tests use simulated MQTT clients. Live Home Assistant discovery
has not been validated against a running installation.

Protocol references: [Home Assistant MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
and [MQTT sensors](https://www.home-assistant.io/integrations/sensor.mqtt/).

## Local SQLite history

The main application can record sensor arrivals, control evaluations, commands,
send attempts, device reports and lifecycle events to a local SQLite database.
Recording is disabled by default; enable it by merging this into `general`:

```yaml
recording:
  enabled: true
  path: recordings/heating.sqlite3
  queue_capacity: 10000
  flush_interval_seconds: 1
  retention_days: 90
```

The path is relative to the application's working directory. Recording works in
both dry-run and live mode and does not enable valve actuation. Preview examples
continue using their existing CSV recorder. Each application run stores its
effective configuration (without MQTT credentials or credential variable names),
software version, Git revision when available, and a dirty-worktree flag.

A single background writer uses WAL and batched transactions. MQTT callbacks and
control ticks enqueue data without waiting for disk. Queue overflow is counted
and logged; write errors stop recording for the run, not heating control. When
Home Assistant discovery is enabled, each room also exposes recording status,
recording fault and dropped-operation diagnostics. Restart the application after
fixing a database fault. These diagnostics describe the shared recorder.

Completed runs older than the retention period are removed at startup and
hourly, with their dependent records. Current and incomplete/crashed runs are
preserved, so retention is not a hard size limit. Deletion makes database pages
reusable rather than shrinking the file. Database files and SQLite sidecars are
ignored by Git. No recorder is started by `--check-config`.

See [the database schema and analysis examples](docs/recording.md) for the seven
tables, command correlation, retention, annotations and recording limitations.
