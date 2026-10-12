import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "script", ["train_win_probability_model.py", "plot_win_probability.py"]
)
def test_script_help(script: str) -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / script), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
