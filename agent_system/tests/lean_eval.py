"""Helpers for the Docker-conditional R-Lean statement tests (not a test module).

``lean_ir`` tests for the *new* statement kinds (grammar_filter, reversal
templates, exists_decomposition, union/intersection branches, ...) do more than
"statement + ``sorry`` compiles": they also evaluate the statement's decidable
companion (``LeanStatement.decidable_decl``, a ``def Lb (w : List Letter) :
Bool``) on short words inside Lean and compare the answers with the task's
Python oracle -- the only mechanical evidence that the generated formulation
means what the IR means (docs/VERDICT_POLICY.md R-Lean: a proof of the wrong
statement would be ``verified``).

Shared by ``agent_system``/``cfl_system``/``dcfl_system`` tests
(``from agent_system.tests.lean_eval import ...``); needs the ``tfl-lean4``
image, see ``agent_system.lib.type_check.is_docker_available``.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import tempfile
from pathlib import Path

from agent_system.lib.type_check import (
    DOCKER_IMAGE,
    LEAN_PROJECT_DIR,
    _parse_lean_json_messages,
    _windows_docker_path,
)


def run_lean(text: str, timeout: int = 300) -> list[dict]:
    """Run ``lake env lean --json`` on *text* in the image, return the parsed
    messages (``severity`` / ``data`` / ``pos``). Raises on Docker failure to
    start or timeout; Lean errors are ordinary ``severity == "error"``
    messages."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".lean", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(text)
        tmp = f.name
    try:
        mount = _windows_docker_path(tmp) if os.name == "nt" else tmp
        env = dict(os.environ)
        env["MSYS_NO_PATHCONV"] = "1"
        proc = subprocess.run(
            [
                "docker", "run", "--rm", "--memory=4g", "--cpus=2",
                "-v", f"{mount}:/home/lean/check.lean:ro",
                "-w", LEAN_PROJECT_DIR, DOCKER_IMAGE,
                "timeout", str(timeout), "lake", "env", "lean", "--json",
                "/home/lean/check.lean",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 30, env=env,
        )
        return _parse_lean_json_messages(proc.stdout)
    finally:
        Path(tmp).unlink(missing_ok=True)


def all_words(alphabet: list[str], max_len: int) -> list[str]:
    """Every word over *alphabet* of length 0..max_len, shortest first."""
    out: list[str] = []
    for n in range(max_len + 1):
        out.extend("".join(t) for t in itertools.product(alphabet, repeat=n))
    return out


def lean_word(word: str, sym_map: dict[str, str]) -> str:
    """A Lean ``List Letter`` literal for *word*."""
    return "[" + ", ".join(f"Letter.{sym_map[ch]}" for ch in word) + "]"


def eval_decidable(stmt, words: list[str], sym_map: dict[str, str], timeout: int = 400):
    """Compile ``stmt`` (with ``sorry``) plus its decidable companion and
    ``#eval`` it on *words*. Returns ``(errors, sorry_warnings, answers)``
    where ``answers`` is ``{word: bool}``. ``stmt.decidable_decl`` must exist
    and define ``stmt.decidable_name``."""
    assert stmt.decidable_decl, "statement has no decidable companion"
    lits = ",\n  ".join(lean_word(w, sym_map) for w in words)
    text = (
        stmt.render("sorry").rstrip("\n")
        + "\n\n"
        + stmt.decidable_decl
        + "\n\n"
        + f"#eval String.mk ([\n  {lits}\n].map fun w => if {stmt.decidable_name} w then '1' else '0')\n"
    )
    msgs = run_lean(text, timeout=timeout)
    errors = [m for m in msgs if m.get("severity") == "error"]
    sorry = [m for m in msgs if m.get("severity") == "warning" and "sorry" in str(m.get("data", ""))]
    answers: dict[str, bool] = {}
    infos = [m for m in msgs if m.get("severity") == "information"]
    if infos:
        raw = infos[-1]["data"].strip().strip('"')
        assert set(raw) <= {"0", "1"} and len(raw) == len(words), (len(raw), len(words), raw[:200])
        answers = {w: ch == "1" for w, ch in zip(words, raw)}
    return errors, sorry, answers


def check_against_oracle(
    tc, stmt, alphabet: list[str], oracle, max_len: int, timeout: int = 500,
    extra_words: list[str] | tuple[str, ...] = (),
) -> dict[str, bool]:
    """``#eval`` *stmt*'s decidable companion on every word over *alphabet* up
    to *max_len* (plus *extra_words*, e.g. long members of a sparse language)
    and assert it agrees with *oracle* (``word -> bool``) on all
    of them; also that the statement compiled with only the ``sorry`` warning
    and that the word set is not degenerate (the oracle accepts some words and
    rejects some), so the comparison actually discriminates. Returns the
    answers."""
    from agent_system.lib.lean_ir import alphabet_decl

    _decl, sym_map = alphabet_decl(alphabet)
    words = all_words(alphabet, max_len)
    words += [w for w in extra_words if w not in words]
    errors, sorry, answers = eval_decidable(stmt, words, sym_map, timeout=timeout)
    tc.assertEqual(errors, [], f"Lean errors: {[e.get('data') for e in errors][:3]}")
    tc.assertTrue(sorry, "expected the theorem's `sorry` warning")
    expected = {w: bool(oracle(w)) for w in words}
    tc.assertTrue(any(expected.values()), "oracle accepts none of the sample words")
    tc.assertFalse(all(expected.values()), "oracle accepts all of the sample words")
    wrong = sorted(w for w in words if answers.get(w) != expected[w])
    tc.assertEqual(wrong, [], f"Lean {stmt.decidable_name} disagrees with the oracle on {wrong[:10]}")
    return answers
