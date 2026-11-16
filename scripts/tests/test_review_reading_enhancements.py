"""Run offline DOM and chart geometry tests without navigating a browser."""
from pathlib import Path
import subprocess


def test_reading_enhancements_dom_and_geometry():
    target = Path(__file__).with_name('review_reading_enhancements.test.cjs')
    result = subprocess.run(['node', '--test', str(target)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
