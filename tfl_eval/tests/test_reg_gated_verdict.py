"""The evaluation exporter must preserve the verdict selected by the REG gate."""

from tfl_eval.runners import extract


def test_gated_verdict_precedes_uncertain_syntax_hint():
    result = {
        "status": "success", "verdict": "non_regular", "confidence": 0.55,
        "evidence": {
            "hypothesis": {"hypothesis": "unknown"},
            "verdict_gate": {"basis": [{"agent": "pumping", "trust": "well_formed"}]},
        },
    }
    assert extract("reg", result)["verdict"] == "non_regular"


def test_gated_fallback_does_not_bypass_unresolved_contradiction():
    result = {
        "status": "partial", "verdict": "non_regular",
        "verdict_gate": {"contradiction": True},
    }
    assert extract("reg", result)["verdict"] is None
