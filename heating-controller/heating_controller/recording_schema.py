"""Version 1 of the local analysis database (timestamps are UTC milliseconds)."""

SCHEMA_VERSION = 1

COMMON = """
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_key INTEGER,
    recorded_at_utc INTEGER NOT NULL,
    elapsed_seconds REAL NOT NULL,
    room_id TEXT,
"""

SCHEMA = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    started_at_utc INTEGER NOT NULL,
    ended_at_utc INTEGER,
    software_version TEXT,
    git_revision TEXT,
    working_tree_dirty INTEGER,
    dry_run INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    stop_reason TEXT,
    dropped_records INTEGER NOT NULL DEFAULT 0
);
"""

TABLE_FIELDS = {
    "temperature_readings": """
        topic TEXT NOT NULL, temperature_c REAL NOT NULL, value_changed INTEGER NOT NULL
    """,
    "control_samples": """
        temperature_reading_id INTEGER REFERENCES temperature_readings(id) ON DELETE SET NULL,
        temperature_c REAL, target_temperature_c REAL NOT NULL,
        measurement_age_seconds REAL,
        supply_available INTEGER NOT NULL, heating_requested INTEGER NOT NULL,
        control_status TEXT NOT NULL,
        proportional_percent REAL, integral_percent REAL,
        requested_opening_percent REAL NOT NULL, saturated INTEGER,
        actuator_phase TEXT, actuator_fault TEXT
    """,
    "actuator_commands": """
        control_sample_id INTEGER REFERENCES control_samples(id) ON DELETE SET NULL,
        command_topic TEXT NOT NULL, payload_json TEXT NOT NULL, reason TEXT NOT NULL,
        outcome TEXT NOT NULL,
        completed_at_utc INTEGER,
        confirmation_report_id INTEGER REFERENCES valve_reports(id) ON DELETE SET NULL,
        failure_reason TEXT
    """,
    "command_attempts": """
        command_id INTEGER REFERENCES actuator_commands(id) ON DELETE SET NULL,
        attempt_number INTEGER NOT NULL, connection_generation INTEGER NOT NULL,
        send_result TEXT NOT NULL, mqtt_return_code INTEGER, error_message TEXT
    """,
    "valve_reports": """
        topic TEXT NOT NULL, payload_json TEXT NOT NULL, retained INTEGER NOT NULL,
        accepted INTEGER NOT NULL, rejection_reason TEXT,
        connection_generation INTEGER NOT NULL, report_sequence INTEGER,
        opening_setting_percent REAL, closing_setting_percent REAL,
        system_mode TEXT, running_state TEXT, external_temperature_c REAL, setpoint_c REAL
    """,
    "events": """
        event_type TEXT NOT NULL, severity TEXT NOT NULL, source TEXT NOT NULL,
        message TEXT NOT NULL, details_json TEXT NOT NULL
    """,
}

for table, fields in TABLE_FIELDS.items():
    SCHEMA += f"CREATE TABLE {table} ({COMMON} {fields}, UNIQUE(run_id, event_key));\n"
    SCHEMA += f"CREATE INDEX {table}_room_time ON {table}(room_id, recorded_at_utc);\n"
for table, column in (("control_samples", "temperature_reading_id"),
                      ("actuator_commands", "control_sample_id"),
                      ("actuator_commands", "confirmation_report_id"),
                      ("command_attempts", "command_id")):
    SCHEMA += f"CREATE INDEX {table}_{column} ON {table}({column});\n"
SCHEMA += "CREATE INDEX runs_started ON runs(started_at_utc);\n"
