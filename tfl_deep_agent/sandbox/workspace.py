"""The sandbox inside the agent's ``CompositeBackend`` (P4-P6 integration).

``agent.make_backend(sandbox=sb)`` builds::

    CompositeBackend(
        default=SandboxedStateBackend(sb),          # scratch files in the graph state;
                                                    # execute -> the sandbox
        routes={"/skills/": <read-only skills>,
                "/workspace/": WorkspaceBackend(sb)})  # files inside the container

Why two adapters:

* deepagents offers the ``execute`` tool only when ``CompositeBackend.default``
  is a ``SandboxBackendProtocol`` (``middleware.filesystem.supports_execution``),
  and decides the state schema (the ``files`` channel) by
  ``isinstance(default, StateBackend)``. :class:`SandboxedStateBackend` is both:
  every file operation is the plain ``StateBackend`` one (so the main agent's
  scratch files stay in the checkpointed state, as without a sandbox) and
  ``execute`` runs in the sandbox. It also refuses writes under ``/input/`` (the
  task file), because the deny rule for ``/input/**`` cannot be passed next to
  an execution backend unless ``/input/`` were a route
  (``FilesystemMiddleware._set_permissions``).
* ``CompositeBackend`` strips the route prefix before calling the routed backend
  (``/workspace/a.py`` -> ``/a.py``), but the container's writable directory is
  ``/workspace`` itself (its root is read-only). :class:`WorkspaceBackend` puts
  the prefix back for the sandbox and strips it from the paths it returns, so
  the composite can re-add it.

Nothing here assigns trust; sandbox output stays at most ``well_formed``
(``docs/VERDICT_POLICY.md``).
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from typing import Any

from deepagents.backends import StateBackend
from deepagents.backends.protocol import (
    PERMISSION_DENIED,
    BackendProtocol,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

__all__ = ["READONLY_STATE_PREFIXES", "WORKSPACE_ROOT", "WORKSPACE_ROUTE", "SandboxedStateBackend",
           "WorkspaceBackend", "is_sandbox"]

WORKSPACE_ROOT = "/workspace"
WORKSPACE_ROUTE = WORKSPACE_ROOT + "/"
#: state paths no built-in tool may change (the task file under /input/)
READONLY_STATE_PREFIXES: tuple[str, ...] = ("/input/",)


def is_sandbox(obj: Any) -> bool:
    """``obj`` can run commands (a deepagents ``SandboxBackendProtocol``)."""
    return isinstance(obj, SandboxBackendProtocol)


class SandboxedStateBackend(StateBackend, SandboxBackendProtocol):
    """``StateBackend`` for files, the sandbox for ``execute``.

    The ``CompositeBackend`` default of a run with a sandbox: deepagents then
    registers ``execute`` (the tool filter of ``agent.py`` keeps it for the
    coder only), and the graph state keeps its ``files`` channel."""

    _MSG = "permission denied: the task file under /input/ is read-only"

    def __init__(self, sandbox: SandboxBackendProtocol, *,
                 readonly_prefixes: Iterable[str] = READONLY_STATE_PREFIXES) -> None:
        if not is_sandbox(sandbox):
            raise TypeError(f"sandbox must implement SandboxBackendProtocol, not {type(sandbox).__name__}")
        super().__init__()
        self.sandbox = sandbox
        self.readonly_prefixes = tuple(readonly_prefixes)

    # -- execute ---------------------------------------------------------------

    @property
    def id(self) -> str:
        return str(self.sandbox.id)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if timeout is None:
            return self.sandbox.execute(command)
        return self.sandbox.execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:  # noqa: ASYNC109
        if timeout is None:
            return await self.sandbox.aexecute(command)
        return await self.sandbox.aexecute(command, timeout=timeout)

    # -- /input/ is read-only --------------------------------------------------

    def _locked(self, path: str) -> bool:
        p = "/" + str(path or "").lstrip("/")
        return any(p == pre.rstrip("/") or p.startswith(pre) for pre in self.readonly_prefixes)

    def write(self, file_path: str, content: str) -> WriteResult:
        if self._locked(file_path):
            return WriteResult(error=f"{self._MSG} ({file_path})")
        return super().write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
        if self._locked(file_path):
            return EditResult(error=f"{self._MSG} ({file_path})")
        return super().edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        if self._locked(file_path):
            return DeleteResult(error=f"{self._MSG} ({file_path})")
        return super().delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        locked = [(p, c) for p, c in files if self._locked(p)]
        if not locked:
            return super().upload_files(files)
        free_files = [(p, c) for p, c in files if not self._locked(p)]
        by_path = {r.path: r for r in (super().upload_files(free_files) if free_files else [])}
        return [FileUploadResponse(path=p, error=PERMISSION_DENIED) if self._locked(p)
                else by_path.get(p, FileUploadResponse(path=p, error=None)) for p, _ in files]


class WorkspaceBackend(BackendProtocol):
    """The sandbox under the ``/workspace/`` route: route-relative paths
    (``/a.py``, as ``CompositeBackend`` passes them) become container paths
    (``/workspace/a.py``); returned paths go back to route-relative ones."""

    def __init__(self, sandbox: BackendProtocol, root: str = WORKSPACE_ROOT) -> None:
        self.sandbox = sandbox
        self.root = "/" + root.strip("/")

    def _inner(self, path: str | None) -> str:
        rel = posixpath.normpath("/" + str(path or "/").lstrip("/"))
        return self.root if rel == "/" else self.root + rel

    def _outer(self, path: str) -> str:
        p = str(path or "")
        if p == self.root:
            return "/"
        if p.startswith(self.root + "/"):
            return p[len(self.root):]
        return p  # a relative or foreign path: leave it as the sandbox said it

    def _remap(self, items: list[Any] | None) -> list[Any] | None:
        if items is None:
            return None
        return [{**it, "path": self._outer(it.get("path", ""))} if isinstance(it, dict) else it for it in items]

    # -- reads -----------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        res = self.sandbox.ls(self._inner(path))
        if isinstance(res, list):  # legacy list return
            return LsResult(entries=self._remap(res))
        return LsResult(error=res.error, entries=self._remap(res.entries))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self.sandbox.read(self._inner(file_path), offset=offset, limit=limit)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *,
             max_count: int | None = None) -> GrepResult:
        try:
            res = self.sandbox.grep(pattern, self._inner(path), glob, max_count=max_count)
        except TypeError:  # a backend without max_count
            res = self.sandbox.grep(pattern, self._inner(path), glob)
        if isinstance(res, str):
            return GrepResult(error=res)
        if isinstance(res, list):
            return GrepResult(matches=self._remap(res))
        return GrepResult(error=res.error, matches=self._remap(res.matches), truncated=res.truncated)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        res = self.sandbox.glob(pattern, self._inner(path))
        if isinstance(res, list):
            return GlobResult(matches=self._remap(res))
        return GlobResult(error=res.error, matches=self._remap(res.matches), truncated=res.truncated,
                          truncation_reason=res.truncation_reason)

    # -- writes ----------------------------------------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        return self.sandbox.write(self._inner(file_path), content)

    def edit(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
        return self.sandbox.edit(self._inner(file_path), old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        return self.sandbox.delete(self._inner(file_path))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        res = self.sandbox.upload_files([(self._inner(p), c) for p, c in files])
        return [FileUploadResponse(path=p, error=r.error) for (p, _), r in zip(files, res, strict=False)]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        res = self.sandbox.download_files([self._inner(p) for p in paths])
        return [FileDownloadResponse(path=p, content=r.content, error=r.error)
                for p, r in zip(paths, res, strict=False)]

    def __repr__(self) -> str:
        return f"WorkspaceBackend({self.sandbox!r} at {self.root})"
