"""P6: tools/lean.py -- lean_statement / lean_check.

No LLM, no network. Every test except the last group replaces the Docker run
(``lean._run_lean``) with a scripted checker. The acceptance test splits the
golden ``docs/examples/reg_grammar_SaSb_lean_proved/proof.lean`` into statement
and body and runs the body through the real tool in the ``tfl-lean4`` image; it
skips (with the reason) when Docker or the image is unavailable, or when the
Docker daemon fails before Lean starts (DEV_NOTES P0: flaky ``runc``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_system.lib import type_check
from tfl_deep_agent.reports import render as R
from tfl_deep_agent.tools import lean
from tfl_deep_agent.tools.base import GROUP_MAX_TRUST, REGISTRY, load_groups
from tfl_deep_agent.tools.ir import ir_fingerprint

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_REG = ROOT / "docs" / "examples" / "reg_grammar_SaSb_lean_proved"
GOLDEN_CFL = ROOT / "docs" / "examples" / "cfl_ticket50_w1bw2w3_lean_proved"
PROMPT = ROOT / "tfl_deep_agent" / "prompts" / "lean.md"

load_groups(["lean"])

STD_AXIOMS = ["propext", "Classical.choice", "Quot.sound"]
THEOREM_LINE = "theorem tfl_main : ¬ L.IsRegular := by\n  "
REPLAY_START = "\n\nopen Lean in\nrun_cmd do"


def golden_ir() -> dict[str, Any]:
    return json.loads((GOLDEN_REG / "input.json").read_text(encoding="utf-8"))


def golden_split() -> tuple[str, str, str]:
    """(file text, the statement part up to ``:= by``, the proof body)."""
    text = (GOLDEN_REG / "proof.lean").read_text(encoding="utf-8")
    head, rest = text.split(THEOREM_LINE, 1)
    body = rest.split(REPLAY_START, 1)[0]
    return text, head + THEOREM_LINE, body


class FakeLean:
    """Scripted stand-in for ``type_check.check_lean_file``."""

    def __init__(self, *results: dict[str, Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, text: str, timeout: int, theorem_name: str) -> dict[str, Any]:
        self.calls.append({"text": text, "timeout": timeout, "theorem_name": theorem_name})
        res = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return {"errors": [], "warnings": [], "axioms": [], "elapsed": 1.0, "message": None, **res}


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    lean.reset_store()
    monkeypatch.setattr(lean, "DAEMON_RETRY_SLEEP_S", 0.0)
    yield
    lean.reset_store()


@pytest.fixture
def fake(monkeypatch):
    def install(*results: dict[str, Any]) -> FakeLean:
        f = FakeLean(*results)
        monkeypatch.setattr(lean, "_run_lean", f)
        return f
    return install


def statement(ir: dict[str, Any] | None = None, direction: str = "non_regular"):
    res = REGISTRY.invoke("lean_statement", {"ir": golden_ir() if ir is None else ir,
                                             "direction": direction})
    assert res.ok, res.error
    return res


def check(statement_id: str, body: str, **extra: Any):
    return REGISTRY.invoke("lean_check", {"statement_id": statement_id, "proof_body": body, **extra})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_registered_in_the_lean_group_with_their_ceilings():
    assert set(REGISTRY.names("lean")) == {"lean_statement", "lean_check"}
    assert GROUP_MAX_TRUST["lean"] == "verified"
    assert REGISTRY.get("lean_check").ceiling == "verified"
    assert REGISTRY.get("lean_statement").ceiling == "not_verified"
    assert not REGISTRY.get("lean_check").allow_refuted
    assert REGISTRY.get("lean_check").timeout_s >= lean.LEAN_TIMEOUT_S + 30


def test_langchain_tools_reject_a_trust_argument_and_carry_the_artifact():
    tools = {t.name: t for t in REGISTRY.langchain_tools(groups=["lean"])}
    assert set(tools) == {"lean_statement", "lean_check"}
    call = {"name": "lean_statement", "args": {"ir": golden_ir(), "direction": "non_regular"},
            "id": "c1", "type": "tool_call"}
    msg = tools["lean_statement"].invoke(call)
    assert msg.artifact["ok"] and msg.artifact["data"]["statement_id"].startswith("lean-")
    bad = {"name": "lean_check", "id": "c2", "type": "tool_call",
           "args": {"statement_id": msg.artifact["data"]["statement_id"], "proof_body": "rfl",
                    "trust": "verified"}}
    out = tools["lean_check"].invoke(bad)
    assert json.loads(out.content)["ok"] is False


# ---------------------------------------------------------------------------
# lean_statement
# ---------------------------------------------------------------------------

def test_statement_is_the_pipeline_rendering_and_reproduces_the_golden_file():
    from agent_system.lib.lean_ir import render_statement_verbose

    res = statement()
    d = res.data
    assert res.trust == "not_verified" and d["evidence_role"] == "reference"
    assert d["direction"] == "non_regular" and d["cls"] == "reg" and d["side"] == "destructive"
    assert d["theorem_decl"] == "theorem tfl_main : ¬ L.IsRegular" and d["name"] == "tfl_main"
    assert d["renderer"] == "agent_system.lib.lean_ir.render_statement_verbose"
    assert d["ir_fingerprint"] == ir_fingerprint(golden_ir())
    expected, reason = render_statement_verbose(golden_ir(), "non_regular")
    assert reason is None
    assert d["template"] == expected.render("<PROOF>")
    assert d["statement"].rstrip().endswith("theorem tfl_main : ¬ L.IsRegular")
    assert ":= by" not in d["statement"]
    # the stored statement + the golden body compose to the golden file byte for byte
    text, head, body = golden_split()
    stored = lean._STORE[d["statement_id"]].statement
    assert lean.compose(stored, body) == text
    assert text.startswith("import Lean.Replay\nimport Lean.Elab.Command\n")
    assert head.split("\n", 2)[2] == expected.render(body).split(body)[0]


def test_statement_id_is_deterministic_and_depends_on_direction_and_ir():
    a = statement().data["statement_id"]
    lean.reset_store()
    assert statement().data["statement_id"] == a
    assert statement(direction="regular").data["statement_id"] != a
    ir2 = golden_ir()
    ir2["language_spec"]["rules"] = ir2["language_spec"]["rules"][:-1]
    assert statement(ir2).data["statement_id"] != a


def test_cfl_and_dcfl_directions_use_their_own_renderers():
    cfl_ir = json.loads((GOLDEN_CFL / "input.json").read_text(encoding="utf-8"))
    res = statement(cfl_ir, "non_cfl")
    assert res.data["renderer"] == "cfl_system.lib.lean_ir.render_statement_verbose"
    assert res.data["theorem_decl"].startswith("theorem tfl_main : ¬")
    assert res.data["cls"] == "cfl" and res.data["side"] == "destructive"
    dcfl = REGISTRY.invoke("lean_statement", {"ir": golden_ir(), "direction": "dcfl"})
    assert dcfl.ok and dcfl.data["renderer"].startswith("dcfl_system.")
    assert dcfl.data["theorem_decl"] == "theorem tfl_main : is_DCF L"


def test_ll_and_unrenderable_irs_are_refused_with_the_reason():
    ll = REGISTRY.invoke("lean_statement", {"ir": golden_ir(), "direction": "ll"})
    assert not ll.ok and ll.error_kind == "unsupported" and "not formalized" in ll.error
    bad = REGISTRY.invoke("lean_statement", {"ir": {"language_spec": {"kind": "nonsense"}},
                                             "direction": "regular"})
    assert not bad.ok and bad.error_kind == "unsupported" and "nonsense" in bad.error
    wrong = REGISTRY.invoke("lean_statement", {"ir": golden_ir(), "direction": "context_free"})
    assert not wrong.ok and wrong.error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# lean_check with a scripted checker
# ---------------------------------------------------------------------------

def test_proved_with_standard_axioms_is_verified(fake):
    f = fake({"status": "proved", "axioms": STD_AXIOMS})
    sid = statement().data["statement_id"]
    _text, _head, body = golden_split()
    res = check(sid, body)
    assert res.ok and res.trust == "verified", res
    d = res.data
    assert d["status"] == "proved" and d["axioms"] == STD_AXIOMS
    assert d["direction"] == "non_regular" and d["cls"] == "reg" and d["side"] == "destructive"
    assert d["ir_fingerprint"] == ir_fingerprint(golden_ir())
    assert d["proof_body"] == body and d["statement"] == statement().data["statement"]
    assert d["evidence_role"] == "claim_check" and d["attempt"] == 1
    # the file Docker sees is composed by code: replay check and #print axioms appended
    sent = f.calls[0]
    assert sent["theorem_name"] == "tfl_main" and sent["timeout"] == lean.LEAN_TIMEOUT_S
    assert sent["text"] == golden_split()[0]
    assert sent["text"].rstrip().endswith("#print axioms tfl_main")


@pytest.mark.parametrize("result", [
    {"status": "has_sorry"},
    {"status": "error", "errors": [{"severity": "error", "pos": {"line": 20, "column": 2},
                                    "data": "unknown identifier 'foo'"}]},
    {"status": "timeout"},
    {"status": "proved", "axioms": STD_AXIOMS + ["sorryAx"]},
    {"status": "proved", "axioms": ["propext", "Lean.ofReduceBool"]},
])
def test_anything_but_a_clean_proof_is_not_evidence(fake, result):
    fake(result)
    sid = statement().data["statement_id"]
    res = check(sid, "simp")
    assert res.ok and res.trust == "not_verified"
    assert res.data["status"] != "proved"
    if result["status"] == "error":
        assert res.data["errors"][0]["data"] == "unknown identifier 'foo'"


def test_errors_are_trimmed_for_the_model(fake):
    many = [{"severity": "error", "pos": {"line": i}, "data": "x" * 5000} for i in range(30)]
    fake({"status": "error", "errors": many})
    res = check(statement().data["statement_id"], "simp")
    assert res.data["n_errors"] == 30 and len(res.data["errors"]) == 10
    assert all(len(e["data"]) <= 2000 for e in res.data["errors"])


@pytest.mark.parametrize("body, label", [
    ("sorry", "sorry"),
    ("exact sorryAx _ false", "sorryAx"),
    ("admit", "admit"),
    ("native_decide", "native_decide"),
    ("exact Lean.ofReduceBool", "Lean namespace"),
    ("simp\n  axiom cheat : False", "axiom declaration"),
    ("simp\n  theorem helper : True := trivial", "declaration keyword"),
    ("exact tfl_main", "tfl_main"),
    ("simp\nexact h", "zero indentation"),
    ("simp\n  #exit", "#exit"),
    ("by simp", "leading `by`"),
    ("simp\n\tdecide", "tab character"),
    ("   ", "empty proof body"),
    ("run_tac Lean.Elab.Tactic.evalTactic", "run_tac"),
    ("set_option debug.skipKernelTC true in\n  decide", "set_option"),
])
def test_guard_rejects_before_docker(fake, body, label):
    f = fake({"status": "proved", "axioms": []})
    sid = statement().data["statement_id"]
    res = check(sid, body)
    assert not res.ok and res.error_kind == "invalid_input" and res.trust == "not_verified"
    assert res.data["status"] == "rejected"
    assert any(label in v for v in res.data["violations"]), res.data["violations"]
    assert f.calls == []  # never compiled
    assert lean.attempts_used(sid) == 0  # and not counted as an attempt


def test_guard_ignores_comments_and_accepts_the_golden_body():
    _text, _head, body = golden_split()
    assert lean.guard_proof_body(body) == []
    assert lean.guard_proof_body("-- no sorry here, and no axiom either\n  simp /- admit -/") == []


def test_unknown_or_mismatched_statement_ids_are_refused(fake):
    fake({"status": "proved", "axioms": STD_AXIOMS})
    res = check("lean-0123456789abcdef", "simp")
    assert not res.ok and res.error_kind == "invalid_input" and "lean_statement first" in res.error
    sid = statement().data["statement_id"]
    lean.reset_store()  # a new process after a resume: the id is re-derived from the IR
    again = check(sid, "simp", ir=golden_ir(), direction="non_regular")
    assert again.ok and again.trust == "verified" and again.data["statement_id"] == sid
    lean.reset_store()
    other = check(sid, "simp", ir=golden_ir(), direction="regular")
    assert not other.ok and other.error_kind == "invalid_input" and "cannot be changed" in other.error
    ir2 = golden_ir()
    ir2["language_spec"]["rules"] = ir2["language_spec"]["rules"][:-1]
    statement()
    tampered = check(sid, "simp", ir=ir2)
    assert not tampered.ok and tampered.error_kind == "invalid_input"
    wrong_dir = check(sid, "simp", direction="regular")
    assert not wrong_dir.ok and "rendered for direction 'non_regular'" in wrong_dir.error
    malformed = check("not-an-id", "simp")
    assert not malformed.ok and malformed.error_kind == "invalid_input"


def test_attempt_limit_per_statement(fake, monkeypatch):
    monkeypatch.setattr(lean, "MAX_CHECKS_PER_STATEMENT", 2)
    fake({"status": "error", "errors": [{"severity": "error", "pos": {"line": 1}, "data": "e"}]})
    sid = statement().data["statement_id"]
    assert check(sid, "simp").data["attempts_left"] == 1
    assert check(sid, "simp").data["attempts_left"] == 0
    third = check(sid, "simp")
    assert not third.ok and third.error_kind == "busy" and "attempt limit" in third.error


def test_two_irs_with_the_same_statement_report_their_own_fingerprint(fake):
    """An IR that differs only outside the language (source_text) renders the
    same statement; results carry the fingerprint of the IR of this call / run,
    never the cached one of the first IR."""
    fake({"status": "proved", "axioms": STD_AXIOMS})
    ir1 = golden_ir()
    ir2 = {**golden_ir(), "source_text": "a different transcription of the same task"}
    fp1, fp2 = ir_fingerprint(ir1), ir_fingerprint(ir2)
    assert fp1 != fp2
    with lean.lean_run("first"):
        s1 = statement(ir1).data
    with lean.lean_run("second"):
        s2 = statement(ir2).data
        assert s2["statement_id"] == s1["statement_id"]
        assert s2["ir_fingerprint"] == fp2
        # explicit ir in lean_check: that IR's fingerprint
        res = check(s2["statement_id"], "simp", ir=ir2)
        assert res.trust == "verified" and res.data["ir_fingerprint"] == fp2
        assert res.data["ir_fingerprint_source"] == "call"
        # no ir: the IR this run rendered the statement from
        res = check(s2["statement_id"], "simp")
        assert res.data["ir_fingerprint"] == fp2 and res.data["ir_fingerprint_source"] == "run"
    with lean.lean_run("first"):
        res1 = check(s1["statement_id"], "simp")
        assert res1.data["ir_fingerprint"] == fp1 and res1.data["ir_fingerprint_source"] == "run"
    # the gate: a proof checked for ir2 is a verified basis for a report on ir2
    from tfl_deep_agent import agent as A

    entry = {"id": "c1", "tool": "lean_check", "ok": True, "trust": res.trust,
             "data": res.data, "args": {}}
    gate = A.verdict_gate({"verdict": "not regular", "language_class": "reg", "ir_fingerprint": fp2},
                          [entry], ["c1"])
    assert gate["trust"] == "verified" and gate["excluded_ir_mismatch"] == []
    gate1 = A.verdict_gate({"verdict": "not regular", "language_class": "reg", "ir_fingerprint": fp1},
                           [entry], ["c1"])
    assert gate1["excluded_ir_mismatch"] == ["c1"]


def test_attempts_are_counted_per_run_not_per_process(fake, monkeypatch):
    monkeypatch.setattr(lean, "MAX_CHECKS_PER_STATEMENT", 1)
    fake({"status": "error", "errors": [{"severity": "error", "pos": {"line": 1}, "data": "e"}]})
    with lean.lean_run("a"):
        sid = statement().data["statement_id"]
        assert check(sid, "simp").ok
        assert check(sid, "simp").error_kind == "busy"
        assert lean.attempts_used(sid) == 1
    with lean.lean_run("b"):  # another run in the same process starts fresh
        assert statement().data["checks_used"] == 0
        assert check(sid, "simp").ok
    assert lean.attempts_used(sid, "run:a") == 1 and lean.attempts_used(sid, "run:b") == 1


def test_run_scope_follows_the_langgraph_thread_id(fake, monkeypatch):
    monkeypatch.setattr(lean, "MAX_CHECKS_PER_STATEMENT", 1)
    fake({"status": "error", "errors": [{"severity": "error", "pos": {"line": 1}, "data": "e"}]})
    tool = {t.name: t for t in REGISTRY.langchain_tools(groups=["lean"])}
    sid = statement().data["statement_id"]

    def call(thread: str) -> dict[str, Any]:
        msg = tool["lean_check"].invoke(
            {"name": "lean_check", "id": "c", "type": "tool_call",
             "args": {"statement_id": sid, "proof_body": "simp"}},
            {"configurable": {"thread_id": thread}})
        return msg.artifact

    assert call("t1")["ok"] and not call("t1")["ok"]  # second attempt of run t1: limit
    assert call("t2")["ok"]  # run t2 has its own counter
    assert lean.attempts_used(sid, "thread:t1") == 1 and lean.attempts_used(sid, "thread:t2") == 1
    assert lean.attempts_used(sid, lean.PROCESS_SCOPE) == 0


def test_unavailable_checker_is_a_failure_and_not_an_attempt(fake):
    fake({"status": "unavailable", "message": "Docker not available"})
    sid = statement().data["statement_id"]
    res = check(sid, "simp")
    assert not res.ok and res.error_kind == "unsupported" and res.trust == "not_verified"
    assert res.data["status"] == "unavailable" and "Docker not available" in res.error
    assert lean.attempts_used(sid) == 0


DAEMON_FLAKE = {"status": "error", "errors": [{"severity": "error", "data": (
    "docker: Error response from daemon: failed to create task for container: OCI runtime create "
    "failed: runc create failed: unable to start container process: waiting for init preliminary "
    "setup: read init-p: connection reset by peer")}]}


def test_a_daemon_flake_is_retried(fake):
    f = fake(DAEMON_FLAKE, {"status": "proved", "axioms": STD_AXIOMS})
    res = check(statement().data["statement_id"], "simp")
    assert res.ok and res.trust == "verified" and res.data["daemon_retries"] == 1
    assert len(f.calls) == 2


def test_a_persistent_daemon_failure_is_unavailable(fake):
    f = fake(DAEMON_FLAKE)
    sid = statement().data["statement_id"]
    res = check(sid, "simp")
    assert not res.ok and res.error_kind == "unsupported" and res.data["status"] == "unavailable"
    assert len(f.calls) == 1 + lean.DAEMON_RETRIES
    assert lean.attempts_used(sid) == 0
    # a real Lean error without a source position is not mistaken for a daemon failure
    assert not lean._is_daemon_failure({"status": "error", "errors": [{"data": "lean exited 137"}]})


def test_crlf_bodies_are_normalized(fake):
    f = fake({"status": "proved", "axioms": STD_AXIOMS})
    _text, _head, body = golden_split()
    res = check(statement().data["statement_id"], body.replace("\n", "\r\n"))
    assert res.trust == "verified" and "\r" not in f.calls[0]["text"]


def test_a_lean_check_entry_renders_in_the_report_lean_section(fake):
    fake({"status": "proved", "axioms": STD_AXIOMS})
    _text, _head, body = golden_split()
    res = check(statement().data["statement_id"], body)
    entry = {"id": "ev-lean", "tool": "lean_check", "ok": res.ok, "trust": res.trust,
             "trust_reason": res.trust_reason, "error": None, "data": res.data}
    report = {"verdict": "non_regular", "answer": "нет", "language_class": "reg",
              "method": "Lean", "steps": [], "conclusion": "Не регулярен.",
              "template": "lean_appendix", "lean": {"evidence_ids": ["ev-lean"]}}
    md = R.render_report({"status": "submitted", "report": report, "evidence": [entry],
                          "trust": "verified", "confidence_cap": 0.98}).markdown
    assert "статус (по журналу): **proved**" in md
    assert "- axioms: propext, Classical.choice, Quot.sound" in md
    assert "theorem tfl_main : ¬ L.IsRegular" in md and "letI : DecidableEq g.NT" in md


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def test_lean_prompt_names_the_tools_and_the_rules():
    text = PROMPT.read_text(encoding="utf-8")
    for token in ("lean_statement", "lean_check", "statement_id", "sorry", "native_decide",
                  "proved", "column 0"):
        assert token in text, token
    assert "arrive in phase P6" not in text and "stub" not in text.lower()


TFL_LEAN = ROOT / "agent_system" / "docker" / "tfl_lean" / "TflLean"


def _tfllean_declarations() -> set[str]:
    import re

    text = (TFL_LEAN / "Lemmas.lean").read_text(encoding="utf-8")
    return set(re.findall(r"^(?:theorem|lemma|def|abbrev|structure)\s+([^\s({\[:]+)", text, re.M))


def test_every_tfllean_name_in_the_lean_prompt_is_declared_in_lemmas_lean():
    import re

    declared = _tfllean_declarations()
    names = {m.rstrip(".") for m in re.findall(r"TflLean\.([A-Za-z_][\w'.]*)", PROMPT.read_text(encoding="utf-8"))}
    assert names, "the prompt names no TflLean lemma"
    missing = sorted(n for n in names if n not in declared)
    assert not missing, f"lean.md names TflLean lemmas that Lemmas.lean does not declare: {missing}"


def test_lean_prompt_knows_the_closure_filter_and_anbn_lemmas():
    text = PROMPT.read_text(encoding="utf-8")
    for name in ("isContextFree_inf_isRegular", "not_isContextFree_of_inf_isRegular",
                 "not_isRegular_of_inf_isRegular", "isDCF_inf_isRegular", "not_isDCF_of_inf_isRegular",
                 "isDCF_of_isRegular", "isContextFree_of_isRegular", "isContextFree_add",
                 "isContextFree_subst", "mem_homomorphicImage_iff", "isContextFree_homomorphicImage",
                 "not_isContextFree_of_homomorphicImage", "isContextFree_map",
                 "isContextFree_inverseHomomorphicImage", "isRegular_length_mod", "isRegular_count_mod",
                 "isRegular_replicate_append_replicate", "isContextFree_anbn", "isContextFree_anbn_mul",
                 "isDCF_anbn", "not_isRegular_anbn", "not_isContextFree_pow2", "isDCF_anbncm",
                 "isDCF_anbncm_pos", "not_isContextFree_of_slice", "isContextFree_of_isDCF"):
        assert f"`TflLean.{name} " in text or f"`TflLean.{name}`" in text, name


def test_lean_prompt_reference_examples_exist_and_quoted_bodies_are_verbatim():
    import re

    text = PROMPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    for name in set(re.findall(r"`([A-Za-z0-9_]+\.lean)`", text)) - {"Lemmas.lean"}:
        assert (TFL_LEAN / "Examples" / name).is_file(), name
    for name in ("AnBnMod4Filter_CF", "AnBnUnionAnB2n_CF", "AnBn_DCF", "AiBjNeq_NotRegular"):
        assert f"`{name}.lean`" in text, name
    quoted = re.findall(r"Proof body of `([A-Za-z0-9_]+\.lean)`.*?```lean\n(.*?)```", text, re.S)
    assert len(quoted) == 4
    for name, body in quoted:
        source = (TFL_LEAN / "Examples" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert body in source, name


def test_lean_prompt_does_not_claim_unformalized_theory():
    text = PROMPT.read_text(encoding="utf-8")
    section = text.split("### Not in the image", 1)[1].split("###", 1)[0]
    for topic in ("LL(k)", "Parikh", "Yu's two-word lemma", "Shallit's theorem", "grammar to a DPDA",
                  "`lean_statement` refuses `ll`"):
        assert topic in section, topic
    assert "LL(k) is not formalized" in text


# ---------------------------------------------------------------------------
# Acceptance: the golden REG proof through the real tool (Docker, tfl-lean4)
# ---------------------------------------------------------------------------

def _docker_or_skip() -> None:
    if not type_check.is_docker_available():
        pytest.skip(f"Docker or the {type_check.DOCKER_IMAGE} image is not available")


def test_golden_reg_proof_verifies_through_the_tools():
    _docker_or_skip()
    _text, _head, body = golden_split()
    sid = statement().data["statement_id"]
    res = check(sid, body)
    if not res.ok and res.error_kind == "unsupported":
        pytest.skip(f"Lean checker unavailable at run time: {res.error[:300]}")
    assert res.ok, res.error
    assert res.data["status"] == "proved", res.data["errors"]
    assert res.trust == "verified", res.trust_reason
    assert set(res.data["axioms"]) <= type_check.ALLOWED_AXIOMS
    assert res.data["direction"] == "non_regular"
