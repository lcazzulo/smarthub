"""Plot recorded PI preview CSV data to a PNG, SVG, or PDF file."""

import argparse
import csv
from datetime import datetime
import math
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from heating_controller.telemetry import CSV_FIELDS


def read_rows(path: str | Path) -> dict[str, list[dict]]:
    rooms = {}
    with open(path, newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not set(CSV_FIELDS).issubset(reader.fieldnames or ()):
            raise ValueError("Input does not contain the PI preview CSV columns")
        for line, row in enumerate(reader, start=2):
            try:
                timestamp = datetime.fromisoformat(row["timestamp"])
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError("Timestamp must include a UTC offset")
                if not row["room_id"] or not row["status"]:
                    raise ValueError("Missing room or status")
                parsed = {"timestamp": timestamp, "status": row["status"]}
                for key in CSV_FIELDS[3:9]:
                    value = float(row[key]) if row[key] else math.nan
                    if row[key] and not math.isfinite(value):
                        raise ValueError(f"Nonfinite {key}")
                    parsed[key] = value
                parsed["saturated"] = row["saturated"] == "True"
                rooms.setdefault(row["room_id"], []).append(parsed)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"CSV line {line}: {exc}") from exc
    if not rooms:
        raise ValueError("CSV contains no data rows")
    for rows in rooms.values():
        rows.sort(key=lambda row: row["timestamp"])
    return rooms


def plot(rooms: dict[str, list[dict]], destination: str | Path, timezone: ZoneInfo,
         max_age_seconds: float = 900) -> None:
    import matplotlib

    matplotlib.use("Agg")  # Saving plots works without a desktop/display server.
    import matplotlib.dates as dates
    import matplotlib.pyplot as plt

    statuses = {"active": 0, "outside_supply": 1, "stale_temperature": 2,
                "waiting_for_temperature": 3, "disabled": 4}
    figure, axes = plt.subplots(4, len(rooms), figsize=(7 * len(rooms), 11),
                                sharex=True, squeeze=False, layout="constrained")
    try:
        for column, (room, rows) in enumerate(sorted(rooms.items())):
            times = [row["timestamp"] for row in rows]
            def values(key):
                return [row[key] for row in rows]

            temperature, opening, age, status = axes[:, column]
            temperature.set_title(room)
            temperature.step(times, values("temperature_c"), where="post", label="Temperature", color="#2374ab")
            temperature.step(times, values("target_temperature_c"), where="post", label="Room target", linestyle="--", color="#d17800")
            temperature.set_ylabel("Temperature (°C)")
            opening.step(times, values("opening_percent"), where="post", label="Opening demand", color="#2374ab", linewidth=2)
            opening.step(times, values("proportional_percent"), where="post", label="P contribution", color="#d17800")
            opening.step(times, values("integral_percent"), where="post", label="I contribution", color="#39834b")
            saturated = [row for row in rows if row["saturated"]]
            if saturated:
                opening.scatter([row["timestamp"] for row in saturated],
                                [row["opening_percent"] for row in saturated],
                                marker="x", color="#b83232", label="Saturated", zorder=4)
            opening.set_ylabel("Demand / contributions (%)")
            opening.axhline(0, color="gray", linewidth=0.5)
            opening.axhline(100, color="gray", linewidth=0.5)
            age.plot(times, values("measurement_age_seconds"), label="Time since temperature changed", color="#7755a3")
            age.axhline(max_age_seconds, linestyle="--", color="#b83232", label="Stale threshold")
            age.set_ylabel("Change age (seconds)")
            status.step(times, [statuses.get(row["status"], 5) for row in rows], where="post", color="#555555")
            status.set_yticks(list(statuses.values()) + [5], list(statuses.keys()) + ["unknown"])
            status.set_ylim(-0.5, 5.5)
            status.set_ylabel("Control status")
            status.set_xlabel(f"Time ({timezone.key})")
            locator = dates.AutoDateLocator(tz=timezone)
            status.xaxis.set_major_locator(locator)
            status.xaxis.set_major_formatter(dates.ConciseDateFormatter(locator, tz=timezone))
            for axis in (temperature, opening, age):
                axis.legend(loc="best", fontsize="small")
            for axis in axes[:, column]:
                axis.grid(alpha=0.2)
        figure.suptitle("PI preview — calculated demand; no valve actuation", fontsize=15)
        figure.savefig(destination, dpi=150)
    finally:
        plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="CSV recorded by watch_pi --csv")
    parser.add_argument("--output", default="pi-output.png", help="PNG, SVG or PDF destination")
    parser.add_argument("--timezone", default="Europe/Rome", help="Timezone for plot labels")
    parser.add_argument("--max-age-seconds", type=float, default=900, help="Stale threshold shown on plot")
    args = parser.parse_args()
    try:
        if not math.isfinite(args.max_age_seconds) or args.max_age_seconds <= 0:
            raise ValueError("Stale threshold must be finite and positive")
        if Path(args.output).resolve() == Path(args.csv).resolve():
            raise ValueError("Plot output must differ from input CSV")
        plot(read_rows(args.csv), args.output, ZoneInfo(args.timezone), args.max_age_seconds)
    except ModuleNotFoundError:
        parser.exit(1, 'Install plotting dependencies: python -m pip install -e ".[plot]"\n')
    except (OSError, ValueError, ZoneInfoNotFoundError) as exc:
        parser.exit(1, f"Plot error: {exc}\n")
    print(f"Saved plot: {args.output}")


if __name__ == "__main__":
    main()
