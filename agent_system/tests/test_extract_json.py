"""Tests for `_extract_json` in agent_system/lib/llm_client.py: raw JSON, fenced JSON (including nested braces), and JSON embedded in
prose that itself contains brace characters -- both mathematical
set-builder notation (``{aⁿbⁿ | n≥0}``) and a bare ``{}`` literal.

`llm_client.py` is owned by a parallel change in this branch (structured
outputs) and is out of scope here -- these tests document its
*current* behavior rather than modify it. A ``{...}``/``{}`` literal
appearing in prose BEFORE a fenced ```json block, or inside a JSON string
value, is already handled correctly (the fenced-block regex only looks
inside the fence; a string value's braces are just characters to the JSON
parser). A brace literal appearing before an UNFENCED JSON object is a
known, already-filed limitation -- marked `xfail(strict=False)` so this
suite stays green today and turns into a visible, non-fatal XPASS (not a
silent regression) once that parallel fix lands.
"""

from __future__ import annotations

import unittest

import pytest

from agent_system.lib.llm_client import _extract_json


class TestRawAndFencedJson(unittest.TestCase):

    def test_raw_json(self):
        self.assertEqual(_extract_json('{"status": "success"}'), {"status": "success"})

    def test_fenced_json_block(self):
        text = '```json\n{"status": "success", "confidence": 0.9}\n```'
        self.assertEqual(
            _extract_json(text), {"status": "success", "confidence": 0.9},
        )

    def test_fenced_json_with_nested_braces(self):
        text = (
            'Here is my answer:\n'
            '```json\n'
            '{"status": "success", "evidence": {"verdict": "regular", "dfa": {"states": ["q0"]}}}\n'
            '```\n'
            'Let me know if you need anything else.'
        )
        result = _extract_json(text)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["evidence"]["verdict"], "regular")
        self.assertEqual(result["evidence"]["dfa"]["states"], ["q0"])

    def test_no_json_anywhere_is_none(self):
        self.assertIsNone(_extract_json("just some prose, no JSON here at all"))


class TestJsonEmbeddedInProseWithBraces(unittest.TestCase):
    """Set-builder notation like ``{aⁿbⁿ | n≥0}`` and a bare ``{}`` (e.g. for
    the empty language/word) use literal ``{``/``}`` inside ordinary prose
    (examples from the audit backlog) -- they must not derail the scan away from
    the real JSON object."""

    def test_set_builder_notation_before_fenced_json(self):
        text = (
            "Рассмотрим множество {aⁿbⁿ | n≥0} и докажем накачкой.\n"
            '```json\n{"status": "success", "verdict": "non_regular"}\n```'
        )
        result = _extract_json(text)
        self.assertEqual(result, {"status": "success", "verdict": "non_regular"})

    def test_bare_empty_braces_before_fenced_json(self):
        text = (
            "Пустой язык ∅ = {} в этом случае. Итог:\n"
            '```json\n{"status": "success", "verdict": "non_regular"}\n```'
        )
        result = _extract_json(text)
        self.assertEqual(result, {"status": "success", "verdict": "non_regular"})

    def test_set_builder_notation_inside_a_json_string_value(self):
        """The notation can also appear INSIDE a JSON string value (e.g. a
        `word_family` or `proof` field) rather than in surrounding prose --
        that must parse straight through, since it's inside a JSON string,
        not a bare unescaped brace."""
        text = '{"status": "success", "evidence": {"word_family": "L = {a^n b^n | n>=0}"}}'
        result = _extract_json(text)
        self.assertEqual(
            result["evidence"]["word_family"], "L = {a^n b^n | n>=0}",
        )

    @pytest.mark.xfail(
        reason=(
            "Known limitation (owned by a parallel llm_client.py "
            "change): a brace pair in prose BEFORE an unfenced (no ```json) "
            "JSON object is included in the first-'{'-to-last-'}' substring "
            "the fallback strategy parses, so the whole thing fails to parse "
            "and nothing is returned. Documented here, not fixed here -- "
            "llm_client.py is out of scope for this change."
        ),
        strict=False,
    )
    def test_set_builder_notation_before_unfenced_json(self):
        text = (
            "Язык L = {aⁿbⁿ | n≥0}. Вывод:\n"
            '{"status": "success", "verdict": "non_regular"}'
        )
        result = _extract_json(text)
        self.assertEqual(result, {"status": "success", "verdict": "non_regular"})

    @pytest.mark.xfail(
        reason=(
            "Known limitation (owned by a parallel llm_client.py "
            "change): same first-'{'-to-last-'}' issue as the set-builder "
            "case above, for a bare '{}' before an unfenced JSON object."
        ),
        strict=False,
    )
    def test_bare_empty_braces_before_unfenced_json(self):
        text = (
            "Как известно, пустой язык ∅ = {} в этом случае. Итог:\n"
            '{"status": "success", "verdict": "non_regular"}'
        )
        result = _extract_json(text)
        self.assertEqual(result, {"status": "success", "verdict": "non_regular"})


if __name__ == "__main__":
    unittest.main()
