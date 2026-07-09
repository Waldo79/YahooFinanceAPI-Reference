import subprocess
from pathlib import Path

def test_issue_templates_validate():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["python", "scripts/validate_issue_templates.py"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "OK: validated v0.3.7" in result.stdout
