"""Preview TRVZB command plans using real temperatures; always dry-run."""

import argparse
from dataclasses import replace
from datetime import datetime
import logging
from threading import Event
from time import monotonic

from heating_controller import ConfigError, load_config
from heating_controller.actuator_mqtt import ActuatorMQTT
from heating_controller.measurements import MeasurementStore
from heating_controller.runtime import HeatingRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="Path to YAML configuration")
    parser.add_argument("--mqtt-host", help="Override broker hostname outside Docker")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    transport = None
    try:
        config = load_config(args.config)
        general = replace(config.general, dry_run=True)
        if args.mqtt_host:
            general = replace(general, mqtt=replace(general.mqtt, host=args.mqtt_host))
        config = replace(config, general=general)
        store = MeasurementStore(config)
        transport = ActuatorMQTT(config, store)
        runtime = HeatingRuntime(config, store, transport)
        logging.info("Actuator preview: commands and acknowledgements are simulated")
        transport.start()
        wait = Event()
        while True:
            runtime.tick(monotonic(), datetime.now(config.general.timezone))
            wait.wait(min(0.5, config.general.control.period_seconds))
    except KeyboardInterrupt:
        logging.info("Stopping actuator preview")
    except (ConfigError, ValueError, OSError, ModuleNotFoundError) as exc:
        parser.exit(1, f"Actuator preview error: {exc}\n")
    finally:
        if transport is not None:
            transport.stop()


if __name__ == "__main__":
    main()
