"""Flushed CSV recording of valid temperature arrivals."""

import csv
from datetime import datetime, timezone
from typing import TextIO


class TemperatureRecorder:
    def __init__(self, stream: TextIO):
        self._stream = stream
        self._writer = csv.writer(stream)
        self._writer.writerow(("received_at_utc", "room_id", "temperature_c"))
        stream.flush()

    def record(self, received_at: datetime, room_id: str, temperature_c: float) -> None:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("Receipt timestamp must include a timezone")
        self._writer.writerow((received_at.astimezone(timezone.utc).isoformat(),
                               room_id, temperature_c))
        self._stream.flush()
