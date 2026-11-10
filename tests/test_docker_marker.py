"""The shared ``docker`` marker: skipped unless TFL_LEAN_TESTS is on (root conftest.py)."""
from __future__ import annotations

import pytest

import conftest as root_conftest


class _Item:
    def __init__(self, docker: bool):
        self.keywords = {"docker": True} if docker else {}
        self.markers: list = []

    def add_marker(self, m):
        self.markers.append(m)


@pytest.mark.parametrize("value", ["", "0", "no", "off"])
def test_docker_items_are_skipped_by_default(monkeypatch, value):
    monkeypatch.setenv("TFL_LEAN_TESTS", value)
    d, plain = _Item(True), _Item(False)
    root_conftest.pytest_collection_modifyitems(None, [d, plain])
    assert len(d.markers) == 1 and "TFL_LEAN_TESTS=1" in d.markers[0].kwargs["reason"]
    assert plain.markers == []


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_docker_items_run_with_tfl_lean_tests(monkeypatch, value):
    monkeypatch.setenv("TFL_LEAN_TESTS", value)
    d = _Item(True)
    root_conftest.pytest_collection_modifyitems(None, [d])
    assert d.markers == []


def test_marker_is_registered_and_lean_workflow_is_on_demand():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert '"docker:' in (root / "pyproject.toml").read_text(encoding="utf-8")
    wf = (root / ".github/workflows/lean.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in wf and "schedule:" not in wf and "TFL_LEAN_TESTS: '1'" in wf
