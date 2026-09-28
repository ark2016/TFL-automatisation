"""Worked Lean 4 examples for R-Lean (docs/VERDICT_POLICY.md) --
``agent_system/docker/tfl_lean/TflLean/Examples/*.lean`` and
``TflLean/Lemmas.lean``.

Every example file is exactly what the harness itself would type-check: the
statement comes from ``lib.lean_ir`` (or, where the translator has no IR kind
for the language yet, a hand-built ``LeanStatement`` of the same shape), the
proof body is spliced in by ``compose_lean_file``, and ``#print axioms
tfl_main`` closes the file. The two regular-language examples double as the
few-shot examples in ``agent_system/prompts/formalizer.md``.

Two layers:

* always run (no Docker): each file is byte-identical to
  ``compose_lean_file(statement, body)`` for its statement, contains no
  ``sorry``/``native_decide``/``admit``, and the few-shot ``proof_body``
  strings quoted in the formalizer prompt are the ones compiled here;
* Docker-conditional (skipped without the ``tfl-lean4`` image): each example
  goes through ``check_lean_file`` with status ``proved``;
  ``AnBnCnNotCF.lean`` needs langlib (``Language.IsContextFree.pumping``),
  which the current image (Lean/Mathlib v4.18.0) does not have -- it is an
  ``xfail`` until the image is rebuilt from the v4.33.0 + langlib pin, and
  then must be ``proved``. Its Mathlib-only half (refuting the CF pumping
  property) is ``AnBnCnPumpingCore.lean`` and is checked for real.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from agent_system.lib.lean_ir import LeanStatement, render_statement
from agent_system.lib.type_check import (
    ALLOWED_AXIOMS,
    check_lean_file,
    compose_lean_file,
    is_docker_available,
)

ROOT = Path(__file__).resolve().parent.parent
TFL_LEAN = ROOT / "docker" / "tfl_lean" / "TflLean"
EXAMPLES = TFL_LEAN / "Examples"
LEMMAS = TFL_LEAN / "Lemmas.lean"
PROMPT = ROOT / "prompts" / "formalizer.md"

PRINT_AXIOMS = "\n\n#print axioms tfl_main\n"

_ANBN_IR = json.loads((ROOT / "examples" / "eval" / "reg-01.json").read_text(encoding="utf-8"))
_ANBNCN_IR = {
    "task_type": "classify_and_prove",
    "source_text": "{a^n b^n c^n | n >= 0}",
    "language_spec": {
        "kind": "natural",
        "alphabet": ["a", "b", "c"],
        "description": "{a^n b^n c^n | n >= 0}",
    },
}


def _anbncn_decls() -> LeanStatement:
    stmt = render_statement(_ANBNCN_IR, "non_regular")
    assert stmt is not None
    return stmt


def _statement(name: str) -> LeanStatement:
    """The statement each example file must start with."""
    if name == "AnBnNotRegular.lean":
        stmt = render_statement(_ANBN_IR, "non_regular")
        assert stmt is not None
        return stmt
    if name == "EvenA_Regular.lean":
        # lean_ir has no IR kind for letter-count conditions yet (reg-03 style
        # descriptions render to None), so this statement is hand-built in the
        # exact shape render_statement produces.
        return LeanStatement(
            alphabet_decl="inductive Sym\n  | a | b\n  deriving DecidableEq, Fintype, Repr",
            language_decl="def L : Language Sym := {w : List Sym | Even (w.count Sym.a)}",
            theorem_decl="theorem tfl_main : L.IsRegular",
            imports=["import TflLean"],
        )
    base = _anbncn_decls()
    if name == "AnBnCnPumpingCore.lean":
        return LeanStatement(
            alphabet_decl=base.alphabet_decl,
            language_decl=base.language_decl,
            theorem_decl=(
                "theorem tfl_main : ¬ ∃ p : ℕ, ∀ w ∈ L, w.length ≥ p → ∃ u v x y z : List Sym,\n"
                "    w = u ++ v ++ x ++ y ++ z ∧ (v ++ y).length > 0 ∧ (v ++ x ++ y).length ≤ p ∧\n"
                "    ∀ i : ℕ, u ++ (List.replicate i v).flatten ++ x ++ "
                "(List.replicate i y).flatten ++ z ∈ L"
            ),
            imports=["import TflLean"],
        )
    if name == "AnBnCnNotCF.lean":
        # cfl_system.lib.lean_ir.render_statement(_ANBNCN_IR, "non_cfl"); the
        # imports are spelled out here because agent_system must not import
        # cfl_system (root CLAUDE.md, "Import direction"). The alphabet and
        # language come from the shared agent_system pattern_body either way.
        return LeanStatement(
            alphabet_decl=base.alphabet_decl,
            language_decl=base.language_decl,
            theorem_decl="theorem tfl_main : ¬ L.IsContextFree",
            imports=[
                "import TflLean",
                "import Mathlib.Computability.ContextFreeGrammar",
                "import Langlib.Classes.ContextFree.Pumping.Pumping",
                "import Langlib.Classes.ContextFree.Basics.Ogden",
            ],
        )
    raise KeyError(name)


EXAMPLE_NAMES = [
    "AnBnNotRegular.lean",
    "EvenA_Regular.lean",
    "AnBnCnPumpingCore.lean",
    "AnBnCnNotCF.lean",
]
MATHLIB_ONLY = ["AnBnNotRegular.lean", "EvenA_Regular.lean", "AnBnCnPumpingCore.lean"]
FEW_SHOT = ["AnBnNotRegular.lean", "EvenA_Regular.lean"]


def _read(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


def _proof_body(name: str) -> str:
    """The proof body exactly as the formalizer would return it."""
    text = _read(name)
    prefix = _statement(name).render("")[:-1]  # "... := by\n  "
    assert text.startswith(prefix), f"{name}: statement differs from the pipeline's rendering"
    assert text.endswith(PRINT_AXIOMS), f"{name}: must end with `#print axioms tfl_main`"
    return text[len(prefix):-len(PRINT_AXIOMS)]


# ---------------------------------------------------------------------------
# Always run
# ---------------------------------------------------------------------------

def test_examples_dir_is_complete():
    assert sorted(p.name for p in EXAMPLES.glob("*.lean")) == sorted(EXAMPLE_NAMES)


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_example_is_harness_composed(name):
    """File == compose_lean_file(statement, body): the example is in the
    pipeline's own format, not a hand-tuned variant of it."""
    body = _proof_body(name)
    assert compose_lean_file(_statement(name), body) == _read(name)


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_example_has_no_escape_hatches(name):
    body = _proof_body(name)
    for bad in ("sorry", "admit", "native_decide", "ofReduceBool", "axiom "):
        assert bad not in body, f"{name}: proof body contains {bad!r}"


def test_example_files_are_utf8_without_bom():
    # (CRLF is tolerated: core.autocrlf checkouts convert it, and read_text()'s
    # universal newlines hand the harness LF text either way.)
    for p in [*EXAMPLES.glob("*.lean"), LEMMAS]:
        raw = p.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{p.name} has a BOM"
        raw.decode("utf-8")


@pytest.mark.parametrize("name", FEW_SHOT)
def test_few_shot_bodies_in_prompt_match_compiled_examples(name):
    """The formalizer prompt quotes each few-shot proof_body as a JSON string;
    it must be the very body that compiles here, byte for byte."""
    prompt = PROMPT.read_text(encoding="utf-8")
    assert json.dumps(_proof_body(name), ensure_ascii=False) in prompt


def test_prompt_lists_lemmas_that_exist_in_lemmas_file():
    prompt = PROMPT.read_text(encoding="utf-8")
    lemmas = LEMMAS.read_text(encoding="utf-8")
    for name in (
        "not_isRegular_of_distinguishable",
        "IsRegular.pumping",
        "isRegular_of_dfa",
        "replicate_append_replicate_inj",
        "count_flatten_replicate",
        "length_eq_sum_count",
        "CFPumping",
    ):
        assert f"TflLean.{name}" in prompt, name
        assert (f"theorem {name}" in lemmas) or (f"def {name}" in lemmas), name


def test_lemmas_file_has_no_escape_hatches():
    text = LEMMAS.read_text(encoding="utf-8")
    code = text.split("-/", 1)[1]  # skip the module docstring
    for bad in ("sorry", "admit", "native_decide", "ofReduceBool", "\naxiom "):
        assert bad not in code, bad


# ---------------------------------------------------------------------------
# Docker: the examples actually compile to `proved`
# ---------------------------------------------------------------------------

docker = pytest.mark.skipif(
    not is_docker_available(), reason="Docker with the tfl-lean4 image not available"
)


def _assert_proved(result: dict, what: str) -> None:
    assert result["status"] == "proved", f"{what}: {result}"
    assert "sorryAx" not in result["axioms"], f"{what}: {result['axioms']}"
    assert set(result["axioms"]) <= ALLOWED_AXIOMS, f"{what}: {result['axioms']}"


@functools.lru_cache(maxsize=1)
def _langlib_available() -> bool:
    probe = (
        "import Langlib.Classes.ContextFree.Pumping.Pumping\n"
        "#check @Language.IsContextFree.pumping\n"
    )
    return check_lean_file(probe, timeout=300)["status"] == "proved"


@docker
@pytest.mark.parametrize("name", MATHLIB_ONLY)
def test_example_proved(name):
    result = check_lean_file(_read(name), timeout=600)
    _assert_proved(result, name)
    assert result["axioms"], f"{name}: `#print axioms tfl_main` output missing"


@docker
def test_lemmas_file_compiles():
    names = [
        "count_replicate_self", "count_replicate_of_ne", "count_flatten_replicate",
        "length_eq_sum_count", "replicate_append_replicate_inj", "evalFrom_cons",
        "isRegular_of_dfa", "not_isRegular_of_distinguishable", "IsRegular.pumping",
        "flatten_replicate_zero", "flatten_replicate_two",
    ]
    text = LEMMAS.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + "\n".join(
        f"#print axioms TflLean.{n}" for n in names
    ) + "\n"
    # check_lean_file reads the axiom list of the first `#print axioms` target;
    # any `sorry` anywhere would still surface as a warning -> has_sorry.
    result = check_lean_file(text, timeout=600)
    _assert_proved(result, "TflLean/Lemmas.lean")


@docker
def test_anbncn_not_cf_proved_with_langlib():
    if not _langlib_available():
        pytest.xfail(
            "langlib is not in the tfl-lean4 image (Lean/Mathlib v4.18.0): "
            "Language.IsContextFree.pumping is unavailable. The Mathlib-only half "
            "(AnBnCnPumpingCore.lean) is proved; rebuild the image from the "
            "v4.33.0 + langlib pin to check this file for real."
        )
    result = check_lean_file(_read("AnBnCnNotCF.lean"), timeout=600)
    _assert_proved(result, "AnBnCnNotCF.lean")
