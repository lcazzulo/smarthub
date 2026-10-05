# Heating controller

The initial implementation provides immutable configuration classes and a strict
YAML parser. There is no MQTT transport or actuator implementation yet.

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
