"""tfl-eval: eval-set harness for the four TFL pipelines (TODO.md §7).

Runs `docs/EVAL_SET.md`'s eval-set tasks through `agent_system`, `cfl_system`,
`dcfl_system` and `ll_system` (mock or `--live`) and reports accuracy,
confidence calibration (Brier score) and related metrics.  See
`tfl_eval/cli.py` for the `tfl-eval` console script.
"""

from __future__ import annotations
