"""settings.py: defaults, load / merge / validate / save, CLI and UI overrides."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tfl_deep_agent import config
from tfl_deep_agent import settings as S

PKG = Path(S.__file__).parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    monkeypatch.delenv(S.SETTINGS_PATH_ENV, raising=False)


def write(path: Path, data) -> Path:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


# -- defaults -----------------------------------------------------------------

def test_defaults_are_the_plan_numbers():
    s = S.default_settings()
    # per_task_usd: PLAN.md had 5.00; user decision 2026-11-08 ("один прогон ограничен ~10$")
    assert s.budget.model_dump() == {"per_task_usd": 10.0, "per_day_usd": 20.0,
                                     "per_subagent_max_usd": 2.0, "reserve_for_report_usd": 1.00}
    assert s.models.allowed == ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    assert s.models.roles == config.MODELS
    assert s.models.opus_needs_reason is True
    assert s.limits.model_dump() == {"max_model_calls": 80, "max_tool_calls": 300,
                                     "max_parallel": 4, "max_spawn_depth": 2}
    assert s.hitl.model_dump() == {"formalization": True, "report": True, "execute": False}
    assert s.intake.model_dump() == {"max_pdf_pages": 20, "max_file_mb": 10}


def test_example_file_equals_defaults():
    example = json.loads((PKG / "settings.example.json").read_text(encoding="utf-8"))
    assert example == S.DEFAULTS
    assert S.validate_settings(example, merge_defaults=False).to_dict() == S.DEFAULTS


def test_default_path_is_the_gitignored_settings_json():
    assert S.DEFAULT_SETTINGS_PATH == PKG / "settings.json"
    lines = (PKG.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "tfl_deep_agent/settings.json" in lines


def test_missing_file_gives_defaults(tmp_path):
    assert S.load_settings(tmp_path / "nope.json").to_dict() == S.DEFAULTS


def test_env_var_points_at_another_file(tmp_path, monkeypatch):
    p = write(tmp_path / "s.json", {"budget": {"per_task_usd": 1.5}})
    monkeypatch.setenv(S.SETTINGS_PATH_ENV, str(p))
    assert S.settings_path() == p
    assert S.load_settings().budget.per_task_usd == 1.5


# -- merge and validation -------------------------------------------------------

def test_partial_file_merges_over_defaults(tmp_path):
    p = write(tmp_path / "s.json", {"budget": {"per_task_usd": 2.0},
                                    "models": {"roles": {"main": "sonnet"}},
                                    "hitl": {"execute": True}})
    s = S.load_settings(p)
    assert s.budget.per_task_usd == 2.0
    assert s.budget.per_day_usd == 20.0                    # untouched default
    assert s.models.roles["main"] == config.SONNET         # alias -> id
    assert s.models.roles["prover"] == config.OPUS         # other roles kept
    assert s.hitl.execute is True and s.hitl.report is True


def test_lists_are_replaced_not_merged(tmp_path):
    roles = {r: "haiku" for r in S.KNOWN_ROLES}
    p = write(tmp_path / "s.json", {"models": {"allowed": ["haiku"], "roles": roles}})
    s = S.load_settings(p)
    assert s.models.allowed == [config.HAIKU]
    assert set(s.models.roles.values()) == {config.HAIKU}


def test_utf8_bom_is_accepted(tmp_path):
    p = tmp_path / "s.json"
    p.write_bytes("﻿".encode() + json.dumps({"budget": {"per_task_usd": 3}}).encode())
    assert S.load_settings(p).budget.per_task_usd == 3.0


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", "\"x\""])
def test_unreadable_file_raises_not_defaults(tmp_path, text):
    p = write(tmp_path / "s.json", text)
    with pytest.raises(S.SettingsError, match="settings file"):
        S.load_settings(p)


@pytest.mark.parametrize("data, needle", [
    ({"budgett": {}}, "budgett"),
    ({"budget": {"per_task": 1}}, "per_task"),
    ({"budget": {"per_task_usd": "5"}}, "per_task_usd"),
    ({"budget": {"per_task_usd": -1}}, ">= 0"),
    ({"budget": {"per_task_usd": 0}}, "per_task_usd must be > 0"),
    ({"budget": {"per_task_usd": 30}}, "larger than budget.per_day_usd"),
    ({"budget": {"reserve_for_report_usd": -1}}, "reserve_for_report_usd"),
    ({"budget": {"per_subagent_max_usd": 0}}, "per_subagent_max_usd must be > 0"),
    ({"limits": {"max_parallel": 0}}, "max_parallel"),
    ({"limits": {"max_parallel": True}}, "max_parallel"),
    ({"limits": {"max_spawn_depth": 1.5}}, "max_spawn_depth"),
    ({"hitl": {"report": "yes"}}, "report"),
    ({"intake": {"max_file_mb": 0}}, "max_file_mb"),
    ({"models": {"allowed": []}}, "at least one"),
    ({"models": {"allowed": ["gpt-4"]}}, "no price card"),
    ({"models": {"roles": {"main": "claude-opus-9"}}}, "no price card"),
    ({"models": {"roles": {"wizard": "haiku"}}}, "unknown role"),
    ({"models": {"allowed": ["haiku", "sonnet"]}}, "not in models.allowed"),
])
def test_invalid_values_are_rejected_with_the_key(data, needle):
    with pytest.raises(S.SettingsError) as exc:
        S.validate_settings(data)
    assert needle in str(exc.value)
    assert exc.value.problems


def test_every_problem_is_listed():
    with pytest.raises(S.SettingsError) as exc:
        S.validate_settings({"limits": {"max_parallel": 0, "max_tool_calls": 0}, "extra": 1})
    assert len(exc.value.problems) == 3


def test_allowed_aliases_and_duplicates_are_normalised():
    s = S.validate_settings({"models": {"allowed": ["opus", "claude-opus-5-5", "sonnet", "haiku"]}})
    assert s.models.allowed == [config.OPUS, config.SONNET, config.HAIKU]
    assert s.is_allowed("sonnet") and s.is_allowed(config.HAIKU)


def test_assignment_is_validated():
    s = S.default_settings()
    with pytest.raises(ValidationError):
        s.limits.max_parallel = 0


# -- helpers ------------------------------------------------------------------

def test_model_for_role(monkeypatch):
    s = S.validate_settings({"models": {"roles": {"critic": "sonnet"}}})
    assert s.model_for_role("critic") == config.SONNET
    assert s.model_for_role("main") == config.OPUS
    assert s.model_for_role(None) == config.DEFAULT_MODEL
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-5-5")
    assert s.model_for_role("critic") == config.HAIKU


# -- overrides --------------------------------------------------------------------

def test_parse_override_pairs():
    got = S.parse_override_pairs([
        "budget.per_task_usd=3", "models.roles.main=sonnet", 'models.allowed=["haiku","sonnet","opus"]',
        "hitl.execute=true", "limits.max_parallel = 2",
    ])
    assert got == {"budget": {"per_task_usd": 3}, "models": {"roles": {"main": "sonnet"},
                                                             "allowed": ["haiku", "sonnet", "opus"]},
                   "hitl": {"execute": True}, "limits": {"max_parallel": 2}}


@pytest.mark.parametrize("bad", ["budget", "=3", "budget..x=1", ".x=1"])
def test_parse_override_pairs_rejects_malformed(bad):
    with pytest.raises(S.SettingsError):
        S.parse_override_pairs([bad])


def test_parse_override_conflict():
    with pytest.raises(S.SettingsError, match="conflicts"):
        S.parse_override_pairs(["budget=1", "budget.per_task_usd=2"])


def test_overrides_on_load_and_apply(tmp_path):
    p = write(tmp_path / "s.json", {"budget": {"per_task_usd": 2.0}})
    s = S.load_settings(p, overrides={"budget": {"per_task_usd": 1.0}})
    assert s.budget.per_task_usd == 1.0
    s2 = S.apply_overrides(s, S.parse_override_pairs(["hitl.formalization=false"]))
    assert s2.hitl.formalization is False and s.hitl.formalization is True   # original unchanged
    with pytest.raises(S.SettingsError, match="override"):
        S.apply_overrides(s, {"budget": {"per_task_usd": 100}})


# -- save -------------------------------------------------------------------------

def test_save_roundtrip_is_atomic_and_complete(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    s = S.apply_overrides(S.default_settings(), {"budget": {"per_task_usd": 4.25}})
    assert S.save_settings(s, p) == p
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["budget"]["per_task_usd"] == 4.25
    assert set(on_disk) == set(S.DEFAULTS)                  # the full document is written
    assert S.load_settings(p) == s
    assert [x.name for x in p.parent.iterdir()] == ["settings.json"]   # no temp file left


def test_save_validates_a_mapping(tmp_path):
    with pytest.raises(S.SettingsError):
        S.save_settings({"budget": {"per_task_usd": -1}}, tmp_path / "s.json")
    assert not (tmp_path / "s.json").exists()
    S.save_settings({"budget": {"per_task_usd": 1}}, tmp_path / "s.json")
    assert S.load_settings(tmp_path / "s.json").budget.per_task_usd == 1.0


def test_json_schema_has_every_section():
    schema = S.settings_json_schema()
    assert set(schema["properties"]) == {"budget", "models", "limits", "hitl", "intake"}
