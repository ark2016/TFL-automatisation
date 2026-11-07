"""Fixed subagent roles of the TFL Deep Agent (architecture.html, "Готовые роли").

Each role is a deepagents declarative ``SubAgent`` reached through the built-in
``task`` tool, with its own context window, its own prompt (``prompts/*.md``),
its own subset of the P1 tool layer and its own model (``settings.json``
``models.roles``; ``TFL_MODEL_OVERRIDE`` beats it).

| role key     | subagent name     | tools                                      |
|--------------|-------------------|--------------------------------------------|
| intake       | intake            | built-in file reading only (/input, P3)    |
| ir_builder   | ir-builder        | IR, words                                  |
| prover       | prover            | theory, class oracles, words, IR           |
| critic       | critic            | class oracles, theory, words, IR           |
| lean         | lean-formalizer   | lean (P6), theory                          |
| coder        | coder             | built-in ``execute`` + /workspace files    |
| general      | general-purpose   | theory only (replaces deepagents' default) |

``general`` exists only because deepagents adds a ``general-purpose`` subagent
with the main agent's model and *all* its tools unless one is supplied; our
replacement is cheap (Haiku by default), has no oracles and no
``submit_report``, and gets the same middleware as the other roles, so the
budget hook covers it.

The roles themselves are data. ``agent.create_tfl_agent`` turns them into
``SubAgent`` dicts with :func:`build_subagents`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import config

PKG_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = PKG_DIR / "prompts"

#: deepagents built-in tools that only the coder may use (it needs a sandbox
#: backend; P5). Every other agent has them filtered out of its tool list.
SANDBOX_ONLY_TOOLS: frozenset[str] = frozenset({"execute"})

#: Tool groups of the main agent: everything in the P1 layer ("все, кроме
#: execute"). Orchestration tools (P4) and the report group join when they
#: register; ``submit_report`` is added by agent.py.
MAIN_TOOL_GROUPS: tuple[str, ...] = (
    "ir", "words", "reg", "cfl", "dcfl", "ll", "theory", "lean", "orchestration", "report",
)

_ORACLE_GROUPS = ("reg", "cfl", "dcfl", "ll", "words")


@dataclasses.dataclass(frozen=True)
class Role:
    """One fixed subagent role."""

    key: str                         # settings.json models.roles key, config.MODELS key
    name: str                        # deepagents subagent name (``task(subagent_type=...)``)
    description: str                 # what the main agent reads when choosing a role
    prompt: str                      # prompts/<prompt>.md, or inline text when ``inline_prompt``
    tool_groups: tuple[str, ...]     # P1 tool groups (tools/base.py GROUPS)
    allow_sandbox: bool = False      # keep the built-in ``execute``
    skills: bool = True              # SkillsMiddleware over /skills/
    default_model: str | None = None  # used when settings and config.MODELS have no entry
    inline_prompt: bool = False

    def system_prompt(self) -> str:
        if self.inline_prompt:
            return self.prompt
        return load_prompt(self.prompt)


def load_prompt(name: str) -> str:
    """Text of ``prompts/<name>.md`` (UTF-8, trailing whitespace stripped)."""
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").rstrip() + "\n"


_GENERAL_PROMPT = """\
You are a small general helper of the TFL Deep Agent. You get only chores that fit no
fixed role: reformatting text, summarising a long tool output, extracting a list from
prose. You have no oracles, so you never decide whether a language is regular,
context-free, deterministic or LL(k), never write proofs, and never say that anything is
verified. If the task needs theory, an oracle or a proof, answer in one line that it
belongs to the prover, critic or ir-builder role. Reply with the result only.
"""

ROLES: dict[str, Role] = {
    "intake": Role(
        key="intake", name="intake",
        description=(
            "Transcribes the task source (a PDF page or an image under /input) into normalized "
            "text with Unicode formulas, splits a multi-task file into a list, and flags every "
            "uncertain reading. Use it only when the task came as a file."),
        prompt="intake", tool_groups=(), skills=False,
    ),
    "ir_builder": Role(
        key="ir_builder", name="ir-builder",
        description=(
            "Turns the task text into an IR for one class (reg / cfl / dcfl / ll) that passes "
            "validate_ir, and returns the IR JSON, a plain-language restatement (ir_to_text) and "
            "sample words in and out of the language. Give it the full task text and the class."),
        prompt="ir_builder", tool_groups=("ir", "words"),
    ),
    "prover": Role(
        key="prover", name="prover",
        description=(
            "Writes the proof for a stated hypothesis (language class verdict) and checks every "
            "concrete step with an oracle: chosen words, decompositions, pumped words, "
            "constructions. Give it the IR, the hypothesis and the method to try."),
        prompt="prover", tool_groups=("theory", "ir", *_ORACLE_GROUPS),
    ),
    "critic": Role(
        key="critic", name="critic",
        description=(
            "Fresh-context reviewer: gets ONLY the claim, the IR and the proof text (never your "
            "reasoning) and looks for the error: pumping quantifiers, an uninstantiated p, "
            "unchecked cases, words that are not in L. Returns a list of defects or 'no defect "
            "found', with the oracle calls it made."),
        prompt="critic", tool_groups=("theory", "ir", *_ORACLE_GROUPS),
    ),
    "lean": Role(
        key="lean", name="lean-formalizer",
        description=(
            "Writes the Lean 4 proof body for a theorem statement fixed by lean_statement and "
            "retries with lean_check until it type-checks or the attempt limit is reached "
            "(the Lean tools arrive in phase P6)."),
        prompt="lean", tool_groups=("lean", "theory"),
    ),
    "coder": Role(
        key="coder", name="coder",
        description=(
            "Writes and runs Python in the network-less Docker sandbox (/workspace, the repository "
            "read-only at /opt/tfl): brute-force searches, hypothesis checks, counterexample hunts. "
            "Returns the code together with its output. Sandbox output is at most well_formed "
            "evidence."),
        prompt="coder", tool_groups=(), allow_sandbox=True, skills=False,
    ),
    "general": Role(
        key="general", name="general-purpose",
        description=(
            "Cheap helper for small generic chores (reformatting, summarising a long tool output). "
            "No oracles, no proofs, no verdicts: use the fixed roles for any TFL work."),
        prompt=_GENERAL_PROMPT, inline_prompt=True, tool_groups=("theory",), skills=False,
        default_model=config.HAIKU,
    ),
}

#: Subagent name -> role key.
ROLE_BY_NAME: dict[str, str] = {r.name: r.key for r in ROLES.values()}


def role_model_request(settings_roles: Mapping[str, str], role_key: str) -> str | None:
    """The model a role asks for before ``TFL_MODEL_OVERRIDE`` and the
    allowed-list check: ``settings.models.roles[role]`` > ``config.MODELS[role]``
    > the role's own default (``general``)."""
    if settings_roles.get(role_key):
        return settings_roles[role_key]
    if role_key in config.MODELS:
        return config.MODELS[role_key]
    role = ROLES.get(role_key)
    return role.default_model if role else None


def role_tools(role: Role) -> list[Any]:
    """LangChain tools of a role, from the P1 registry (groups loaded on demand)."""
    from .tools import base

    if not role.tool_groups:
        return []
    loadable = [g for g in role.tool_groups if g in base.GROUP_MODULES]
    base.load_groups(loadable)
    return base.REGISTRY.langchain_tools(groups=role.tool_groups)


def build_subagents(
    *,
    model_for_role: Callable[[str], Any],
    middleware_for_role: Callable[[str], Sequence[Any]],
    skills_sources: list[str] | None,
    roles: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """deepagents ``SubAgent`` dicts for the fixed roles.

    ``model_for_role(role_key)`` returns the chat model instance,
    ``middleware_for_role(role_key)`` the role's extra middleware (budget,
    evidence, concurrency hooks; agent.py adds the tool filter)."""
    specs: list[dict[str, Any]] = []
    for key in (roles or list(ROLES)):
        role = ROLES[key]
        spec: dict[str, Any] = {
            "name": role.name,
            "description": role.description,
            "system_prompt": role.system_prompt(),
            "model": model_for_role(key),
            "tools": role_tools(role),
            "middleware": list(middleware_for_role(key)),
        }
        if role.skills and skills_sources:
            spec["skills"] = list(skills_sources)
        specs.append(spec)
    return specs
