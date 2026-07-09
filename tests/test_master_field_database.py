from pathlib import Path
import subprocess
import sys

def test_master_field_database_validates():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "validate_master_field_database.py"
    csv_file = root / "data" / "master_field_database.csv"
    result = subprocess.run([sys.executable, str(script), str(csv_file)], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
