# Heating controller

The implementation provides immutable configuration classes, a strict YAML
parser, independent pure PI controllers, and a shared subscription-only MQTT
transport with per-room temperature storage, and a timed room lifecycle.
There is no actuator implementation yet.

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
status, temperature, target, change age, requested opening, P/I contributions,
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
measurement change age with its stale threshold, and control status. PNG, SVG,
and PDF outputs work without a desktop. Use `--max-age-seconds VALUE` if your
configured threshold differs from 900; `--timezone` defaults to Europe/Rome.
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
refresh freshness. Data becomes stale when time since the last change is strictly
greater than `measurement_max_age_seconds`. Consequently, a healthy sensor with
a constant temperature becomes stale too. This policy is not proof of genuine
measurement freshness; merged cached values can still initialize the store.
Humidity-only and malformed payloads do not refresh temperature timestamps.
Thread-safe immutable snapshots keep acquisition independent of PI evaluation.
