"""tfl_deep_agent.config stays in sync with the four pipelines and llm_client."""

from __future__ import annotations

import pytest

import agent_system.config as reg_cfg
import cfl_system.config as cfl_cfg
import dcfl_system.config as dcfl_cfg
import ll_system.config as ll_cfg
from agent_system.lib import llm_client
from tfl_deep_agent import config

PIPELINE_CONFIGS = (reg_cfg, cfl_cfg, dcfl_cfg, ll_cfg)


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)


def test_prices_are_the_shared_llm_client_tables():
    assert config.MODEL_PRICING is llm_client.MODEL_PRICING
    assert config.MODEL_PRICING_LONG_PROMPT is llm_client.MODEL_PRICING_LONG_PROMPT
    assert config.LONG_PROMPT_THRESHOLD_TOKENS == llm_client.LONG_PROMPT_THRESHOLD_TOKENS
    assert config.estimate_cost_usd is llm_client.estimate_cost_usd


def test_every_model_has_a_full_price_card_with_cache_prices():
    for model in {config.OPUS, config.SONNET, config.HAIKU, *config.MODELS.values()}:
        card = config.MODEL_PRICING[model]
        for key in ("input", "output", "cache_read", "cache_write"):
            assert isinstance(card[key], float) and card[key] > 0, (model, key)


def test_model_ids_match_the_pipelines():
    used = set()
    for cfg in PIPELINE_CONFIGS:
        used |= set(cfg.MODELS.values())
        if hasattr(cfg, "JSON_REPAIR_MODEL"):
            used.add(cfg.JSON_REPAIR_MODEL)
    assert {config.OPUS, config.SONNET, config.HAIKU} == used
    assert set(config.MODELS.values()) <= used


def test_conventions_match_the_pipelines():
    for cfg in PIPELINE_CONFIGS:
        assert config.MAX_TOKENS == cfg.MAX_TOKENS, cfg.__name__
        assert config.DEFAULT_EFFORT == cfg.DEFAULT_EFFORT, cfg.__name__
        assert config.REFUSAL_FALLBACK == cfg.REFUSAL_FALLBACK, cfg.__name__
    assert config.TEMPERATURE == reg_cfg.TEMPERATURE
    assert config.MAX_TOKENS_PER_ROLE["lean"] == cfl_cfg.FORMALIZE_MAX_TOKENS
    assert config.LEAN_TIMEOUT == cfl_cfg.LEAN_TIMEOUT


def test_roles_have_model_and_effort_and_match_settings_example():
    import json
    from pathlib import Path

    assert set(config.EFFORT) == set(config.MODELS)
    for effort in config.EFFORT.values():
        assert effort in config.EFFORT_LEVELS
    example = json.loads(
        (Path(config.__file__).parent / "settings.example.json").read_text(encoding="utf-8"))
    assert example["models"]["roles"] == config.MODELS
    assert set(example["models"]["allowed"]) == {config.OPUS, config.SONNET, config.HAIKU}


def test_resolve_model_priority(monkeypatch):
    assert config.resolve_model("main") == config.OPUS
    assert config.resolve_model("intake") == config.SONNET
    assert config.resolve_model("unknown-adhoc") == config.DEFAULT_MODEL
    assert config.resolve_model(None) == config.DEFAULT_MODEL
    assert config.resolve_model("main", "haiku") == config.HAIKU
    assert config.resolve_model("main", " Sonnet ") == config.SONNET
    assert config.resolve_model("main", "claude-haiku-4-5") == "claude-haiku-4-5"
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", " claude-haiku-5-5 ")
    assert config.model_override() == "claude-haiku-5-5"
    assert config.resolve_model("main", "opus") == config.HAIKU
    assert config.resolve_model(None, None) == config.HAIKU
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "sonnet")
    assert config.resolve_model("critic") == config.SONNET


def test_resolve_effort():
    assert config.resolve_effort("main") == "high"
    assert config.resolve_effort("intake") == "medium"
    assert config.resolve_effort("adhoc") == config.DEFAULT_EFFORT
    assert config.resolve_effort(None) == config.DEFAULT_EFFORT
    assert config.resolve_effort("main", "low") == "low"
    with pytest.raises(ValueError, match="unknown effort"):
        config.resolve_effort("main", "extreme")


def test_resolve_max_tokens():
    assert config.resolve_max_tokens("main", config.OPUS) == 64000
    assert config.resolve_max_tokens("lean", config.OPUS) == 128000
    assert config.resolve_max_tokens("lean", "claude-haiku-4-5") == 64000
    assert config.resolve_max_tokens("main", config.OPUS, 4096) == 4096
    assert config.resolve_max_tokens(None) == config.MAX_TOKENS
    with pytest.raises(ValueError):
        config.resolve_max_tokens("main", config.OPUS, 0)


# ---------------------------------------------------------------------------
# Unpriced model ids fail closed; worst-case cost
# ---------------------------------------------------------------------------

def test_unpriced_model_ids_are_refused(monkeypatch):
    with pytest.raises(config.UnknownModelError, match="no price card"):
        config.resolve_model("main", "claude-opus-4-1")
    with pytest.raises(config.UnknownModelError):
        config.resolve_model("main", "claude-opsu-5-5")
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-9-9")
    with pytest.raises(config.UnknownModelError):
        config.resolve_model("main")


def test_dated_snapshot_ids_resolve_and_keep_their_spelling():
    assert config.resolve_model("main", "claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"
    assert config.pricing_key("claude-haiku-4-5-20251001") == "claude-haiku-4-5"


def test_worst_case_usd():
    # Opus: 1000 prompt tokens at the cache-write rate ($5 > $4 input) + 64000 out at $20.
    assert config.worst_case_usd(config.OPUS, 1000, 64000) == pytest.approx(0.005 + 1.28)
    assert config.worst_case_usd(config.SONNET, 0, 64000) == pytest.approx(0.64)
    # Haiku 5.5: the long-prompt card above the threshold, output included.
    n = config.LONG_PROMPT_THRESHOLD_TOKENS
    short = config.worst_case_usd(config.HAIKU, n, 1000)
    long_ = config.worst_case_usd(config.HAIKU, n + 1, 1000)
    assert short == pytest.approx((n * 0.125 + 1000 * 0.50) / 1e6)
    assert long_ == pytest.approx(((n + 1) * 0.625 + 1000 * 2.50) / 1e6)
    with pytest.raises(config.UnknownModelError):
        config.worst_case_usd("claude-opus-4-1", 0, 1)
    with pytest.raises(ValueError):
        config.worst_case_usd(config.OPUS, -1, 1)


@pytest.mark.xfail(strict=True, reason=(
    "OPEN user decision before P2 (DEV_NOTES.md, P0 fix): with MAX_TOKENS 64000 / lean 128000 "
    "the worst case of one main call exceeds reserve_for_report_usd and of one lean call "
    "exceeds per_subagent_max_usd. Remove this xfail when per-role ceilings are decided."))
def test_default_ceilings_fit_the_example_budgets():
    import json
    from pathlib import Path

    budget = json.loads((Path(config.__file__).parent / "settings.example.json")
                        .read_text(encoding="utf-8"))["budget"]
    main = config.resolve_model("main")
    lean = config.resolve_model("lean")
    report_call = config.worst_case_usd(main, 0, config.resolve_max_tokens("main", main))
    lean_call = config.worst_case_usd(lean, 0, config.resolve_max_tokens("lean", lean))
    assert report_call <= budget["reserve_for_report_usd"], report_call
    assert lean_call <= budget["per_subagent_max_usd"], lean_call
