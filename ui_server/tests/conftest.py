"""ui_server tests never touch the user's real run registry or settings:
RUNS_DIR / SETTINGS_PATH point into tmp_path and the in-memory registry is
fresh for every test."""
import pytest

from ui_server import server as srv


@pytest.fixture(autouse=True)
def isolated_lab_state(tmp_path, monkeypatch):
    runs = tmp_path / "lab_runs"
    runs.mkdir()
    monkeypatch.setattr(srv, "RUNS_DIR", runs)
    monkeypatch.setattr(srv, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(srv, "_runs", {})
    monkeypatch.setattr(srv, "MAX_CONCURRENT_RUNS", 2)
    yield
