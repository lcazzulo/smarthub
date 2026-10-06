"""Load configuration and print thermometer temperatures as MQTT messages arrive."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import logging
from queue import Empty, Full, Queue
from threading import Event

from heating_controller import ConfigError, load_config
from heating_controller.measurements import MeasurementStore
from heating_controller.mqtt import TemperatureSubscriber
from heating_controller.temperature_recording import TemperatureRecorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="Path to the YAML configuration file")
    parser.add_argument("--mqtt-host", help="Override broker hostname when running outside Docker")
    parser.add_argument("--csv", help="Record arrivals to a new CSV file (UTC timestamps)")
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
    overflow = Event()

    def enqueue(room_id: str, temperature_c: float) -> None:
        timestamp = datetime.now(timezone.utc)
        try:
            events.put_nowait((timestamp, room_id, temperature_c))
        except Full:
            overflow.set()
            logging.warning("Console queue full; skipped display of reading for %s", room_id)

    store = MeasurementStore(config)
    try:
        subscriber = TemperatureSubscriber(config, store, on_temperature=enqueue)
    except ModuleNotFoundError as exc:
        parser.exit(1, f"Missing dependency: {exc.name}. Install with: python -m pip install -e .\n")
    broker = config.general.mqtt
    print(f"Connecting to {broker.host}:{broker.port}; press Ctrl+C to stop.", flush=True)
    stream = None
    recorder = None
    try:
        if args.csv:
            stream = open(args.csv, "x", newline="", encoding="utf-8")
        recorder = TemperatureRecorder(stream) if stream is not None else None
        subscriber.start()
        while True:
            if overflow.is_set() and recorder is not None:
                raise ValueError("Recording queue overflow; stopped because arrivals were lost")
            try:
                timestamp, room_id, temperature_c = events.get(timeout=0.5)
            except Empty:
                continue
            if recorder is not None:
                recorder.record(timestamp, room_id, temperature_c)
            print(f"{timestamp.isoformat()} {room_id}: {temperature_c:.2f}°C", flush=True)
    except KeyboardInterrupt:
        print("\nStopping subscription.", flush=True)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Acquisition error: {exc}\n")
    finally:
        subscriber.stop()
        if stream is not None:
            stream.close()


if __name__ == "__main__":
    main()
