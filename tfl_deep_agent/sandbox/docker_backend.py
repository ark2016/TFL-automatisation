"""DockerSandbox(BaseSandbox): no network, read-only repo at /opt/tfl, limits (P5).

The coder role runs Python here. One container per run::

    docker run -d --rm --name tfl-sbx-<run>-<hex> --label tfl.sandbox=1
        --network none --read-only --tmpfs /workspace:... --tmpfs /tmp:...
        --memory 1g --memory-swap 1g --cpus 1 --pids-limit 256 --user 1000:1000
        --security-opt no-new-privileges --cap-drop ALL
        -v <repo>/<pkg>:/opt/tfl/<pkg>:ro ...  (secret files masked by empty files,
        hidden dirs such as tests/, examples/, .git/ by empty read-only tmpfs)
        tfl-sandbox:latest sleep infinity

and one ``docker exec ... timeout -k 5 <T> sh -c <command>`` per ``execute``.
deepagents' ``BaseSandbox`` derives ls / read / write / edit / grep / glob from
``execute`` plus :meth:`DockerSandbox.upload_files` /
:meth:`DockerSandbox.download_files` (``docker cp`` does not see tmpfs mounts,
so both go through ``docker exec -i ... sh -c``).

Cleanup is guaranteed three ways: the context manager (``with DockerSandbox()
as sb``), :meth:`DockerSandbox.close`, and a ``weakref.finalize`` that also
runs at interpreter exit; each does ``docker rm -f`` (and ``--rm`` removes the
container if its ``sleep`` ever dies). Containers carry the label
``tfl.sandbox=1``; :func:`remove_stale_containers` removes leftovers of a
crashed process on explicit request only.

What is mounted: by default the four pipeline packages and ``docs/THEORY.md``
under ``/opt/tfl/<name>`` (read-only), not the whole working tree, so ``.env``,
run directories and the venv stay outside, and not the rest of ``docs/``
(``EVAL_SET.md`` holds the expected verdict of every eval task, ``examples/``
worked solutions). Inside a mount, every directory whose name matches
``SandboxConfig.hidden_dirs`` (VCS, venvs, ``.claude``, run dirs, ``tmp_*``,
and ``tests`` / ``examples``, which hold solved tasks with expected verdicts)
is covered by an empty read-only tmpfs, and every secret-looking file
(``.env``, ``.env.*`` except examples, ``*.pem``, ``*.key``, ssh keys) and
every file named in ``hidden_files`` (``EVAL_SET.md``, ``EVAL_RESULTS.md``)
by an empty read-only file. The scan walks every mounted directory that is not
hidden (``__pycache__`` and tool caches included). ``mounts=(".",)`` mounts
the whole repo (the same masking applies). ``PYTHONPATH=/opt/tfl`` is set, so ``import
cfl_system.lib.cyk`` works with or without ``sys.path.insert(0, "/opt/tfl")``.

Code output from the sandbox is at most ``well_formed`` evidence
(``docs/VERDICT_POLICY.md``); nothing here assigns trust.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import weakref
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    PERMISSION_DENIED,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from agent_system.lib.type_check import _windows_docker_path

__all__ = [
    "DEFAULT_HIDDEN_DIRS",
    "DEFAULT_HIDDEN_FILES",
    "DEFAULT_IMAGE",
    "DEFAULT_MOUNTS",
    "DOCKERFILE",
    "REPO_ROOT",
    "DockerCLI",
    "DockerSandbox",
    "SandboxConfig",
    "SandboxError",
    "SandboxUnavailable",
    "StreamResult",
    "build_exec_args",
    "build_image",
    "build_run_args",
    "docker_path",
    "find_hidden_dirs",
    "find_secret_files",
    "open_sandbox",
    "remove_stale_containers",
    "sandbox_status",
    "scan_mounts",
]

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DOCKERFILE: Path = Path(__file__).resolve().parent / "Dockerfile.sandbox"
DEFAULT_IMAGE = "tfl-sandbox:latest"
REPO_MOUNT = "/opt/tfl"
WORKDIR = "/workspace"
LABEL = "tfl.sandbox"
NAME_PREFIX = "tfl-sbx-"

#: repo paths mounted under /opt/tfl by default (what the coder may import / read)
DEFAULT_MOUNTS: tuple[str, ...] = ("agent_system", "cfl_system", "dcfl_system", "ll_system", "docs/THEORY.md")

#: directory names (fnmatch patterns) covered by an empty read-only tmpfs
#: wherever they occur inside a mount: VCS metadata (credential URLs), venvs
#: (their test keys), Claude / run state, scratch dirs, and the solved tasks
#: with expected verdicts (``tests``, ``examples``: eval contamination, P8)
DEFAULT_HIDDEN_DIRS: tuple[str, ...] = (
    ".git", ".venv", "venv", ".claude", ".tfl_lab_runs", "node_modules", "tmp_*", "tests", "examples",
)
#: file names (fnmatch patterns) covered by an empty file inside a mount: the
#: eval set's expected verdicts and the recorded eval results
DEFAULT_HIDDEN_FILES: tuple[str, ...] = ("EVAL_SET.md", "EVAL_RESULTS.md")
#: options of the empty tmpfs over a hidden directory
HIDDEN_TMPFS_OPTS = "ro,nosuid,nodev,noexec,size=4k,mode=0555"

#: extra writable tmpfs mounts besides /workspace and /tmp: deepagents offloads
#: large tool results and summarised history there when the sandbox is the
#: default backend; the root filesystem itself is read-only.
DEFAULT_EXTRA_TMPFS: tuple[str, ...] = ("/large_tool_results", "/conversation_history")

#: seconds `timeout` waits after SIGTERM before SIGKILL inside the container
KILL_AFTER_S = 5
#: extra seconds the host waits for `docker exec` beyond the in-container timeout
HOST_GRACE_S = 15
#: exit code reported for a command killed by the timeout (coreutils convention)
TIMEOUT_EXIT_CODE = 124

#: stderr fragments of a daemon-level failure (flaky Docker Desktop, see
#: DEV_NOTES "Docker (state on 2026-11-07)"); such a failure is retried, and if
#: it persists the sandbox counts as unavailable rather than broken.
TRANSIENT_MARKERS: tuple[str, ...] = (
    "read init-p",
    "connection reset by peer",
    "runc did not terminate",
    "exit status 139",
    "OCI runtime create failed",
    "init-p",
    "failed to create shim task",
    "OCI runtime start failed",
    "error during connect",
    "Cannot connect to the Docker daemon",
)

_SECRET_NAMES = re.compile(r"^(\.env(\..+)?|.+\.(pem|key)|id_(rsa|ed25519|ecdsa))$")
_SECRET_OK = re.compile(r"^\.env\.(example|sample|template|dist)$")
_NAME_OK = re.compile(r"[^a-zA-Z0-9_.-]+")

# Download: exit 2 = missing, 3 = directory, 4 = unreadable, 5 = too large.
_DOWNLOAD_SCRIPT = (
    'p="$1"; max="$2"; '
    'if [ -d "$p" ]; then exit 3; fi; '
    'if [ ! -e "$p" ]; then exit 2; fi; '
    'if [ ! -r "$p" ]; then exit 4; fi; '
    's=$(stat -c %s -- "$p") || exit 4; '
    'if [ "$s" -gt "$max" ]; then echo "file is $s bytes, limit $max" >&2; exit 5; fi; '
    'exec cat -- "$p"'
)
_UPLOAD_SCRIPT = 'p="$1"; if [ -d "$p" ]; then exit 3; fi; mkdir -p -- "$(dirname -- "$p")" && cat > "$p"'

# Last resort after the host-side wait expired: kill every process of the
# container except PID 1 (`sleep infinity`) and this script itself.
_KILL_ALL_SCRIPT = (
    "import os,signal\n"
    "me=os.getpid()\n"
    "for p in os.listdir('/proc'):\n"
    "    if p.isdigit() and int(p) not in (1, me):\n"
    "        try: os.kill(int(p), signal.SIGKILL)\n"
    "        except OSError: pass\n"
)


class SandboxError(RuntimeError):
    """The sandbox could not do what was asked (container gone, closed, ...)."""


class SandboxUnavailable(SandboxError):
    """Docker, the daemon or the image is not available, or the container
    could not be started (also after retrying a transient daemon failure)."""


@dataclass(frozen=True)
class SandboxConfig:
    """Limits and layout of one sandbox container."""

    image: str = DEFAULT_IMAGE
    memory: str = "1g"
    cpus: str = "1"
    pids_limit: int = 256
    user: str = "1000:1000"
    workspace_size: str = "256m"
    tmp_size: str = "64m"
    extra_tmpfs: tuple[str, ...] = DEFAULT_EXTRA_TMPFS
    mounts: tuple[str, ...] = DEFAULT_MOUNTS
    hidden_dirs: tuple[str, ...] = DEFAULT_HIDDEN_DIRS
    hidden_files: tuple[str, ...] = DEFAULT_HIDDEN_FILES
    default_timeout: int = 60          # seconds, per execute when the caller gives none
    max_timeout: int = 600             # a larger per-call timeout is refused (ValueError)
    max_output_bytes: int = 64 * 1024  # combined stdout + stderr returned per execute
    max_download_bytes: int = 16 * 1024 * 1024
    op_timeout: int = 60               # upload / download / start / rm, seconds
    start_attempts: int = 6

    def __post_init__(self) -> None:
        if self.default_timeout <= 0 or self.max_timeout <= 0:
            raise ValueError("sandbox timeouts must be positive")
        if self.default_timeout > self.max_timeout:
            raise ValueError("default_timeout exceeds max_timeout")
        if self.max_output_bytes <= 0 or self.pids_limit <= 0 or self.start_attempts <= 0:
            raise ValueError("sandbox limits must be positive")
        for m in self.mounts:
            p = Path(m)
            if p.is_absolute() or p.drive or m.startswith(("/", "\\")) or ".." in p.parts:
                raise ValueError(f"mount {m!r} must be a path relative to the repo root")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without Docker)
# ---------------------------------------------------------------------------

def docker_path(p: str | os.PathLike[str]) -> str:
    """Host path in the form ``docker run -v`` accepts (``/c/...`` on Windows,
    via ``agent_system.lib.type_check._windows_docker_path``)."""
    return _windows_docker_path(str(p)) if os.name == "nt" else str(Path(p))


def _is_secret(name: str) -> bool:
    return bool(_SECRET_NAMES.match(name)) and not _SECRET_OK.match(name)


def _matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def scan_mounts(repo_root: str | os.PathLike[str], mounts: Iterable[str], *,
                hidden_dirs: Iterable[str] = DEFAULT_HIDDEN_DIRS,
                hidden_files: Iterable[str] = DEFAULT_HIDDEN_FILES) -> tuple[list[str], list[str]]:
    """``(masked files, hidden dirs)`` inside the mounted paths, as
    repo-relative POSIX paths, sorted.

    Masked files (covered by an empty read-only file): secret-looking ones
    (``.env``, ``.env.*`` except examples, ``*.pem``, ``*.key``, ssh keys) and
    those whose name matches ``hidden_files``. Hidden dirs (covered by an
    empty read-only tmpfs): the outermost directories whose name matches
    ``hidden_dirs``. Every other mounted directory is walked, caches such as
    ``__pycache__`` / ``.pytest_cache`` included."""
    root = Path(repo_root).resolve()
    hd, hf = tuple(hidden_dirs), tuple(hidden_files)
    files: set[str] = set()
    dirs: set[str] = set()

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    for m in mounts:
        base = (root / m).resolve()
        if base.is_file():
            if _is_secret(base.name) or _matches(base.name, hf):
                files.add(rel(base))
            continue
        if not base.is_dir():
            continue
        if base != root and _matches(base.name, hd):
            dirs.add(rel(base))
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            keep = []
            for d in dirnames:
                if _matches(d, hd):
                    dirs.add(rel(Path(dirpath) / d))
                else:
                    keep.append(d)
            dirnames[:] = keep
            for fn in filenames:
                if _is_secret(fn) or _matches(fn, hf):
                    files.add(rel(Path(dirpath) / fn))
    return sorted(files), sorted(dirs)


def find_secret_files(repo_root: str | os.PathLike[str], mounts: Iterable[str], *,
                      hidden_dirs: Iterable[str] = DEFAULT_HIDDEN_DIRS,
                      hidden_files: Iterable[str] = DEFAULT_HIDDEN_FILES) -> list[str]:
    """The masked files of :func:`scan_mounts` (covered by empty files)."""
    return scan_mounts(repo_root, mounts, hidden_dirs=hidden_dirs, hidden_files=hidden_files)[0]


def find_hidden_dirs(repo_root: str | os.PathLike[str], mounts: Iterable[str], *,
                     hidden_dirs: Iterable[str] = DEFAULT_HIDDEN_DIRS) -> list[str]:
    """The hidden directories of :func:`scan_mounts` (covered by empty tmpfs)."""
    return scan_mounts(repo_root, mounts, hidden_dirs=hidden_dirs, hidden_files=())[1]


def _container_target(rel: str) -> str:
    rel_posix = Path(rel).as_posix().strip("/")
    return REPO_MOUNT if rel_posix in ("", ".") else f"{REPO_MOUNT}/{rel_posix}"


def build_run_args(
    name: str,
    config: SandboxConfig,
    *,
    repo_root: str | os.PathLike[str],
    mask_file: str | os.PathLike[str] | None = None,
    secret_files: Sequence[str] = (),
    hidden_dirs: Sequence[str] = (),
    run_id: str | None = None,
    docker: str = "docker",
) -> list[str]:
    """The ``docker run -d ... sleep infinity`` argv of one sandbox.
    ``secret_files`` are covered by ``mask_file`` (empty, read-only) and
    ``hidden_dirs`` by an empty read-only tmpfs, after the mounts."""
    root = Path(repo_root)
    tmpfs_opts = "rw,nosuid,nodev,noexec,mode=1777"
    args = [
        docker, "run", "-d", "--rm",
        "--name", name,
        "--label", f"{LABEL}=1",
        "--label", f"{LABEL}.run={_NAME_OK.sub('-', run_id or '-')[:63]}",
        "--network", "none",
        "--read-only",
        "--tmpfs", f"{WORKDIR}:{tmpfs_opts},size={config.workspace_size}",
        "--tmpfs", f"/tmp:{tmpfs_opts},size={config.tmp_size}",
    ]
    for extra in config.extra_tmpfs:
        args += ["--tmpfs", f"{extra}:{tmpfs_opts},size={config.tmp_size}"]
    args += [
        "--memory", config.memory,
        "--memory-swap", config.memory,  # no swap on top of the memory limit
        "--cpus", str(config.cpus),
        "--pids-limit", str(config.pids_limit),
        "--user", config.user,
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--workdir", WORKDIR,
        "-e", f"PYTHONPATH={REPO_MOUNT}",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONUNBUFFERED=1",
        "-e", f"HOME={WORKDIR}",
    ]
    for m in config.mounts:
        args += ["-v", f"{docker_path((root / m).resolve())}:{_container_target(m)}:ro"]
    if secret_files:
        if mask_file is None:
            raise ValueError("secret files to mask need a mask_file")
        src = docker_path(mask_file)
        for rel in secret_files:
            args += ["-v", f"{src}:{_container_target(rel)}:ro"]
    for rel in hidden_dirs:
        args += ["--tmpfs", f"{_container_target(rel)}:{HIDDEN_TMPFS_OPTS}"]
    args += [config.image, "sleep", "infinity"]
    return args


def build_exec_args(name: str, command: str, timeout: int, *, docker: str = "docker",
                    workdir: str = WORKDIR) -> list[str]:
    """``docker exec`` argv of one ``execute``: the command runs under
    coreutils ``timeout`` inside the container, so the process group is killed
    there and not only the host-side ``docker exec`` client."""
    return [docker, "exec", "-w", workdir, name,
            "timeout", "-k", str(KILL_AFTER_S), str(int(timeout)), "sh", "-c", command]


def _is_transient(stderr: str) -> bool:
    return any(m in stderr for m in TRANSIENT_MARKERS)


def _is_transient_exec(returncode: int | None, output: str) -> bool:
    """A ``docker exec`` that failed before the command started, because of the
    daemon (the error text comes on stdout or stderr, exit 126/127). Retrying
    it cannot run the command twice. A missing executable is not transient."""
    if returncode not in (126, 127):
        return False
    if "executable file not found" in output:
        return False
    return "OCI runtime exec failed" in output or _is_transient(output)


#: attempts of one ``docker exec`` that hit a transient daemon failure
EXEC_ATTEMPTS = 5


def _normalize_path(path: str) -> str | None:
    """Absolute normalised POSIX path, or ``None`` if the path is unusable."""
    if not isinstance(path, str) or not path or "\x00" in path or not path.startswith("/"):
        return None
    norm = posixpath.normpath(path)
    if norm.startswith("//"):
        norm = "/" + norm.lstrip("/")
    return norm


def _map_file_error(stderr: str, default: str) -> str:
    low = stderr.lower()
    if "read-only file system" in low or "permission denied" in low:
        return PERMISSION_DENIED
    if "is a directory" in low:
        return IS_DIRECTORY
    if "no such file" in low or "not a directory" in low:
        return FILE_NOT_FOUND
    return default


# ---------------------------------------------------------------------------
# Docker CLI (injectable, so lifecycle tests run without Docker)
# ---------------------------------------------------------------------------

@dataclass
class StreamResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    stdout_total: int
    stderr_total: int
    timed_out: bool  # the host-side wait expired and the client was killed
    elapsed: float


class DockerCLI:
    """Thin wrapper over the ``docker`` executable. Tests replace it."""

    def __init__(self, docker: str = "docker") -> None:
        self.docker = docker

    def run(self, args: Sequence[str], *, input: bytes | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            list(args), input=input, capture_output=True, timeout=timeout, check=False,
            stdin=None if input is not None else subprocess.DEVNULL)

    def stream(self, args: Sequence[str], *, timeout: float, limit: int) -> StreamResult:
        """Run ``args``, keep at most ``limit`` bytes of each stream (the rest is
        drained and counted, so memory stays bounded), kill the client after
        ``timeout`` seconds."""
        start = time.monotonic()
        proc = subprocess.Popen(
            list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        bufs: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}
        totals = {"out": 0, "err": 0}

        def pump(key: str, pipe: Any) -> None:
            try:
                while True:
                    chunk = pipe.read(65536)
                    if not chunk:
                        break
                    totals[key] += len(chunk)
                    room = limit - len(bufs[key])
                    if room > 0:
                        bufs[key] += chunk[:room]
            except (OSError, ValueError):
                pass

        threads = [threading.Thread(target=pump, args=("out", proc.stdout), daemon=True),
                   threading.Thread(target=pump, args=("err", proc.stderr), daemon=True)]
        for t in threads:
            t.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - a stuck client
                pass
        for t in threads:
            t.join(timeout=5)
        return StreamResult(
            returncode=proc.returncode, stdout=bytes(bufs["out"]), stderr=bytes(bufs["err"]),
            stdout_total=totals["out"], stderr_total=totals["err"], timed_out=timed_out,
            elapsed=time.monotonic() - start)


# ---------------------------------------------------------------------------
# Status / image
# ---------------------------------------------------------------------------

def sandbox_status(image: str = DEFAULT_IMAGE, *, docker: str = "docker") -> tuple[bool, str]:
    """``(True, "")`` when the docker CLI, the daemon and ``image`` are there;
    otherwise ``(False, reason)``. Cheap enough to call once per run."""
    if shutil.which(docker) is None:
        return False, f"the {docker!r} executable is not on PATH"
    cli = DockerCLI(docker)
    try:
        info = cli.run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker info failed: {exc}"
    if info.returncode != 0:
        return False, "the Docker daemon is not responding: " + info.stderr.decode("utf-8", "replace").strip()[:300]
    try:
        img = cli.run([docker, "image", "inspect", "--format", "{{.Id}}", image], timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker image inspect failed: {exc}"
    if img.returncode != 0:
        return False, (f"image {image} is not built; run tfl_deep_agent/sandbox/build.ps1 "
                       "(or build.sh)")
    return True, ""


def build_image(image: str = DEFAULT_IMAGE, *, docker: str = "docker",
                timeout: float = 1800) -> subprocess.CompletedProcess[bytes]:
    """``docker build -t image -`` with ``Dockerfile.sandbox`` on stdin: no
    build context, so no repository file can end up in the image."""
    return DockerCLI(docker).run([docker, "build", "-t", image, "-"],
                                 input=DOCKERFILE.read_bytes(), timeout=timeout)


def remove_stale_containers(*, docker: str = "docker", cli: DockerCLI | None = None) -> list[str]:
    """Remove every container labelled ``tfl.sandbox=1`` (leftovers of a killed
    process). Also removes the containers of other live runs, so call it only
    when no run is active. Returns the removed ids."""
    cli = cli or DockerCLI(docker)
    ls = cli.run([docker, "ps", "-aq", "--filter", f"label={LABEL}=1"], timeout=30)
    ids = ls.stdout.decode("utf-8", "replace").split()
    if ids:
        cli.run([docker, "rm", "-f", *ids], timeout=60)
    return ids


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------

def _cleanup(cli: DockerCLI, docker: str, name: str, mask_dir: str | None, state: dict[str, Any]) -> None:
    """Finalizer body: must not reference the sandbox object itself."""
    if state.get("started"):
        try:
            cli.run([docker, "rm", "-f", name], timeout=60)
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - daemon gone
            pass
        state["started"] = False
    if mask_dir:
        shutil.rmtree(mask_dir, ignore_errors=True)


@dataclass
class _Started:
    container_id: str = ""
    secret_files: list[str] = field(default_factory=list)
    hidden_dirs: list[str] = field(default_factory=list)


class DockerSandbox(BaseSandbox):
    """deepagents sandbox backend over one local Docker container.

    Usage::

        with DockerSandbox(run_id="run-42") as sb:
            sb.write("/workspace/a.py", "print(1)")
            sb.execute("python /workspace/a.py")

    The container starts on first use (or on ``start()`` / ``__enter__``) and
    is removed by ``close()`` / ``__exit__`` / garbage collection / interpreter
    exit, whichever comes first. A failed start raises
    :class:`SandboxUnavailable`.
    """

    def __init__(self, config: SandboxConfig | None = None, *, run_id: str | None = None,
                 repo_root: str | os.PathLike[str] | None = None, docker: str = "docker",
                 cli: DockerCLI | None = None, auto_start: bool = True) -> None:
        self.config = config or SandboxConfig()
        self.run_id = run_id
        self.repo_root = Path(repo_root) if repo_root is not None else REPO_ROOT
        self.docker = docker
        self.cli = cli or DockerCLI(docker)
        self.auto_start = auto_start
        tag = _NAME_OK.sub("-", run_id or "run").strip("-.")[:40] or "run"
        self._name = f"{NAME_PREFIX}{tag}-{uuid.uuid4().hex[:8]}"
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"started": False}
        self._closed = False
        self._started: _Started | None = None
        self._mask_dir: str | None = None
        self._finalizer: weakref.finalize | None = None

    # -- identity / lifecycle -------------------------------------------------

    @property
    def id(self) -> str:
        return self._name

    @property
    def name(self) -> str:
        return self._name

    @property
    def started(self) -> bool:
        return bool(self._state.get("started"))

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def secret_files(self) -> list[str]:
        """Repo-relative files masked in this container (after start)."""
        return list(self._started.secret_files) if self._started else []

    @property
    def hidden_dirs(self) -> list[str]:
        """Repo-relative directories hidden by an empty tmpfs (after start)."""
        return list(self._started.hidden_dirs) if self._started else []

    def run_args(self) -> list[str]:
        """The ``docker run`` argv this sandbox uses (secret files resolved
        now; the mask file path is a placeholder until start)."""
        secrets, hidden = self._masks()
        mask = Path(self._mask_dir or tempfile.gettempdir()) / "empty"
        return build_run_args(self._name, self.config, repo_root=self.repo_root, mask_file=mask,
                              secret_files=secrets, hidden_dirs=hidden, run_id=self.run_id,
                              docker=self.docker)

    def _masks(self) -> tuple[list[str], list[str]]:
        return scan_mounts(self.repo_root, self.config.mounts, hidden_dirs=self.config.hidden_dirs,
                           hidden_files=self.config.hidden_files)

    def start(self) -> DockerSandbox:
        with self._lock:
            if self._closed:
                raise SandboxError(f"sandbox {self._name} is closed")
            if self.started:
                return self
            for m in self.config.mounts:
                if not (self.repo_root / m).exists():
                    raise SandboxUnavailable(f"mount source {self.repo_root / m} does not exist")
            secrets, hidden = self._masks()
            self._mask_dir = tempfile.mkdtemp(prefix="tfl-sbx-mask-")
            mask = Path(self._mask_dir) / "empty"
            mask.write_bytes(b"")
            # registered before the container exists: a crash between run and
            # the end of start() still removes it
            self._finalizer = weakref.finalize(
                self, _cleanup, self.cli, self.docker, self._name, self._mask_dir, self._state)
            args = build_run_args(self._name, self.config, repo_root=self.repo_root, mask_file=mask,
                                  secret_files=secrets, hidden_dirs=hidden, run_id=self.run_id,
                                  docker=self.docker)
            last = ""
            for attempt in range(self.config.start_attempts):
                self._state["started"] = True  # a partial create must be rm -f'd as well
                try:
                    proc = self.cli.run(args, timeout=self.config.op_timeout)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    last = f"docker run failed: {exc}"
                    self._rm_quiet()
                    break
                if proc.returncode == 0:
                    self._started = _Started(container_id=proc.stdout.decode("utf-8", "replace").strip(),
                                             secret_files=secrets, hidden_dirs=hidden)
                    return self
                last = proc.stderr.decode("utf-8", "replace").strip()
                self._rm_quiet()
                if not _is_transient(last):
                    break
                time.sleep(0.5 * (attempt + 1))
            self._finalizer()
            raise SandboxUnavailable(f"could not start sandbox container {self._name}: {last[:500]}")

    def _rm_quiet(self) -> None:
        try:
            self.cli.run([self.docker, "rm", "-f", self._name], timeout=self.config.op_timeout)
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
            pass
        self._state["started"] = False

    def close(self) -> None:
        """Remove the container (``docker rm -f``) and the mask file. Idempotent."""
        with self._lock:
            self._closed = True
            if self._finalizer is not None:
                self._finalizer()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _ensure_started(self) -> None:
        if self._closed:
            raise SandboxError(f"sandbox {self._name} is closed")
        if not self.started:
            if not self.auto_start:
                raise SandboxError(f"sandbox {self._name} is not started")
            self.start()

    # -- execute --------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run ``command`` with ``sh -c`` in ``/workspace`` (combined output,
        stderr lines prefixed ``[stderr]``, at most ``max_output_bytes``)."""
        if not command or not isinstance(command, str):
            return ExecuteResponse(output="Error: Command must be a non-empty string.", exit_code=1)
        t = self.config.default_timeout if timeout is None else timeout
        if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
            raise ValueError(f"timeout must be a positive integer, got {t!r}")
        if t > self.config.max_timeout:
            raise ValueError(f"timeout {t}s exceeds the sandbox maximum ({self.config.max_timeout}s)")
        try:
            self._ensure_started()
        except SandboxError as exc:
            return ExecuteResponse(output=f"Error: sandbox unavailable: {exc}", exit_code=None)
        limit = self.config.max_output_bytes
        args = build_exec_args(self._name, command, t, docker=self.docker)
        for attempt in range(EXEC_ATTEMPTS):
            res = self.cli.stream(args, timeout=t + KILL_AFTER_S + HOST_GRACE_S, limit=limit + 1)
            seen = res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace")
            if res.timed_out or not _is_transient_exec(res.returncode, seen):
                break
            time.sleep(0.3 * (attempt + 1))  # daemon hiccup before the command started
        err_text = res.stderr.decode("utf-8", "replace")
        if res.timed_out:
            self._kill_all()
        timed_out = res.timed_out or (res.returncode in (124, 137) and res.elapsed >= t - 0.5)
        return self._format(res, err_text, limit=limit, timeout=t, timed_out=timed_out)

    def _format(self, res: StreamResult, err_text: str, *, limit: int, timeout: int,
                timed_out: bool) -> ExecuteResponse:
        parts: list[str] = []
        out_text = res.stdout.decode("utf-8", "replace")
        if out_text:
            parts.append(out_text.rstrip("\n"))
        if err_text.strip():
            parts.extend(f"[stderr] {line}" for line in err_text.rstrip("\n").split("\n"))
        output = "\n".join(parts) if parts else "<no output>"
        total = res.stdout_total + res.stderr_total
        truncated = False
        raw = output.encode("utf-8")
        if len(raw) > limit or res.stdout_total > len(res.stdout) or res.stderr_total > len(res.stderr):
            output = raw[:limit].decode("utf-8", "ignore")
            output += f"\n\n... [output truncated: {limit} of {total} bytes shown]"
            truncated = True
        exit_code = res.returncode
        if timed_out:
            output += f"\n\n[timeout] the command exceeded {timeout}s and was killed"
            exit_code = TIMEOUT_EXIT_CODE
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)

    def _exec_run(self, args: list[str], *, input: bytes | None = None
                  ) -> subprocess.CompletedProcess[bytes]:
        """``cli.run`` of a ``docker exec``, retried on a transient daemon failure."""
        proc = self.cli.run(args, input=input, timeout=self.config.op_timeout)
        for attempt in range(EXEC_ATTEMPTS - 1):
            seen = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
            if not _is_transient_exec(proc.returncode, seen):
                break
            time.sleep(0.3 * (attempt + 1))
            proc = self.cli.run(args, input=input, timeout=self.config.op_timeout)
        return proc

    def _kill_all(self) -> None:
        try:
            self._exec_run([self.docker, "exec", self._name, "python3", "-c", _KILL_ALL_SCRIPT])
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
            pass

    # -- files ----------------------------------------------------------------

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        out: list[FileUploadResponse] = []
        for path, content in files:
            norm = _normalize_path(path)
            if norm is None or not isinstance(content, (bytes, bytearray)):
                out.append(FileUploadResponse(path=path, error=INVALID_PATH))
                continue
            try:
                self._ensure_started()
                proc = self._exec_run(
                    [self.docker, "exec", "-i", self._name, "sh", "-c", _UPLOAD_SCRIPT, "sh", norm],
                    input=bytes(content))
            except SandboxError as exc:
                out.append(FileUploadResponse(path=path, error=f"sandbox unavailable: {exc}"))
                continue
            except (OSError, subprocess.TimeoutExpired) as exc:
                out.append(FileUploadResponse(path=path, error=f"upload failed: {exc}"))
                continue
            if proc.returncode == 0:
                out.append(FileUploadResponse(path=path, error=None))
            elif proc.returncode == 3:
                out.append(FileUploadResponse(path=path, error=IS_DIRECTORY))
            else:
                err = proc.stderr.decode("utf-8", "replace").strip()
                out.append(FileUploadResponse(path=path, error=_map_file_error(err, err[:300] or "upload failed")))
        return out

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        out: list[FileDownloadResponse] = []
        for path in paths:
            norm = _normalize_path(path)
            if norm is None:
                out.append(FileDownloadResponse(path=path, error=INVALID_PATH))
                continue
            try:
                self._ensure_started()
                proc = self._exec_run(
                    [self.docker, "exec", self._name, "sh", "-c", _DOWNLOAD_SCRIPT, "sh", norm,
                     str(self.config.max_download_bytes)])
            except SandboxError as exc:
                out.append(FileDownloadResponse(path=path, error=f"sandbox unavailable: {exc}"))
                continue
            except (OSError, subprocess.TimeoutExpired) as exc:
                out.append(FileDownloadResponse(path=path, error=f"download failed: {exc}"))
                continue
            code = proc.returncode
            if code == 0:
                out.append(FileDownloadResponse(path=path, content=proc.stdout))
            elif code == 2:
                out.append(FileDownloadResponse(path=path, error=FILE_NOT_FOUND))
            elif code == 3:
                out.append(FileDownloadResponse(path=path, error=IS_DIRECTORY))
            elif code == 4:
                out.append(FileDownloadResponse(path=path, error=PERMISSION_DENIED))
            else:
                err = proc.stderr.decode("utf-8", "replace").strip()
                out.append(FileDownloadResponse(path=path, error=err[:300] or f"download failed (exit {code})"))
        return out

    def __repr__(self) -> str:
        state = "closed" if self._closed else ("running" if self.started else "not started")
        return f"DockerSandbox(name={self._name!r}, image={self.config.image!r}, {state})"


def open_sandbox(run_id: str | None = None, config: SandboxConfig | None = None, **kw: Any
                 ) -> tuple[DockerSandbox | None, str]:
    """Start a sandbox for a run: ``(sandbox, "")``, or ``(None, reason)`` when
    Docker / the image is unavailable (then the agent runs without
    ``execute`` and the report says so)."""
    cfg = config or SandboxConfig()
    ok, reason = sandbox_status(cfg.image, docker=kw.get("docker", "docker"))
    if not ok:
        return None, reason
    sb = DockerSandbox(cfg, run_id=run_id, **kw)
    try:
        sb.start()
    except SandboxUnavailable as exc:
        return None, str(exc)
    return sb, ""

