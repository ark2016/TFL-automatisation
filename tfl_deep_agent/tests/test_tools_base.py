"""P1: tools/base.py -- ToolResult, trust helpers, limits, timeouts, registry,
LangChain conversion. No LLM, no network."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from agent_system.lib import claim_verifier
from tfl_deep_agent.tools import base
from tfl_deep_agent.tools.base import (
    CONFIDENCE_CAPS,
    GROUP_MAX_TRUST,
    GROUP_MODULES,
    GROUPS,
    TRUST_LEVELS,
    Alphabet,
    Bounds,
    Deadline,
    InputTooLarge,
    ToolArgs,
    ToolError,
    ToolInputError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolTimeout,
    ToolUnsupported,
    Word,
    check_alphabet,
    check_enumeration,
    check_grammar_size,
    check_json_size,
    check_word,
    check_words,
    clamp_trust,
    confidence_cap,
    enumeration_size,
    max_len_within,
    run_with_timeout,
    strongest_trust,
    to_jsonable,
    trust_at_least,
    trust_rank,
)

from . import _tool_helpers as helpers


# ---------------------------------------------------------------------------
# Trust taxonomy vs docs/VERDICT_POLICY.md
# ---------------------------------------------------------------------------

def test_trust_levels_follow_policy_order():
    assert TRUST_LEVELS == ("refuted", "not_verified", "well_formed", "bounded_pass", "verified")
    ranks = [trust_rank(t) for t in TRUST_LEVELS]
    assert ranks == sorted(ranks)
    assert trust_rank(None) == trust_rank("not_verified") == trust_rank("bogus")


def test_confidence_caps_are_the_policy_object():
    assert CONFIDENCE_CAPS is claim_verifier.CONFIDENCE_CAPS
    assert dict(CONFIDENCE_CAPS) == {"verified": 0.98, "bounded_pass": 0.85,
                                     "well_formed": 0.55, "not_verified": 0.40}
    assert confidence_cap("refuted") == confidence_cap(None) == 0.40


def test_trust_at_least_excludes_refuted():
    assert trust_at_least("verified", "bounded_pass")
    assert trust_at_least("bounded_pass", "bounded_pass")
    assert not trust_at_least("well_formed", "bounded_pass")
    assert not trust_at_least("refuted", "not_verified")


def test_clamp_and_strongest():
    assert clamp_trust("verified", "bounded_pass") == "bounded_pass"
    assert clamp_trust("well_formed", "bounded_pass") == "well_formed"
    assert clamp_trust("refuted", "well_formed") == "refuted"
    assert clamp_trust("refuted", "verified", allow_refuted=False) == "not_verified"
    assert clamp_trust("nonsense", "verified") == "not_verified"
    assert strongest_trust(["refuted", "well_formed", "bounded_pass"]) == "bounded_pass"
    assert strongest_trust(["refuted"]) == "not_verified"
    assert strongest_trust([]) == "not_verified"


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

def test_success_and_failure_shapes():
    ok = ToolResult.success({"accepted": True}, trust="verified", reason="exact DFA run")
    assert ok.ok and ok.trust == "verified" and ok.error is None
    bad = ToolResult.failure("boom", kind="unsupported")
    assert not bad.ok and bad.trust == "not_verified" and bad.error_kind == "unsupported"
    assert "not evidence" in bad.trust_reason


def test_failure_cannot_carry_positive_trust():
    with pytest.raises(ValidationError):
        ToolResult(ok=False, trust="verified", trust_reason="x", error="e")
    with pytest.raises(ValidationError):
        ToolResult(ok=False, trust="not_verified", trust_reason="x")  # no error text
    with pytest.raises(ValidationError):
        ToolResult(ok=True, trust="well_formed", trust_reason="x", error="e")


def test_bounded_pass_requires_bounds_and_reason_required():
    with pytest.raises(ValidationError):
        ToolResult.success(None, trust="bounded_pass", reason="oracle agreed")
    r = ToolResult.success(None, trust="bounded_pass", reason="oracle agreed",
                           bounds={"max_len": 8, "n_words": 40, "max_steps": 1000})
    assert r.bounds.max_len == 8 and r.bounds.model_extra == {"max_steps": 1000}
    with pytest.raises(ValidationError):
        ToolResult.success(None, trust="well_formed", reason="")


def test_result_is_frozen_and_rejects_unknown_fields():
    r = ToolResult.success(1, trust="well_formed", reason="parsed")
    with pytest.raises(ValidationError):
        r.trust = "verified"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ToolResult(ok=True, trust="well_formed", trust_reason="x", confidence=0.9)


def test_data_is_made_jsonable():
    r = ToolResult.success({"first": {"S": {"b", "a", ""}}, ("A", "a"): (1, 2), "x": float("nan")},
                           trust="verified", reason="exact")
    assert r.data == {"first": {"S": ["", "a", "b"]}, "A,a": [1, 2], "x": None}
    json.dumps(r.model_dump(mode="json"))


def test_to_jsonable_misc():
    class M(BaseModel):
        a: set[int]

    assert to_jsonable(M(a={2, 1})) == {"a": [1, 2]}
    assert to_jsonable(frozenset({"b", "a"})) == ["a", "b"]
    class Opaque:
        def __str__(self) -> str:
            return "opaque"

    assert to_jsonable(Opaque()) == "opaque"


def test_to_content_shortens_large_data():
    r = ToolResult.success({"words": ["ab" * 50] * 500}, trust="well_formed", reason="listed")
    text = r.to_content(max_chars=2000)
    assert len(text) < 2600
    payload = json.loads(text)
    assert payload["data"]["truncated"] is True and payload["trust"] == "well_formed"
    small = json.loads(ToolResult.success([1], trust="well_formed", reason="r").to_content())
    assert small["data"] == [1] and "error" not in small


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

def test_word_and_words_limits():
    assert check_word("abba", alphabet=["a", "b"]) == "abba"
    assert check_word("") == ""
    with pytest.raises(InputTooLarge):
        check_word("a" * 101)
    with pytest.raises(ToolInputError) as e:
        check_word("abc", alphabet=["a", "b"])
    assert "['c']" in str(e.value) and e.value.kind == "invalid_input"
    with pytest.raises(InputTooLarge) as e2:
        check_words(["a"] * 501)
    assert e2.value.kind == "input_too_large"
    with pytest.raises(InputTooLarge):
        check_words(["ab", "a" * 20], max_len=10)


def test_alphabet_limits():
    assert check_alphabet(["a", "b"]) == ["a", "b"]
    for bad in ([], ["a", "a"], ["a", ""]):
        with pytest.raises(ToolInputError):
            check_alphabet(bad)
    with pytest.raises(InputTooLarge):
        check_alphabet([chr(97 + i) for i in range(17)])


def test_grammar_size_limits():
    g = {"terminals": ["a"], "nonterminals": ["S"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["a", "S"]}, {"lhs": "S", "rhs": []}]}
    check_grammar_size(g)
    with pytest.raises(InputTooLarge):
        check_grammar_size({**g, "rules": [{"lhs": "S", "rhs": []}] * 301})
    with pytest.raises(InputTooLarge):
        check_grammar_size({**g, "rules": [{"lhs": "S", "rhs": ["a"] * 25}]})
    with pytest.raises(ToolInputError):
        check_grammar_size({**g, "rules": [{"lhs": "S", "rhs": "aS"}]})


def test_enumeration_helpers_match_dcfl_rule():
    assert enumeration_size(2, 3) == 1 + 2 + 4 + 8
    assert enumeration_size(1, 5) == 6
    assert enumeration_size(0, 5) == 1
    assert check_enumeration(2, 10) == 2047
    with pytest.raises(InputTooLarge):
        check_enumeration(3, 10)
    from dcfl_system.lib.oracle_verifier import _full_enumeration_max_len

    for size in (1, 2, 3, 4, 5):
        assert max_len_within(size, 3000, 10) == _full_enumeration_max_len(size)


def test_json_size_limit():
    assert check_json_size({"a": "б"}) == len('{"a":"б"}'.encode())
    with pytest.raises(InputTooLarge):
        check_json_size({"x": "a" * 100}, limit=50)


def test_annotated_types_in_args_schema():
    class A(ToolArgs):
        word: Word
        alphabet: Alphabet

    A(word="ab", alphabet=["a", "b"])
    with pytest.raises(ValidationError):
        A(word="a" * 101, alphabet=["a"])
    with pytest.raises(ValidationError):
        A(word="a", alphabet=[])
    with pytest.raises(ValidationError):
        A(word="a", alphabet=["a"], trust="verified")  # extra="forbid"


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

def test_deadline():
    d = Deadline(0.05)
    assert not d.expired() and d.remaining() > 0
    d.check()
    time.sleep(0.07)
    assert d.expired() and d.remaining() == 0
    with pytest.raises(ToolTimeout):
        d.check("enumeration")
    forever = Deadline(None)
    assert not forever.expired() and forever.remaining() == float("inf")


def test_thread_timeout_returns_raises_and_times_out():
    assert run_with_timeout(helpers.double, 21, timeout_s=5) == 42
    with pytest.raises(ValueError):
        run_with_timeout(helpers.raise_value_error, timeout_s=5)
    release = threading.Event()
    before = base.abandoned_thread_count()
    t0 = time.monotonic()
    with pytest.raises(ToolTimeout):
        run_with_timeout(release.wait, 10, timeout_s=0.1)
    assert time.monotonic() - t0 < 2
    assert base.abandoned_thread_count() == before + 1
    release.set()
    for _ in range(100):
        if base.abandoned_thread_count() == before:
            break
        time.sleep(0.01)
    assert base.abandoned_thread_count() == before
    assert run_with_timeout(helpers.double, 1, timeout_s=None) == 2
    assert run_with_timeout(helpers.double, 1, timeout_s=1, mode="none") == 2


def test_thread_mode_refuses_when_too_many_runaways(monkeypatch):
    monkeypatch.setattr(base, "MAX_ABANDONED_THREADS", base.abandoned_thread_count() + 1)
    release = threading.Event()
    try:
        with pytest.raises(ToolTimeout):
            run_with_timeout(release.wait, 10, timeout_s=0.05)
        with pytest.raises(base.ToolBusy):
            run_with_timeout(helpers.double, 1, timeout_s=1)
    finally:
        release.set()


def test_process_mode_value_error_and_kill():
    assert run_with_timeout(helpers.double, 4, timeout_s=60, mode="process") == 8
    with pytest.raises(ToolUnsupported, match="no oracle"):
        run_with_timeout(helpers.raise_unsupported, timeout_s=60, mode="process")
    with pytest.raises(ToolError) as e:
        run_with_timeout(helpers.raise_value_error, timeout_s=60, mode="process")
    assert e.value.kind == "lib_error" and "ValueError: bad grammar" in str(e.value)
    t0 = time.monotonic()
    with pytest.raises(ToolTimeout, match="killed"):
        run_with_timeout(helpers.sleep_then, 30, timeout_s=1.0, mode="process")
    assert time.monotonic() - t0 < 15


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class WordArgs(ToolArgs):
    word: Word
    alphabet: Alphabet = ["a", "b"]


def _reg_with(func, **kw) -> ToolRegistry:
    reg = ToolRegistry()
    reg.tool(**{"group": "words", "max_trust": "bounded_pass", **kw})(func)
    return reg


def even_a(args: WordArgs) -> ToolResult:
    """Is the number of a's even? (test oracle)"""
    check_word(args.word, alphabet=args.alphabet)
    return ToolResult.success({"in_language": args.word.count("a") % 2 == 0},
                              trust="bounded_pass", reason="exact count",
                              bounds={"n_words": 1, "max_len": len(args.word)})


def claims_verified(args: WordArgs) -> ToolResult:
    """Over-claims trust."""
    return ToolResult.success(None, trust="verified", reason="claims full check")


def refutes(args: WordArgs) -> ToolResult:
    """Finds a counterexample."""
    return ToolResult.success({"counterexample": args.word}, trust="refuted", reason="z not in L")


def returns_dict(args: WordArgs):
    """Wrong return type."""
    return {"trust": "verified"}


def raises_lib(args: WordArgs) -> ToolResult:
    """Lib error."""
    raise KeyError("rules")


def raises_unsupported(args: WordArgs) -> ToolResult:
    """No oracle."""
    raise ToolUnsupported("kind 'natural' has no oracle", data={"kind": "natural"})


def builds_invalid(args: WordArgs) -> ToolResult:
    """Builds a bounded_pass without bounds."""
    return ToolResult.success(None, trust="bounded_pass", reason="no bounds")


def test_decorator_infers_name_schema_description_and_returns_func():
    reg = ToolRegistry()
    assert reg.tool(group="words", max_trust="bounded_pass")(even_a) is even_a
    spec = reg.get("even_a")
    assert spec.args_schema is WordArgs and spec.group == "words"
    assert spec.description.startswith("Is the number")
    assert reg.names() == ["even_a"] and reg.names("reg") == [] and "even_a" in reg and len(reg) == 1


def test_invoke_success_stamps_tool_and_elapsed():
    r = _reg_with(even_a).invoke("even_a", {"word": "aab"})
    assert r.ok and r.tool == "even_a" and r.data == {"in_language": True}
    assert r.trust == "bounded_pass" and r.elapsed_s is not None and r.elapsed_s >= 0


def test_model_cannot_inject_trust():
    reg = _reg_with(even_a)
    r = reg.invoke("even_a", {"word": "aa", "trust": "verified"})
    assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"
    assert "trust" in r.error


def test_ceiling_lowers_over_claims():
    r = _reg_with(claims_verified, max_trust="well_formed").invoke("claims_verified", {"word": "a"})
    assert r.ok and r.trust == "well_formed" and "lowered from verified" in r.trust_reason
    # bounded_pass ceiling, but the result reports no bounds -> well_formed
    r2 = _reg_with(claims_verified).invoke("claims_verified", {"word": "a"})
    assert r2.trust == "well_formed"


def test_group_ceiling_is_enforced_at_registration():
    with pytest.raises(ValueError, match="group ceiling"):
        _reg_with(even_a, group="ir", max_trust="verified")
    with pytest.raises(ValueError, match="positive trust"):
        _reg_with(even_a, max_trust="refuted")
    assert GROUP_MAX_TRUST["theory"] == GROUP_MAX_TRUST["report"] == "not_verified"
    assert set(GROUP_MAX_TRUST) == set(GROUPS)


def test_refuted_passes_unless_disallowed():
    assert _reg_with(refutes).invoke("refutes", {"word": "b"}).trust == "refuted"
    r = _reg_with(refutes, allow_refuted=False).invoke("refutes", {"word": "b"})
    assert r.trust == "not_verified" and "may not refute" in r.trust_reason


@pytest.mark.parametrize("func,kind", [
    (returns_dict, "internal"),
    (raises_lib, "lib_error"),
    (raises_unsupported, "unsupported"),
    (builds_invalid, "internal"),
])
def test_errors_become_not_verified_failures(func, kind):
    r = _reg_with(func).invoke(func.__name__, {"word": "a"})
    assert not r.ok and r.error_kind == kind and r.trust == "not_verified"
    if kind == "unsupported":
        assert r.data == {"kind": "natural"}


def test_invalid_inputs():
    reg = _reg_with(even_a)
    r = reg.invoke("even_a", {"word": "abc"})       # outside the alphabet (group check)
    assert r.error_kind == "invalid_input"
    r = reg.invoke("even_a", {"word": "a" * 101})   # schema max_length
    assert r.error_kind == "invalid_input" and "word" in r.error
    r = reg.invoke("even_a", ["a"])                 # not an object
    assert r.error_kind == "invalid_input"
    r = reg.invoke("nope", {})
    assert r.error_kind == "invalid_input" and r.tool == "nope"
    r = reg.invoke("even_a", WordArgs(word="aa"))   # an args model is accepted as is
    assert r.ok


def test_input_byte_limit():
    reg = _reg_with(even_a, max_input_bytes=40)
    r = reg.invoke("even_a", {"word": "a" * 60})
    assert r.error_kind == "input_too_large"


def test_timeout_through_invoke():
    reg = ToolRegistry()
    reg.tool(group="words", max_trust="well_formed", timeout_s=0.1)(helpers.sleepy_tool)
    r = reg.invoke("sleepy_tool", {"seconds": 1.0})
    assert not r.ok and r.error_kind == "timeout" and r.bounds.timeout_s == 0.1
    assert reg.invoke("sleepy_tool", {"seconds": 0}).ok


def test_process_mode_through_invoke():
    reg = ToolRegistry()
    reg.tool(group="words", max_trust="well_formed", timeout_s=60,
             timeout_mode="process")(helpers.sleepy_tool)
    r = reg.invoke("sleepy_tool", {"seconds": 0})
    assert r.ok and r.data == {"slept": 0.0} and r.tool == "sleepy_tool"


def test_reserved_and_nested_reserved_fields_are_rejected():
    class Bad(ToolArgs):
        word: str
        confidence: float = 0.5

    class Claim(BaseModel):
        text: str
        trust: str

    class NestedBad(ToolArgs):
        claims: list[Claim]

    def bad_tool(args: Bad) -> ToolResult:
        """Declares confidence."""

    def nested_bad_tool(args: NestedBad) -> ToolResult:
        """Declares trust in a nested model."""

    for func, schema in ((bad_tool, Bad), (nested_bad_tool, NestedBad)):
        with pytest.raises(ValueError, match="reserved"):
            _reg_with(func, args_schema=schema)


def test_schema_must_be_toolargs_and_names_unique():
    class Plain(BaseModel):
        word: str

    def plain(args: Plain) -> ToolResult:
        """Plain pydantic schema."""

    with pytest.raises(TypeError):
        _reg_with(plain, args_schema=Plain)
    with pytest.raises(TypeError, match="exactly one"):
        _reg_with(lambda a, b: None)
    reg = _reg_with(even_a)
    reg.tool(group="words", max_trust="bounded_pass")(even_a)   # same function again: replaced
    with pytest.raises(ValueError, match="already registered"):
        reg.tool(group="words", max_trust="bounded_pass", name="even_a")(claims_verified)
    with pytest.raises(ValueError, match="must match"):
        reg.tool(group="words", max_trust="bounded_pass", name="Bad-Name")(even_a)
    with pytest.raises(ValueError, match="unknown group"):
        reg.tool(group="nope", max_trust="bounded_pass", name="x_tool")(even_a)
    with pytest.raises(ValueError, match="timeout_s"):
        reg.tool(group="words", max_trust="bounded_pass", name="slow_tool", timeout_s=10_000)(even_a)


def test_specs_selection():
    reg = ToolRegistry()
    reg.tool(group="words", max_trust="bounded_pass")(even_a)
    reg.tool(group="reg", max_trust="verified")(claims_verified)
    assert [s.name for s in reg.specs(groups=["reg"])] == ["claims_verified"]
    assert [s.name for s in reg.specs(names=["claims_verified", "even_a"])] == \
        ["claims_verified", "even_a"]
    with pytest.raises(KeyError):
        reg.specs(names=["missing"])
    assert isinstance(reg.get("even_a"), ToolSpec)


# ---------------------------------------------------------------------------
# LangChain conversion
# ---------------------------------------------------------------------------

def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_langchain_tool_schema_and_content():
    reg = _reg_with(even_a)
    lc = reg.as_langchain_tool("even_a")
    assert reg.as_langchain_tool("even_a") is lc
    assert lc.name == "even_a" and lc.description.startswith("Is the number")
    schema = lc.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"word", "alphabet"}
    assert "trust" not in base._schema_field_names(schema)
    assert lc.metadata == {"tfl_group": "words", "tfl_max_trust": "bounded_pass"}
    content = lc.invoke({"word": "aa"})
    assert json.loads(content)["data"] == {"in_language": True}


def test_langchain_tool_call_carries_full_result_as_artifact():
    from langchain_core.messages import ToolMessage

    lc = _reg_with(even_a).as_langchain_tool("even_a")
    msg = lc.invoke(_tool_call("even_a", {"word": "ab"}))
    assert isinstance(msg, ToolMessage) and msg.tool_call_id == "call_1"
    assert msg.artifact["trust"] == "bounded_pass" and msg.artifact["tool"] == "even_a"
    assert msg.artifact["data"] == {"in_language": False}
    assert json.loads(msg.content)["trust"] == "bounded_pass"


def test_langchain_validation_error_returns_failure_json():
    lc = _reg_with(even_a).as_langchain_tool("even_a")
    msg = lc.invoke(_tool_call("even_a", {"word": "aa", "trust": "verified"}))
    assert msg.status == "error"
    payload = json.loads(msg.content)
    assert payload["ok"] is False and payload["error_kind"] == "invalid_input"
    assert payload["trust"] == "not_verified" and payload["tool"] == "even_a"


def test_langchain_tool_with_nested_model_args():
    class PairArgs(ToolArgs):
        pair: helpers.Inner
        others: list[helpers.Inner] = []

    def pair_tool(args: PairArgs) -> ToolResult:
        """Echo a nested model."""
        return ToolResult.success({"w": args.pair.word, "n": len(args.others)},
                                  trust="well_formed", reason="parsed")

    reg = ToolRegistry()
    reg.tool(group="ir", max_trust="well_formed", args_schema=PairArgs)(pair_tool)
    msg = reg.as_langchain_tool("pair_tool").invoke(
        _tool_call("pair_tool", {"pair": {"word": "ab"}, "others": [{"word": ""}]}))
    assert msg.artifact["data"] == {"w": "ab", "n": 1}


def test_tool_node_runs_registered_tools():
    pytest.importorskip("langgraph.prebuilt")
    from langchain_core.messages import AIMessage
    from langgraph.prebuilt import ToolNode

    reg = ToolRegistry()
    reg.tool(group="words", max_trust="bounded_pass")(even_a)
    reg.tool(group="words", max_trust="bounded_pass")(raises_lib)
    from langgraph.graph import END, START, MessagesState, StateGraph

    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode(reg.langchain_tools()))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    ai = AIMessage(content="", tool_calls=[
        _tool_call("even_a", {"word": "aa"}, "c1"),
        _tool_call("raises_lib", {"word": "a"}, "c2"),
    ])
    out = [m for m in graph.invoke({"messages": [ai]})["messages"] if m.type == "tool"]
    assert len(out) == 2
    by_id = {m.tool_call_id: m for m in out}
    assert by_id["c1"].artifact["trust"] == "bounded_pass"
    assert by_id["c2"].artifact["ok"] is False and by_id["c2"].artifact["error_kind"] == "lib_error"


# ---------------------------------------------------------------------------
# Module-level registry and group modules
# ---------------------------------------------------------------------------

def test_group_modules_load_into_the_global_registry():
    assert set(GROUP_MODULES) <= set(GROUPS)
    reg = base.load_groups()
    assert reg is base.REGISTRY
    with pytest.raises(KeyError):
        base.load_groups(["orchestration"])
    for spec in reg.specs():  # whatever the group modules registered so far
        assert spec.group in GROUPS
        assert trust_rank(spec.ceiling) <= trust_rank(GROUP_MAX_TRUST[spec.group])
