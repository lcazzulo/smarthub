"""Bounded asynchronous SQLite recorder; control never waits for disk writes."""

from dataclasses import asdict, dataclass
from datetime import time as LocalTime
from importlib.metadata import PackageNotFoundError, version
import json
import logging
from pathlib import Path
from queue import Empty, Full, Queue
import sqlite3
import subprocess
from threading import Event, Lock, Thread
from time import monotonic, time
from zoneinfo import ZoneInfo

from .config import Configuration
from .recording_schema import SCHEMA, SCHEMA_VERSION, TABLE_FIELDS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reference:
    table: str
    key: int | None


def config_snapshot(config: Configuration) -> str:
    document = asdict(config)
    # Never resolve or store credential environment variables, including their names.
    document['general']['mqtt'].pop('username_env', None)
    document['general']['mqtt'].pop('password_env', None)

    def encode(value):
        if isinstance(value, ZoneInfo):
            return value.key
        if isinstance(value, LocalTime):
            return value.isoformat(timespec='minutes')
        raise TypeError(f'Unsupported configuration value: {type(value).__name__}')

    return json.dumps(document, default=encode, allow_nan=False)


class SQLiteRecorder:
    def __init__(self, config: Configuration, *, clock=monotonic, utc_clock=time):
        self.settings = config.general.recording
        self._config = config_snapshot(config)
        self._dry_run = config.general.dry_run
        self._clock, self._utc = clock, utc_clock
        self._began = clock()
        self._started_at = self.utc_ms()
        self._queue = Queue(maxsize=self.settings.queue_capacity)
        self._lock = Lock()
        self._closing = Event()
        self.ready = Event()
        self._next_key = 0
        self._dropped = 0
        self._fault = None
        self._failed = False
        self._closed = False
        self._stop_reason = None
        self.run_id = None
        self._thread = Thread(target=self._worker, name='heating-sqlite', daemon=True)
        self._thread.start()

    def utc_ms(self):
        return int(self._utc() * 1000)

    @property
    def status(self):
        with self._lock:
            return {
                'recording_status': 'fault' if self._fault else ('recording' if self.ready.is_set() else 'starting'),
                'recording_fault': self._fault or 'none',
                'recording_dropped_records': self._dropped,
            }

    def _enqueue(self, table, values, *, key=None, now=None):
        # All payloads are copied/serialized by callers before admission. No disk I/O here.
        with self._lock:
            if self._closed or self._failed:
                self._dropped += 1
                return None
            self._next_key += 1
            event_key = self._next_key
            if key is None:
                values = dict(values, event_key=event_key, recorded_at_utc=self.utc_ms(),
                              elapsed_seconds=(self._clock() if now is None else now) - self._began)
            try:
                self._queue.put_nowait((table, key, dict(values)))
            except Full:
                self._dropped += 1
                if self._fault is None:
                    self._fault = 'Recording queue overflow; records lost'
                    logger.error(self._fault)
                return None
            return event_key

    def record(self, table, *, now=None, **values):
        return self._enqueue(table, values, now=now)

    def update(self, table, key, **values):
        if key is not None:
            self._enqueue(table, values, key=key)

    def event(self, event_type, message, *, room_id=None, severity='info',
              source='controller', now=None, **details):
        return self.record('events', now=now, room_id=room_id, event_type=event_type,
                           severity=severity, source=source, message=message,
                           details_json=json.dumps(details, allow_nan=False))

    def annotate(self, message, *, room_id=None, **details):
        return self.event('annotation', message, room_id=room_id, source='operator', **details)

    def finish_command(self, key, outcome, *, report_key=None, failure_reason=None):
        self.update('actuator_commands', key, outcome=outcome, completed_at_utc=self.utc_ms(),
                    confirmation_report_id=Reference('valve_reports', report_key),
                    failure_reason=failure_reason)

    def close(self, reason='normal_shutdown', timeout=5.0):
        with self._lock:
            self._closed = True
            self._stop_reason = reason
            self._closing.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            with self._lock:
                self._fault = 'Recorder shutdown timed out; queued records may be lost'
            logger.error(self._fault)
        return not self._thread.is_alive()

    def _connect(self):
        path = Path(self.settings.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=0.25)
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            schema_version = connection.execute('PRAGMA user_version').fetchone()[0]
            if schema_version == 0:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError('Refusing an unversioned, nonempty recording database')
                connection.executescript('BEGIN;\n' + SCHEMA + f'PRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;')
            elif schema_version != SCHEMA_VERSION:
                raise ValueError(f'Unsupported recording schema version: {schema_version}')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=NORMAL')
            return connection
        except Exception:
            connection.close()
            raise

    def _metadata(self):
        try:
            software_version = version('smarthub-heating-controller')
        except PackageNotFoundError:
            software_version = 'unknown'
        revision, dirty = None, None
        root = Path(__file__).resolve().parent.parent
        try:
            revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                                      capture_output=True, text=True, timeout=2, check=True).stdout.strip()
            dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', '.'], cwd=root,
                                        capture_output=True, text=True, timeout=2, check=True).stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
        return software_version, revision, dirty

    def _write(self, connection, operation):
        table, key, values = operation
        if table not in TABLE_FIELDS:
            raise ValueError(f'Unknown recording table: {table}')
        values = dict(values)
        for column, value in values.items():
            if isinstance(value, Reference):
                if value.table not in TABLE_FIELDS:
                    raise ValueError('Unknown reference table')
                row = connection.execute(
                    f'SELECT id FROM {value.table} WHERE run_id=? AND event_key=?',
                    (self.run_id, value.key)).fetchone()
                values[column] = row[0] if row else None
        if key is None:
            values['run_id'] = self.run_id
            columns = ','.join(values)
            placeholders = ','.join('?' for _ in values)
            connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(values.values()))
        else:
            assignments = ','.join(f'{column}=?' for column in values)
            connection.execute(f'UPDATE {table} SET {assignments} WHERE run_id=? AND event_key=?',
                               (*values.values(), self.run_id, key))

    def _prune(self, connection):
        cutoff = self.utc_ms() - self.settings.retention_days * 86400000
        # Retain incomplete/crashed runs; never delete a possibly active writer's data.
        connection.execute('DELETE FROM runs WHERE ended_at_utc < ? AND id != ?', (cutoff, self.run_id))

    def _worker(self):
        connection = None
        batch = []
        try:
            connection = self._connect()
            software_version, revision, dirty = self._metadata()
            with connection:
                self.run_id = connection.execute(
                    'INSERT INTO runs (started_at_utc, software_version, git_revision, working_tree_dirty, dry_run, config_json) VALUES (?,?,?,?,?,?)',
                    (self._started_at, software_version, revision, dirty, self._dry_run, self._config)).lastrowid
                self._prune(connection)
            self.ready.set()
            reported_drops = 0
            last_prune = monotonic()
            while not self._closing.is_set() or not self._queue.empty():
                try:
                    batch = [self._queue.get(timeout=min(self.settings.flush_interval_seconds, 0.1))]
                except Empty:
                    continue
                deadline = monotonic() + self.settings.flush_interval_seconds
                while len(batch) < 100:
                    try:
                        batch.append(self._queue.get_nowait())
                    except Empty:
                        if self._closing.is_set() or monotonic() >= deadline:
                            break
                        self._closing.wait(min(0.01, max(0, deadline - monotonic())))
                with connection:
                    for operation in batch:
                        self._write(connection, operation)
                    with self._lock:
                        dropped = self._dropped
                    if dropped != reported_drops:
                        connection.execute('UPDATE runs SET dropped_records=? WHERE id=?', (dropped, self.run_id))
                        self._write(connection, ('events', None, dict(
                            recorded_at_utc=self.utc_ms(), elapsed_seconds=self._clock() - self._began,
                            event_type='recording_gap', severity='error', source='recorder',
                            message='Recording operations dropped', details_json=json.dumps({'count': dropped - reported_drops}))))
                    if monotonic() - last_prune >= 3600:
                        self._prune(connection)
                        last_prune = monotonic()
                reported_drops = dropped
                batch = []
            with connection:
                connection.execute('UPDATE runs SET ended_at_utc=?, stop_reason=?, dropped_records=? WHERE id=?',
                                   (self.utc_ms(), self._stop_reason, self._dropped, self.run_id))
        except Exception as exc:
            # Recorder faults must never escape into the MQTT or control threads.
            with self._lock:
                self._failed = True
                self._dropped += len(batch)
                while True:
                    try:
                        self._queue.get_nowait()
                        self._dropped += 1
                    except Empty:
                        break
                self._fault = f'SQLite recording stopped: {type(exc).__name__}: {exc}'
            logger.exception('SQLite recording stopped; heating control continues')
            if connection is not None and self.run_id is not None:
                try:
                    connection.rollback()
                    with connection:
                        connection.execute('UPDATE runs SET dropped_records=?, stop_reason=? WHERE id=?',
                                           (self._dropped, 'recording_failure', self.run_id))
                except sqlite3.Error:
                    pass
        finally:
            self.ready.set()
            if connection is not None:
                connection.close()
