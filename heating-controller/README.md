# Heating controller

The implementation provides immutable configuration classes, a strict YAML
parser, and independent pure PI controllers. There is no MQTT transport or
actuator implementation yet.

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

The parser rejects unknown/missing fields, duplicate YAML keys, invalid numeric
values, duplicate room/device assignments, and overlapping supply intervals.
Intervals use quoted `HH:MM` strings, allow overnight ranges, and have inclusive
starts and exclusive ends. Empty intervals mean no supply. Runtime scheduling,
including daylight-saving behavior, will be implemented separately.

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
unwinding. `reset()` clears integration. The future room lifecycle must gate
calculations on room enablement, fresh measurements, and supply availability,
and reset integration when suspended. Calculation alone sends no commands.
