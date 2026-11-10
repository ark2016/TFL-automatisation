"""docs/LEAN_LLK.md: the LL(k) definition and its example are real Lean.

The note proposes ``TflLL.IsLLk`` (leftmost derivations over Mathlib's
``ContextFreeGrammar``) and proves ``¬ IsLLk g 1`` for ``S → aS | a``. Its one
``lean`` code block is a complete file with nothing but ``import TflLean``;
here it must stay free of escape hatches (always) and compile to ``proved``
with the standard axioms only (Docker-conditional, the ``tfl-lean4`` image).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_system.lib.type_check import ALLOWED_AXIOMS, check_lean_file, is_docker_available

DOC = Path(__file__).resolve().parents[2] / "docs" / "LEAN_LLK.md"


def _lean_blocks() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    return re.findall(r"```lean\n(.*?)```", text, flags=re.DOTALL)


def test_doc_has_one_complete_lean_file():
    blocks = _lean_blocks()
    assert len(blocks) == 1
    block = blocks[0]
    assert block.startswith("import TflLean\n")
    assert block.rstrip().endswith("#print axioms tfl_main")
    for name in ("def ProducesLeftmost", "abbrev DerivesLeftmost", "def IsLLk",
                 "def IsLLkLanguage", "def IsLLLanguage", "theorem IsLLk.mono",
                 "theorem tfl_main : ¬ TflLL.IsLLk g 1"):
        assert name in block, name


def test_doc_block_has_no_escape_hatches():
    block = _lean_blocks()[0]
    for bad in ("sorry", "admit", "native_decide", "ofReduceBool", "\naxiom "):
        assert bad not in block, bad


@pytest.mark.docker


@pytest.mark.skipif(not is_docker_available(), reason="Docker with the tfl-lean4 image not available")
def test_doc_block_compiles_to_proved():
    result = check_lean_file(_lean_blocks()[0], timeout=600)
    assert result["status"] == "proved", result
    assert result["axioms"] and set(result["axioms"]) <= ALLOWED_AXIOMS, result
