"""Tests for Phase 3 (Lean 4 formalization)."""

import unittest
from pathlib import Path
from unittest.mock import patch

from agent_system.lib.type_check import (
    ALLOWED_AXIOMS,
    check_lean,
    check_lean_file,
    compose_lean_file,
    is_docker_available,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DOCKER_DIR = Path(__file__).resolve().parent.parent / "docker"


# ── type_check ──────────────────────────────────────────────────────────────

class TestTypeCheck(unittest.TestCase):

    def test_check_lean_returns_dict(self):
        """check_lean always returns a well-formed result dict."""
        result = check_lean("def x := 1")
        self.assertIn("status", result)
        self.assertIn(result["status"], ("valid", "invalid", "timeout", "skipped"))
        self.assertIn("errors", result)
        self.assertIn("time_seconds", result)

    def test_skipped_has_message(self):
        result = check_lean("def x := 1")
        if result["status"] == "skipped":
            self.assertIsNotNone(result.get("message"))


# ── templates existence ─────────────────────────────────────────────────────

class TestTemplatesExist(unittest.TestCase):

    def test_reglang_exists(self):
        self.assertTrue((TEMPLATES_DIR / "lib" / "RegLang.lean").exists())

    def test_dfa_template_exists(self):
        self.assertTrue((TEMPLATES_DIR / "prove_regular_via_dfa.lean").exists())

    def test_pumping_template_exists(self):
        self.assertTrue((TEMPLATES_DIR / "prove_non_regular_via_pumping.lean").exists())

    def test_nerode_template_exists(self):
        self.assertTrue((TEMPLATES_DIR / "prove_non_regular_via_nerode.lean").exists())


# ── templates compile (requires Docker) ─────────────────────────────────────

@unittest.skipUnless(
    is_docker_available(),
    "Docker with tfl-lean4 image not available"
)
class TestTemplatesCompile(unittest.TestCase):

    def test_dfa_template_compiles(self):
        content = (TEMPLATES_DIR / "prove_regular_via_dfa.lean").read_text(encoding="utf-8")
        result = check_lean(content)
        self.assertEqual(result["status"], "valid", f"Errors: {result.get('errors')}")

    def test_pumping_template_compiles(self):
        content = (TEMPLATES_DIR / "prove_non_regular_via_pumping.lean").read_text(encoding="utf-8")
        result = check_lean(content)
        self.assertEqual(result["status"], "valid", f"Errors: {result.get('errors')}")

    def test_nerode_template_compiles(self):
        content = (TEMPLATES_DIR / "prove_non_regular_via_nerode.lean").read_text(encoding="utf-8")
        result = check_lean(content)
        self.assertEqual(result["status"], "valid", f"Errors: {result.get('errors')}")


# ── Mathlib import (requires Docker image built with the tfl_lean project) ──
#
# The formalizer prompt (agent_system/prompts/formalizer.md) and the LL(k)
# live-run Lean examples import Mathlib.Computability.DFA /
# Mathlib.Computability.RegularExpressions. Skipped unless Docker (and the
# tfl-lean4 image) is available; when it is, this exercises the actual
# `lake env lean` path used by check_lean() against a real Mathlib import,
# not just the self-contained, Mathlib-free templates above.

@unittest.skipUnless(
    is_docker_available(),
    "Docker with tfl-lean4 image not available"
)
class TestMathlibImportCompiles(unittest.TestCase):

    def test_dfa_import_compiles(self):
        code = (
            "import Mathlib.Computability.DFA\n\n"
            "theorem tfl_mathlib_smoke (n : Nat) : n + 0 = n := by simp\n"
        )
        result = check_lean(code, timeout=600)
        self.assertEqual(result["status"], "valid", f"Errors: {result.get('errors')}")


# ── R-Lean v2: compose_lean_file (docs/VERDICT_POLICY.md R-Lean) ───────────

_STATEMENT = {
    "imports": ["import Mathlib.Computability.DFA"],
    "alphabet_decl": "abbrev Alpha := Fin 2",
    "language_decl": "def inLang (w : List Alpha) : Prop := True",
    "theorem_decl": "theorem tfl_main : inLang ([] : List Alpha) := <PROOF>",
    "name": "tfl_main",
}


class TestComposeLeanFile(unittest.TestCase):

    def test_substitutes_placeholder_and_appends_print_axioms(self):
        text = compose_lean_file(_STATEMENT, "by trivial")
        self.assertIn("import Mathlib.Computability.DFA", text)
        self.assertIn("abbrev Alpha := Fin 2", text)
        self.assertIn("def inLang", text)
        self.assertIn("theorem tfl_main : inLang ([] : List Alpha) := by trivial", text)
        self.assertNotIn("<PROOF>", text)
        self.assertTrue(text.rstrip().endswith("#print axioms tfl_main"))

    def test_accepts_a_dict_or_an_object_with_the_same_fields(self):
        class _Statement:
            imports = ["import Foo"]
            alphabet_decl = ""
            language_decl = ""
            theorem_decl = "theorem tfl_main : True := <PROOF>"
            name = "tfl_main"

        text_dict = compose_lean_file(dict(_STATEMENT, theorem_decl="theorem tfl_main : True := <PROOF>"), "trivial")
        text_obj = compose_lean_file(_Statement(), "trivial")
        self.assertIn("theorem tfl_main : True := trivial", text_dict)
        self.assertIn("theorem tfl_main : True := trivial", text_obj)

    def test_list_imports_are_joined_by_newline(self):
        statement = dict(_STATEMENT, imports=["import A", "import B"])
        text = compose_lean_file(statement, "trivial")
        self.assertIn("import A\nimport B", text)

    def test_bare_theorem_decl_gets_assign_by_appended(self):
        """The real `lib.lean_ir.LeanStatement.theorem_decl` has no `:=`
        at all (just `"theorem tfl_main : <Prop>"`) -- the dict/no-`.render`
        fallback path must still produce a complete statement."""
        statement = dict(_STATEMENT, theorem_decl="theorem tfl_main : True")
        text = compose_lean_file(statement, "trivial")
        self.assertIn("theorem tfl_main : True := by\n  trivial", text)

    def test_uses_statement_render_when_available(self):
        """The real contract's `LeanStatement.render(proof)` builds the
        whole file (and appends `:= by\\n  <proof>` itself) -- compose_lean_file
        must defer to it rather than re-assembling the pieces by hand."""
        calls = []

        class _RenderingStatement:
            def render(self, proof):
                calls.append(proof)
                return f"-- rendered\ntheorem tfl_main : True := by\n  {proof}\n"

        text = compose_lean_file(_RenderingStatement(), "trivial")
        self.assertEqual(calls, ["trivial"])
        self.assertIn("-- rendered", text)
        self.assertIn("theorem tfl_main : True := by\n  trivial", text)
        self.assertTrue(text.rstrip().endswith("#print axioms tfl_main"))


# ── R-Lean v2: check_lean_file JSON parsing (mocked `docker run`, no Docker
# actually required) ─────────────────────────────────────────────────────
#
# Fixtures below are verbatim `lake env lean --json` output captured against
# the real tfl-lean4 image (agent_system/docker) for the three cases the
# R-Lean gate cares about: a compile error, a `sorry`, and a clean proof.

_JSON_ERROR = (
    '{"severity":"error","pos":{"line":1,"column":41},"kind":"[anonymous]",'
    '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":1,"column":52},'
    '"data":"unknown identifier \'foo_bar_baz\'","caption":""}\n'
    '{"severity":"information","pos":{"line":3,"column":0},"kind":"[anonymous]",'
    '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":3,"column":6},'
    '"data":"\'tfl_main\' depends on axioms: [sorryAx]","caption":""}\n'
)

_JSON_SORRY = (
    '{"severity":"warning","pos":{"line":1,"column":8},"kind":"[anonymous]",'
    '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":1,"column":16},'
    '"data":"declaration uses \'sorry\'","caption":""}\n'
    '{"severity":"information","pos":{"line":3,"column":0},"kind":"[anonymous]",'
    '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":3,"column":6},'
    '"data":"\'tfl_main\' depends on axioms: [sorryAx]","caption":""}\n'
)

_JSON_PROVED = (
    '{"severity":"information","pos":{"line":3,"column":0},"kind":"[anonymous]",'
    '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":3,"column":6},'
    '"data":"\'tfl_main\' does not depend on any axioms","caption":""}\n'
)

_JSON_DISALLOWED_AXIOM = (
    '{"severity":"information","pos":{"line":3,"column":0},"kind":"[anonymous]",'
    '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":3,"column":6},'
    '"data":"\'tfl_main\' depends on axioms: [propext, myUnsafeAxiom]","caption":""}\n'
)


def _mock_messages_for_proved(text: str) -> str:
    """The `--json` message stream `check_lean_file` needs to see
    status="proved" for *text* -- a `TFL_REPLAY_OK` message at the
    independent-replay `run_cmd`'s own line, and an axiom-free axioms
    message at the `#print axioms` line -- wherever `compose_lean_file`
    actually put those commands in *text*, rather than a second
    hard-coded copy of its layout (code review 2026-09-28: `proved` now
    requires the replay check to confirm too, when *text* carries it)."""
    replay_line = text.count("\n", 0, text.index("run_cmd do")) + 1
    axioms_line = text.count("\n", 0, text.index("#print axioms")) + 1
    return (
        '{"severity":"information","pos":{"line":%d,"column":0},"kind":"[anonymous]",'
        '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":%d,"column":7},'
        '"data":"TFL_REPLAY_OK","caption":""}\n'
        '{"severity":"information","pos":{"line":%d,"column":0},"kind":"[anonymous]",'
        '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":{"line":%d,"column":6},'
        '"data":"\'tfl_main\' does not depend on any axioms","caption":""}\n'
    ) % (replay_line, replay_line, axioms_line, axioms_line)


def _fake_completed_process(stdout: str, returncode: int):
    class _Result:
        pass
    r = _Result()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


class TestCheckLeanFileParsing(unittest.TestCase):

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_error_message_yields_status_error(self, mock_run, _mock_avail):
        mock_run.return_value = _fake_completed_process(_JSON_ERROR, returncode=1)
        result = check_lean_file("theorem tfl_main : 1 = 1 := by exact foo_bar_baz\n\n#print axioms tfl_main\n")
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("foo_bar_baz", result["errors"][0]["data"])

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_sorry_warning_yields_status_has_sorry(self, mock_run, _mock_avail):
        mock_run.return_value = _fake_completed_process(_JSON_SORRY, returncode=0)
        result = check_lean_file("theorem tfl_main : 1 = 1 := by sorry\n\n#print axioms tfl_main\n")
        self.assertEqual(result["status"], "has_sorry")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["axioms"], ["sorryAx"])

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_clean_proof_yields_status_proved(self, mock_run, _mock_avail):
        mock_run.return_value = _fake_completed_process(_JSON_PROVED, returncode=0)
        result = check_lean_file("theorem tfl_main : 1 = 1 := by decide\n\n#print axioms tfl_main\n")
        self.assertEqual(result["status"], "proved")
        self.assertEqual(result["axioms"], [])
        self.assertEqual(result["errors"], [])

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_disallowed_axiom_is_not_proved(self, mock_run, _mock_avail):
        """axioms must be a subset of ALLOWED_AXIOMS -- a proof that
        depends on some other axiom (e.g. a `native_decide`-style escape
        hatch) never reaches `proved`, even with zero compiler errors and
        no `sorry` warning (docs/VERDICT_POLICY.md R-Lean)."""
        mock_run.return_value = _fake_completed_process(_JSON_DISALLOWED_AXIOM, returncode=0)
        result = check_lean_file("theorem tfl_main : 1 = 1 := by native_decide\n\n#print axioms tfl_main\n")
        self.assertEqual(result["status"], "error")
        self.assertIn("myUnsafeAxiom", str(result["errors"]))
        self.assertNotEqual(result["status"], "proved")

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_timeout_wrapper_returncode_yields_status_timeout(self, mock_run, _mock_avail):
        mock_run.return_value = _fake_completed_process("", returncode=124)
        result = check_lean_file("theorem tfl_main : True := trivial\n\n#print axioms tfl_main\n")
        self.assertEqual(result["status"], "timeout")

    @patch("agent_system.lib.type_check.is_docker_available", return_value=False)
    def test_no_docker_yields_status_unavailable(self, _mock_avail):
        result = check_lean_file("theorem tfl_main : True := trivial\n\n#print axioms tfl_main\n")
        self.assertEqual(result["status"], "unavailable")

    def test_allowed_axioms_matches_policy(self):
        self.assertEqual(ALLOWED_AXIOMS, frozenset({"propext", "Classical.choice", "Quot.sound"}))


# ── R-Lean v2: full compose -> check_lean_file round trip (mocked docker) ──

class TestComposeAndCheckRoundTrip(unittest.TestCase):

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_sorry_proof_body_is_has_sorry(self, mock_run, _mock_avail):
        mock_run.return_value = _fake_completed_process(_JSON_SORRY, returncode=0)
        text = compose_lean_file(_STATEMENT, "by sorry")
        result = check_lean_file(text)
        self.assertEqual(result["status"], "has_sorry")

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_proved_proof_body_is_proved(self, mock_run, _mock_avail):
        text = compose_lean_file(_STATEMENT, "by trivial")
        mock_run.return_value = _fake_completed_process(_mock_messages_for_proved(text), returncode=0)
        result = check_lean_file(text)
        self.assertEqual(result["status"], "proved", result)


# ── R-Lean: real lib.lean_ir.render_statement + compose_lean_file ──────────
#
# lib.lean_ir is a parallel agent's contract (see graph.py's lazy import in
# formalize_node) -- these tests exercise the real module when it is present
# on disk, skipping gracefully when it is not, rather than injecting a fake
# (that's what test_graph_edges.py's TestFormalizeNode does, to unit-test
# formalize_node's own control flow independent of lean_ir's availability).

try:
    from agent_system.lib.lean_ir import render_statement as _real_render_statement
except ImportError:
    _real_render_statement = None


@unittest.skipUnless(_real_render_statement is not None, "agent_system.lib.lean_ir not available")
class TestComposeWithRealLeanIr(unittest.TestCase):
    """compose_lean_file against the actual LeanStatement contract
    (imports/alphabet_decl/language_decl/bare theorem_decl + .render)."""

    def test_anbn_non_regular_statement_composes(self):
        ir = {
            "language_spec": {
                "kind": "natural",
                "alphabet": ["a", "b"],
                "description": "{a^n b^n | n >= 0}",
            },
        }
        statement = _real_render_statement(ir, "non_regular")
        self.assertIsNotNone(statement)
        text = compose_lean_file(statement, "sorry")
        self.assertIn("import TflLean", text)
        self.assertIn("inductive Letter", text)
        self.assertIn("theorem tfl_main : ¬ L.IsRegular := by\n  sorry", text)
        self.assertTrue(text.rstrip().endswith("#print axioms tfl_main"))

    def test_ll_direction_is_none(self):
        """docs/VERDICT_POLICY.md R-Lean: 'LL(k) — не формализуется'."""
        self.assertIsNone(_real_render_statement({"language_spec": {}}, "ll"))


# ── R-Lean, real Docker: a^n b^n statement + `sorry` -> has_sorry ──────────
#
# Compiles a real (Mathlib-free, so this stays fast) a^n b^n statement
# through the actual tfl-lean4 image -- skipped unless Docker + the image
# are available, same guard as TestTemplatesCompile above.

@unittest.skipUnless(
    is_docker_available(),
    "Docker with tfl-lean4 image not available"
)
class TestCheckLeanFileRealDockerHasSorry(unittest.TestCase):

    def test_anbn_statement_with_sorry_body_is_has_sorry(self):
        statement = {
            "imports": [],
            "alphabet_decl": "abbrev Alpha := Fin 2",
            "language_decl": (
                "def inLang (w : List Alpha) : Prop :=\n"
                "  exists n, w = List.replicate n 0 ++ List.replicate n 1"
            ),
            "theorem_decl": "theorem tfl_main : inLang ([] : List Alpha) := <PROOF>",
            "name": "tfl_main",
        }
        text = compose_lean_file(statement, "sorry")
        result = check_lean_file(text, timeout=120)
        self.assertEqual(result["status"], "has_sorry", f"result: {result}")
        self.assertEqual(result["errors"], [])


@unittest.skipUnless(
    is_docker_available() and _real_render_statement is not None,
    "Docker with tfl-lean4 image, or agent_system.lib.lean_ir, not available"
)
class TestRealLeanIrEndToEndDocker(unittest.TestCase):
    """The full R-Lean chain against the real IR -> statement translator:
    render_statement -> compose_lean_file -> check_lean_file, `sorry` body
    -> has_sorry, exactly `formalize_node` does (minus the LLM step)."""

    def test_anbn_non_regular_sorry_is_has_sorry(self):
        ir = {
            "language_spec": {
                "kind": "natural",
                "alphabet": ["a", "b"],
                "description": "{a^n b^n | n >= 0}",
            },
        }
        statement = _real_render_statement(ir, "non_regular")
        text = compose_lean_file(statement, "sorry")
        result = check_lean_file(text, timeout=180)
        self.assertEqual(result["status"], "has_sorry", f"result: {result}")
        self.assertEqual(result["errors"], [])


# ── Docker infrastructure files ─────────────────────────────────────────────

class TestDockerFiles(unittest.TestCase):

    def test_dockerfile_exists(self):
        self.assertTrue((DOCKER_DIR / "Dockerfile.lean4").exists())

    def test_build_script_exists(self):
        self.assertTrue((DOCKER_DIR / "build.sh").exists())

    def test_run_check_script_exists(self):
        self.assertTrue((DOCKER_DIR / "run_check.sh").exists())


if __name__ == "__main__":
    unittest.main()
