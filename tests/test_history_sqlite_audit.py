from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "history_sqlite_audit.py"
SPEC = importlib.util.spec_from_file_location("history_sqlite_audit_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL
        );
        CREATE TABLE bars (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            timestamp_utc INTEGER NOT NULL,
            datetime_utc TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adjclose REAL,
            volume INTEGER,
            first_seen_run_id TEXT NOT NULL,
            last_seen_run_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY(symbol, interval, timestamp_utc)
        );
        CREATE INDEX idx_bars_interval_timestamp ON bars(interval, timestamp_utc);
        CREATE TABLE events (
            symbol TEXT NOT NULL,
            event_json TEXT NOT NULL
        );
        """
    )
    connection.execute("INSERT INTO runs VALUES (?, ?)", ("run-1", "baseline"))
    rows = []
    for index in range(600):
        rows.append(
            (
                "AAPL" if index < 300 else "MSFT",
                "1d",
                1_700_000_000 + index * 86_400,
                f"2026-01-{(index % 28) + 1:02d}T00:00:00Z",
                100.0,
                101.0,
                99.0,
                100.5,
                100.4,
                1_000_000,
                "run-1",
                "run-1",
                "runs/example/raw/chart/AAPL.json.gz" if index < 300 else "runs/example/raw/chart/MSFT.json.gz",
                "a" * 64 if index < 300 else "b" * 64,
            )
        )
    connection.executemany(
        "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.execute("INSERT INTO events VALUES (?, ?)", ("AAPL", '{"amount":0.25}'))
    connection.commit()
    connection.close()


def test_connect_read_only_rejects_write(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    connection = audit.connect_read_only(database)
    assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO runs VALUES ('run-2', 'sync')")
    connection.close()


def test_missing_database_is_rejected(tmp_path: Path):
    with pytest.raises(audit.AuditError, match="does not exist"):
        audit.connect_read_only(tmp_path / "missing.sqlite")


def test_database_summary_and_row_counts(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    connection = audit.connect_read_only(database)
    summary = audit.collect_database_summary(connection, database)
    master = audit.collect_master_rows(connection)
    tables = audit.table_names(master)
    counts = audit.collect_row_counts(connection, tables, exact=True)
    connection.close()
    assert summary["query_only"] == 1
    assert summary["database_file_bytes"] > 0
    assert counts["bars"] == 600
    assert counts["events"] == 1


def test_columns_detect_json_and_no_blob(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    connection = audit.connect_read_only(database)
    master = audit.collect_master_rows(connection)
    columns = audit.collect_columns(connection, audit.table_names(master))
    connection.close()
    event_json = next(item for item in columns if item.table_name == "events" and item.column_name == "event_json")
    assert event_json.payload_candidate is True
    assert all("BLOB" not in item.declared_type.upper() for item in columns)


def test_object_and_index_sizes_when_dbstat_available(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    connection = audit.connect_read_only(database)
    summary = audit.collect_database_summary(connection, database)
    master = audit.collect_master_rows(connection)
    objects, source = audit.collect_object_sizes(connection, master, summary["database_bytes_from_pages"])
    indexes = audit.collect_indexes(connection, audit.table_names(master), objects, summary["database_bytes_from_pages"])
    connection.close()
    if source == "dbstat":
        assert any(item.name == "bars" and item.bytes > 0 for item in objects)
        assert any(item.index_name == "idx_bars_interval_timestamp" and item.bytes > 0 for item in indexes)
    else:
        assert objects == []


def test_text_sampling_identifies_repeated_provenance(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    connection = audit.connect_read_only(database)
    master = audit.collect_master_rows(connection)
    tables = audit.table_names(master)
    columns = audit.collect_columns(connection, tables)
    counts = audit.collect_row_counts(connection, tables, exact=True)
    samples = audit.collect_text_samples(connection, tables, columns, counts, sample_rows=300)
    connection.close()
    source_sha = next(item for item in samples if item.table_name == "bars" and item.column_name == "source_sha256")
    assert source_sha.non_null_values > 0
    assert source_sha.distinct_values <= 2
    assert source_sha.repeated_fraction > 0.95
    assert source_sha.projected_text_bytes == 600 * 64


def test_skip_exact_counts_returns_none(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    connection = audit.connect_read_only(database)
    master = audit.collect_master_rows(connection)
    counts = audit.collect_row_counts(connection, audit.table_names(master), exact=False)
    connection.close()
    assert counts["bars"] is None


def test_quick_integrity_check_is_ok(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    connection = audit.connect_read_only(database)
    assert audit.run_integrity_check(connection, "quick") == ["ok"]
    assert audit.run_integrity_check(connection, "skip") == ["SKIPPED"]
    connection.close()


def test_full_audit_writes_reports_and_preserves_database(tmp_path: Path):
    database = tmp_path / "archive" / "history.sqlite"
    database.parent.mkdir()
    create_database(database)
    output = tmp_path / "reports" / "audit-1"
    before = audit.fingerprint_files(database)
    args = audit.build_parser().parse_args(
        [
            "--database",
            str(database),
            "--output-dir",
            str(output),
            "--sample-rows",
            "300",
            "--integrity-check",
            "quick",
        ]
    )
    output_dir, manifest = audit.run_audit(args)
    after = audit.fingerprint_files(database)
    assert before[0] == after[0]
    assert manifest["main_database_unchanged"] is True
    assert manifest["network_access"] is False
    assert manifest["absolute_database_path_persisted"] is False
    expected = {
        "audit_report.txt",
        "audit_manifest.json",
        "database_summary.csv",
        "schema_objects.csv",
        "object_sizes.csv",
        "tables.csv",
        "indexes.csv",
        "columns.csv",
        "text_storage_sample.csv",
    }
    assert expected.issubset({path.name for path in output_dir.iterdir()})
    report = (output_dir / "audit_report.txt").read_text(encoding="utf-8")
    assert "read-only" in report.casefold()
    assert "bars table repeats text" in report
    assert str(database.parent) not in (output_dir / "audit_manifest.json").read_text(encoding="utf-8")


def test_csv_reports_are_parseable(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    output = tmp_path / "audit"
    args = audit.build_parser().parse_args(["--database", str(database), "--output-dir", str(output), "--sample-rows", "30"])
    audit.run_audit(args)
    with (output / "tables.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["table_name"] == "bars" and row["row_count"] == "600" for row in rows)


def test_output_directory_inside_repository_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(audit, "REPOSITORY_ROOT", repository)
    with pytest.raises(audit.AuditError, match="outside"):
        audit.validate_output_directory(repository / "audit")


def test_existing_output_directory_is_not_overwritten(tmp_path: Path):
    output = tmp_path / "audit"
    output.mkdir()
    with pytest.raises(FileExistsError):
        audit.validate_output_directory(output)


def test_negative_sample_rows_rejected(tmp_path: Path):
    database = tmp_path / "history.sqlite"
    create_database(database)
    args = audit.build_parser().parse_args(["--database", str(database), "--sample-rows", "-1"])
    with pytest.raises(audit.AuditError, match="cannot be negative"):
        audit.run_audit(args)


def test_source_has_no_network_import_or_optimization_statements():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "urlopen" not in source
    assert "requests" not in source
    for forbidden in ("VACUUM;", "REINDEX;", "ANALYZE;", "wal_checkpoint("):
        assert forbidden not in source
