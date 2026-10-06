import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
import unittest

from heating_controller.temperature_recording import TemperatureRecorder


class TemperatureRecordingTests(unittest.TestCase):
    def test_utc_precision_and_repeated_values(self):
        stream = StringIO()
        recorder = TemperatureRecorder(stream)
        timestamp = datetime(2026, 10, 6, 12, 0, 0, 123456,
                             tzinfo=timezone(timedelta(hours=2)))
        recorder.record(timestamp, "bedroom", 20.123456)
        recorder.record(timestamp + timedelta(seconds=30), "bedroom", 20.123456)
        rows = list(csv.DictReader(StringIO(stream.getvalue())))
        self.assertEqual(rows[0]["received_at_utc"], "2026-10-06T10:00:00.123456+00:00")
        self.assertEqual(rows[0]["temperature_c"], "20.123456")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["received_at_utc"], "2026-10-06T10:00:30.123456+00:00")

    def test_naive_timestamp_is_rejected(self):
        recorder = TemperatureRecorder(StringIO())
        with self.assertRaises(ValueError):
            recorder.record(datetime(2026, 10, 6), "bedroom", 20)
