"""Runnable heating controller; CLI defaults to dry-run, live requires --live."""

import argparse
from dataclasses import replace
from datetime import datetime
import logging
import math
import signal
from threading import Event
from time import gmtime, monotonic
from typing import Callable

from .actuation import ActuatorCoordinator
from .actuator_mqtt import ActuatorMQTT
from .config import ConfigError, Configuration, load_config
from .measurements import MeasurementStore
from .runtime import HeatingRuntime


logger = logging.getLogger(__name__)


def positive_number(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Expected a finite, positive number")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="YAML configuration file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Publish real valve commands")
    mode.add_argument("--dry-run", action="store_true", help="Simulate commands (default, regardless of YAML)")
    parser.add_argument("--mqtt-host", help="Override broker hostname, e.g. localhost on the Docker host")
    parser.add_argument("--room", action="append", help="Control only this room ID; repeat to select several")
    parser.add_argument("--target-temperature", type=positive_number, metavar="CELSIUS",
                        help="Override the room/PI target for selected rooms; TRV margin is added separately")
    parser.add_argument("--trv-setpoint-margin", type=positive_number, metavar="CELSIUS",
                        help="Override the positive TRV setpoint margin; target + margin must be <= 35°C")
    parser.add_argument("--external-sensor-mode", choices=("external", "remote_temperature"),
                        help="Override the external sensor enum to match installed Zigbee2MQTT")
    parser.add_argument("--run-seconds", type=positive_number,
                        help="Stop control after this duration, then request closure")
    parser.add_argument("--shutdown-timeout", type=positive_number, default=30.0, metavar="SECONDS",
                        help="Maximum wait for shutdown closure reports (default: 30)")
    parser.add_argument("--check-config", action="store_true", help="Validate selected rooms and exit without connecting")
    return parser


def resolve_configuration(args: argparse.Namespace) -> Configuration:
    config = load_config(args.config)
    if args.room:
        selected = set(args.room)
        unknown = selected - {room.id for room in config.rooms}
        if unknown:
            raise ConfigError(f"Unknown room IDs: {', '.join(sorted(unknown))}")
        config = replace(config, rooms=tuple(room for room in config.rooms if room.id in selected))
    if args.target_temperature is not None:
        config = replace(config, rooms=tuple(replace(room, target_temperature_c=args.target_temperature)
                                            for room in config.rooms))
    general = replace(config.general, dry_run=not args.live)
    if args.mqtt_host:
        general = replace(general, mqtt=replace(general.mqtt, host=args.mqtt_host))
    settings = general.actuator
    if args.trv_setpoint_margin is not None:
        settings = replace(settings, trv_setpoint_margin_c=args.trv_setpoint_margin)
    if args.external_sensor_mode is not None:
        settings = replace(settings, external_sensor_mode=args.external_sensor_mode)
    config = replace(config, general=replace(general, actuator=settings))
    # Fail before constructing a network client, including in --check-config mode.
    ActuatorCoordinator(config)
    return config


def run_application(config: Configuration, *, stop: Event, force_stop: Event,
                    run_seconds: float | None = None, shutdown_timeout: float = 30.0,
                    transport_factory: Callable = ActuatorMQTT,
                    clock: Callable[[], float] = monotonic,
                    wait: Callable[[float], None] | None = None) -> int:
    """Own one transport and loop; injected clocks/transports never need hardware."""
    store = MeasurementStore(config, clock=clock)
    transport = transport_factory(config, store)
    runtime = HeatingRuntime(config, store, transport)
    started = False
    exit_code = 0
    try:
        mode = "DRY RUN (simulated commands)" if config.general.dry_run else "LIVE (real valve commands)"
        logger.info("Starting %s; rooms=%s; broker=%s:%s", mode,
                    ",".join(room.id for room in config.rooms),
                    config.general.mqtt.host, config.general.mqtt.port)
        transport.start()
        started = True
        began = clock()
        previous_outputs = None
        while not stop.is_set():
            now = clock()
            if run_seconds is not None and now - began >= run_seconds:
                logger.info("Run duration reached")
                break
            runtime.tick(now, datetime.now(config.general.timezone))
            if runtime.last_outputs is not previous_outputs:
                previous_outputs = runtime.last_outputs
                for output in runtime.last_outputs:
                    progress = runtime.actuators.progress[output.room_id]
                    logger.info("%s status=%s temperature=%s target=%.1f demand=%.1f%% actuator=%s pending=%s fault=%s",
                                output.room_id, output.status, output.temperature_c,
                                output.target_temperature_c, output.opening_percent,
                                progress.phase, progress.pending.command.reason if progress.pending else "none",
                                progress.fault or "none")
                if any(state.fault for state in runtime.actuators.progress.values()):
                    raise RuntimeError("Actuator fault; stopping control and requesting closure")
            delay = min(0.5, config.general.control.period_seconds)
            (wait or stop.wait)(delay)
    except (ValueError, OSError, RuntimeError) as exc:
        logger.error("Application error: %s", exc)
        exit_code = 1
    finally:
        stop.set()
        try:
            if started:
                logger.info("Stopping: requesting closure; waiting up to %.1fs for reports", shutdown_timeout)
                deadline = clock() + shutdown_timeout
                closed = False
                while not force_stop.is_set():
                    now = clock()
                    closed = runtime.shutdown_tick(now)
                    if closed or now >= deadline:
                        break
                    (wait or force_stop.wait)(min(0.2, max(0.0, deadline - clock())))
                if closed:
                    logger.info("%s", "Dry-run shutdown closure simulated" if config.general.dry_run
                                else "Shutdown closure settings reported by all selected valves; physical closure unverified")
                else:
                    logger.error("Shutdown closure unconfirmed; valves may remain open")
                    exit_code = 1
        except (ValueError, OSError, RuntimeError) as exc:
            logger.error("Shutdown closure failed: %s", exc)
            exit_code = 1
        finally:
            transport.stop()
    return exit_code


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.Formatter.converter = gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    try:
        config = resolve_configuration(args)
    except (ConfigError, ValueError) as exc:
        parser.exit(1, f"Configuration error: {exc}\n")
    if args.check_config:
        print(f"Actuator configuration valid for: {', '.join(room.id for room in config.rooms)}")
        return
    stop, force_stop = Event(), Event()

    def handle_signal(signum, frame):
        if stop.is_set():
            force_stop.set()
        stop.set()

    previous_handlers = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, handle_signal)
        try:
            code = run_application(config, stop=stop, force_stop=force_stop,
                                   run_seconds=args.run_seconds, shutdown_timeout=args.shutdown_timeout)
        except (ModuleNotFoundError, ValueError, OSError) as exc:
            parser.exit(1, f"Startup error: {exc}\n")
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    raise SystemExit(code)
