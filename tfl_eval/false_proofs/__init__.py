"""Corpus of deliberately false agent proofs, replayed through the four
pipelines' own verifiers and verdict gates (mock only, no LLM).

``python -m tfl_eval.false_proofs`` prints, per pipeline, how many false
proofs reach trust above ``well_formed`` and how many end in a wrong
verdict; ``tfl_eval/tests/test_false_proofs.py`` pins those numbers. See
``docs/SEMANTIC_CHECK.md`` for what the numbers mean for a symbolic checker.
"""

from tfl_eval.false_proofs.corpus import CASES, PIPELINES, FalseProofCase
from tfl_eval.false_proofs.replay import ReplayResult, check_counterexample, replay
from tfl_eval.false_proofs.runner import run_cases, summarize

__all__ = [
    "CASES", "PIPELINES", "FalseProofCase", "ReplayResult",
    "check_counterexample", "replay", "run_cases", "summarize",
]
