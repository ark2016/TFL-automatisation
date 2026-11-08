"""create_tfl_agent(...) -> deepagents.create_deep_agent(...): the main TFL agent.

One ReAct agent (deepagents on ``langchain.agents.create_agent``) for all four
classes: the model plans with ``write_todos``, calls the P1 tools, delegates to
the fixed roles (``roles.py``) through ``task`` and ends the run with
``submit_report``.

What is wired here (P2):

* the main model and one model per fixed role, built by ``model_factory``
  (default: ``models.make_chat_model``; tests and ``--mock-script`` pass
  :func:`scripted_model_factory`), with ``settings.models.roles`` /
  ``models.allowed`` and ``TFL_MODEL_OVERRIDE`` applied by
  :func:`resolve_role_model`;
* the P1 tool layer (``tools/``), the main agent getting every group, each
  role its subset;
* skills under ``/skills/`` served read-only from ``tfl_deep_agent/skills`` via a
  ``CompositeBackend`` route (:class:`ReadOnlyFilesystemBackend` plus a deny rule);
* ``write_todos`` (langchain's ``TodoListMiddleware``: deepagents 0.7.34 no
  longer adds it by itself);
* the P3 ``submit_report`` tool (``tools/report.py``) and
  :class:`ReportGateMiddleware`, which ends the run after an accepted report;
* P3: the evidence ledger (``evidence=``; ``EvidenceMiddleware`` on every
  agent), the task file under ``/input/`` (read-only, ``run_task(files=)``)
  and HITL (``hitl=``, ``hitl.py``): HITL 1 after an ir-builder answer, HITL 2
  on ``submit_report`` (``interrupt_on``); ``collect_result(evidence=)``
  rates the report from the ledger (``evidence.policy.evaluate_report``);
* middleware slots: ``middleware=`` (main agent) and ``subagent_middleware=``
  (each role). When they are not given, :func:`default_middleware` fills them
  from ``middleware/budget.py`` / ``middleware/concurrency.py`` /
  ``middleware/evidence.py`` if those modules provide a middleware class
  (another phase writes them), otherwise the slot stays empty;
* one budget for the run (``budget=``): the main agent and every role charge
  the same ``BudgetLedger``, each ``task`` call in its own allocation
  (``SubagentBudgetMiddleware``); prompt caching comes from deepagents'
  own Anthropic caching middleware in every stack.

:func:`collect_result` turns the final state into a plain dict (report, tool
evidence by tool-call id, trust per claim). It is a P2 stand-in for the P3
renderer and EvidenceMiddleware ledger: the trust of a claim is computed by
code from the referenced tool results; an id with no tool result behind it
makes the claim ``not_verified``.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from . import config
from .settings import report_reserve
from .roles import MAIN_TOOL_GROUPS, PKG_DIR, ROLES, SANDBOX_ONLY_TOOLS, load_prompt, role_model_request

SKILLS_DIR = PKG_DIR / "skills"
SKILLS_ROUTE = "/skills/"
#: the task file (PDF / image) in the run's state files; the built-in write
#: tools are denied there, so the source stays as the human gave it
INPUT_ROUTE = "/input/"
SETTINGS_EXAMPLE = PKG_DIR / "settings.example.json"

TEMPLATES: tuple[str, ...] = ("full_proof", "exam_solution", "short_verdict", "lean_appendix")
LANGUAGE_CLASSES: tuple[str, ...] = ("reg", "cfl", "dcfl", "ll")

#: ``model_factory(role_key, model_id) -> BaseChatModel``.
ModelFactory = Callable[[str, str], Any]


class ModelNotAllowedError(ValueError):
    """A role resolved to a model that ``settings.models.allowed`` does not list."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def settings_dict(settings: Any = None) -> dict[str, Any]:
    """Plain-dict view of the run settings.

    Accepts a mapping, a pydantic model (``model_dump``), a dataclass, or
    ``None``. ``None`` is ``settings.load_settings()`` (``settings.json`` over
    the code defaults). A mapping is validated by
    ``settings.validate_settings`` (merged over the defaults), so a broken
    budget or model section fails closed with ``settings.SettingsError``
    instead of reaching the ledger."""
    from . import settings as settings_mod

    if settings is None:
        return settings_mod.load_settings().to_dict()
    if isinstance(settings, settings_mod.Settings):
        return settings.to_dict()
    if isinstance(settings, Mapping):
        return settings_mod.validate_settings(copy.deepcopy(dict(settings))).to_dict()
    dump = getattr(settings, "model_dump", None)
    if callable(dump):
        return dump()
    if dataclasses.is_dataclass(settings) and not isinstance(settings, type):
        return dataclasses.asdict(settings)
    raise TypeError(f"settings must be a mapping, a pydantic model or a dataclass, not {type(settings).__name__}")


def _section(settings: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = settings.get(name) or {}
    return value if isinstance(value, Mapping) else {}


def resolve_role_model(settings: Mapping[str, Any], role_key: str, requested: str | None = None) -> str:
    """Model id for a role: ``TFL_MODEL_OVERRIDE`` > ``requested`` >
    ``settings.models.roles[role]`` > ``config.MODELS[role]`` > the role's own
    default. Fails closed: an unpriced id raises ``config.UnknownModelError``,
    and a model outside ``settings.models.allowed`` raises
    :class:`ModelNotAllowedError`."""
    models = _section(settings, "models")
    roles_cfg = models.get("roles") or {}
    want = requested or role_model_request(roles_cfg, role_key)
    model = config.resolve_model(role_key, want)
    allowed = models.get("allowed")
    if allowed:
        allowed_keys = {config.pricing_key(config.normalize_model(m)) for m in allowed}
        if config.pricing_key(model) not in allowed_keys:
            if _uses_role_default(roles_cfg, role_key, requested):
                # A role with only a code default (``general``: Haiku) must not make
                # valid settings unbuildable: it takes the cheapest allowed model.
                return cheapest_model(allowed)
            raise ModelNotAllowedError(
                f"role {role_key!r} resolved to {model!r}, which settings.models.allowed "
                f"does not list ({sorted(allowed)})")
    return model


def _uses_role_default(roles_cfg: Mapping[str, Any], role_key: str, requested: str | None) -> bool:
    """The role's model is its own code default: not in ``config.MODELS``,
    not configured in ``settings.models.roles``, not requested, no
    ``TFL_MODEL_OVERRIDE``."""
    return (requested is None and not roles_cfg.get(role_key) and role_key not in config.MODELS
            and not config.model_override())


def cheapest_model(models: Iterable[str]) -> str:
    """The model with the lowest worst case for a typical call (1000 input +
    1000 output tokens at config prices)."""
    ids = [config.normalize_model(m) for m in models]
    if not ids:
        raise ModelNotAllowedError("no model to choose from")
    return min(ids, key=lambda m: config.worst_case_usd(m, 1000, 1000))


def default_model_factory(role_key: str, model_id: str) -> Any:
    """A real ``ChatAnthropic`` for the role (``models.make_chat_model``).
    Building it sends nothing; only the ``--live`` path uses it. Marked
    ``tfl_live``: :func:`create_tfl_agent` then insists on a ledger with the
    per-day check and the shared spend log."""
    from .models import make_chat_model

    return make_chat_model(model_id, role=role_key if role_key in config.MODELS else None)


default_model_factory.tfl_live = True  # type: ignore[attr-defined]


def is_live_factory(factory: Any) -> bool:
    """A factory that builds real API models (:func:`default_model_factory`,
    or any callable carrying ``tfl_live = True``)."""
    return bool(getattr(factory, "tfl_live", False))


# ---------------------------------------------------------------------------
# Backend: /skills/ read-only
# ---------------------------------------------------------------------------

def _readonly_backend_class() -> type:
    from deepagents.backends import FilesystemBackend
    from deepagents.backends.protocol import DeleteResult, EditResult, FileUploadResponse, WriteResult

    class ReadOnlyFilesystemBackend(FilesystemBackend):
        """A ``FilesystemBackend`` that refuses every mutation (skills, theory)."""

        _MSG = "permission denied: this path is read-only"

        def write(self, file_path: str, content: str) -> WriteResult:  # noqa: ARG002
            return WriteResult(error=f"{self._MSG} ({file_path})")

        async def awrite(self, file_path: str, content: str) -> WriteResult:
            return self.write(file_path, content)

        def edit(self, file_path: str, old_string: str, new_string: str,  # noqa: ARG002
                 replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
            return EditResult(error=f"{self._MSG} ({file_path})")

        async def aedit(self, file_path: str, old_string: str, new_string: str,
                        replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
            return self.edit(file_path, old_string, new_string, replace_all)

        def delete(self, file_path: str) -> DeleteResult:
            return DeleteResult(error=f"{self._MSG} ({file_path})")

        async def adelete(self, file_path: str) -> DeleteResult:
            return self.delete(file_path)

        def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
            return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]

        async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
            return self.upload_files(files)

    return ReadOnlyFilesystemBackend


def __getattr__(name: str) -> Any:  # lazy: deepagents / langchain are the optional [deep] extra
    if name == "ReadOnlyFilesystemBackend":
        return _readonly_backend_class()
    if name == "ScriptedChatModel":
        return scripted_model_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def make_backend(*, default: Any = None, routes: Mapping[str, Any] | None = None,
                 skills_dir: Path | str | None = None, sandbox: Any = None) -> Any:
    """``CompositeBackend``: ``/skills/`` -> the package's skills directory,
    read-only; everything else -> ``default`` (a ``StateBackend``: scratch
    files in the graph state). ``routes`` adds more prefixes.

    ``sandbox`` (P5, a ``DockerSandbox`` or any deepagents
    ``SandboxBackendProtocol``): ``/workspace/`` is routed into the container
    (``sandbox.workspace.WorkspaceBackend``) and the default becomes
    ``SandboxedStateBackend`` -- still the state for files, the sandbox for
    ``execute`` -- so deepagents registers ``execute``; the task file under
    ``/input/`` stays read-only through that default."""
    from deepagents.backends import CompositeBackend, StateBackend

    ro = _readonly_backend_class()(root_dir=str(skills_dir or SKILLS_DIR), virtual_mode=True)
    all_routes: dict[str, Any] = {SKILLS_ROUTE: ro}
    if sandbox is not None:
        from .sandbox.workspace import WORKSPACE_ROUTE, SandboxedStateBackend, WorkspaceBackend

        all_routes[WORKSPACE_ROUTE] = WorkspaceBackend(sandbox)
        if default is None:
            default = SandboxedStateBackend(sandbox)
    all_routes.update(routes or {})
    return CompositeBackend(default=default if default is not None else StateBackend(), routes=all_routes)


def readonly_permissions(prefixes: Iterable[str] = (SKILLS_ROUTE, INPUT_ROUTE)) -> list[Any]:
    """Deny rules for the built-in write tools on the read-only routes (the
    backend refuses as well; the rule gives the model a clear message first)."""
    from deepagents.middleware.filesystem import FilesystemPermission

    paths: list[str] = []
    for p in prefixes:
        root = p.rstrip("/")
        paths += [root, f"{root}/**"]
    return [FilesystemPermission(operations=["write"], paths=paths, mode="deny")]


def backend_can_execute(backend: Any) -> bool:
    """deepagents would register a working ``execute`` for ``backend``
    (``middleware.filesystem.supports_execution``)."""
    from deepagents.middleware.filesystem import supports_execution

    return bool(supports_execution(backend))


def permissions_for(backend: Any) -> list[Any]:
    """The write-deny rules that fit ``backend``.

    Without execution: :func:`readonly_permissions` (``/skills`` and
    ``/input``, roots and contents). deepagents refuses rules next to an
    execution backend unless every rule path starts with a route prefix of the
    ``CompositeBackend`` (``FilesystemMiddleware._set_permissions``), so then
    only ``<route>/**`` for the read-only routes the backend has
    (``/skills/``, and ``/input/`` when routed); ``/input/`` in the state is
    protected by ``SandboxedStateBackend`` itself, and both routed backends
    refuse writes on their own."""
    if not backend_can_execute(backend):
        return readonly_permissions()
    from deepagents.middleware.filesystem import FilesystemPermission

    routes = getattr(backend, "routes", None) or {}
    paths = [f"{p.rstrip('/')}/**" for p in (SKILLS_ROUTE, INPUT_ROUTE) if p in routes]
    return [FilesystemPermission(operations=["write"], paths=paths, mode="deny")] if paths else []


# ---------------------------------------------------------------------------
# Sandbox (P5): which run has ``execute``
# ---------------------------------------------------------------------------

#: ``create_tfl_agent(sandbox=...)`` value that opens a Docker sandbox when
#: Docker and the ``tfl-sandbox`` image are there (the container starts lazily,
#: on the first file or execute call)
SANDBOX_AUTO = "auto"


@dataclasses.dataclass
class SandboxInfo:
    """The code sandbox of one agent build (``graph.tfl_sandbox``).

    ``available`` False: deepagents gets no execution backend, so ``execute``
    is not registered; the ``coder`` role is left out and the system prompt
    says why (``reason``). ``owned``: :func:`create_tfl_agent` opened the
    sandbox (``sandbox="auto"``) and :meth:`close` removes its container
    (the ``DockerSandbox`` finalizer also does at garbage collection / exit).
    A sandbox the caller passed is the caller's to close."""

    available: bool
    reason: str = ""
    sandbox: Any = None
    owned: bool = False
    kind: str | None = None

    @property
    def id(self) -> str | None:
        return str(getattr(self.sandbox, "id", "")) or None if self.sandbox is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "id": self.id, "kind": self.kind,
                "owned": self.owned}

    def close(self) -> None:
        if self.owned and self.sandbox is not None:
            close = getattr(self.sandbox, "close", None)
            if callable(close):
                close()


def resolve_sandbox(sandbox: Any, *, backend: Any = None, run_id: str | None = None) -> SandboxInfo:
    """What :func:`create_tfl_agent`'s ``sandbox`` means.

    * ``None`` -- no sandbox configured (the library default: building an
      agent never touches Docker);
    * ``False`` -- switched off;
    * ``"auto"`` / ``True`` -- a ``DockerSandbox`` when
      ``sandbox_status()`` says Docker, the daemon and the image are there
      (not started yet), else unavailable with that reason;
    * a ``SandboxBackendProtocol`` instance (a started ``DockerSandbox``, a
      test double) -- used as is.

    An explicit ``backend`` decides by itself (``backend_can_execute``);
    passing both is an error."""
    if backend is not None:
        if sandbox not in (None, False):
            raise ValueError("pass either backend= (a complete backend) or sandbox=, not both")
        if backend_can_execute(backend):
            return SandboxInfo(True, sandbox=getattr(backend, "default", backend), kind="backend")
        return SandboxInfo(False, "the backend given to create_tfl_agent cannot execute commands")
    if sandbox is None:
        return SandboxInfo(False, "no code sandbox was configured for this run")
    if sandbox is False:
        return SandboxInfo(False, "the code sandbox is switched off for this run")
    if sandbox is True or sandbox == SANDBOX_AUTO:
        from .sandbox import docker_backend

        ok, why = docker_backend.sandbox_status()
        if not ok:
            return SandboxInfo(False, f"Docker sandbox unavailable: {why}")
        sb = docker_backend.DockerSandbox(run_id=run_id, auto_start=True)
        return SandboxInfo(True, sandbox=sb, owned=True, kind="docker")
    from .sandbox.workspace import is_sandbox

    if is_sandbox(sandbox):
        kind = "docker" if type(sandbox).__name__ == "DockerSandbox" else type(sandbox).__name__
        return SandboxInfo(True, sandbox=sandbox, kind=kind)
    raise TypeError(f"sandbox must be None, False, True, {SANDBOX_AUTO!r} or a SandboxBackendProtocol, "
                    f"not {type(sandbox).__name__}")


def close_sandbox(graph: Any) -> None:
    """Remove the container of a sandbox :func:`create_tfl_agent` opened
    itself (``sandbox="auto"``); a no-op otherwise."""
    info = getattr(graph, "tfl_sandbox", None)
    if isinstance(info, SandboxInfo):
        info.close()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def _agent_middleware_base() -> type:
    from langchain.agents.middleware import AgentMiddleware

    return AgentMiddleware


def exclude_tools_middleware(excluded: Iterable[str], *, name: str = "TFLToolFilter") -> Any:
    """Middleware that removes the named tools from every model request (the
    built-in ``execute`` for everyone but the coder) and refuses a call to one
    of them anyway (a hallucinated or injected tool call still reaches the
    graph's tool node, which has every built-in tool registered): the tool is
    not run, the model gets an error ToolMessage."""
    base = _agent_middleware_base()
    excluded_set = frozenset(excluded)
    _name = name

    class TFLToolFilter(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            super().__init__()
            self.excluded = excluded_set

        @property
        def name(self) -> str:  # noqa: D401
            return _name

        def _filter(self, request: Any) -> Any:
            tools = [t for t in request.tools
                     if (t.get("name") if isinstance(t, dict) else getattr(t, "name", None)) not in self.excluded]
            return request.override(tools=tools) if len(tools) != len(request.tools) else request

        def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return handler(self._filter(request))

        async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return await handler(self._filter(request))

        def _refused(self, request: Any) -> Any:
            call = request.tool_call
            tool_name = call.get("name") if isinstance(call, Mapping) else getattr(call, "name", None)
            if tool_name not in self.excluded:
                return None
            from langchain_core.messages import ToolMessage

            return ToolMessage(
                content=f"Refused: the tool {tool_name!r} is not available to this agent.",
                tool_call_id=(call.get("id") if isinstance(call, Mapping) else getattr(call, "id", "")) or "",
                name=tool_name, status="error")

        def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            refused = self._refused(request)
            return refused if refused is not None else handler(request)

        async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            refused = self._refused(request)
            return refused if refused is not None else await handler(request)

    return TFLToolFilter()


def _is_submitted_report(msg: Any) -> bool:
    from langchain_core.messages import ToolMessage

    return (isinstance(msg, ToolMessage) and msg.name == SUBMIT_REPORT
            and msg.status != "error" and isinstance(msg.artifact, dict)
            and msg.artifact.get("ok") is True and isinstance(msg.artifact.get("report"), dict))


def report_accepted_last_turn(messages: Sequence[Any]) -> bool:
    """One of the ToolMessages answering the last AIMessage (the trailing
    run of ToolMessages) is an accepted ``submit_report``. ``submit_report``
    sent together with other tool calls need not be the last of them."""
    from langchain_core.messages import ToolMessage

    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            return False
        if _is_submitted_report(msg):
            return True
    return False


def report_gate_middleware() -> Any:
    """Ends the run once ``submit_report`` has returned a valid report: in
    ``before_model`` the ToolMessages of the last turn include that report,
    so the next model call is skipped (``jump_to: end``). A report rejected by
    validation (or, in P3, by the human at HITL 2) has no ``ok`` artifact, so
    the loop continues and the model can fix it."""
    from langchain.agents.middleware import hook_config

    base = _agent_middleware_base()

    class ReportGateMiddleware(base):  # type: ignore[misc, valid-type]
        @hook_config(can_jump_to=["end"])
        def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
            if report_accepted_last_turn(state.get("messages") or []):
                return {"jump_to": "end"}
            return None

        @hook_config(can_jump_to=["end"])
        async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return self.before_model(state, runtime)

    return ReportGateMiddleware()


# ---------------------------------------------------------------------------
# Run-wide call limits (settings.limits)
# ---------------------------------------------------------------------------

CALL_LIMIT_STOP = "call_limit"

LIMIT_WRAP_UP = (
    "CALL LIMIT WRAP-UP. {left} model call(s) left for you in this run, this one included "
    "(limits.max_model_calls = {limit}). "
    "Stop exploring now and {action}, marking unfinished parts as not verified.")

LIMIT_REFUSAL = (
    "LIMIT: model call refused before it was made: this run has used its {limit} model calls "
    "(limits.max_model_calls). No further model calls are made; the run ends without a new answer.")


class RunLimits:
    """Counters shared by the main agent and every subagent of one run
    (``settings.limits``): ``max_model_calls`` and ``max_tool_calls`` count
    run-wide; ``max_spawn_depth`` 0 forbids subagents. The last
    ``main_reserve`` model calls are kept for the main agent (its report).
    ``max_parallel`` bounds concurrent model calls of the run (the semaphore
    goes to every ``ConcurrencyMiddleware`` of the run, inside the process-wide
    ``TFL_MAX_CONCURRENCY`` one)."""

    #: tool names that are never refused by the tool-call limit
    ALWAYS_ALLOWED = frozenset({"submit_report", "write_todos"})
    SPAWNING_TOOLS = frozenset({"task", "spawn_subagent", "run_workflow"})

    def __init__(self, *, max_model_calls: int, max_tool_calls: int, max_parallel: int,
                 max_spawn_depth: int) -> None:
        import threading

        self.max_model_calls = int(max_model_calls)
        self.max_tool_calls = int(max_tool_calls)
        self.max_parallel = int(max_parallel)
        self.max_spawn_depth = int(max_spawn_depth)
        self.main_reserve = 2 if self.max_model_calls >= 10 else (1 if self.max_model_calls >= 3 else 0)
        self.model_calls = 0
        self.tool_calls = 0
        self.refused_model_calls = 0
        self.refused_tool_calls = 0
        self.lock = threading.Lock()
        self.semaphore = threading.BoundedSemaphore(self.max_parallel)

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> RunLimits:
        lim = _section(settings, "limits")
        return cls(max_model_calls=int(lim.get("max_model_calls", 80)),
                   max_tool_calls=int(lim.get("max_tool_calls", 300)),
                   max_parallel=int(lim.get("max_parallel", 4)),
                   max_spawn_depth=int(lim.get("max_spawn_depth", 2)))

    def take_model_call(self, *, main: bool) -> tuple[bool, int]:
        """``(admitted, calls left after this one)``."""
        with self.lock:
            cap = self.max_model_calls if main else self.max_model_calls - self.main_reserve
            if self.model_calls >= cap:
                self.refused_model_calls += 1
                return False, max(0, self.max_model_calls - self.model_calls)
            self.model_calls += 1
            return True, self.max_model_calls - self.model_calls

    def take_tool_call(self, name: str | None) -> str | None:
        """``None`` (allowed) or the refusal reason."""
        with self.lock:
            if name in self.SPAWNING_TOOLS and self.max_spawn_depth < 1:
                self.refused_tool_calls += 1
                return f"subagents are disabled in this run (limits.max_spawn_depth = {self.max_spawn_depth})"
            if name in self.ALWAYS_ALLOWED:
                self.tool_calls += 1
                return None
            if self.tool_calls >= self.max_tool_calls:
                self.refused_tool_calls += 1
                return f"this run has used its {self.max_tool_calls} tool calls (limits.max_tool_calls)"
            self.tool_calls += 1
            return None

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {"max_model_calls": self.max_model_calls, "model_calls": self.model_calls,
                    "max_tool_calls": self.max_tool_calls, "tool_calls": self.tool_calls,
                    "max_parallel": self.max_parallel, "max_spawn_depth": self.max_spawn_depth,
                    "refused_model_calls": self.refused_model_calls,
                    "refused_tool_calls": self.refused_tool_calls}


def call_limit_middleware(limits: RunLimits, *, role: str) -> Any:
    """Middleware enforcing :class:`RunLimits` for one agent. Place it outside
    (before) the budget middleware, so a refused call reserves no money. Near
    the model-call limit a wrap-up note is appended to the request (at the
    end, outside the cached prefix); past it the call is not made and an
    ``AIMessage`` without tool calls ends the agent's loop
    (``response_metadata.stop_reason == "call_limit"``)."""
    base = _agent_middleware_base()
    is_main = role == "main"

    class CallLimitMiddleware(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            super().__init__()
            self.limits = limits
            self.role = role

        @property
        def name(self) -> str:  # noqa: D401
            return "TFLCallLimit"

        def _before(self, request: Any) -> tuple[Any, Any]:
            from langchain.agents.middleware.types import ModelResponse
            from langchain_core.messages import AIMessage, HumanMessage

            ok, left = self.limits.take_model_call(main=is_main)
            if not ok:
                msg = AIMessage(content=LIMIT_REFUSAL.format(limit=self.limits.max_model_calls),
                                additional_kwargs={"tfl_limit": "refused"},
                                response_metadata={"stop_reason": CALL_LIMIT_STOP,
                                                   "tfl_limits": self.limits.status()})
                return None, ModelResponse(result=[msg])
            # calls this agent may still make, this one included (subagents
            # leave the main agent's reserve untouched)
            own_left = left + 1 if is_main else left + 1 - self.limits.main_reserve
            if own_left <= (max(1, self.limits.main_reserve) if is_main else 1):
                action = ("submit the report (submit_report)" if is_main
                          else "return your final answer to the caller")
                note = HumanMessage(content=LIMIT_WRAP_UP.format(left=own_left, limit=self.limits.max_model_calls,
                                                                 action=action),
                                    additional_kwargs={"tfl_limit": "wrap_up"})
                request = request.override(messages=[*request.messages, note])
            return request, None

        def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            request, refused = self._before(request)
            return refused if refused is not None else handler(request)

        async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            request, refused = self._before(request)
            return refused if refused is not None else await handler(request)

        def _tool_refusal(self, request: Any) -> Any:
            call = request.tool_call
            get = call.get if isinstance(call, Mapping) else (lambda k, d=None: getattr(call, k, d))
            reason = self.limits.take_tool_call(get("name"))
            if reason is None:
                return None
            from langchain_core.messages import ToolMessage

            return ToolMessage(content=f"Refused: {reason}. Continue with what is established or submit the "
                                       "report (submit_report).",
                               tool_call_id=get("id") or "", name=get("name"), status="error",
                               additional_kwargs={"tfl_limit": "tool_refused"})

        def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            refused = self._tool_refusal(request)
            return refused if refused is not None else handler(request)

        async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            refused = self._tool_refusal(request)
            return refused if refused is not None else await handler(request)

    return CallLimitMiddleware()


def default_middleware(settings: Mapping[str, Any], role_key: str, *,
                       run_semaphore: Any = None) -> list[Any]:
    """The budget / evidence / concurrency middleware slot for one agent.

    HOOK for the phases that write ``middleware/``: each of
    ``middleware.budget``, ``middleware.concurrency`` and ``middleware.evidence``
    may expose ``make_middleware(settings, role=...)`` returning a middleware
    instance or a list of them, or ``None``. A module that does not (yet)
    provides nothing. Pass ``middleware=`` / ``subagent_middleware=`` to
    :func:`create_tfl_agent` to bypass this. ``run_semaphore`` (the run's
    ``limits.max_parallel`` bound) goes to the concurrency middleware."""
    import importlib

    out: list[Any] = []
    for mod_name in ("budget", "concurrency", "evidence"):
        try:
            mod = importlib.import_module(f"{__package__}.middleware.{mod_name}")
        except ImportError:
            continue
        maker = getattr(mod, "make_middleware", None)
        if not callable(maker):
            continue
        extra: dict[str, Any] = {}
        if mod_name == "concurrency" and run_semaphore is not None:
            extra["run_semaphore"] = run_semaphore
        if mod_name == "budget":
            # the model the role really resolves to (a role without a
            # config.MODELS entry, "general", falls back per resolve_role_model)
            extra["model"] = resolve_role_model(settings, role_key)
        made = maker(settings, role=role_key, **extra)
        if made is None:
            continue
        out.extend(made if isinstance(made, (list, tuple)) else [made])
    return out


# ---------------------------------------------------------------------------
# submit_report (the P3 schema of tools/report.py; HITL 2 interrupts on it)
# ---------------------------------------------------------------------------

SUBMIT_REPORT = "submit_report"


def _report_args_models() -> tuple[type[BaseModel], type[BaseModel]]:
    """``(ReportStep, SubmitReportArgs)`` of the report tool: the P3 schema of
    ``tools/report.py`` (a superset of the P2 schema, so P2 mock reports
    still validate)."""
    from .tools.report import report_args_models

    return report_args_models()


def make_submit_report_tool(evidence: Any = None) -> Any:
    """The ``submit_report`` LangChain tool: ``tools.report.make_submit_report_tool``
    (P3 schema: lemmas, claims, construction, conclusion, Lean block on top
    of the P2 fields). It validates the structured fields and returns them as
    the ToolMessage artifact ``{"ok": true, "report": {...}}``;
    :class:`ReportGateMiddleware` then ends the run (HITL 2, when on,
    interrupts before the tool runs). It never sets trust: the evidence and
    trust labels are computed by code (:func:`collect_result`, the renderer).
    ``evidence`` (the run's ledger): a report citing an id not in it is refused."""
    from .tools.report import make_submit_report_tool as _make

    return _make(evidence)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

def sandbox_prompt_line(info: SandboxInfo | Mapping[str, Any]) -> str:
    """The "This run" line about the code sandbox."""
    available = info.available if isinstance(info, SandboxInfo) else bool(info.get("available"))
    reason = info.reason if isinstance(info, SandboxInfo) else str(info.get("reason") or "")
    if available:
        return ("- Code sandbox: available (Docker, no network, the pipeline packages read-only at /opt/tfl). "
                "Only the `coder` role runs code (`execute`, files under /workspace/); you have no `execute`. Sandbox "
                "output is at most `well_formed` evidence and never the basis of the verdict.")
    return (f"- Code sandbox: NOT available in this run ({reason}). There is no `execute` tool and no `coder` "
            "role: do not plan code runs; use the oracle tools, and say in the report that no code was run.")


def main_system_prompt(settings: Mapping[str, Any], *, template: str = "full_proof",
                       hitl: Mapping[str, bool] | None = None,
                       sandbox: SandboxInfo | Mapping[str, Any] | None = None,
                       orchestration: bool = False) -> str:
    """``prompts/main.md`` plus the run's own parameters (template, budget,
    allowed models, HITL points, the sandbox, the orchestration tools).
    Static per run, so prompt caching keeps working."""
    budget = _section(settings, "budget")
    models = _section(settings, "models")
    limits = _section(settings, "limits")
    lines = [
        load_prompt("main").rstrip(),
        "",
        "## This run",
        f"- Report template requested: `{template}` (pass it to submit_report).",
    ]
    points = dict(hitl or {})
    if points.get("formalization"):
        lines.append(
            "- HITL 1 is on: after each ir-builder answer a human reviews the transcription, the IR "
            "restatement and sample words. Their decision arrives as a message starting with "
            "\"HITL 1\": on approval go on with that IR; on approval with edits use exactly the "
            "edited text / IR; on rejection fix the formalization (intake / ir-builder again, with "
            "the comment) before any proof. An IR you validate yourself with validate_ir is reviewed "
            "the same way. Only oracle results for the IR approved at HITL 1 count for the verdict "
            "(checked by code): after an edit re-run the checks you rely on against the new IR. An IR "
            "built in a workflow step or by a spawned subagent is NOT reviewed: build the IR with the "
            "ir-builder role (task), or call validate_ir on such an IR yourself before any proof.")
    if points.get("report"):
        lines.append(
            "- HITL 2 is on: a human reviews submit_report before it is accepted. They may approve "
            "it, edit fields, or return it with a comment; a returned report comes back as an error "
            "result of submit_report with the comment: address every point, then call "
            "submit_report again.")
    if points.get("execute"):
        lines.append("- A human approves every `execute` call of the coder before it runs.")
    if budget:
        lines.append(
            "- Budget: per task ${:.2f}, per subagent at most ${:.2f}, reserve for the report ${:.2f}.".format(
                float(budget.get("per_task_usd", 0)), float(budget.get("per_subagent_max_usd", 0)),
                report_reserve(float(budget.get("per_task_usd", 0)), float(budget.get("reserve_for_report_usd", 0)))))
    if models.get("allowed"):
        lines.append("- Allowed models: " + ", ".join(f"`{m}`" for m in models["allowed"]) + ".")
    if limits:
        lines.append(
            "- Limits (enforced by code for the whole run, subagents included): at most {} model calls "
            "(the last ones are kept for your report), at most {} tool calls (submit_report is never "
            "refused), at most {} model calls at the same time{}.".format(
                limits.get("max_model_calls"), limits.get("max_tool_calls"), limits.get("max_parallel"),
                "; subagents are disabled (max_spawn_depth = 0)" if int(limits.get("max_spawn_depth", 1)) < 1
                else ""))
    if orchestration and int(limits.get("max_spawn_depth", 1)) < 1:
        lines.append(
            "- Orchestration tools: only `budget_status` (free). `spawn_subagent` and `run_workflow` are "
            "not available in this run (max_spawn_depth = 0).")
    elif orchestration:
        lines.append(
            "- Orchestration tools: `budget_status` (free), `spawn_subagent` (one ad-hoc agent with the "
            "model, tools and budget you choose), `run_workflow` (a plan of steps, independent ones in "
            "parallel, each step with its own model and budget; `dry_run` checks a plan first). Their "
            "answers are not evidence: cite the evidence ids of the oracle calls they return.")
    if sandbox is not None:
        lines.append(sandbox_prompt_line(sandbox))
    return "\n".join(lines) + "\n"


def main_tools(evidence: Any = None) -> list[Any]:
    """The P1 tools of the main agent (every registered group) plus
    ``submit_report`` (over ``evidence``, the run's ledger: it refuses a report
    citing ids that are not in it)."""
    from .tools import base

    base.load_groups([g for g in MAIN_TOOL_GROUPS if g in base.GROUP_MODULES])
    tools = [t for t in base.REGISTRY.langchain_tools(groups=MAIN_TOOL_GROUPS) if t.name != SUBMIT_REPORT]
    tools.append(make_submit_report_tool(evidence))
    return tools


def create_tfl_agent(
    settings: Any = None,
    *,
    model_factory: ModelFactory | None = None,
    checkpointer: Any = None,
    middleware: Sequence[Any] | None = None,
    subagent_middleware: Sequence[Any] | Callable[[str], Sequence[Any]] | None = None,
    interrupt_on: Mapping[str, Any] | None = None,
    backend: Any = None,
    template: str = "full_proof",
    roles: Sequence[str] | None = None,
    extra_tools: Sequence[Any] = (),
    store: Any = None,
    name: str = "tfl-deep-agent",
    debug: bool = False,
    budget: Any = None,
    budget_options: Mapping[str, Any] | None = None,
    evidence: Any = None,
    hitl: Any = None,
    task_source: Mapping[str, Any] | None = None,
    sandbox: Any = None,
    orchestration: bool = True,
) -> Any:
    """Build the TFL Deep Agent graph.

    Orchestration (P4, ``orchestration=True``): the main agent gets
    ``spawn_subagent``, ``run_workflow`` and ``budget_status``
    (``orchestration/spawn.py``) over one ``OrchestrationContext`` with the
    run's settings, model factory, budget scope, evidence ledger and
    ``RunLimits`` (children count against the run's call limits and charge
    allocations under the run's budget). Tools of the same name in
    ``extra_tools`` replace them. The context is ``graph.tfl_orchestration``.

    Sandbox (P5, ``sandbox=``; :func:`resolve_sandbox`): ``None`` (default)
    or ``False`` -- no sandbox; ``"auto"`` / ``True`` -- a ``DockerSandbox``
    if Docker and the image are there; or a ``SandboxBackendProtocol``
    instance. With a sandbox, :func:`make_backend` routes ``/workspace/`` into
    it and ``execute`` is registered; only the ``coder`` role keeps it (every
    other agent has it filtered out of its requests and refused at dispatch).
    Without one, ``execute`` is not registered, the ``coder`` role is left out
    and the system prompt says that no sandbox is available and why. An
    explicit ``backend=`` decides by itself whether it executes.
    ``graph.tfl_sandbox`` is the :class:`SandboxInfo`; :func:`close_sandbox`
    removes a container opened by ``"auto"``.

    Lean (P6): the ``lean`` tool group (``lean_statement``, ``lean_check``)
    is part of the main agent's tools and of the ``lean-formalizer`` role; a
    ``lean_check`` with status ``proved`` for the task's IR is a ``verified``
    basis for its side of the verdict (R-Lean, :func:`verdict_gate`).

    Evidence (``evidence=``): an ``evidence.ledger.EvidenceLedger`` makes every
    stack (main and each role) record its tool calls into it
    (``EvidenceMiddleware`` through the default middleware hook; the build runs
    inside ``use_evidence(ledger)``). ``None``: the ledger of an enclosing
    ``use_evidence`` block, if any (else nothing is recorded). The ledger used
    is attached as ``graph.tfl_evidence``.

    HITL (``hitl=``; ``hitl.py``): ``None`` / ``False`` = off (library default;
    the CLI and ``hitl.start_run`` pass the settings). ``True`` = the points of
    ``settings.hitl``; a mapping ``{"formalization", "report", "execute"}``
    picks them. HITL 1 (``formalization``) interrupts the main agent after an
    ir-builder answer (``hitl.FormalizationReviewMiddleware``), HITL 2
    (``report``) interrupts on ``submit_report`` (deepagents ``interrupt_on``
    with a validity predicate), ``execute`` interrupts the coder's sandbox
    calls. Any point needs a ``checkpointer``. ``task_source`` (the input file's
    ``TaskInput.metadata()``) is shown at HITL 1. The points in force are
    attached as ``graph.tfl_hitl``.

    Budget (one ledger for the whole run): the main agent and every fixed role
    get a ``BudgetMiddleware`` over the same scope, and the main agent a
    ``SubagentBudgetMiddleware`` that gives each ``task`` call its own
    allocation (at most ``per_subagent_max_usd``) under that scope, so every
    subagent call is charged to the run's ledger, inside its allocation.
    ``budget`` picks the scope:

    * a ``BudgetLedger`` (its root) or a ``BudgetNode``: that scope (the CLI /
      UI pass ``BudgetLedger.for_task``, which writes the per-day log);
    * ``None`` (default): the scope of an enclosing ``budget.use_budget``
      block, else a fresh in-memory ledger from ``settings.budget`` (no
      per-day log: building an agent never writes files);
    * ``False``: no budget middleware at all (tests of other parts).

    ``budget_options`` go to every ``BudgetMiddleware`` (for example
    ``{"missing_usage": "zero"}``). The scope used is attached to the graph as
    ``graph.tfl_budget`` (``None`` without a budget). Explicit ``middleware=``
    / ``subagent_middleware=`` lists replace the default hook, budget included.

    Prompt caching: deepagents puts its Anthropic prompt-caching middleware
    into the main stack and every subagent stack itself (a no-op for
    non-Anthropic models such as the scripted ones), after our middleware, so
    the system prompt and tool list are the cached prefix and the budget's
    wrap-up note (appended at the end of the request) does not break it.

    Args:
        settings: run settings (see :func:`settings_dict`); ``None`` loads them.
        model_factory: ``(role_key, model_id) -> BaseChatModel``, required
            (``None`` raises ``TypeError``, so a library caller never builds
            real API models by accident). :func:`default_model_factory` (real
            ``ChatAnthropic``, used only with ``--live``) is a *live* factory:
            with it ``budget=None`` outside a ``use_budget`` block means
            ``BudgetLedger.for_task`` (per-day check, shared spend log) and
            ``budget=False`` is refused. Mock runs pass
            :func:`scripted_model_factory`.
        checkpointer: LangGraph checkpointer (P3: SQLite in the run dir; needed
            for HITL interrupts).
        middleware: extra middleware of the main agent (budget, evidence,
            concurrency). ``None`` -> :func:`default_middleware` for "main".
        subagent_middleware: the same for each fixed role: a list (shared by
            every role) or ``role_key -> list``. ``None`` ->
            :func:`default_middleware` per role.
        interrupt_on: HITL config per tool name (P3 sets ``submit_report``
            from ``settings.hitl``); needs a checkpointer.
        backend: override the ``CompositeBackend`` from :func:`make_backend`.
        template: the requested report template (named in the system prompt).
        roles: subset of ``roles.ROLES`` keys to expose (default: all).
        extra_tools: more tools for the main agent (P4 orchestration tools).
    """
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; one of {TEMPLATES}")
    if model_factory is None:
        raise TypeError(
            "create_tfl_agent needs an explicit model_factory: scripted_model_factory(...) for mock runs, "
            "or default_model_factory for live API runs (then a budget ledger with the per-day log is used)")
    cfg = settings_dict(settings)
    if is_live_factory(model_factory):
        from .middleware import budget as budget_mod

        if budget is False:
            raise ValueError("a live model factory needs a budget: budget=False is only for scripted models")
        if budget is None and budget_mod.current_budget() is None:
            from . import settings as settings_mod

            # live without a ledger: the per-task ledger with the per-day check
            # and the shared spend log, never a fresh in-memory one
            budget = budget_mod.BudgetLedger.for_task(settings_mod.validate_settings(cfg))
    points = hitl_points(cfg, hitl)
    if any(points.values()) and checkpointer is None:
        raise ValueError("HITL needs a checkpointer (hitl.start_run uses SQLite in the run dir); "
                         "pass checkpointer=..., or hitl=False")
    scope_cm, node = _budget_scope(budget, cfg, budget_options)
    evidence_cm, ledger = _evidence_scope(evidence)
    sandbox_info = resolve_sandbox(sandbox, backend=backend, run_id=name)
    try:
        with scope_cm, evidence_cm:
            graph = _build_agent(
                cfg, model_factory=model_factory, checkpointer=checkpointer, middleware=middleware,
                subagent_middleware=subagent_middleware, interrupt_on=interrupt_on, backend=backend,
                template=template, roles=roles, extra_tools=extra_tools, store=store, name=name, debug=debug,
                hitl=points, evidence_ledger=ledger, task_source=task_source, sandbox=sandbox_info,
                orchestration=orchestration)
    except BaseException:
        sandbox_info.close()  # a container opened for a build that failed
        raise
    for attr, value in (("tfl_budget", node), ("tfl_evidence", ledger), ("tfl_hitl", points),
                        ("tfl_sandbox", sandbox_info)):
        try:
            setattr(graph, attr, value)
        except (AttributeError, TypeError):  # pragma: no cover - a graph class without __dict__
            pass
    return graph


HITL_POINTS: tuple[str, ...] = ("formalization", "report", "execute")


def hitl_points(settings: Mapping[str, Any], hitl: Any) -> dict[str, bool]:
    """The HITL points in force: ``None`` / ``False`` -> all off, ``True`` ->
    ``settings.hitl``, a mapping -> its flags (missing ones off)."""
    if hitl is None or hitl is False:
        return dict.fromkeys(HITL_POINTS, False)
    if hitl is True:
        src: Mapping[str, Any] = _section(settings, "hitl")
    elif isinstance(hitl, Mapping):
        src = hitl
    else:
        dump = getattr(hitl, "model_dump", None)
        if not callable(dump):
            raise TypeError(f"hitl must be None, a bool or a mapping, not {type(hitl).__name__}")
        src = dump()
    unknown = sorted(set(src) - set(HITL_POINTS))
    if unknown:
        raise ValueError(f"unknown HITL point(s) {unknown}; known: {list(HITL_POINTS)}")
    return {p: bool(src.get(p, False)) for p in HITL_POINTS}


def _evidence_scope(evidence: Any) -> tuple[Any, Any]:
    """``(context manager, ledger)`` for :func:`create_tfl_agent`'s ``evidence``."""
    import contextlib

    from .evidence.ledger import EvidenceLedger, current_evidence, use_evidence

    if evidence is None:
        return contextlib.nullcontext(), current_evidence()
    if not isinstance(evidence, EvidenceLedger):
        raise TypeError(f"evidence must be an EvidenceLedger or None, not {type(evidence).__name__}")
    return use_evidence(evidence), evidence


def _budget_scope(budget: Any, cfg: Mapping[str, Any],
                  options: Mapping[str, Any] | None) -> tuple[Any, Any]:
    """``(context manager, node)`` for :func:`create_tfl_agent`'s ``budget``."""
    import contextlib

    from .middleware import budget as budget_mod

    opts = dict(options or {})
    if budget is False:
        return budget_mod.no_budget(), None
    if budget is None:
        current = budget_mod.current_budget()
        if current is not None and not opts:
            return contextlib.nullcontext(current), current
        if current is not None:
            return budget_mod.use_budget(current, **opts), current
        b = _section(cfg, "budget")
        budget = budget_mod.BudgetLedger(
            float(b["per_task_usd"]),
            reserve_for_report_usd=report_reserve(float(b["per_task_usd"]), float(b.get("reserve_for_report_usd", 0.0))),
            per_subagent_max_usd=b.get("per_subagent_max_usd"), per_day_usd=None, spend_log=None)
    if isinstance(budget, budget_mod.BudgetLedger):
        node = budget.root
    elif isinstance(budget, budget_mod.BudgetNode):
        node = budget
    else:
        raise TypeError(f"budget must be a BudgetLedger, a BudgetNode, None or False, not {type(budget).__name__}")
    return budget_mod.use_budget(node, **opts), node


def _with_subagent_budget(mw: list[Any]) -> list[Any]:
    """Insert a ``SubagentBudgetMiddleware`` right after the main agent's
    ``BudgetMiddleware`` (inside it: the wrap-up refusal of ``task`` comes
    first), unless the list has one already or has no budget."""
    from .middleware import budget as budget_mod

    if any(isinstance(m, budget_mod.SubagentBudgetMiddleware) for m in mw):
        return mw
    for i, m in enumerate(mw):
        if isinstance(m, budget_mod.BudgetMiddleware):
            return [*mw[:i + 1], budget_mod.SubagentBudgetMiddleware(m._base_node), *mw[i + 1:]]
    return mw


def _build_agent(
    cfg: dict[str, Any], *, model_factory: ModelFactory | None, checkpointer: Any,
    middleware: Sequence[Any] | None,
    subagent_middleware: Sequence[Any] | Callable[[str], Sequence[Any]] | None,
    interrupt_on: Mapping[str, Any] | None, backend: Any, template: str, roles: Sequence[str] | None,
    extra_tools: Sequence[Any], store: Any, name: str, debug: bool,
    hitl: Mapping[str, bool] | None = None, evidence_ledger: Any = None,
    task_source: Mapping[str, Any] | None = None, sandbox: SandboxInfo | None = None,
    orchestration: bool = True,
) -> Any:
    from deepagents import create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware

    factory = model_factory or default_model_factory
    role_keys = list(roles) if roles is not None else list(ROLES)
    unknown = [r for r in role_keys if r not in ROLES]
    if unknown:
        raise ValueError(f"unknown role(s) {unknown}; known: {sorted(ROLES)}")
    if "general" not in role_keys:
        role_keys.append("general")  # always replace deepagents' default general-purpose agent

    sandbox = sandbox if sandbox is not None else resolve_sandbox(None, backend=backend)
    if not sandbox.available:
        # no execution backend: no execute, and no role that exists only to run code
        role_keys = [r for r in role_keys if not ROLES[r].allow_sandbox]
    limits = RunLimits.from_settings(cfg)
    if backend is None:
        backend = make_backend(sandbox=sandbox.sandbox if sandbox.available else None)
    models: dict[str, Any] = {}

    def model_for(role_key: str) -> Any:
        # one instance per role: the agent and its summarization use the same one
        if role_key not in models:
            models[role_key] = factory(role_key, resolve_role_model(cfg, role_key))
        return models[role_key]

    def extra_mw_for(role_key: str) -> list[Any]:
        if subagent_middleware is None:
            return default_middleware(cfg, role_key, run_semaphore=limits.semaphore)
        if callable(subagent_middleware):
            return list(subagent_middleware(role_key))
        return list(subagent_middleware)

    def role_mw(role_key: str) -> list[Any]:
        mw = [call_limit_middleware(limits, role=role_key), *extra_mw_for(role_key)]
        if not ROLES[role_key].allow_sandbox:
            mw.append(exclude_tools_middleware(SANDBOX_ONLY_TOOLS))
        return _with_budgeted_summarization(mw, model_for(role_key), backend)

    from .roles import build_subagents

    skills_sources = [SKILLS_ROUTE]
    subagents = build_subagents(model_for_role=model_for, middleware_for_role=role_mw,
                                skills_sources=skills_sources, roles=role_keys)

    main_mw = (list(middleware) if middleware is not None
               else default_middleware(cfg, "main", run_semaphore=limits.semaphore))
    main_mw = _with_subagent_budget(main_mw)
    points = dict(hitl or {})
    hitl_mw: list[Any] = []
    all_interrupts: dict[str, Any] = {}
    if any(points.values()):
        from . import hitl as hitl_mod

        if points.get("formalization"):
            hitl_mw.append(hitl_mod.FormalizationReviewMiddleware(
                evidence=evidence_ledger, source=task_source))
        if points.get("report"):
            all_interrupts[SUBMIT_REPORT] = hitl_mod.report_interrupt_config(
                evidence_ledger, formalization_on=bool(points.get("formalization")))
        if points.get("execute") and sandbox.available:
            # inherited by every declarative subagent; only the coder has execute
            # (no sandbox: execute is not registered, nothing to interrupt)
            all_interrupts["execute"] = hitl_mod.execute_interrupt_config()
    all_interrupts.update(interrupt_on or {})
    main_mw = [TodoListMiddleware(), call_limit_middleware(limits, role="main"), *main_mw,
               exclude_tools_middleware(SANDBOX_ONLY_TOOLS), *hitl_mw, report_gate_middleware()]
    main_mw = _with_budgeted_summarization(main_mw, model_for("main"), backend)

    extra = list(extra_tools)
    orch_ctx, orch_tools = _orchestration_tools(cfg, factory, limits, evidence_ledger, enabled=orchestration,
                                                taken={getattr(t, "name", None) for t in extra})
    graph = create_deep_agent(
        model=model_for("main"),
        tools=[*main_tools(evidence_ledger), *orch_tools, *extra],
        system_prompt=main_system_prompt(cfg, template=template, hitl=points, sandbox=sandbox,
                                         orchestration=bool(orch_tools) or any(
                                             getattr(t, "name", None) in _orchestration_names() for t in extra)),
        middleware=main_mw,
        subagents=subagents,
        skills=skills_sources,
        permissions=permissions_for(backend),
        backend=backend,
        interrupt_on=all_interrupts or None,
        checkpointer=checkpointer,
        store=store,
        name=name,
        debug=debug,
    )
    for attr, value in (("tfl_limits", limits), ("tfl_orchestration", orch_ctx)):
        try:
            setattr(graph, attr, value)
        except (AttributeError, TypeError):  # pragma: no cover
            pass
    return graph


def _orchestration_names() -> tuple[str, ...]:
    from .orchestration.spawn import ORCHESTRATION_TOOLS

    return tuple(ORCHESTRATION_TOOLS)


def _orchestration_tools(cfg: Mapping[str, Any], factory: Any, limits: RunLimits, evidence_ledger: Any, *,
                         enabled: bool, taken: set[Any]) -> tuple[Any, list[Any]]:
    """``(OrchestrationContext | None, tools)`` for the main agent (P4).

    The context shares the run's ``RunLimits`` (children count against the
    same call limits and depth) and evidence ledger, and takes the budget of
    the enclosing ``use_budget`` scope -- :func:`create_tfl_agent` builds inside
    it, so allocations are children of the run's budget node (a live factory
    without a budget is refused before this, by :func:`create_tfl_agent`).
    Names already in ``taken`` (the caller's ``extra_tools``) are left to the
    caller's tools. With ``limits.max_spawn_depth`` < 1 only ``budget_status``
    is bound: every ``spawn_subagent`` / ``run_workflow`` call would be refused."""
    if not enabled:
        return None, []
    from .orchestration.spawn import STATUS_TOOL, OrchestrationContext, make_orchestration_tools

    ctx = OrchestrationContext.create(cfg, model_factory=factory, budget=None,
                                      evidence=evidence_ledger if evidence_ledger is not None else False,
                                      limits=limits)
    names = [n for n in _orchestration_names() if n not in taken
             and (limits.max_spawn_depth >= 1 or n == STATUS_TOOL)]
    return ctx, make_orchestration_tools(ctx, names=names) if names else []


def _with_budgeted_summarization(mw: list[Any], model: Any, backend: Any) -> list[Any]:
    """Add a summarization middleware whose summary call goes through this
    stack's ``BudgetMiddleware`` (and ``ConcurrencyMiddleware``). It has
    deepagents' name ``SummarizationMiddleware``, so ``create_deep_agent``
    puts it in place of its own (same position, same thresholds from
    ``create_summarization_middleware``); deepagents' one would call the
    model outside every user middleware, uncharged. A stack without a budget
    middleware, or a model that is not a ``BaseChatModel``, keeps deepagents'
    default."""
    from .middleware import budget as budget_mod
    from .middleware import concurrency as concurrency_mod

    budget_mw = next((m for m in mw if isinstance(m, budget_mod.BudgetMiddleware)), None)
    if budget_mw is None or any(getattr(m, "name", None) == "SummarizationMiddleware" for m in mw):
        return mw
    from deepagents.middleware.summarization import create_summarization_middleware

    wrappers = [m for m in mw if isinstance(m, concurrency_mod.ConcurrencyMiddleware)]
    try:
        summarization = create_summarization_middleware(
            model, backend, summarizer=budget_mod.budgeted_summarizer(model, budget_mw, wrappers=wrappers))
    except TypeError:  # not a BaseChatModel
        return mw
    return [*mw, summarization]


def run_task(agent: Any, task_text: str, *, thread_id: str | None = None,
             config_overrides: Mapping[str, Any] | None = None,
             files: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Invoke the agent on one task text and return the final state.
    ``files`` seeds the state's files (``{"/input/task.pdf": FileData}``,
    ``intake.files.TaskInput.to_file_data``). With HITL the state may stop at
    an interrupt (``"__interrupt__"`` in the returned state); ``hitl.py``
    drives that case."""
    from langchain_core.messages import HumanMessage

    run_config: dict[str, Any] = dict(config_overrides or {})
    if thread_id is not None:
        run_config.setdefault("configurable", {})["thread_id"] = thread_id
    inputs: dict[str, Any] = {"messages": [HumanMessage(content=task_text)]}
    if files:
        inputs["files"] = dict(files)
    return agent.invoke(inputs, run_config or None)



# ---------------------------------------------------------------------------
# Verdict gate (docs/VERDICT_POLICY.md R1-R6, applied by code to the report)
# ---------------------------------------------------------------------------

#: per class: the policy token of a constructive / destructive / inconclusive
#: verdict (VERDICT_POLICY R1: cfl/dcfl null, ll uncertain, reg partial)
VERDICT_TOKENS: dict[str, dict[str, str | None]] = {
    "reg": {"constructive": "regular", "destructive": "non_regular", "inconclusive": "partial"},
    "cfl": {"constructive": "cfl", "destructive": "non_cfl", "inconclusive": None},
    "dcfl": {"constructive": "dcfl", "destructive": "non_dcfl", "inconclusive": None},
    "ll": {"constructive": "ll", "destructive": "not_ll", "inconclusive": "uncertain"},
}

_INCONCLUSIVE_WORDS = ("unknown", "null", "none", "uncertain", "partial", "inconclusive", "undetermined",
                       "undecided", "not established", "неизвестно", "не установлено")
_NEGATIONS = ("not ", "non ", "non", "no ", "не", "нe ")

#: the argument naming the artifact a check is about (two checks of the same
#: artifact share it)
_ARTIFACT_KEYS = ("dfa", "regex", "grammar", "dpda", "pda", "automaton")


def verdict_direction(verdict: str, answer: str | None = None) -> tuple[str, str | None]:
    """``(direction, note)``: ``constructive`` / ``destructive`` /
    ``inconclusive`` for the verdict text, conservatively (a negation not at
    the start makes the text unclear), reconciled with the report's ``answer``
    field. ``note`` explains an inconclusive outcome caused by the parse."""
    import re

    text = re.sub(r"[\s_\-]+", " ", (verdict or "").strip().lower())
    parsed: str | None
    if not text or text in _INCONCLUSIVE_WORDS or text.startswith(_INCONCLUSIVE_WORDS):
        parsed = "inconclusive"
    elif text.startswith(_NEGATIONS):
        parsed = "destructive"
    elif re.search(r"\b(not|non|no)\b|(^|\s)не(\s|$)", text):
        parsed = None  # e.g. "CFL but not DCFL": unclear without the answer field
    else:
        parsed = "constructive"
    by_answer = {"yes": "constructive", "no": "destructive", "inconclusive": "inconclusive"}.get(answer or "")
    if by_answer is None:
        if parsed is None:
            return "inconclusive", f"verdict text {verdict!r} has a negation inside; give answer yes/no"
        return parsed, None
    if parsed is not None and parsed != by_answer:
        return "inconclusive", f"verdict text {verdict!r} ({parsed}) disagrees with answer {answer!r}"
    return by_answer, None


def _ll_verdict_k(verdict: str) -> int | None:
    import re

    m = re.search(r"ll\s*\(\s*(\d+)\s*\)", (verdict or "").lower())
    return int(m.group(1)) if m else None


def _artifact_key(args: Mapping[str, Any]) -> str | None:
    for key in _ARTIFACT_KEYS:
        if key in args:
            return key + ":" + json.dumps(args[key], sort_keys=True, ensure_ascii=False, default=str)
    return None


def _entry_fingerprint(entry: Mapping[str, Any]) -> str | None:
    data = entry.get("data") or {}
    if isinstance(data.get("ir_fingerprint"), str):
        return data["ir_fingerprint"]
    ir = (entry.get("args") or {}).get("ir")
    if isinstance(ir, Mapping):
        from .tools.ir import ir_fingerprint

        return ir_fingerprint(ir)
    return None


def _is_task_definition_dfa(entry: Mapping[str, Any]) -> bool:
    """A ``regex_to_dfa`` result built from the IR's own regex / linear grammar
    (``regex_to_dfa(ir=...)``): the tool, not the model, read the definition."""
    data = entry.get("data") or {}
    return (entry.get("tool") == "regex_to_dfa" and entry.get("trust") == "verified"
            and data.get("task_definition") is True and data.get("source") == "ir.language_spec"
            and data.get("cls") == "reg" and isinstance(data.get("ir_fingerprint"), str))


def _task_definition_dfa_is_bound(entry: Mapping[str, Any], *, ir_named: bool, ir_human_approved: bool) -> bool:
    """Whether a task-definition ``regex_to_dfa`` record is the task's OWN
    definition, not one the model encoded: the verdict names the IR, a human
    approved that IR at HITL 1 (not ``--auto-approve``, not HITL 1 off: an IR
    the ir-builder wrote may re-encode a natural-language task as a regex), and
    the regex / grammar occurs in the IR's source text."""
    data = entry.get("data") or {}
    return ir_named and ir_human_approved and data.get("definition_in_source_text") is True


def _classify(entry: Mapping[str, Any], lang: str, verdict_k: int | None,
              entries: Sequence[Mapping[str, Any]], *, ir_named: bool = False,
              ir_human_approved: bool = False) -> list[tuple[str, str]]:
    """``[(side, trust)]``: what one tool result says about the task's verdict.

    Only checks against the task (the IR) count: ``dfa_vs_ir`` /
    ``grammar_vs_ir`` / ``dpda_vs_ir`` / ``input_grammar_cfg`` for the
    constructive side, ``nerode_probe`` / ``pump_check_reg`` /
    ``pump_check_cfl`` (claim mode) for the destructive side, and for LL the
    ``ll_table`` result by what its grammar is (``data.grammar_source``).
    ``regex_to_dfa`` counts only in its IR mode (the task's own regex or
    linear grammar, read from the IR by the tool, cross-checked with the IR's
    oracle): ``verified`` (VERDICT_POLICY §1) only when it is bound to the
    task (:func:`_task_definition_dfa_is_bound`: a named IR a human approved
    at HITL 1, whose source text holds the definition), else ``bounded_pass``
    like ``dfa_vs_ir`` (the same bounded comparison with the IR's oracle,
    whose IR may be the model's own formalization). Everything else
    (``regex_to_dfa`` on an expression the model wrote, ``cyk``,
    ``run_dfa``, the words and theory tools, ...) is supporting evidence about
    its own input, never the basis of the verdict."""
    from .tools.base import clamp_trust

    tool, trust = entry.get("tool"), entry.get("trust")
    data = entry.get("data") or {}
    args = entry.get("args") or {}
    if tool == "lean_check":
        # R-Lean (docs/VERDICT_POLICY.md): a kernel-checked proof of the statement
        # code generated from this task's IR (lean_statement) is a verified basis
        # for its side; the tool already gives "verified" only for status proved
        # with standard axioms. The IR binding is the fingerprint rule of verdict_gate.
        if (lang in ("reg", "cfl", "dcfl") and data.get("cls") == lang and data.get("status") == "proved"
                and trust == "verified" and data.get("side") in ("constructive", "destructive")):
            return [(str(data["side"]), "verified")]
        return []
    if lang == "reg":
        if tool == "dfa_vs_ir":
            return [("constructive", trust)]
        if tool == "regex_to_dfa":
            # VERDICT_POLICY §1 "регулярность заданного regex/DFA": verified, but only for
            # the task's own definition (human-approved IR, definition in the source text);
            # never for a regex the model passed. An IR-mode record otherwise is the bounded
            # comparison of the compiled DFA with the IR's oracle, as dfa_vs_ir.
            if not _is_task_definition_dfa(entry):
                return []
            if _task_definition_dfa_is_bound(entry, ir_named=ir_named, ir_human_approved=ir_human_approved):
                return [("constructive", "verified")]
            cross = data.get("oracle_cross_check") or {}
            return [("constructive", "bounded_pass")] if (cross.get("n_words") or 0) > 0 else []
        if tool == "nerode_probe" and data.get("cls", "reg") == "reg" and data.get("pairs_checked"):
            return [("destructive", trust)]
        if tool == "pump_check_reg" and data.get("cls") == "reg" and data.get("mode") == "claim":
            # R6: a pumping argument checked by the tool is at most well_formed (<= 0.55).
            return [("destructive", clamp_trust(trust, "well_formed"))]
        return []
    if lang in ("cfl", "dcfl"):
        if tool == "pump_check_cfl" and data.get("mode") == "claim":
            return [("destructive", trust)]
        if lang == "cfl" and tool in ("grammar_vs_ir", "input_grammar_cfg") and args.get("cls", "cfl") == "cfl":
            return [("constructive", trust)]
        if lang == "dcfl" and tool == "dpda_vs_ir":
            return [("constructive", trust)]
        if lang == "dcfl" and tool == "nerode_probe" and data.get("cls") == "dcfl" and data.get("pairs_checked"):
            # finitely many pairs, not "every class finite": at most well_formed
            return [("destructive", clamp_trust(trust, "well_formed"))]
        return []
    if lang != "ll" or tool != "ll_table":
        return []
    source, mode = data.get("grammar_source"), data.get("mode")
    k = data.get("k")
    positive = (mode == "check" and trust == "verified") or (mode == "min_k" and bool(data.get("found")))
    negative_check = mode == "check" and trust == "refuted"
    out: list[tuple[str, str]] = []
    if source == "grammar":  # a grammar the agent wrote: an artifact, not the task's grammar
        if positive and (verdict_k is None or (isinstance(k, int) and k <= verdict_k)):
            matched = any(e.get("tool") == "grammar_vs_ir" and e.get("trust") == "bounded_pass"
                          and (e.get("args") or {}).get("grammar") == args.get("grammar") for e in entries)
            out.append(("constructive", "bounded_pass" if matched else "well_formed"))
        elif negative_check and (verdict_k is None or (isinstance(k, int) and k >= verdict_k)):
            out.append(("constructive", "refuted"))
        return out
    if source not in ("ir.grammar", "ir.language_spec"):
        return []  # a grammar_filter's grammar is not the language
    if positive:
        if verdict_k is None or (isinstance(k, int) and k <= verdict_k):
            out.append(("constructive", "verified"))
        elif mode == "min_k" and source == "ir.grammar":
            out.append(("destructive", "verified"))  # the least k is above the asked one
        return out
    if source != "ir.grammar":
        return []  # R5: for a language question a non-LL grammar is no evidence
    if negative_check and isinstance(k, int):
        if verdict_k is not None and k >= verdict_k:
            out += [("destructive", "verified"), ("constructive", "refuted")]
        elif verdict_k is None:
            out.append(("destructive", "bounded_pass"))  # "not LL(k) for this k" only
    elif mode == "min_k" and data.get("certificate"):
        out += [("destructive", "verified"), ("constructive", "refuted")]
    elif mode == "min_k" and trust == "bounded_pass":
        checked = data.get("max_k_checked")
        if verdict_k is not None and isinstance(checked, int) and checked >= verdict_k:
            out += [("destructive", "verified"), ("constructive", "refuted")]
        elif verdict_k is None:
            out.append(("destructive", "bounded_pass"))  # R5: "not LL(k) for k <= K", <= 0.85
    return out


#: ``formalization["status"]`` values that leave the verdict without an approved IR
_FORMALIZATION_BLOCKS = {
    "not_held": ("HITL 1 is on, but no formalization was reviewed (no ir-builder answer or validate_ir "
                 "reached HITL 1): no human-approved IR"),
    "rejected": "the last HITL 1 decision rejected the formalization and no IR was approved after it",
    "no_ir": "the formalization approved at HITL 1 has no IR (no fingerprint)",
}


def verdict_gate(report: Mapping[str, Any], entries: Sequence[Mapping[str, Any]],
                 cited: Iterable[str], *, formalization: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Trust and confidence ceiling of the report's verdict, by code
    (VERDICT_POLICY R1-R4, R4', R5, R6).

    The basis is the strongest admissible result for the verdict's side among
    the main thread's checks against the task (:func:`_classify`), whether the
    report cites it or not (R4'); citations can only lower the ceiling:

    * a cited result refuting the verdict's side blocks the verdict (R2);
      an uncited one drops the artifact it refutes;
    * a constructive verdict needs a basis >= bounded_pass (R2), a destructive
      one >= well_formed (R1); an inconclusive verdict is capped at 0.40;
      for REG, ``regex_to_dfa(ir=...)`` on the task's own regex / linear grammar
      is a ``verified`` constructive basis (§1) when a human approved the IR at
      HITL 1 and its source text holds the definition, else ``bounded_pass``; a
      ``pump_check_reg`` claim a destructive one of at most ``well_formed`` (R6);
    * the other side's admissible evidence is a contradiction (R3): the
      verdict keeps only a ``verified`` basis against a non-verified side,
      with the ceiling 0.85; otherwise it is inconclusive, <= 0.50;
    * a cited supporting result that is refuted is a contradiction (<= 0.50);
    * a cited id without a result lowers the trust one level;
    * with the report's ``ir_fingerprint``, results for another IR are
      excluded; without it, a basis spanning several IRs is not admissible;
    * ``formalization`` (HITL 1 on; ``hitl.formalization_binding``): the IR
      the human approved or edited at HITL 1 is the required one, whatever the
      report names -- results for another IR are excluded, and a report
      fingerprint that differs is a downgrade line. A rejected, missing or
      IR-less formalization gives no basis at all (``not_verified``): the
      model's own field never decides which IR its evidence is measured
      against.

    Returns ``trust``, ``confidence_cap``, ``direction``, ``verdict_token``,
    ``basis`` (ids), ``verdict_gate`` (the downgrade reasons, R4),
    ``contradiction``, ``excluded_ir_mismatch``, ``required_ir_fingerprint``
    (the fingerprint results were filtered by, if any) and ``formalization``."""
    from .tools.base import CONFIDENCE_CAPS, TRUST_LEVELS, confidence_cap, trust_at_least, trust_rank

    lang = report.get("language_class") or ""
    direction, note = verdict_direction(str(report.get("verdict") or ""), report.get("answer"))
    tokens = VERDICT_TOKENS.get(lang, {})
    cited = list(dict.fromkeys(cited))
    by_id = {e["id"]: e for e in entries}
    gate: list[str] = []
    if note:
        gate.append(note)
    out: dict[str, Any] = {"direction": direction, "verdict_token": tokens.get(direction), "basis": [],
                           "contradiction": False, "excluded_ir_mismatch": [],
                           "required_ir_fingerprint": None,
                           "formalization": dict(formalization) if formalization is not None else None}

    def finish(trust: str, cap: float) -> dict[str, Any]:
        unknown = [i for i in cited if i not in by_id]
        if unknown and trust != "not_verified":
            lowered = TRUST_LEVELS[max(trust_rank(trust) - 1, trust_rank("not_verified"))]
            gate.append(f"cited id(s) without a tool result {unknown}: trust lowered {trust} -> {lowered}")
            trust, cap = lowered, min(cap, confidence_cap(lowered))
        if trust == "not_verified" and direction != "inconclusive":
            out["verdict_token"] = tokens.get("inconclusive")
        out.update(trust=trust, confidence_cap=cap, verdict_gate=gate)
        return out

    report_fp = report.get("ir_fingerprint") or None
    whose = "the report's"
    if formalization is not None:
        status = str(formalization.get("status") or "not_held")
        approved_fp = formalization.get("ir_fingerprint") or None
        if status == "approved" and not approved_fp:
            status = "no_ir"
        if status != "approved":
            why = _FORMALIZATION_BLOCKS.get(status, f"HITL 1 status {status!r}: no human-approved IR")
            gate.append(why + "; oracle results have no approved IR to refer to: not_verified")
            return finish("not_verified", CONFIDENCE_CAPS["not_verified"])
        if report_fp and report_fp != approved_fp:
            gate.append(f"the report names IR {report_fp}, but the IR approved at HITL 1 is {approved_fp}: "
                        "results are judged against the approved IR")
        report_fp, whose = approved_fp, "the HITL 1-approved"
    out["required_ir_fingerprint"] = report_fp
    usable = [e for e in entries if e.get("ok")]
    if report_fp:
        mismatch = [e for e in usable if _entry_fingerprint(e) not in (None, report_fp)]
        if mismatch:
            out["excluded_ir_mismatch"] = [e["id"] for e in mismatch]
            gate.append(f"results for another IR than {whose} {report_fp} excluded: "
                        f"{out['excluded_ir_mismatch']}")
        usable = [e for e in usable if e not in mismatch]

    if direction == "inconclusive":
        gate.append("the verdict is inconclusive: confidence <= 0.40 (VERDICT_POLICY §2)")
        return finish("not_verified", CONFIDENCE_CAPS["not_verified"])

    verdict_k = _ll_verdict_k(str(report.get("verdict") or "")) if lang == "ll" else None
    ir_named = bool(report_fp)
    ir_human = (formalization is not None and formalization.get("status") == "approved"
                and not formalization.get("auto"))
    task_dfas = [e for e in usable if lang == "reg" and _is_task_definition_dfa(e)]
    unbound = [e["id"] for e in task_dfas
               if not _task_definition_dfa_is_bound(e, ir_named=ir_named, ir_human_approved=ir_human)]
    if unbound:
        why = ("HITL 1 is off" if formalization is None else
               "the IR was auto-approved (review mode or --auto-approve)" if formalization.get("auto") else
               "its definition does not occur in the IR's source_text" if ir_named else "no IR is named")
        gate.append(f"regex_to_dfa(ir=...) {unbound} is a verified basis only for the task's own definition: a "
                    "human-approved IR at HITL 1 whose source text holds the regex / grammar; here "
                    f"{why}, so it counts as a bounded check against the IR (bounded_pass, as dfa_vs_ir)")
    sides: list[tuple[Mapping[str, Any], str, str]] = [
        (e, side, t) for e in usable
        for side, t in _classify(e, lang, verdict_k, usable, ir_named=ir_named, ir_human_approved=ir_human)]
    opposite = "destructive" if direction == "constructive" else "constructive"
    same = [(e, t) for e, side, t in sides if side == direction]
    other = [(e, t) for e, side, t in sides if side == opposite]

    refuted_cited = [e["id"] for e, t in same if t == "refuted" and e["id"] in cited]
    if refuted_cited:
        gate.append(f"a cited result refutes the {direction} side {refuted_cited} (VERDICT_POLICY R2)")
        return finish("not_verified", CONFIDENCE_CAPS["not_verified"])
    refuted_keys = {_artifact_key(e.get("args") or {}) for e, t in same if t == "refuted"} - {None}
    candidates = []
    for e, t in same:
        if t == "refuted":
            continue
        if _artifact_key(e.get("args") or {}) in refuted_keys:
            gate.append(f"{e['id']} dropped: another check refutes the same artifact")
            continue
        candidates.append((e, t))
    need = "bounded_pass" if direction == "constructive" else "well_formed"
    admissible = [(e, t) for e, t in candidates if trust_at_least(t, need)]
    if not admissible:
        rule = ("R2: a constructive verdict needs a check of its artifact against the task >= bounded_pass"
                if direction == "constructive" else
                "R1: a destructive verdict needs destructive evidence >= well_formed")
        best = max((t for _, t in candidates), key=trust_rank, default=None)
        gate.append(rule + (f" (best: {best})" if best else " (none found)"))
        return finish("not_verified", CONFIDENCE_CAPS["not_verified"])
    fps = {_entry_fingerprint(e) for e, _ in admissible} - {None}
    if not report_fp and len(fps) > 1:
        gate.append(f"the {direction} results refer to {len(fps)} different IRs and the report names none "
                    "(ir_fingerprint)")
        return finish("not_verified", CONFIDENCE_CAPS["not_verified"])
    top = max(trust_rank(t) for _, t in admissible)
    trust = TRUST_LEVELS[top]
    out["basis"] = [e["id"] for e, t in admissible if trust_rank(t) == top]
    cap = confidence_cap(trust)

    other_need = "well_formed" if direction == "constructive" else "bounded_pass"
    against = [(e, t) for e, t in other if trust_at_least(t, other_need)]
    if against:
        out["contradiction"] = True
        other_verified = any(t == "verified" for _, t in against)
        ids = [e["id"] for e, _ in against]
        if trust == "verified" and not other_verified:
            gate.append(f"R3: contradicted by {ids}; the verified side keeps the verdict, ceiling 0.85")
            cap = min(cap, 0.85)
        elif other_verified and trust != "verified":
            gate.append(f"R3: verified evidence {ids} supports the opposite verdict")
            return finish("not_verified", CONFIDENCE_CAPS["not_verified"])
        else:
            gate.append(f"R3: unresolved contradiction with {ids}: inconclusive, ceiling 0.50")
            out["verdict_token"] = tokens.get("inconclusive")
            cap = min(cap, 0.50)
    side_ids = {e["id"] for e, _, _ in sides}
    refuted_support = [i for i in cited if i in by_id and i not in side_ids and by_id[i].get("trust") == "refuted"]
    if refuted_support:
        out["contradiction"] = True
        gate.append(f"cited result(s) {refuted_support} are refuted: ceiling 0.50")
        cap = min(cap, 0.50)
    return finish(trust, cap)

# ---------------------------------------------------------------------------
# Result (P2 stand-in for the P3 renderer / evidence ledger)
# ---------------------------------------------------------------------------

def collect_result(state: Mapping[str, Any], *, human_review: str = "not reviewed",
                   ledger: Any = None, evidence: Any = None,
                   pending_report: Mapping[str, Any] | None = None,
                   formalization: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Report + evidence from the final state.

    ``evidence`` (the run's ``EvidenceLedger``, P3): the evidence list, the
    per-step trust and the verdict gate come from the ledger
    (``evidence.policy.evaluate_report``), so the tool results of subagents
    count, and **any** cited id that is not in the ledger makes its step
    ``not_verified`` (verdict-level unknown ids lower the verdict one level).
    Without it the P2 rule below applies (main-thread tool results only).
    ``pending_report`` rates a report that is not accepted yet (the HITL 2
    draft): the result gets ``status: awaiting_human``.

    ``formalization`` (``hitl.formalization_binding``; ``None`` = HITL 1 off):
    the IR approved at HITL 1 is the one the verdict's evidence must refer to
    (:func:`verdict_gate`); it is copied to ``result["formalization"]``.

    ``ledger`` (the run's ``BudgetLedger``, optional): a refused call anywhere
    in its tree makes a run without a report ``budget_exhausted`` even when the
    last message is not the refusal itself, and ``budget_refusals`` lists the
    scopes that refused. A run without a report gets a ``partial_report``
    built by code from what the state holds: the evidence collected so far,
    the subagents' answers, the todo list and the last message, with the
    verdict "not established" at trust ``not_verified``.

    ``evidence`` lists every main-thread tool result that carries a trust
    level (the ToolResult artifact of the P1 tools), keyed by tool-call id.
    Each step's ``trust`` is the strongest non-refuted trust among the results
    it references (the trust of that *evidence*, within the tool's own scope,
    not a judgement of the step's wording); the verdict's ``trust`` and
    ``confidence_cap`` come from the report's verdict-level ``evidence_ids``
    (the step ids when there are none). An id with no result behind it is
    listed in ``unknown_ids`` and contributes nothing, so a claim with only
    such ids is ``not_verified``.

    The verdict's ``trust`` and ``confidence_cap`` are set by
    :func:`verdict_gate` (VERDICT_POLICY R1-R6): its basis is the strongest
    admissible check *against the task* for the verdict's side, chosen by code;
    the report's citations can only lower it (a cited refuted result, an id
    without a result, a fingerprint mismatch). ``verdict_gate`` lists every
    downgrade, ``verdict_token`` is the policy token (``partial`` / ``null`` /
    ``uncertain`` for an inconclusive verdict).

    A run without a report whose main budget scope reached wrap-up is
    ``budget_exhausted``: the wrap-up note asked for
    the report and the model answered in text instead.
    Subagents' own tool calls are not in the main thread; the P3
    EvidenceMiddleware ledger will cover them."""
    from langchain_core.messages import AIMessage, ToolMessage

    from .tools.base import confidence_cap, strongest_trust

    ledger_of_evidence = evidence  # the parameter; ``evidence`` below is the main-thread view
    messages = list(state.get("messages") or [])
    evidence: dict[str, dict[str, Any]] = {}  # type: ignore[no-redef]
    gate_entries: list[dict[str, Any]] = []
    report: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = []
    call_args: dict[str, Any] = {}
    for msg in messages:
        if isinstance(msg, AIMessage):
            tool_calls.extend({"id": tc.get("id"), "name": tc.get("name")} for tc in msg.tool_calls)
            call_args.update({tc.get("id"): tc.get("args") or {} for tc in msg.tool_calls})
        if not isinstance(msg, ToolMessage):
            continue
        art = msg.artifact if isinstance(msg.artifact, dict) else None
        if _is_submitted_report(msg):
            report = art["report"]  # type: ignore[index]
            continue
        if art is not None and "trust" in art:
            evidence[msg.tool_call_id] = {
                "id": msg.tool_call_id, "tool": art.get("tool") or msg.name, "ok": art.get("ok"),
                "trust": art.get("trust"), "trust_reason": art.get("trust_reason"),
                "bounds": art.get("bounds"), "error": art.get("error"),
            }
            data = art.get("data") if isinstance(art.get("data"), Mapping) else {}
            gate_entries.append({**evidence[msg.tool_call_id], "data": data,
                                 "args": call_args.get(msg.tool_call_id) or {}})
            fp = _entry_fingerprint(gate_entries[-1])
            if fp:
                evidence[msg.tool_call_id]["ir_fingerprint"] = fp

    def judge(ids: Iterable[str]) -> dict[str, Any]:
        ids = list(ids)
        found = [evidence[i] for i in ids if i in evidence]
        unknown = [i for i in ids if i not in evidence]
        trusts = [e["trust"] for e in found if e.get("ok")]
        return {"trust": strongest_trust(trusts) if trusts else "not_verified",
                "refuted_evidence": [e["id"] for e in found if e.get("trust") == "refuted"],
                "unknown_ids": unknown}

    status = "submitted" if report is not None else "incomplete"
    if report is None and pending_report is not None:
        report, status = dict(pending_report), "awaiting_human"
    result: dict[str, Any] = {
        "status": status,
        "human_review": human_review,
        "report": report,
        "evidence": list(evidence.values()),
        "tool_calls": tool_calls,
        "note": "P2 result: evidence from the main thread's tool results (no evidence ledger given).",
    }
    if formalization is not None:
        result["formalization"] = dict(formalization)
    if ledger_of_evidence is not None:
        result["evidence"] = ledger_evidence(ledger_of_evidence)
        result["note"] = ("evidence from the run's evidence ledger (every tool call of the main agent and "
                          "the subagents); trust set by code")
    if report is not None and ledger_of_evidence is not None:
        _rate_from_ledger(result, report, ledger_of_evidence, formalization=formalization)
    elif report is not None:
        steps = []
        step_ids: list[str] = []
        for step in report.get("steps") or []:
            ids = list(step.get("evidence_ids") or [])
            step_ids += ids
            steps.append({**step, **judge(ids)})
        every_ids = list(dict.fromkeys([*(report.get("evidence_ids") or []), *step_ids]))
        every = judge(every_ids)
        gate = verdict_gate(report, gate_entries, every_ids, formalization=formalization)
        result["steps"] = steps
        result["trust"] = gate["trust"]
        result["confidence_cap"] = gate["confidence_cap"]
        result["trust_basis"] = ("code-derived (VERDICT_POLICY): " + ", ".join(gate["basis"]) if gate["basis"]
                                 else "no admissible basis (VERDICT_POLICY)")
        result["verdict_direction"] = gate["direction"]
        result["verdict_token"] = gate["verdict_token"]
        result["verdict_gate"] = gate["verdict_gate"]
        result["basis"] = gate["basis"]
        result["contradiction"] = gate["contradiction"]
        result["excluded_ir_mismatch"] = gate["excluded_ir_mismatch"]
        result["required_ir_fingerprint"] = gate["required_ir_fingerprint"]
        result["unknown_ids"] = every["unknown_ids"]
        result["refuted_evidence"] = every["refuted_evidence"]
    else:
        last_text = ""
        last_ai = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai = last_ai or msg
                if msg.text:
                    last_text = msg.text
                    break
        result["reason"] = "the run ended without submit_report"
        meta = getattr(last_ai, "response_metadata", None) or {}
        if meta.get("stop_reason") == "budget_exhausted":  # BudgetMiddleware's refusal message
            result["status"] = "budget_exhausted"
            result["reason"] = "a model call did not fit in the budget and was refused"
            result["budget"] = meta.get("tfl_budget")
        if result["status"] == "incomplete" and ledger is not None and ledger.exhausted:
            result["status"] = "budget_exhausted"
            result["reason"] = "a model call did not fit in the budget and was refused"
        if result["status"] == "incomplete" and ledger is not None and ledger_wrapped_up(ledger):
            result["status"] = "budget_exhausted"
            result["reason"] = ("the budget reached wrap-up mode (the report was asked for) and the run "
                                "ended without submit_report")
        if result["status"] == "incomplete" and meta.get("stop_reason") == CALL_LIMIT_STOP:
            result["reason"] = "the run's model-call limit (limits.max_model_calls) was reached"
            result["limits"] = meta.get("tfl_limits")
        result["last_message"] = last_text[:4000]
        result["trust"] = "not_verified"
        result["confidence_cap"] = confidence_cap("not_verified")
        result["partial_report"] = _partial_report(state, result)
    if ledger is not None:
        result["budget_refusals"] = budget_refusals(ledger)
    return result


def ledger_evidence(evidence: Any) -> list[dict[str, Any]]:
    """The tool results of an evidence ledger (records with a ``ToolResult``)
    in the compact shape of ``result["evidence"]``: id, tool, ok, trust,
    trust_reason, bounds, error, role, ir_fingerprint (no args / data)."""
    from .evidence.policy import as_records

    out: list[dict[str, Any]] = []
    for rec in as_records(evidence).values():
        if not rec.get("has_tool_result", rec.get("trust") is not None):
            continue
        entry = {"id": rec["id"], "tool": rec.get("tool"), "ok": rec.get("ok"), "trust": rec.get("trust"),
                 "trust_reason": rec.get("trust_reason"), "bounds": rec.get("bounds"),
                 "error": rec.get("error"), "role": rec.get("role")}
        fp = _entry_fingerprint({"data": rec.get("data") if isinstance(rec.get("data"), Mapping) else {},
                                 "args": rec.get("args") if isinstance(rec.get("args"), Mapping) else {}})
        if fp:
            entry["ir_fingerprint"] = fp
        out.append(entry)
    return out


def _rate_from_ledger(result: dict[str, Any], report: Mapping[str, Any], evidence: Any, *,
                      formalization: Mapping[str, Any] | None = None) -> None:
    """Fill the rating fields of ``result`` from ``evidence.policy.evaluate_report``."""
    from .evidence.policy import evaluate_report

    ev = evaluate_report(report, evidence, formalization=formalization)
    steps = []
    for step, rating in zip(report.get("steps") or [], ev["steps"], strict=False):
        steps.append({**step, "trust": rating["trust"], "unknown_ids": rating["unknown_ids"],
                      "refuted_evidence": rating["refuted_ids"], "not_evidence": rating["not_evidence"],
                      "trust_reasons": rating["reasons"]})
    result["steps"] = steps
    if ev["claims"]:
        result["claims"] = [{**claim, "trust": rating["trust"], "unknown_ids": rating["unknown_ids"],
                             "refuted_evidence": rating["refuted_ids"], "not_evidence": rating["not_evidence"],
                             "trust_reasons": rating["reasons"]}
                            for claim, rating in zip(report.get("claims") or [], ev["claims"], strict=False)]
    result["trust"] = ev["trust"]
    result["confidence_cap"] = ev["confidence_cap"]
    result["trust_basis"] = ("code-derived (VERDICT_POLICY): " + ", ".join(ev["basis"]) if ev["basis"]
                             else "no admissible basis (VERDICT_POLICY)")
    result["verdict_direction"] = ev["verdict_direction"]
    result["verdict_token"] = ev["verdict_token"]
    result["published_verdict"] = ev["verdict"]
    result["verdict_gate"] = ev["verdict_gate"]
    result["basis"] = ev["basis"]
    result["contradiction"] = ev["contradiction"]
    result["excluded_ir_mismatch"] = ev["excluded_ir_mismatch"]
    result["required_ir_fingerprint"] = ev["required_ir_fingerprint"]
    result["unknown_ids"] = ev["unknown_ids"]
    result["refuted_evidence"] = ev["refuted_ids"]
    result["trust_source"] = ev["trust_source"]


def ledger_wrapped_up(ledger: Any) -> bool:
    """The run's own scope (the ledger's root, the main agent's) is in
    wrap-up mode or exhausted; a subagent's allocation reaching wrap-up does
    not count (the main agent may still have had money for its report)."""
    with ledger.lock:
        return ledger.root.in_wrap_up()


def budget_refusals(ledger: Any) -> list[dict[str, Any]]:
    """Scopes of ``ledger`` that refused a model call or an allocation."""
    out: list[dict[str, Any]] = []
    stack = [ledger.root]
    while stack:
        n = stack.pop()
        if n.refusals:
            out.append({"node": n.path, "kind": n.kind, "refusals": n.refusals, "state": n.state})
        stack.extend(reversed(n.children))
    return out


def _partial_report(state: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    """The partial report of a run that ended without ``submit_report``
    (P2 stand-in for the P3 renderer's ``budget_exhausted`` template). Built
    by code only: nothing the model wrote is promoted to a verdict."""
    from langchain_core.messages import AIMessage, ToolMessage

    from .tools.base import confidence_cap

    messages = list(state.get("messages") or [])
    calls: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls:
                calls[tc.get("id") or ""] = tc
    subagents = []
    rejected_reports = 0
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if msg.name == "task":
            args = (calls.get(msg.tool_call_id) or {}).get("args") or {}
            subagents.append({"id": msg.tool_call_id, "subagent": args.get("subagent_type"),
                              "status": msg.status, "answer": str(msg.text or msg.content)[:2000]})
        elif msg.name == SUBMIT_REPORT and not _is_submitted_report(msg):
            rejected_reports += 1
    evidence = list(result.get("evidence") or [])
    return {
        "status": result["status"],
        "verdict": "not established",
        "reason": result.get("reason"),
        "trust": "not_verified",
        "confidence_cap": confidence_cap("not_verified"),
        "established": [{"id": e["id"], "tool": e["tool"], "trust": e["trust"],
                         "trust_reason": e.get("trust_reason")}
                        for e in evidence if e.get("ok") and e.get("trust") not in (None, "refuted")],
        "refuted": [{"id": e["id"], "tool": e["tool"], "trust_reason": e.get("trust_reason")}
                    for e in evidence if e.get("trust") == "refuted"],
        "failed_tool_calls": [{"id": e["id"], "tool": e["tool"], "error": e.get("error")}
                              for e in evidence if not e.get("ok")],
        "subagents": subagents,
        "rejected_reports": rejected_reports,
        "todos": list(state.get("todos") or []),
        "last_message": result.get("last_message", ""),
        "note": "partial report generated by code from the state: no submit_report was accepted; "
                "the evidence entries keep their own trust, the task verdict is not established.",
    }


# ---------------------------------------------------------------------------
# Scripted replay models (tests and --mock-script; never call the API)
# ---------------------------------------------------------------------------

class ScriptError(RuntimeError):
    """A mock script is malformed, exhausted, or calls a tool the role does not have."""


def _substitute(obj: Any, defs: Mapping[str, Any]) -> Any:
    """Replace every ``{"$def": "name"}`` with a deep copy of ``defs[name]``."""
    if isinstance(obj, dict):
        if set(obj) == {"$def"}:
            key = obj["$def"]
            if key not in defs:
                raise ScriptError(f"mock script refers to unknown def {key!r}; defs: {sorted(defs)}")
            return copy.deepcopy(defs[key])
        return {k: _substitute(v, defs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, defs) for v in obj]
    return obj


def _scripted_model_class() -> type:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ScriptedChatModel(BaseChatModel):
        """Replays a fixed list of replies, one per model call.

        Each reply is ``{"content": str, "tool_calls": [{"name", "args", "id"?}],
        "usage"?: {...}, "expect"?: {"last_message_contains": str}}``. Tool
        names are checked against the tools bound by the agent (a script that
        calls a tool the role does not have fails loudly). ``bind_tools``
        returns the model itself, so the reply cursor is shared across calls."""

        model: str = "scripted"
        role: str = "main"
        replies: list[dict[str, Any]] = Field(default_factory=list)
        defs: dict[str, Any] = Field(default_factory=dict)
        strict_tools: bool = True
        _cursor: int = PrivateAttr(default=0)
        _bound: list[str] | None = PrivateAttr(default=None)
        _received: list[list[Any]] = PrivateAttr(default_factory=list)
        _on_advance: Any = PrivateAttr(default=None)  # ScriptedModels persists the cursor

        @property
        def _llm_type(self) -> str:
            return "tfl-scripted"

        @property
        def model_name(self) -> str:
            return self.model

        @property
        def calls_made(self) -> int:
            return self._cursor

        @property
        def received(self) -> list[list[Any]]:
            """The message lists of every call so far (for tests)."""
            return self._received

        @property
        def bound_tools(self) -> list[str] | None:
            return self._bound

        def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:  # noqa: ARG002
            names = []
            for t in tools:
                if isinstance(t, dict):
                    names.append(t.get("name") or (t.get("function") or {}).get("name"))
                else:
                    names.append(getattr(t, "name", None))
            self._bound = [n for n in names if n]
            return self

        def _generate(self, messages: list[Any], stop: list[str] | None = None,  # noqa: ARG002
                      run_manager: Any = None, **kwargs: Any) -> ChatResult:  # noqa: ARG002
            self._received.append(list(messages))
            if self._cursor >= len(self.replies):
                raise ScriptError(
                    f"mock script for role {self.role!r} has no reply #{self._cursor + 1} "
                    f"(it has {len(self.replies)}); the agent made more model calls than scripted")
            index = self._cursor
            reply = self.replies[index]
            self._cursor += 1
            if self._on_advance is not None:
                self._on_advance()
            expect = reply.get("expect") or {}
            needle = expect.get("last_message_contains")
            if needle is not None:
                last = messages[-1] if messages else None
                text = getattr(last, "text", None) or str(getattr(last, "content", ""))
                if needle not in text:
                    raise ScriptError(f"role {self.role!r} reply #{index + 1}: expected the last "
                                      f"message to contain {needle!r}, got {text[:300]!r}")
            tool_calls = []
            for j, tc in enumerate(reply.get("tool_calls") or []):
                name = tc.get("name")
                if not name:
                    raise ScriptError(f"role {self.role!r} reply #{index + 1}: a tool call has no name")
                if self.strict_tools and self._bound is not None and name not in self._bound:
                    raise ScriptError(
                        f"role {self.role!r} reply #{index + 1} calls {name!r}, which is not bound "
                        f"for this agent; bound: {sorted(self._bound)}")
                tool_calls.append({
                    "name": name, "args": _substitute(tc.get("args") or {}, self.defs),
                    "id": tc.get("id") or f"call_{self.role}_{index + 1}_{j}", "type": "tool_call",
                })
            content = reply.get("content", "")
            usage = reply.get("usage")
            if usage is None:
                prompt_chars = sum(len(str(getattr(m, "content", ""))) for m in messages)
                out_chars = len(content) + sum(len(json.dumps(tc["args"])) for tc in tool_calls)
                usage = {"input_tokens": prompt_chars // 4 + 1, "output_tokens": out_chars // 4 + 1}
            usage = {**usage, "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)}
            msg = AIMessage(content=content, tool_calls=tool_calls, usage_metadata=usage,
                            response_metadata={"model_name": self.model, "stop_reason":
                                               "tool_use" if tool_calls else "end_turn"})
            return ChatResult(generations=[ChatGeneration(message=msg)])

    return ScriptedChatModel


_SCRIPTED_CLASS: type | None = None


def scripted_model_class() -> type:
    """The ``ScriptedChatModel`` class (built once, lazily: langchain-core is
    part of the optional ``[deep]`` extra)."""
    global _SCRIPTED_CLASS  # noqa: PLW0603
    if _SCRIPTED_CLASS is None:
        _SCRIPTED_CLASS = _scripted_model_class()
    return _SCRIPTED_CLASS


#: mock-script keys of P4 children (orchestration/spawn.py NAME_PATTERN after the prefix)
_SCRIPT_CHILD_KEY = re.compile(r"^(spawn|workflow):[a-z0-9][a-z0-9_-]{0,47}$")


def load_mock_script(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Read and check a mock script: ``{"models": {role_key: [reply, ...]},
    "defs"?: {...}, "task"?: str, "template"?: str, "description"?: str}``."""
    if isinstance(source, Mapping):
        data = copy.deepcopy(dict(source))
        where = "<mapping>"
    else:
        path = Path(source)
        where = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ScriptError(f"mock script not found: {where}") from None
        except json.JSONDecodeError as exc:
            raise ScriptError(f"mock script {where} is not valid JSON: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict) or not data["models"]:
        raise ScriptError(f"mock script {where}: needs a non-empty 'models' object {{role: [replies]}}")
    known = set(ROLES) | {"main"}
    # P4: an ad-hoc agent is scripted as "spawn:<name>", a workflow step as "workflow:<step id>"
    bad = sorted(k for k in data["models"] if k not in known and not _SCRIPT_CHILD_KEY.match(str(k)))
    if bad:
        raise ScriptError(f"mock script {where}: unknown role(s) {bad}; known: {sorted(known)} "
                          "and spawn:<name> / workflow:<step id>")
    for role, replies in data["models"].items():
        if not isinstance(replies, list) or not all(isinstance(r, dict) for r in replies):
            raise ScriptError(f"mock script {where}: models.{role} must be a list of reply objects")
    if "main" not in data["models"]:
        raise ScriptError(f"mock script {where}: has no 'main' replies")
    data.setdefault("defs", {})
    return data


class ScriptedModels:
    """``model_factory`` over a mock script: one :class:`ScriptedChatModel`
    per role, each replaying its own list. ``models[role]`` gives the
    instance (for assertions on calls made / messages received).

    ``state_path`` (a JSON file, ``{role: replies used}``) makes the replay
    survive a process boundary: the cursors start where the file says and
    are written after every reply. A HITL run resumed in a new process
    (``hitl.resume_run``) continues the script instead of replaying it from
    the start."""

    def __init__(self, script: str | Path | Mapping[str, Any], *,
                 state_path: str | Path | None = None) -> None:
        import threading

        self.script = load_mock_script(script)
        self.models: dict[str, Any] = {}
        self.state_path = Path(state_path) if state_path is not None else None
        self._lock = threading.Lock()
        self._start: dict[str, int] = {}
        if self.state_path is not None and self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ScriptError(f"mock cursor file {self.state_path} is unreadable: {exc}") from None
            self._start = {str(k): int(v) for k, v in (raw.get("cursors") or {}).items()}

    def __call__(self, role_key: str, model_id: str) -> Any:
        cls = scripted_model_class()
        model = cls(model=model_id, role=role_key,
                    replies=self.script["models"].get(role_key, []), defs=self.script["defs"])
        model._cursor = self._start.get(role_key, 0)
        if self.state_path is not None:
            model._on_advance = self._save
        self.models[role_key] = model
        return model

    def cursors(self) -> dict[str, int]:
        """Replies used so far per role (this process and the earlier ones)."""
        out = dict(self._start)
        out.update({role: m.calls_made for role, m in self.models.items()})
        return out

    def _save(self) -> None:
        assert self.state_path is not None
        with self._lock:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cursors": self.cursors()}, sort_keys=True), encoding="utf-8")
            tmp.replace(self.state_path)

    def unused_replies(self) -> dict[str, int]:
        """Replies left over per role (a finished script has none)."""
        out = {}
        used_by_role = self.cursors()
        for role, replies in self.script["models"].items():
            used = used_by_role.get(role, 0)
            if used < len(replies):
                out[role] = len(replies) - used
        return out


def scripted_model_factory(script: str | Path | Mapping[str, Any], *,
                           state_path: str | Path | None = None) -> ScriptedModels:
    """A ``model_factory`` replaying ``script`` (see :func:`load_mock_script`);
    ``state_path`` persists the reply cursors (see :class:`ScriptedModels`)."""
    return ScriptedModels(script, state_path=state_path)
