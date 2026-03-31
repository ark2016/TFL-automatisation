"""Tests for Phase 3 (Lean 4 formalization)."""

import unittest
from pathlib import Path

from agent_system.lib.type_check import check_lean, is_docker_available

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
