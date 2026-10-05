import csv
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from heating_controller.examples.plot_pi import read_rows
from heating_controller.pi import PIResult
from heating_controller.room import RoomOutput
from heating_controller.telemetry import CSVRecorder


class TelemetryTests(unittest.TestCase):
    def test_csv_round_trip_keeps_precision_and_missing_values(self):
        timestamp = datetime(2026, 10, 5, 19, 58, 36, tzinfo=timezone.utc)
        output = RoomOutput("bathroom", "active", 30.5, 25.4, 260.4, 64.261234,
                            PIResult(5.1, 51.0, 13.261234, 64.261234, False))
        stream = StringIO()
        recorder = CSVRecorder(stream)
        recorder.record(timestamp, output)
        recorder.record(timestamp, RoomOutput("bedroom", "waiting_for_temperature", 30.5, None, None, 0, None))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.csv"
            path.write_text(stream.getvalue())
            rooms = read_rows(path)
        self.assertEqual(rooms["bathroom"][0]["opening_percent"], 64.261234)
        self.assertEqual(rooms["bathroom"][0]["timestamp"], timestamp)
        rows = list(csv.DictReader(StringIO(stream.getvalue())))
        self.assertEqual(rows[1]["temperature_c"], "")
        self.assertEqual(rows[1]["integral_percent"], "")
        self.assertEqual(rows[1]["opening_percent"], "0")

    def test_empty_or_invalid_recordings_have_clear_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.csv"
            for content in ("timestamp,room_id\n", "", "not a CSV"):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    read_rows(path)
            stream = StringIO()
            CSVRecorder(stream)
            path.write_text(stream.getvalue())
            with self.assertRaisesRegex(ValueError, "no data rows"):
                read_rows(path)


if __name__ == "__main__":
    unittest.main()
