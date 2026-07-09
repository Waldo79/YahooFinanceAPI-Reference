import subprocess
import sys
from pathlib import Path

def test_validate_review_classification_script():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_review_classification.py")],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: validated v0.3.8" in result.stdout
