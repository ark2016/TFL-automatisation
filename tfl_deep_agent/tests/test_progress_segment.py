"""P7 fix: ``hitl._segment`` opens the SQLite checkpointer before the progress
emitter, so a checkpointer that cannot be opened never leaves a
``segment_start`` without its ``segment_end`` in ``progress.jsonl`` (an open
segment in the TFL Lab timeline) nor listeners attached to the ledgers."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langgraph.checkpoint.sqlite")

from tfl_deep_agent import hitl  # noqa: E402
from tfl_deep_agent import progress as progress_mod  # noqa: E402
from tfl_deep_agent.middleware import budget as B  # noqa: E402

from . import hitl_scripts as H  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    monkeypatch.setenv(B.RUNS_DIR_ENV, str(tmp_path / "runs"))


def _events(run_dir) -> list[str]:
    path = run_dir / progress_mod.PROGRESS_FILE
    if not path.is_file():
        return []
    return [json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_checkpointer_failure_leaves_no_open_segment(tmp_path, monkeypatch):
    out = hitl.start_run(tmp_path / "run", H.base_script()["task"], settings=H.example_settings(),
                         mock_script=H.base_script())
    assert out.status == hitl.STATUS_AWAITING
    before = _events(out.run_dir)
    assert before.count("segment_start") == before.count("segment_end") == 1

    opened = []
    real_open = progress_mod.open_emitter

    def spy(*a, **kw):
        opened.append(1)
        return real_open(*a, **kw)

    def broken(_d):
        raise OSError("disk I/O error")

    monkeypatch.setattr(progress_mod, "open_emitter", spy)
    monkeypatch.setattr(hitl, "_open_checkpointer", broken)
    with pytest.raises(OSError, match="disk I/O"):
        hitl.resume_run(out.run_dir, hitl.Decision("approve"))
    assert not opened  # the emitter was never opened: no listeners on the ledgers
    assert _events(out.run_dir) == before  # no segment_start without a segment_end
