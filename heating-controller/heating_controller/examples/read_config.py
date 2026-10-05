"""Read a configuration file and print its room settings."""

import argparse

from heating_controller import ConfigError, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="Path to the YAML configuration file")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        parser.exit(1, f"Configuration error: {exc}\n")

    general = config.general
    print(f"Configuration loaded: {args.config}")
    print(f"Dry-run: {general.dry_run}")
    print(f"MQTT broker: {general.mqtt.host}:{general.mqtt.port}")
    print(f"Timezone: {general.timezone.key}")
    print(f"Rooms: {len(config.rooms)}")
    for room in config.rooms:
        print(f"  {room.id}: target={room.target_temperature_c}°C, enabled={room.enabled}")
        print(f"    Thermometer: {room.thermometer.state_topic(general.zigbee2mqtt.base_topic)}")
        print(f"    Valve: {room.valve.state_topic(general.zigbee2mqtt.base_topic)}")


if __name__ == "__main__":
    main()
