from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "history_compact_xlsx_export.py"
SPEC = importlib.util.spec_from_file_location("history_compact_xlsx_export_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def create_compact(path: Path, *, verified: bool = True, symbols: int = 3, bars_each: int = 4) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE symbols (symbol_id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE);
        CREATE TABLE intervals (interval_id INTEGER PRIMARY KEY, interval TEXT NOT NULL UNIQUE);
        CREATE TABLE run_ids (run_key INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE);
        CREATE TABLE sources (source_id INTEGER PRIMARY KEY, source_file TEXT NOT NULL, source_sha256 BLOB NOT NULL, UNIQUE(source_file, source_sha256));
        CREATE TABLE event_types (event_type_id INTEGER PRIMARY KEY, event_type TEXT NOT NULL UNIQUE);
        CREATE TABLE runs (run_key INTEGER PRIMARY KEY, mode TEXT NOT NULL, interval_id INTEGER NOT NULL,
          overlap_days INTEGER NOT NULL, started_at_utc TEXT NOT NULL, completed_at_utc TEXT,
          status TEXT NOT NULL, input_file_name TEXT NOT NULL, requested_symbols INTEGER NOT NULL,
          completed_symbols INTEGER NOT NULL, run_folder_name TEXT NOT NULL, utility_version TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE symbol_state (symbol_id INTEGER NOT NULL, interval_id INTEGER NOT NULL,
          last_bar_timestamp INTEGER, last_checked_at_utc TEXT, last_success_run_key INTEGER,
          baseline_run_key INTEGER, full_refresh_required INTEGER NOT NULL,
          full_refresh_reason TEXT NOT NULL, PRIMARY KEY(symbol_id, interval_id)) WITHOUT ROWID;
        CREATE TABLE bars (symbol_id INTEGER NOT NULL, interval_id INTEGER NOT NULL, timestamp_utc INTEGER NOT NULL,
          open REAL, high REAL, low REAL, close REAL, adjclose REAL, volume INTEGER,
          first_seen_run_key INTEGER NOT NULL, last_seen_run_key INTEGER NOT NULL, source_id INTEGER NOT NULL,
          PRIMARY KEY(symbol_id, interval_id, timestamp_utc)) WITHOUT ROWID;
        CREATE TABLE bar_revisions (revision_id INTEGER PRIMARY KEY, run_key INTEGER NOT NULL,
          symbol_id INTEGER NOT NULL, interval_id INTEGER NOT NULL, timestamp_utc INTEGER NOT NULL,
          detected_at_utc TEXT NOT NULL, action TEXT NOT NULL, changed_fields_json TEXT NOT NULL,
          old_values_json TEXT, new_values_json TEXT);
        CREATE TABLE events (symbol_id INTEGER NOT NULL, interval_id INTEGER NOT NULL,
          event_type_id INTEGER NOT NULL, event_timestamp_utc INTEGER NOT NULL, event_key TEXT NOT NULL,
          event_json TEXT NOT NULL, first_seen_run_key INTEGER NOT NULL, last_seen_run_key INTEGER NOT NULL,
          source_id INTEGER NOT NULL, PRIMARY KEY(symbol_id, interval_id, event_type_id, event_key)) WITHOUT ROWID;
        CREATE TABLE event_revisions (revision_id INTEGER PRIMARY KEY, run_key INTEGER NOT NULL,
          symbol_id INTEGER NOT NULL, interval_id INTEGER NOT NULL, event_type_id INTEGER NOT NULL,
          event_timestamp_utc INTEGER NOT NULL, detected_at_utc TEXT NOT NULL, action TEXT NOT NULL,
          old_event_json TEXT, new_event_json TEXT);
        CREATE TABLE symbol_runs (run_key INTEGER NOT NULL, task_key TEXT NOT NULL, task_sequence INTEGER NOT NULL,
          symbol_id INTEGER NOT NULL, interval_id INTEGER NOT NULL, mode TEXT NOT NULL, full_range INTEGER NOT NULL,
          request_start_epoch INTEGER, request_end_epoch INTEGER NOT NULL, classification TEXT NOT NULL,
          http_status INTEGER, bars_returned INTEGER NOT NULL, new_bars INTEGER NOT NULL,
          revised_bars INTEGER NOT NULL, unchanged_bars INTEGER NOT NULL, missing_bars INTEGER NOT NULL,
          events_returned INTEGER NOT NULL, new_events INTEGER NOT NULL, revised_events INTEGER NOT NULL,
          unchanged_events INTEGER NOT NULL, full_refresh_required INTEGER NOT NULL,
          full_refresh_reason TEXT NOT NULL, raw_source_id INTEGER, raw_file_fallback TEXT,
          raw_sha256_fallback TEXT, elapsed_ms INTEGER NOT NULL, attempts INTEGER NOT NULL,
          error_description TEXT, PRIMARY KEY(run_key, task_key)) WITHOUT ROWID;
        """
    )
    status = "VERIFIED_COMPLETE" if verified else "BUILDING"
    con.executemany("INSERT INTO meta VALUES(?,?)", [
        ("schema_name", exporter.COMPACT_SCHEMA_NAME),
        ("schema_version", exporter.COMPACT_SCHEMA_VERSION),
        ("build_status", status),
    ])
    con.execute("INSERT INTO archive_meta VALUES('source','test')")
    con.execute("INSERT INTO intervals VALUES(1,'1d')")
    con.execute("INSERT INTO run_ids VALUES(1,'run-1')")
    con.execute("INSERT INTO runs VALUES(1,'baseline',1,30,'2026-08-01T00:00:00Z','2026-08-01T01:00:00Z','COMPLETE','symbols.csv',?,?, 'run-folder','test')", (symbols, symbols))
    con.execute("INSERT INTO event_types VALUES(1,'DIVIDEND')")
    epoch = 1_700_000_000
    for index in range(symbols):
        symbol_id = index + 1
        symbol = f"SYM{index:03d}"
        source_file = f"runs/run-folder/raw/{symbol}.json.gz"
        digest = hashlib.sha256(symbol.encode()).digest()
        con.execute("INSERT INTO symbols VALUES(?,?)", (symbol_id, symbol))
        con.execute("INSERT INTO sources VALUES(?,?,?)", (symbol_id, source_file, digest))
        last = epoch + (bars_each - 1) * 86_400
        con.execute("INSERT INTO symbol_state VALUES(?,1,?,'2026-08-01T01:00:00Z',1,1,0,'')", (symbol_id, last))
        for j in range(bars_each):
            ts = epoch + j * 86_400
            con.execute("INSERT INTO bars VALUES(?,1,?,?,?,?,?,?,?,1,1,?)", (
                symbol_id, ts, 10.0 + j, 11.0 + j, 9.0 + j, 10.5 + j, 10.25 + j, 1000 + j, symbol_id,
            ))
        event_ts = epoch + 86_400
        con.execute("INSERT INTO events VALUES(?,1,1,?,?,?,1,1,?)", (
            symbol_id, event_ts, f"event-{symbol}", json.dumps({"amount": index + 0.5}), symbol_id,
        ))
    con.execute("INSERT INTO bar_revisions VALUES(1,1,1,1,?,'2026-08-01T01:00:00Z','REVISED','[\"close\"]','{\"close\":10}','{\"close\":11}')", (epoch,))
    con.execute("INSERT INTO event_revisions VALUES(1,1,1,1,1,?,'2026-08-01T01:00:00Z','REVISED','{}','{\"amount\":1}')", (epoch,))
    con.commit()
    con.close()


def workbook_sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    return [node.attrib["name"] for node in root.findall("m:sheets/m:sheet", NS)]


def sheet_row_count(path: Path, sheet_index: int) -> int:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet_index}.xml"))
    return len(root.findall("m:sheetData/m:row", NS))


def test_source_connection_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db)
    monkeypatch.setattr(exporter, "REPOSITORY_ROOT", tmp_path / "repo")
    con = exporter.connect_read_only(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM bars")
    con.close()


def test_full_export_writes_valid_xlsx_and_preserves_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db, symbols=3, bars_each=4)
    repo = tmp_path / "repo"; repo.mkdir(); monkeypatch.setattr(exporter, "REPOSITORY_ROOT", repo)
    output = tmp_path / "export"
    args = exporter.build_parser().parse_args([
        "--database", str(db), "--output-dir", str(output), "--include-revisions",
    ])
    before = db.read_bytes()
    out, manifest = exporter.run_export(args)
    assert out == output
    assert db.read_bytes() == before
    assert manifest["source_database_unchanged"] is True
    assert manifest["source_counts"] == {"symbols": 3, "bars": 12, "events": 3, "bar_revisions": 1, "event_revisions": 1}
    workbook = output / "Yahoo_Long_History.xlsx"
    assert workbook.is_file()
    assert workbook_sheet_names(workbook) == ["Summary", "Symbols", "Bars", "Events", "BarRevisions", "EventRevisions"]
    assert sheet_row_count(workbook, 3) == 13
    assert (output / "export-manifest.json").is_file()
    assert (output / "export-report.txt").is_file()


def test_symbol_filter_and_date_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db, symbols=3, bars_each=4)
    monkeypatch.setattr(exporter, "REPOSITORY_ROOT", tmp_path / "repo")
    output = tmp_path / "filtered"
    args = exporter.build_parser().parse_args([
        "--database", str(db), "--output-dir", str(output), "--symbols", "SYM001",
        "--start-date", "2023-11-15", "--through-date", "2023-11-16",
    ])
    _, manifest = exporter.run_export(args)
    assert manifest["source_counts"]["symbols"] == 1
    assert manifest["source_counts"]["bars"] == 2
    assert manifest["source_counts"]["events"] == 1


def test_data_sheets_split_before_excel_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db, symbols=2, bars_each=4)
    monkeypatch.setattr(exporter, "REPOSITORY_ROOT", tmp_path / "repo")
    output = tmp_path / "split"
    args = exporter.build_parser().parse_args([
        "--database", str(db), "--output-dir", str(output), "--max-data-rows-per-sheet", "3",
    ])
    _, manifest = exporter.run_export(args)
    names = [sheet["name"] for sheet in manifest["sheets"]]
    assert "Bars" in names and "Bars_002" in names and "Bars_003" in names
    assert all(sheet["data_rows"] <= 3 for sheet in manifest["sheets"] if sheet["name"].startswith("Bars"))


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db)
    monkeypatch.setattr(exporter, "REPOSITORY_ROOT", tmp_path / "repo")
    args = exporter.build_parser().parse_args(["--database", str(db), "--dry-run", "--smoke"])
    output, plan = exporter.run_export(args)
    assert output is None
    assert plan["files_written"] == 0
    assert plan["network_requests_sent"] == 0
    assert plan["source_database_unchanged"] is True


def test_unverified_database_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db, verified=False)
    monkeypatch.setattr(exporter, "REPOSITORY_ROOT", tmp_path / "repo")
    args = exporter.build_parser().parse_args(["--database", str(db), "--dry-run"])
    with pytest.raises(exporter.ExportError, match="not verified"):
        exporter.run_export(args)


def test_output_inside_repository_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db)
    repo = tmp_path / "repo"; repo.mkdir(); monkeypatch.setattr(exporter, "REPOSITORY_ROOT", repo)
    args = exporter.build_parser().parse_args([
        "--database", str(db), "--output-dir", str(repo / "export"),
    ])
    with pytest.raises(exporter.ExportError, match="outside"):
        exporter.run_export(args)


def sheet_values(path: Path, sheet_index: int) -> list[list[str | None]]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet_index}.xml"))
    output: list[list[str | None]] = []
    for row in root.findall("m:sheetData/m:row", NS):
        values: list[str | None] = []
        for cell in row.findall("m:c", NS):
            if cell.attrib.get("t") == "inlineStr":
                node = cell.find("m:is/m:t", NS)
            else:
                node = cell.find("m:v", NS)
            values.append(node.text if node is not None else None)
        output.append(values)
    return output


def test_symbols_sheet_counts_respect_date_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir()
    db = archive / exporter.COMPACT_DATABASE_FILENAME; create_compact(db, symbols=3, bars_each=4)
    monkeypatch.setattr(exporter, "REPOSITORY_ROOT", tmp_path / "repo")
    output = tmp_path / "filtered-symbol-summary"
    args = exporter.build_parser().parse_args([
        "--database", str(db), "--output-dir", str(output), "--symbols", "SYM001",
        "--start-date", "2023-11-15", "--through-date", "2023-11-16",
    ])
    exporter.run_export(args)
    rows = sheet_values(output / "Yahoo_Long_History.xlsx", 2)
    assert rows[1][0:3] == ["SYM001", "1d", "2"]
    assert rows[1][5] == "1"


def test_pre_1970_timestamps_and_xml_character_filtering() -> None:
    assert exporter.epoch_to_datetime(-1) == exporter.datetime(1969, 12, 31, 23, 59, 59, tzinfo=exporter.timezone.utc)
    cleaned = exporter.xml_text('A\x00B\ufffeC<&"')
    assert cleaned == "ABC&lt;&amp;&quot;"
