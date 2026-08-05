from __future__ import annotations

import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_IGNORE_PATTERNS = {
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/",
    ".pytest-temp/",
    "captures/local/",
    "config/local/fast_mode_local.json",
}


def _tracked_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def test_required_private_and_generated_paths_are_ignored() -> None:
    ignore_file = REPOSITORY_ROOT / ".gitignore"
    entries = {
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = sorted(REQUIRED_IGNORE_PATTERNS - entries)
    assert not missing, f"Required .gitignore entries are missing: {missing}"


def test_generated_private_or_accidentally_nested_files_are_not_tracked() -> None:
    violations: list[str] = []

    for path in _tracked_paths():
        parts = path.split("/")
        suffix = Path(path).suffix.lower()

        if "__pycache__" in parts or suffix in {".pyc", ".pyo"}:
            violations.append(f"generated Python file: {path}")
        elif ".pytest_cache" in parts or ".pytest-temp" in parts:
            violations.append(f"pytest working file: {path}")
        elif path == "captures/local" or path.startswith("captures/local/"):
            violations.append(f"private local capture: {path}")
        elif path == "config/local/fast_mode_local.json":
            violations.append(f"machine-local capture configuration: {path}")
        elif path == "tests/tools" or path.startswith("tests/tools/"):
            violations.append(f"duplicate utility tree under tests: {path}")
        elif path == "observations/raw/observations" or path.startswith(
            "observations/raw/observations/"
        ):
            violations.append(f"accidentally nested observations tree: {path}")

    assert not violations, "Repository hygiene violations:\n- " + "\n- ".join(
        sorted(violations)
    )
