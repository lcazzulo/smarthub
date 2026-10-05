# Repository Guidelines

## Project Structure & Module Organization

The Git repository root is the parent directory, `smarthub/`. Its `docker-compose.yml` orchestrates Mosquitto, Zigbee2MQTT, Frigate, and the EMDC MQTT writer. Service configuration lives in corresponding directories; runtime data and secrets are generally excluded from Git.

This `heating-controller/` directory contains the Python package in `heating_controller/`, tests in `tests/`, and `config.example.yaml`. The user authorized implementation here; the existing Python writer still builds from its external repository. Keep changes scoped to this service.

## Build, Test, and Development Commands

Run Compose commands from the repository root:

- `docker compose config --quiet`: validate Compose configuration without starting services; requires the referenced environment configuration.
- `git diff --check`: detect whitespace errors before submitting changes.
- `git status --short`: identify existing changes and keep unrelated work intact.
- `docker compose build <service>`: build a configured service when authorized and needed.

For this service, install with `python -m pip install -e .` in a virtual environment and run `python -m unittest discover -s tests -v` from `heating-controller/`. No formatter or linter is configured. Do not start or deploy services as part of documentation or planning work.

## Coding Style & Naming Conventions

Use two-space indentation for YAML, descriptive service names, and concise Markdown documentation. For future Python code, use four-space indentation, `snake_case` functions/modules, and explicit configuration types. Keep pure PI calculations, room lifecycle, MQTT transport, and the TRVZB adapter separate. Use one shared MQTT connection with independent room controllers.

## Testing Guidelines

Use standard-library `unittest`, files named `tests/test_*.py`, and the documented discovery command. No coverage threshold is established. Prioritize configuration validation, anti-windup, stale measurements, supply intervals including daylight-saving transitions, reconnect reconciliation, command limiting, and proof that dry-run publishes no device commands. Use simulated transports; tests must not actuate real valves.

## Commit & Pull Request Guidelines

History uses short imperative subjects such as `Update mosquitto configuration.` Follow that style and keep commits focused. PRs should explain the behavior changed, affected services, configuration requirements, validation performed, and any hardware assumptions. Link relevant issues when available. Preserve unrelated Compose and Frigate changes.

## Security & Heating Constraints

Never commit credentials, `.env`, or runtime device data. Do not invent broker settings, device topics, supply hours, or tuned PI gains. Default heating control to dry-run. Use `Europe/Rome` for shared supply intervals; outside those intervals, request closure and reset integration. Boiler control is unavailable. External-temperature fallback and closure/recovery remain unverified; never assume an offline process can close valves.
