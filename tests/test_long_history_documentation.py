from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
OVERVIEW = REPO_ROOT / "docs" / "high-volume" / "LONG_HISTORY_OVERVIEW.md"
CAPTURE_GUIDE = (
    REPO_ROOT / "tools" / "capture-utility" / "HISTORY_CAPTURE_README.md"
)
XLSX_GUIDE = (
    REPO_ROOT / "docs" / "high-volume" / "LONG_HISTORY_XLSX_EXPORT.md"
)
EXCLUSIONS = (
    REPO_ROOT / "data" / "high-volume" / "long_history_exclusions_v0_1.csv"
)
VALIDATION = (
    REPO_ROOT
    / "docs"
    / "verification"
    / "long-history-full-xlsx-export-validation-2026-08-06.md"
)
RELEASE_READINESS = (
    REPO_ROOT / "docs" / "high-volume" / "LONG_HISTORY_RELEASE_READINESS.md"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing documentation target: {path}"
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_root_readme_exposes_long_history_subsystem() -> None:
    text = _read(README)

    assert "## Long-history subsystem" in text
    assert "`docs/high-volume/LONG_HISTORY_OVERVIEW.md`" in text
    assert "`tools/capture-utility/HISTORY_CAPTURE_README.md`" in text
    assert "`docs/high-volume/LONG_HISTORY_XLSX_EXPORT.md`" in text
    assert "`tools/capture-utility/history_compact_xlsx_export.py`" in text


def test_root_readme_preserves_release_and_study_status() -> None:
    text = _read(README)

    assert "Formal release:       v0.4.3" in text
    assert "Development line:     v0.5.0-draft" in text
    assert (
        "Long-history development does not change the formal v0.4.3 release"
        in text
    )


def test_overview_separates_fast_mode_and_long_history() -> None:
    text = _read(OVERVIEW)

    flat = _flat(text)
    assert "Fast mode remains the high-volume current-snapshot workflow." in flat
    assert (
        "Long History remains the persistent historical-archive workflow."
        in flat
    )
    assert "daily, weekly, and monthly Yahoo Chart history" in flat


def test_overview_locks_database_and_export_safety_boundary() -> None:
    text = _read(OVERVIEW)

    flat = _flat(text)
    assert "`history.sqlite`" in flat
    assert "`history_compact.sqlite`" in flat
    assert "Do not move, replace, rename, or delete either database." in flat
    assert "SQLite URI `mode=ro`" in flat
    assert "`PRAGMA query_only=ON`" in flat
    assert "never opens `history.sqlite`" in flat
    assert "performs no network requests" in flat


def test_overview_links_existing_long_history_materials() -> None:
    text = _read(OVERVIEW)

    for path in (CAPTURE_GUIDE, XLSX_GUIDE, EXCLUSIONS):
        assert path.is_file(), f"missing linked Long-history material: {path}"

    assert "`tools/capture-utility/HISTORY_CAPTURE_README.md`" in text
    assert "`docs/high-volume/LONG_HISTORY_XLSX_EXPORT.md`" in text
    assert "`data/high-volume/long_history_exclusions_v0_1.csv`" in text


def test_full_export_validation_records_verified_totals_and_safety() -> None:
    text = _read(VALIDATION)
    flat = _flat(text)

    assert "**PASS.**" in text
    assert "1,537" in text
    assert "11,034,219" in text
    assert "256,040" in text
    assert "739,149,341" in text
    assert "95ddabc95b57241172d168880878f1f85f35ab909f632e252a031bfdc15af848" in text
    assert "12 Bars worksheets sum exactly to 11,034,219 rows" in flat
    assert "original `history.sqlite` database was not opened" in flat
    assert "compact database fingerprint was unchanged" in flat


def test_release_readiness_is_bounded_and_does_not_claim_a_tag() -> None:
    text = _read(RELEASE_READINESS)
    flat = _flat(text)

    assert "no tag or GitHub Release has been created" in flat
    assert "`long-history-v0.1.0`" in text
    assert "only after explicit authorization" in flat
    assert "project-wide formal release" in flat
    assert "v0.4.3" in text
    assert "v0.5.0-draft" in text


def test_release_documents_exclude_private_paths_and_binary_evidence() -> None:
    for path in (VALIDATION, RELEASE_READINESS):
        text = _read(path)
        assert "C:\\Users\\" not in text
        assert "JMW" not in text
        assert "Downloads\\YAHOO" not in text
        assert not any(
            item.suffix.casefold() in {".sqlite", ".sqlite3", ".db", ".xlsx"}
            for item in path.parent.rglob("*")
            if item.is_file() and item not in {path}
        )
