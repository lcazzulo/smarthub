"""CSV diagnostic recording for the subscription-only PI preview."""

import csv
from datetime import datetime
from typing import TextIO

from .room import RoomOutput


CSV_FIELDS = (
    "timestamp", "room_id", "status", "temperature_c", "target_temperature_c",
    "measurement_age_seconds", "opening_percent", "proportional_percent",
    "integral_percent", "saturated",
)


class CSVRecorder:
    def __init__(self, stream: TextIO):
        self._stream = stream
        self._writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        self._writer.writeheader()
        stream.flush()

    def record(self, timestamp: datetime, output: RoomOutput) -> None:
        pi = output.pi
        self._writer.writerow({
            "timestamp": timestamp.isoformat(),
            "room_id": output.room_id,
            "status": output.status,
            "temperature_c": output.temperature_c,
            "target_temperature_c": output.target_temperature_c,
            "measurement_age_seconds": output.measurement_age_seconds,
            "opening_percent": output.opening_percent,
            "proportional_percent": pi.proportional_percent if pi else None,
            "integral_percent": pi.integral_percent if pi else None,
            "saturated": pi.saturated if pi else None,
        })
        self._stream.flush()
