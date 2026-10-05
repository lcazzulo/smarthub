"""Subscribe to room temperatures and print independently timed PI outputs."""

import argparse
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
import logging
from threading import Event
from time import monotonic

from heating_controller import ConfigError, load_config
from heating_controller.measurements import MeasurementStore
from heating_controller.mqtt import TemperatureSubscriber
from heating_controller.room import ControlLoop
from heating_controller.telemetry import CSVRecorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="Path to YAML configuration")
    parser.add_argument("--mqtt-host", help="Override broker hostname outside Docker")
    parser.add_argument("--csv", help="Record outputs to a new CSV file (existing files are preserved)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        parser.exit(1, f"Configuration error: {exc}\n")
    if args.mqtt_host:
        config = replace(config, general=replace(
            config.general, mqtt=replace(config.general.mqtt, host=args.mqtt_host)
        ))
    store = MeasurementStore(config)
    loop = ControlLoop(config, store)
    try:
        subscriber = TemperatureSubscriber(config, store)
    except ModuleNotFoundError as exc:
        parser.exit(1, f"Missing dependency: {exc.name}. Run: python -m pip install -e .\n")
    print("PI output preview only; no commands are published. Ctrl+C to stop.", flush=True)
    wait = Event()
    resources = ExitStack()
    try:
        recorder = None
        if args.csv:
            stream = resources.enter_context(open(args.csv, "x", newline="", encoding="utf-8"))
            recorder = CSVRecorder(stream)
        subscriber.start()
        while True:
            wall_time = datetime.now(config.general.timezone)
            for output in loop.tick(monotonic(), wall_time):
                if recorder is not None:
                    recorder.record(wall_time, output)
                temperature = "missing" if output.temperature_c is None else f"{output.temperature_c:.2f}°C"
                details = ""
                if output.pi is not None:
                    details = (f" P={output.pi.proportional_percent:.2f}%"
                               f" I={output.pi.integral_percent:.2f}%"
                               f" saturated={output.pi.saturated}")
                age = "missing" if output.measurement_age_seconds is None else f"{output.measurement_age_seconds:.1f}s"
                print(f"{wall_time.isoformat(timespec='seconds')} {output.room_id}:"
                      f" status={output.status} temperature={temperature}"
                      f" target={output.target_temperature_c:.2f}°C age={age}"
                      f" opening={output.opening_percent:.2f}%{details}", flush=True)
            wait.wait(min(0.5, config.general.control.period_seconds))
    except KeyboardInterrupt:
        print("\nStopping PI preview.", flush=True)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Preview error: {exc}\n")
    finally:
        subscriber.stop()
        resources.close()


if __name__ == "__main__":
    main()
