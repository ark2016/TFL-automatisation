"""Regression tests for the code review 2026-09-28 findings against
``agent_system/lib/type_check.py`` (docs/VERDICT_POLICY.md R-Lean, both
blockers on this file):

(a) elaborator-level escape hatches (`run_tac` + `Lean.addDecl` with
    `debug.skipKernelTC`, `elab`/`macro`/`run_cmd`/`run_meta`, ...) that can
    make a compile-and-`#print axioms` check alone report a false theorem
    as axiom-free and error-free;
(b) `#print axioms` message spoofing/suppression (`#exit` truncating the
    file before it runs at all, a `trace` call forging an earlier "does
    not depend on any axioms" message, a non-zero exit with no `error`
    message among what a killed process managed to print).

No Docker is required for most of this file (`scan_proof_body` and the
fast rejection path in `check_lean_file` are pure Python); the two tests
that replay the actual PoC from the review through a real kernel are
Docker-conditional, same guard as the rest of the Lean test suite.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_system.lib.type_check import (
    check_lean_file,
    compose_lean_file,
    is_docker_available,
    scan_proof_body,
)

_STATEMENT = {
    "imports": ["import Mathlib.Computability.DFA"],
    "alphabet_decl": "abbrev Alpha := Fin 2",
    "language_decl": "def inLang (w : List Alpha) : Prop := True",
    "theorem_decl": "theorem tfl_main : inLang ([] : List Alpha) := <PROOF>",
    "name": "tfl_main",
}


class TestScanProofBody(unittest.TestCase):

    def test_clean_bodies_pass(self):
        for body in ("trivial", "by decide", "simp\n  omega", "exact absurd h hn"):
            self.assertEqual(scan_proof_body(body), [], body)

    def test_run_tac_skip_kernel_tc_exploit_is_rejected(self):
        """The exact PoC from the review (type_check.py, blocker #1):
        `run_tac` + `Lean.addDecl` under `debug.skipKernelTC` to smuggle a
        declaration the kernel never checked, then have `tfl_main`
        reference it."""
        exploit = (
            "run_tac do\n"
            "    let g ← Lean.Elab.Tactic.getMainGoal\n"
            "    let t ← g.getType\n"
            "    Lean.withOptions (fun o => o.setBool `debug.skipKernelTC true) <|\n"
            "      Lean.addDecl (.thmDecl {name := `tfl_main.fake, levelParams := [], "
            "type := t, value := Lean.mkConst ``True.intro})\n"
            "    g.assign (Lean.mkConst `tfl_main.fake)"
        )
        violations = scan_proof_body(exploit)
        self.assertIn("run_tac", violations)
        self.assertIn("Lean namespace reference (Lean.*)", violations)

    def test_each_forbidden_token_is_individually_caught(self):
        cases = {
            "run_cmd": "run_cmd do pure ()",
            "run_meta": "run_meta pure ()",
            "elab": "elab foo : tactic => pure ()",
            "macro": "macro \"foo\" : tactic => `(tactic| skip)",
            "syntax": "syntax \"foo\" : tactic",
            "Lean namespace reference (Lean.*)": "exact Lean.mkConst `foo",
            "set_option": "set_option maxHeartbeats 0",
            "unsafe": "unsafe def foo := 1",
            "implemented_by": "attribute [implemented_by foo] bar",
            "extern attribute": "attribute [extern \"foo\"] bar",
            "native_decide": "native_decide",
            "trace": "trace \"hello\"",
            "#exit": "trivial\n#exit",
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                self.assertIn(label, scan_proof_body(body), body)

    def test_exit_truncation_forgery_is_rejected(self):
        """Code review, blocker #2: `#exit` right after the proof would
        truncate the composed file before the appended `#print axioms`
        (and replay check) ever ran."""
        self.assertIn("#exit", scan_proof_body("native_decide\n\n#exit"))

    def test_trace_forged_axiom_message_is_rejected(self):
        """Code review, blocker #2: a `trace` call printing a fake
        "does not depend on any axioms" message earlier than the real
        one. `trace` alone is banned regardless of its argument."""
        body = 'trace "\'tfl_main\' does not depend on any axioms"'
        self.assertIn("trace", scan_proof_body(body))

    def test_command_token_anywhere_is_rejected(self):
        self.assertIn("'#' command token", scan_proof_body("trivial\n  #eval 1"))

    def test_zero_indent_line_is_rejected(self):
        """A later line starting at column 0 would dedent out of the `by`
        block appended `:= by\\n  <proof>` and be parsed as a new
        top-level command."""
        self.assertIn("line at zero indentation", scan_proof_body("trivial\naxiom evil : False"))

    def test_first_line_indentation_is_not_required(self):
        """The first line inherits compose_lean_file's own `":= by\\n  "`
        -- it must not itself be flagged for "starting at column 0"."""
        self.assertEqual(scan_proof_body("trivial"), [])
        self.assertEqual(scan_proof_body("simp\n  <;> omega"), [])

    def test_comments_do_not_trigger_false_positives(self):
        """Code review verification: a real, hand-verified proof
        (AnBnCnPumpingCore.lean) has a comment `-- ... has length #a + #b
        + #c.` -- a `#` preceded by whitespace, inside a `--` comment.
        Comments are invisible to Lean's parser and must not themselves
        trip the lexical gate (this was an actual false positive hit
        while regenerating the example files for this fix)."""
        body = (
            "-- Every word over {a, b, c} has length #a + #b + #c.\n"
            "  have hlen : True := trivial\n"
            "  trivial"
        )
        self.assertEqual(scan_proof_body(body), [])

    def test_comment_only_zero_indent_line_is_not_flagged(self):
        """A whole-line `--` comment at column 0 is harmless (comments
        carry no indentation-sensitivity for Lean's parser) and must not
        be flagged as "dedenting out of the `by` block"."""
        body = "simp\n-- a comment sitting at column 0\n  omega"
        self.assertEqual(scan_proof_body(body), [])

    def test_block_comment_hiding_a_token_is_still_ignored(self):
        body = "trivial\n  /- run_tac would go here but doesn't -/"
        self.assertEqual(scan_proof_body(body), [])

    def test_non_string_input_is_rejected_not_raised(self):
        self.assertEqual(scan_proof_body(None), ["proof body is not a string"])
        self.assertEqual(scan_proof_body(123), ["proof body is not a string"])


class TestComposeLeanFileRejectsViolations(unittest.TestCase):

    def test_violating_body_never_reaches_the_composed_text(self):
        exploit = "run_tac do\n    Lean.addDecl (.thmDecl {})"
        text = compose_lean_file(_STATEMENT, exploit)
        # The actual dangerous source is never substituted into the file;
        # only the violated pattern *labels* (e.g. "run_tac") appear, and
        # only inside the leading rejection marker/comment.
        self.assertNotIn("Lean.addDecl", text)
        self.assertNotIn(".thmDecl", text)
        self.assertTrue(text.lstrip().startswith("-- TFL_PROOF_BODY_REJECTED:"))
        self.assertIn("run_tac", text)

    def test_clean_body_composes_normally(self):
        text = compose_lean_file(_STATEMENT, "trivial")
        self.assertFalse(text.lstrip().startswith("-- TFL_PROOF_BODY_REJECTED:"))
        self.assertIn("trivial", text)


class TestCheckLeanFileFastRejection(unittest.TestCase):

    @patch("agent_system.lib.type_check.subprocess.run")
    def test_rejected_marker_short_circuits_before_docker(self, mock_run):
        """`check_lean_file` must recognize compose_lean_file's own
        rejection marker and never invoke Docker/subprocess for it --
        even when Docker happens to be unavailable, this must not be
        confused with status="unavailable"."""
        text = compose_lean_file(_STATEMENT, "run_tac do pure ()")
        result = check_lean_file(text)
        self.assertEqual(result["status"], "error")
        self.assertIn("run_tac", str(result["errors"]))
        mock_run.assert_not_called()


class TestCheckLeanFileReplayGating(unittest.TestCase):
    """Mocked `--json` output -- no Docker required (mirrors
    test_phase3.py's TestCheckLeanFileParsing fixtures)."""

    def _msg(self, line: int, data: str, severity: str = "information") -> str:
        return (
            '{"severity":"%s","pos":{"line":%d,"column":0},"kind":"[anonymous]",'
            '"keepFullRange":false,"fileName":"/home/lean/check.lean","endPos":'
            '{"line":%d,"column":6},"data":"%s","caption":""}\n'
        ) % (severity, line, line, data)

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_replay_fail_blocks_proved_even_with_clean_axioms(self, mock_run, _mock_avail):
        """The core guarantee: a file with compose_lean_file's replay
        marker whose axioms message is clean but whose replay message
        reports failure must never reach `proved`."""
        text = compose_lean_file(_STATEMENT, "trivial")
        replay_line = text.count("\n", 0, text.index("run_cmd do")) + 1
        axioms_line = text.count("\n", 0, text.index("#print axioms")) + 1
        stdout = (
            self._msg(replay_line, "TFL_REPLAY_FAIL: kernel rejected it", severity="error")
            + self._msg(axioms_line, "'tfl_main' does not depend on any axioms")
        )

        class _Result:
            pass
        r = _Result()
        r.stdout, r.stderr, r.returncode = stdout, "", 1
        mock_run.return_value = r

        result = check_lean_file(text)
        self.assertEqual(result["status"], "error", result)

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_missing_replay_message_blocks_proved(self, mock_run, _mock_avail):
        """Clean axioms, no replay message at all (e.g. it never ran) --
        must not reach `proved`."""
        text = compose_lean_file(_STATEMENT, "trivial")
        axioms_line = text.count("\n", 0, text.index("#print axioms")) + 1

        class _Result:
            pass
        r = _Result()
        r.stdout = self._msg(axioms_line, "'tfl_main' does not depend on any axioms")
        r.stderr, r.returncode = "", 0
        mock_run.return_value = r

        result = check_lean_file(text)
        self.assertEqual(result["status"], "error", result)

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_forged_earlier_axioms_message_is_ignored(self, mock_run, _mock_avail):
        """Code review, blocker #2 (position-based matching): a message
        claiming "does not depend on any axioms" at the *wrong* line
        (as a `trace` forgery earlier in the file would produce) must be
        ignored -- only the message at #print axioms' own line counts."""
        text = compose_lean_file(_STATEMENT, "trivial")
        axioms_line = text.count("\n", 0, text.index("#print axioms")) + 1

        class _Result:
            pass
        r = _Result()
        r.stdout = (
            self._msg(1, "'tfl_main' does not depend on any axioms")  # forged, wrong line
            + self._msg(axioms_line, "'tfl_main' depends on axioms: [propext, myEvilAxiom]")
        )
        r.stderr, r.returncode = "", 0
        mock_run.return_value = r

        result = check_lean_file(text)
        self.assertEqual(result["status"], "error", result)
        self.assertIn("myEvilAxiom", str(result["errors"]))

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_nonzero_exit_with_only_clean_messages_is_error_not_proved(self, mock_run, _mock_avail):
        """Code review, blocker #2 (part 3): an OOM kill (returncode 137)
        that still printed some clean-looking JSON messages before dying
        must not fall through to `proved`."""
        text = compose_lean_file(_STATEMENT, "trivial")
        replay_line = text.count("\n", 0, text.index("run_cmd do")) + 1
        axioms_line = text.count("\n", 0, text.index("#print axioms")) + 1
        stdout = (
            self._msg(replay_line, "TFL_REPLAY_OK")
            + self._msg(axioms_line, "'tfl_main' does not depend on any axioms")
        )

        class _Result:
            pass
        r = _Result()
        r.stdout, r.stderr, r.returncode = stdout, "", 137
        mock_run.return_value = r

        result = check_lean_file(text)
        self.assertEqual(result["status"], "error", result)

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_everything_confirmed_is_proved(self, mock_run, _mock_avail):
        text = compose_lean_file(_STATEMENT, "trivial")
        replay_line = text.count("\n", 0, text.index("run_cmd do")) + 1
        axioms_line = text.count("\n", 0, text.index("#print axioms")) + 1
        stdout = (
            self._msg(replay_line, "TFL_REPLAY_OK")
            + self._msg(axioms_line, "'tfl_main' does not depend on any axioms")
        )

        class _Result:
            pass
        r = _Result()
        r.stdout, r.stderr, r.returncode = stdout, "", 0
        mock_run.return_value = r

        result = check_lean_file(text)
        self.assertEqual(result["status"], "proved", result)

    @patch("agent_system.lib.type_check.is_docker_available", return_value=True)
    @patch("agent_system.lib.type_check.subprocess.run")
    def test_text_without_replay_marker_is_not_held_to_it(self, mock_run, _mock_avail):
        """A trusted, hand-written text that never went through
        compose_lean_file (no replay marker at all, e.g. TflLean/Lemmas.lean's
        own compile check) reaches `proved` on axioms alone, unchanged
        from before this fix."""
        text = "theorem tfl_main : 1 = 1 := by decide\n\n#print axioms tfl_main\n"

        class _Result:
            pass
        r = _Result()
        r.stdout = self._msg(3, "'tfl_main' does not depend on any axioms")
        r.stderr, r.returncode = "", 0
        mock_run.return_value = r

        result = check_lean_file(text)
        self.assertEqual(result["status"], "proved", result)


@unittest.skipUnless(
    is_docker_available(), "Docker with tfl-lean4 image not available"
)
class TestReplayCatchesTheRealExploitViaDocker(unittest.TestCase):
    """The independent kernel replay (defense in depth for whatever
    `scan_proof_body` misses) against the *exact* PoC from the review,
    through the real tfl-lean4 image. We bypass `scan_proof_body` here on
    purpose (calling `check_lean_file` directly on hand-composed text) to
    exercise barrier (b) in isolation -- in the real pipeline this
    specific PoC is already stopped by barrier (a), `scan_proof_body`,
    before ever reaching Docker (see TestScanProofBody above)."""

    def test_skip_kernel_tc_exploit_fails_replay_for_a_false_theorem(self):
        text = (
            "import Lean.Replay\n"
            "import Lean.Elab.Command\n"
            "import TflLean\n\n"
            "theorem tfl_main : False := by\n"
            "  run_tac do\n"
            "    let g ← Lean.Elab.Tactic.getMainGoal\n"
            "    let t ← g.getType\n"
            "    Lean.withOptions (fun o => o.setBool `debug.skipKernelTC true) <|\n"
            "      Lean.addDecl (.thmDecl {name := `tfl_main.fake, levelParams := [], "
            "type := t, value := Lean.mkConst ``True.intro})\n"
            "    g.assign (Lean.mkConst `tfl_main.fake)\n\n"
            "open Lean in\n"
            "run_cmd do\n"
            "  let env ← getEnv\n"
            "  let mut newConsts : Std.HashMap Name ConstantInfo := {}\n"
            "  for (n, ci) in env.constants.toList do\n"
            "    if !env.const2ModIdx.contains n then\n"
            "      newConsts := newConsts.insert n ci\n"
            "  try\n"
            "    let importEnv ← importModules env.imports {} (trustLevel := 0)\n"
            "    let _ ← Lean.Environment.replay newConsts importEnv\n"
            "    logInfo \"TFL_REPLAY_OK\"\n"
            "  catch e =>\n"
            "    logError s!\"TFL_REPLAY_FAIL: {(← e.toMessageData.toString)}\"\n\n"
            "#print axioms tfl_main\n"
        )
        result = check_lean_file(text, timeout=600)
        # Without the replay check, this PoC's plain compile + `#print
        # axioms` looks like a clean, axiom-free proof of `False`.
        self.assertEqual(result["status"], "error", result)
        self.assertIn("replay", str(result["errors"]).lower())


if __name__ == "__main__":
    unittest.main()
