"""The CFL "transfer" Lean examples (``agent_system/docker/tfl_lean/TflLean/
Examples/``, registry ``agent_system.tests.test_lean_examples.TRANSFER_EXAMPLES``)
against this package's statement renderer and formalizer prompt.

agent_system may not import cfl_system (root CLAUDE.md, "Import direction"),
so the example statements are spelled out there; this module checks that they
are exactly ``cfl_system.lib.lean_ir.render_statement(IR, direction)`` for the
IR the registry names, and that the ``cfl_lean_formalizer`` prompt quotes
those statements and the compiled proof bodies byte for byte. Compilation
itself (``check_lean_file`` -> ``proved``) is
``agent_system/tests/test_lean_examples.py::test_example_proved``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_system.tests.test_lean_examples import TRANSFER_EXAMPLES, TRANSFER_LEMMA_NAMES
from agent_system.tests.test_lean_examples import _proof_body as example_proof_body
from agent_system.tests.test_lean_examples import _statement as example_statement
from cfl_system.lib.lean_ir import render_statement

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
PROMPT = ROOT / "prompts" / "cfl_lean_formalizer.md"

CFL_EXAMPLES = [name for name, spec in TRANSFER_EXAMPLES.items() if spec[1] == "cfl"]


def test_there_are_examples_for_both_directions():
    directions = {TRANSFER_EXAMPLES[n][2] for n in CFL_EXAMPLES}
    assert directions == {"cfl", "non_cfl"}


@pytest.mark.parametrize("name", CFL_EXAMPLES)
def test_example_statement_is_the_renderers(name):
    ir_path, _system, direction, _stmt = TRANSFER_EXAMPLES[name]
    ir = json.loads((REPO / ir_path).read_text(encoding="utf-8"))
    rendered = render_statement(ir, direction)
    assert rendered is not None, ir_path
    expected = example_statement(name)
    for field in ("imports", "alphabet_decl", "language_decl", "theorem_decl", "name"):
        assert getattr(rendered, field) == getattr(expected, field), (name, field)


@pytest.mark.parametrize("name", CFL_EXAMPLES)
def test_prompt_quotes_the_compiled_example(name):
    prompt = PROMPT.read_text(encoding="utf-8")
    assert json.dumps(example_proof_body(name), ensure_ascii=False) in prompt, name
    stmt = example_statement(name)
    for field in ("imports", "alphabet_decl", "language_decl", "theorem_decl"):
        value = getattr(stmt, field)
        assert f'"{field}": {json.dumps(value, ensure_ascii=False)}' in prompt, (name, field)


def test_prompt_names_the_transfer_lemmas_cfl_proofs_use():
    prompt = PROMPT.read_text(encoding="utf-8")
    for name in ("not_isContextFree_of_slice", "replicate_append_inj", "fin3Map_injective"):
        assert name in TRANSFER_LEMMA_NAMES
        assert f"TflLean.{name}" in prompt, name
