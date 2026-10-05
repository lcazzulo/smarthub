"""Load configuration and print thermometer temperatures as MQTT messages arrive."""

import argparse
from dataclasses import replace
from datetime import datetime
import logging
from queue import Empty, Full, Queue

from heating_controller import ConfigError, load_config
from heating_controller.measurements import MeasurementStore
from heating_controller.mqtt import TemperatureSubscriber


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="Path to the YAML configuration file")
    parser.add_argument("--mqtt-host", help="Override broker hostname when running outside Docker")
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
    events = Queue(maxsize=1000)

    def enqueue(room_id: str, temperature_c: float) -> None:
        timestamp = datetime.now(config.general.timezone).isoformat(timespec="seconds")
        try:
            events.put_nowait((timestamp, room_id, temperature_c))
        except Full:
            logging.warning("Console queue full; skipped display of reading for %s", room_id)

    store = MeasurementStore(config)
    try:
        subscriber = TemperatureSubscriber(config, store, on_temperature=enqueue)
    except ModuleNotFoundError as exc:
        parser.exit(1, f"Missing dependency: {exc.name}. Install with: python -m pip install -e .\n")
    broker = config.general.mqtt
    print(f"Connecting to {broker.host}:{broker.port}; press Ctrl+C to stop.", flush=True)
    try:
        subscriber.start()
        while True:
            try:
                timestamp, room_id, temperature_c = events.get(timeout=0.5)
            except Empty:
                continue
            print(f"{timestamp} {room_id}: {temperature_c:.2f}°C", flush=True)
    except KeyboardInterrupt:
        print("\nStopping subscription.", flush=True)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"MQTT error: {exc}\n")
    finally:
        subscriber.stop()


if __name__ == "__main__":
    main()
