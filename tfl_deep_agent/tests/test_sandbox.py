"""P5: DockerSandbox (deepagents BaseSandbox over one local container).

Two layers:

* command construction and lifecycle with a fake Docker CLI: always run, no
  Docker needed (``docker run`` flags, secret masking, exec argv, retries on a
  flaky daemon, output truncation / timeout formatting, cleanup paths);
* real isolation (``@needs_docker``): only when the docker CLI, the daemon and
  the ``tfl-sandbox`` image are there (build it with
  ``tfl_deep_agent/sandbox/build.ps1`` or ``build.sh``), otherwise skipped with
  the reason. A container that cannot start because of the daemon (the
  ``OCI runtime ... read init-p`` failures noted in DEV_NOTES) skips as well.

No test calls a model API: the mock coder scenario uses scripted chat models.
"""

from __future__ import annotations

import gc
import json
import os
import re
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("deepagents")

from deepagents.backends.protocol import (
    SandboxBackendProtocol,
    execute_accepts_timeout,
)

from tfl_deep_agent.sandbox import docker_backend as D
from tfl_deep_agent.sandbox.docker_backend import (
    DockerSandbox,
    SandboxConfig,
    SandboxError,
    SandboxUnavailable,
    StreamResult,
)

SANDBOX_DIR = Path(D.__file__).resolve().parent
REPO = D.REPO_ROOT


# ---------------------------------------------------------------------------
# Fake docker CLI
# ---------------------------------------------------------------------------

def _cp(rc: int = 0, out: bytes = b"", err: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


def _sr(rc: int | None = 0, out: bytes = b"", err: bytes = b"", *, timed_out: bool = False,
        elapsed: float = 0.1, out_total: int | None = None, err_total: int | None = None) -> StreamResult:
    return StreamResult(returncode=rc, stdout=out, stderr=err,
                        stdout_total=len(out) if out_total is None else out_total,
                        stderr_total=len(err) if err_total is None else err_total,
                        timed_out=timed_out, elapsed=elapsed)


class FakeCLI:
    """Records argv; ``run_replies`` / ``stream_replies`` are consumed in order
    (the last one repeats); ``docker run`` defaults to success."""

    def __init__(self, *, run_replies: list[Any] | None = None, stream_replies: list[Any] | None = None):
        self.runs: list[tuple[list[str], bytes | None]] = []
        self.streams: list[tuple[list[str], float, int]] = []
        self.run_replies = list(run_replies or [])
        self.stream_replies = list(stream_replies or [])

    def _next(self, queue: list[Any], default: Any) -> Any:
        if not queue:
            return default
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def run(self, args, *, input=None, timeout=None):
        self.runs.append((list(args), input))
        if args[1] in ("rm", "ps"):
            return _cp()
        reply = self._next(self.run_replies, _cp(0, b"cid123\n"))
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def stream(self, args, *, timeout, limit):
        self.streams.append((list(args), timeout, limit))
        return self._next(self.stream_replies, _sr(0, b"ok\n"))

    def verbs(self) -> list[str]:
        return [a[1] for a, _ in self.runs]

    def rm_calls(self) -> list[list[str]]:
        return [a for a, _ in self.runs if a[1] == "rm"]


def _fake_sandbox(tmp_path: Path, cli: FakeCLI, **cfg: Any) -> DockerSandbox:
    (tmp_path / "pkg").mkdir(exist_ok=True)
    config = SandboxConfig(mounts=("pkg",), **cfg)
    return DockerSandbox(config, run_id="unit", repo_root=tmp_path, cli=cli)


def _pairs(args: list[str]) -> set[tuple[str, str]]:
    return {(args[i], args[i + 1]) for i in range(len(args) - 1)}


# ---------------------------------------------------------------------------
# docker run / exec argv
# ---------------------------------------------------------------------------

def test_run_args_have_every_isolation_flag():
    args = D.build_run_args("tfl-sbx-x-1", SandboxConfig(), repo_root=REPO, run_id="r1")
    pairs = _pairs(args)
    assert args[:4] == ["docker", "run", "-d", "--rm"]
    for flag in [("--network", "none"), ("--memory", "1g"), ("--memory-swap", "1g"), ("--cpus", "1"),
                 ("--pids-limit", "256"), ("--user", "1000:1000"),
                 ("--security-opt", "no-new-privileges"), ("--cap-drop", "ALL"),
                 ("--name", "tfl-sbx-x-1"), ("--label", "tfl.sandbox=1"), ("--workdir", "/workspace"),
                 ("-e", "PYTHONPATH=/opt/tfl")]:
        assert flag in pairs, flag
    assert "--read-only" in args
    tmpfs = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs"]
    assert any(t.startswith("/workspace:") and "size=256m" in t for t in tmpfs)
    assert any(t.startswith("/tmp:") for t in tmpfs)
    assert all("noexec" in t and "nosuid" in t for t in tmpfs)
    assert args[-3:] == ["tfl-sandbox:latest", "sleep", "infinity"]
    assert "--privileged" not in args and "--network=host" not in args


def test_run_args_mount_the_packages_read_only_under_opt_tfl():
    args = D.build_run_args("n", SandboxConfig(), repo_root=REPO)
    vols = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    targets = {v.rsplit(":", 2)[1] for v in vols}
    assert targets == {f"/opt/tfl/{m}" for m in D.DEFAULT_MOUNTS}
    assert all(v.endswith(":ro") for v in vols)
    # the whole working tree (root .env, run dirs, venv) is not mounted by default
    assert "/opt/tfl" not in targets
    for v in vols:
        src = v.rsplit(":", 2)[0]
        assert src == D.docker_path((REPO / v.rsplit(":", 2)[1][len("/opt/tfl/"):]).resolve())


def test_whole_repo_mount_is_opt_in():
    args = D.build_run_args("n", SandboxConfig(mounts=(".",)), repo_root=REPO)
    vols = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    assert vols == [f"{D.docker_path(REPO.resolve())}:/opt/tfl:ro"]


@pytest.mark.skipif(os.name != "nt", reason="Windows path conversion")
def test_windows_paths_go_through_type_check_conversion():
    assert D.docker_path(r"C:\Users\x\repo") == "/c/Users/x/repo"
    args = D.build_run_args("n", SandboxConfig(mounts=("cfl_system",)), repo_root=REPO)
    vol = args[args.index("-v") + 1]
    assert re.match(r"^/[a-z]/", vol), vol


def test_secret_files_are_found_and_masked(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__pycache__").mkdir()
    for rel in [".env", ".env.local", ".env.example", "sub/key.pem", "sub/id_rsa", "ok.py",
                "__pycache__/.env", "environment.py"]:
        (pkg / rel).write_text("SECRET=1", encoding="utf-8")
    secrets = D.find_secret_files(tmp_path, ["pkg"])
    # caches are walked too: a .env under __pycache__ is mounted, so it is masked
    assert secrets == ["pkg/.env", "pkg/.env.local", "pkg/__pycache__/.env", "pkg/sub/id_rsa",
                       "pkg/sub/key.pem"]
    mask = tmp_path / "empty"
    args = D.build_run_args("n", SandboxConfig(mounts=("pkg",)), repo_root=tmp_path,
                            mask_file=mask, secret_files=secrets)
    vols = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    assert vols[0].endswith(":/opt/tfl/pkg:ro")  # the parent mount comes first
    masked = {v.rsplit(":", 2)[1] for v in vols[1:]}
    assert masked == {"/opt/tfl/" + s for s in secrets}
    assert all(v.startswith(D.docker_path(mask) + ":") for v in vols[1:])
    with pytest.raises(ValueError, match="mask_file"):
        D.build_run_args("n", SandboxConfig(mounts=("pkg",)), repo_root=tmp_path, secret_files=secrets)


def test_hidden_dirs_are_covered_by_empty_tmpfs_and_not_walked(tmp_path):
    for rel in ["pkg/lib/a.py", "pkg/tests/golden.json", "pkg/examples/task.json", "pkg/.git/config",
                "pkg/.venv/lib/test.key", "pkg/.pytest_cache/v/.env", "pkg/tmp_x/notes.md",
                "pkg/.claude/settings.json", "pkg/docs/EVAL_SET.md"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    files, dirs = D.scan_mounts(tmp_path, ["pkg"])
    assert dirs == ["pkg/.claude", "pkg/.git", "pkg/.venv", "pkg/examples", "pkg/tests", "pkg/tmp_x"]
    # the key inside .venv is covered by the hidden dir; caches are walked
    assert files == ["pkg/.pytest_cache/v/.env", "pkg/docs/EVAL_SET.md"]
    mask = tmp_path / "empty"
    args = D.build_run_args("n", SandboxConfig(mounts=("pkg",)), repo_root=tmp_path, mask_file=mask,
                            secret_files=files, hidden_dirs=dirs)
    tmpfs = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs"]
    for d in dirs:
        assert f"/opt/tfl/{d}:{D.HIDDEN_TMPFS_OPTS}" in tmpfs
    assert all(t.split(":", 1)[1].startswith("ro,") for t in tmpfs if t.startswith("/opt/tfl/"))
    # a mount that is itself a hidden dir is hidden whole
    assert D.scan_mounts(tmp_path, ["pkg/tests"])[1] == ["pkg/tests"]


def test_whole_repo_mount_hides_vcs_venv_claude_runs_and_eval_answers():
    files, dirs = D.scan_mounts(REPO, (".",))
    for d in (".git", ".venv"):
        if (REPO / d).is_dir():
            assert d in dirs
    for d in (".claude", ".tfl_lab_runs", "docs/examples", "tfl_deep_agent/tests"):
        if (REPO / d).is_dir():
            assert d in dirs, d
    assert "docs/EVAL_SET.md" in files
    if (REPO / ".env").exists():
        assert ".env" in files
    assert not any(f.startswith((".venv/", ".git/")) for f in files)


def test_default_mounts_do_not_expose_the_eval_set():
    assert "docs" not in D.DEFAULT_MOUNTS
    assert "docs/THEORY.md" in D.DEFAULT_MOUNTS
    _files, dirs = D.scan_mounts(REPO, D.DEFAULT_MOUNTS)
    for pkg in ("agent_system", "cfl_system", "dcfl_system", "ll_system"):
        for sub in ("tests", "examples"):
            if (REPO / pkg / sub).is_dir():
                assert f"{pkg}/{sub}" in dirs
    args = D.build_run_args("n", SandboxConfig(), repo_root=REPO)
    vols = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    assert not any(v.rsplit(":", 2)[1] == "/opt/tfl/docs" for v in vols)


def test_real_repo_env_file_would_be_masked():
    secrets = D.find_secret_files(REPO, D.DEFAULT_MOUNTS)
    for rel in secrets:
        assert Path(rel).parts[0] in D.DEFAULT_MOUNTS
    if (REPO / "agent_system" / ".env").exists():
        assert "agent_system/.env" in secrets
    assert "agent_system/.env.example" not in secrets


def test_exec_args_run_the_command_under_timeout_in_workspace():
    args = D.build_exec_args("box", "python a.py | head", 7)
    assert args == ["docker", "exec", "-w", "/workspace", "box",
                    "timeout", "-k", str(D.KILL_AFTER_S), "7", "sh", "-c", "python a.py | head"]


@pytest.mark.parametrize("kw", [
    {"default_timeout": 0}, {"max_timeout": -1}, {"default_timeout": 700, "max_timeout": 600},
    {"max_output_bytes": 0}, {"pids_limit": 0}, {"mounts": ("/etc",)}, {"mounts": ("../x",)},
])
def test_config_rejects_bad_limits(kw):
    with pytest.raises(ValueError):
        SandboxConfig(**kw)


def test_is_a_deepagents_sandbox_backend():
    sb = DockerSandbox(cli=FakeCLI())
    assert isinstance(sb, SandboxBackendProtocol)
    assert execute_accepts_timeout(DockerSandbox)
    assert sb.id.startswith("tfl-sbx-run-") and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", sb.id)
    assert re.fullmatch(r"tfl-sbx-a-b-c-[0-9a-f]{8}", DockerSandbox(run_id="a/b c", cli=FakeCLI()).id)


# ---------------------------------------------------------------------------
# Lifecycle with the fake CLI
# ---------------------------------------------------------------------------

def test_starts_lazily_once_and_close_removes_the_container(tmp_path):
    cli = FakeCLI()
    sb = _fake_sandbox(tmp_path, cli)
    assert not sb.started and cli.runs == []
    assert sb.execute("echo 1").output == "ok"
    sb.execute("echo 2")
    assert cli.verbs() == ["run"] and sb.started
    assert len(cli.streams) == 2
    sb.close()
    sb.close()
    assert cli.rm_calls() == [["docker", "rm", "-f", sb.name]]
    assert sb.closed and not sb.started
    resp = sb.execute("echo 3")
    assert resp.exit_code is None and "closed" in resp.output
    with pytest.raises(SandboxError, match="closed"):
        sb.start()


def test_context_manager_removes_container_on_error(tmp_path):
    cli = FakeCLI()
    with pytest.raises(RuntimeError, match="boom"), _fake_sandbox(tmp_path, cli) as sb:
        assert sb.started
        raise RuntimeError("boom")
    assert cli.rm_calls() == [["docker", "rm", "-f", sb.name]]


def test_garbage_collection_removes_container(tmp_path):
    cli = FakeCLI()
    sb = _fake_sandbox(tmp_path, cli)
    sb.start()
    name = sb.name
    del sb
    gc.collect()
    assert cli.rm_calls() == [["docker", "rm", "-f", name]]


def test_mask_dir_is_removed_on_close(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / ".env").write_text("K=1", encoding="utf-8")
    cli = FakeCLI()
    sb = _fake_sandbox(tmp_path, cli)
    sb.start()
    assert sb.secret_files == ["pkg/.env"]
    run_argv = cli.runs[0][0]
    mask_src = next(v for i, v in enumerate(run_argv) if run_argv[i - 1] == "-v" and "/opt/tfl/pkg/.env" in v)
    assert mask_src.endswith(":/opt/tfl/pkg/.env:ro")
    mask_dir = Path(sb._mask_dir)
    assert (mask_dir / "empty").read_bytes() == b""
    sb.close()
    assert not mask_dir.exists()


def test_start_retries_a_transient_daemon_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    flaky = _cp(125, err=b"docker: Error response from daemon: OCI runtime create failed: runc create failed: "
                         b"waiting for init preliminary setup: read init-p: connection reset by peer")
    cli = FakeCLI(run_replies=[flaky, _cp(0, b"cid\n")])
    sb = _fake_sandbox(tmp_path, cli)
    sb.start()
    assert cli.verbs() == ["run", "rm", "run"]  # the half-created container is removed first
    assert sb.started
    sb.close()


def test_start_gives_up_on_a_real_error(tmp_path, monkeypatch):
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    cli = FakeCLI(run_replies=[_cp(125, err=b"Unable to find image 'tfl-sandbox:latest' locally")])
    sb = _fake_sandbox(tmp_path, cli)
    with pytest.raises(SandboxUnavailable, match="Unable to find image"):
        sb.start()
    assert cli.verbs() == ["run", "rm"]
    assert not sb.started
    assert sb._mask_dir is not None and not Path(sb._mask_dir).exists()
    # execute reports it instead of raising
    resp = sb.execute("echo")
    assert resp.exit_code is None and "unavailable" in resp.output


def test_start_needs_existing_mount_sources(tmp_path):
    sb = DockerSandbox(SandboxConfig(mounts=("missing",)), repo_root=tmp_path, cli=FakeCLI())
    with pytest.raises(SandboxUnavailable, match="does not exist"):
        sb.start()


def test_transient_exec_failure_is_retried_but_a_missing_program_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    oci = _sr(126, b"OCI runtime exec failed: exec failed: unable to start container process: "
                   b"waiting for init preliminary setup: read init-p: connection reset by peer: unknown\r")
    cli = FakeCLI(stream_replies=[oci, oci, _sr(0, b"42\n")])
    sb = _fake_sandbox(tmp_path, cli)
    resp = sb.execute("python -c 'print(42)'")
    assert resp.output == "42" and resp.exit_code == 0
    assert len(cli.streams) == 3

    cli2 = FakeCLI(stream_replies=[_sr(127, err=b"sh: 1: nosuch: not found\n")])
    sb2 = _fake_sandbox(tmp_path, cli2)
    resp2 = sb2.execute("nosuch")
    assert resp2.exit_code == 127 and "[stderr] sh: 1: nosuch: not found" in resp2.output
    assert len(cli2.streams) == 1
    assert not D._is_transient_exec(126, 'OCI runtime exec failed: exec: "x": executable file not found in $PATH')
    sb.close()
    sb2.close()


def test_upload_and_download_retry_transient_exec_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    oci = _cp(126, b"OCI runtime exec failed: exec failed: read init-p: connection reset by peer\r")
    cli = FakeCLI(run_replies=[_cp(0, b"cid"), oci, _cp(0)])
    sb = _fake_sandbox(tmp_path, cli)
    assert sb.upload_files([("/workspace/a.py", b"x")])[0].error is None
    assert cli.verbs() == ["run", "exec", "exec"]
    sb.close()


# ---------------------------------------------------------------------------
# execute: timeout, output
# ---------------------------------------------------------------------------

def test_execute_timeout_argument(tmp_path):
    cli = FakeCLI()
    sb = _fake_sandbox(tmp_path, cli, default_timeout=30, max_timeout=100)
    sb.execute("true")
    args, host_wait, limit = cli.streams[-1]
    assert args[args.index("timeout") + 3] == "30"
    assert host_wait == 30 + D.KILL_AFTER_S + D.HOST_GRACE_S
    assert limit == sb.config.max_output_bytes + 1
    sb.execute("true", timeout=5)
    assert cli.streams[-1][0][cli.streams[-1][0].index("timeout") + 3] == "5"
    for bad in (0, -1, 101, 1.5, True):
        with pytest.raises(ValueError):
            sb.execute("true", timeout=bad)
    assert sb.execute("").exit_code == 1
    sb.close()


def test_execute_formats_stderr_and_exit_code(tmp_path):
    cli = FakeCLI(stream_replies=[_sr(3, b"out1\nout2\n", b"err1\nerr2\n")])
    sb = _fake_sandbox(tmp_path, cli)
    resp = sb.execute("x")
    assert resp.output == "out1\nout2\n[stderr] err1\n[stderr] err2"
    assert resp.exit_code == 3 and not resp.truncated
    cli.stream_replies = [_sr(0)]
    assert sb.execute("true").output == "<no output>"
    sb.close()


def test_execute_truncates_output(tmp_path):
    limit = 100
    cli = FakeCLI(stream_replies=[_sr(0, b"y" * (limit + 1), out_total=10_000)])
    sb = _fake_sandbox(tmp_path, cli, max_output_bytes=limit)
    resp = sb.execute("yes")
    assert resp.truncated
    assert resp.output.startswith("y" * limit)
    assert f"{limit} of 10000 bytes shown" in resp.output
    sb.close()


def test_in_container_timeout_is_reported_as_124(tmp_path):
    cli = FakeCLI(stream_replies=[_sr(124, b"partial\n", elapsed=2.05)])
    sb = _fake_sandbox(tmp_path, cli)
    resp = sb.execute("sleep 30", timeout=2)
    assert resp.exit_code == D.TIMEOUT_EXIT_CODE
    assert "partial" in resp.output and "exceeded 2s" in resp.output
    # a fast command that itself exits 124 is not a timeout
    cli.stream_replies = [_sr(124, b"", elapsed=0.1)]
    resp2 = sb.execute("exit 124", timeout=10)
    assert resp2.exit_code == 124 and "[timeout]" not in resp2.output
    sb.close()


def test_host_side_timeout_kills_every_process_in_the_container(tmp_path):
    cli = FakeCLI(stream_replies=[_sr(None, timed_out=True, elapsed=40)])
    sb = _fake_sandbox(tmp_path, cli)
    resp = sb.execute("sleep 1000", timeout=10)
    assert resp.exit_code == D.TIMEOUT_EXIT_CODE and "[timeout]" in resp.output
    kill = [a for a, _ in cli.runs if a[1] == "exec"]
    assert kill and kill[0][:5] == ["docker", "exec", sb.name, "python3", "-c"]
    sb.close()


# ---------------------------------------------------------------------------
# upload / download
# ---------------------------------------------------------------------------

def test_upload_paths_and_error_mapping(tmp_path):
    cli = FakeCLI(run_replies=[
        _cp(0, b"cid"),
        _cp(0),
        _cp(1, err=b"sh: 1: cannot create /opt/tfl/x: Read-only file system"),
        _cp(3),
        _cp(1, err=b"sh: cannot create /root/x: Permission denied"),
    ])
    sb = _fake_sandbox(tmp_path, cli)
    res = sb.upload_files([
        ("/workspace/d/a.py", b"print(1)"),
        ("/opt/tfl/x", b"x"),
        ("/workspace", b"x"),
        ("/root/x", b"x"),
        ("relative.py", b"x"),
        ("/bad\x00name", b"x"),
    ])
    assert [r.error for r in res] == [None, "permission_denied", "is_directory", "permission_denied",
                                       "invalid_path", "invalid_path"]
    assert [r.path for r in res][:2] == ["/workspace/d/a.py", "/opt/tfl/x"]
    first = cli.runs[1]
    assert first[0][:4] == ["docker", "exec", "-i", sb.name]
    assert first[0][-1] == "/workspace/d/a.py" and first[1] == b"print(1)"
    assert len([1 for a, _ in cli.runs if a[1] == "exec"]) == 4  # invalid paths never reach docker
    sb.close()


def test_upload_normalises_the_path(tmp_path):
    cli = FakeCLI()
    sb = _fake_sandbox(tmp_path, cli)
    sb.upload_files([("/workspace/./d/../a.py", b"")])
    assert cli.runs[-1][0][-1] == "/workspace/a.py"
    sb.close()


def test_download_error_mapping(tmp_path):
    cli = FakeCLI(run_replies=[_cp(0, b"cid"), _cp(0, b"\x00\x01data"), _cp(2), _cp(3), _cp(4),
                               _cp(5, err=b"file is 99 bytes, limit 10")])
    sb = _fake_sandbox(tmp_path, cli)
    res = sb.download_files(["/workspace/a.bin", "/nope", "/workspace", "/root/x", "/workspace/big", "x"])
    assert res[0].content == b"\x00\x01data" and res[0].error is None
    assert [r.error for r in res[1:4]] == ["file_not_found", "is_directory", "permission_denied"]
    assert "limit 10" in res[4].error
    assert res[5].error == "invalid_path"
    sb.close()


def test_sandbox_status_without_docker(monkeypatch):
    monkeypatch.setattr(D.shutil, "which", lambda name: None)
    ok, reason = D.sandbox_status()
    assert not ok and "not on PATH" in reason
    sb, why = D.open_sandbox("r")
    assert sb is None and "not on PATH" in why


def test_remove_stale_containers_uses_the_label():
    class CLI(FakeCLI):
        def run(self, args, *, input=None, timeout=None):
            self.runs.append((list(args), input))
            return _cp(0, b"a1\nb2\n") if args[1] == "ps" else _cp()

    cli = CLI()
    assert D.remove_stale_containers(cli=cli) == ["a1", "b2"]
    assert cli.runs[0][0] == ["docker", "ps", "-aq", "--filter", "label=tfl.sandbox=1"]
    assert cli.runs[1][0] == ["docker", "rm", "-f", "a1", "b2"]


# ---------------------------------------------------------------------------
# Image definition and build scripts
# ---------------------------------------------------------------------------

def test_dockerfile_is_slim_python_without_repo_files():
    text = (SANDBOX_DIR / "Dockerfile.sandbox").read_text(encoding="utf-8")
    text.encode("ascii")  # build.ps1 pipes it as text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert lines[0] == "FROM python:3.12-slim"
    assert not any(ln.split()[0] in ("COPY", "ADD") for ln in lines)
    assert "USER 1000:1000" in lines
    assert "ENV PYTHONPATH=/opt/tfl \\" in lines


def test_dockerfile_pins_the_repo_runtime_dependencies():
    """Every runtime dependency of the pipeline packages is installed, pinned to
    the version in this venv (a venv upgrade should rebuild the image). CI resolves
    the newest versions, so there only the presence of each pin is checked."""
    text = (SANDBOX_DIR / "Dockerfile.sandbox").read_text(encoding="utf-8")
    pins = dict(re.findall(r'"([A-Za-z0-9_.-]+)==([^"]+)"', text))
    for dist in ("anthropic", "httpx2", "python-dotenv", "langgraph", "langchain-core"):
        assert dist in pins, dist
        if os.environ.get("CI"):
            continue
        try:
            installed = metadata.version(dist)
        except metadata.PackageNotFoundError:  # pragma: no cover
            continue
        assert pins[dist] == installed, f"{dist}: Dockerfile pins {pins[dist]}, venv has {installed}"


def test_build_scripts_send_the_dockerfile_without_context():
    sh = (SANDBOX_DIR / "build.sh").read_text(encoding="utf-8")
    ps = (SANDBOX_DIR / "build.ps1").read_text(encoding="utf-8")
    assert 'docker build -t "$IMAGE" - < "$HERE/Dockerfile.sandbox"' in sh
    assert "tfl-sandbox:latest" in sh and "tfl-sandbox:latest" in ps
    assert "docker build -t $Image -" in ps and "Dockerfile.sandbox" in ps


# ---------------------------------------------------------------------------
# Real Docker
# ---------------------------------------------------------------------------

_STATUS = D.sandbox_status()
needs_docker = pytest.mark.skipif(not _STATUS[0], reason=f"Docker sandbox unavailable: {_STATUS[1]}")


def _start_or_skip(sb: DockerSandbox) -> DockerSandbox:
    try:
        return sb.start()
    except SandboxUnavailable as exc:
        pytest.skip(f"the Docker daemon could not start the sandbox: {exc}")


def _run_ok(sb: DockerSandbox, command: str, **kw: Any):
    resp = sb.execute(command, **kw)
    if resp.exit_code in (125, 126) and "OCI runtime" in resp.output:  # pragma: no cover - flaky daemon
        pytest.skip(f"Docker daemon failure: {resp.output[:200]}")
    return resp


@pytest.fixture(scope="module")
def box():
    if not _STATUS[0]:
        pytest.skip(_STATUS[1])
    sb = DockerSandbox(run_id="pytest")
    _start_or_skip(sb)
    yield sb
    sb.close()


@needs_docker
def test_real_no_network(box):
    resp = _run_ok(box, "python - <<'EOF'\n"
                        "import os, socket\n"
                        "print('ifaces', sorted(os.listdir('/sys/class/net')))\n"
                        "try:\n"
                        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
                        "    print('CONNECTED')\n"
                        "except OSError as e:\n"
                        "    print('refused', type(e).__name__)\n"
                        "try:\n"
                        "    socket.getaddrinfo('pypi.org', 443)\n"
                        "    print('RESOLVED')\n"
                        "except OSError as e:\n"
                        "    print('no dns', type(e).__name__)\n"
                        "EOF")
    assert resp.exit_code == 0, resp.output
    assert "ifaces ['lo']" in resp.output
    assert "refused" in resp.output and "no dns" in resp.output
    assert "CONNECTED" not in resp.output and "RESOLVED" not in resp.output


@needs_docker
def test_real_timeout_fires(box):
    t0 = time.monotonic()
    resp = _run_ok(box, "echo started; sleep 60; echo never", timeout=2)
    elapsed = time.monotonic() - t0
    assert resp.exit_code == D.TIMEOUT_EXIT_CODE, resp.output
    assert "started" in resp.output and "never" not in resp.output and "exceeded 2s" in resp.output
    assert elapsed < 2 + D.KILL_AFTER_S + 10
    # the process is gone inside the container, not only the docker exec client
    ps = _run_ok(box, "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done")
    assert "sleep 60" not in ps.output


@needs_docker
def test_real_repo_is_read_only_and_only_tmpfs_is_writable(box):
    resp = _run_ok(box, "ls /opt/tfl; touch /opt/tfl/x; touch /opt/tfl/cfl_system/x; touch /etc/x; "
                        "touch /usr/local/x; echo hi > /workspace/w; cat /workspace/w; echo t > /tmp/t; cat /tmp/t")
    for m in D.DEFAULT_MOUNTS:
        assert m.split("/")[0] in resp.output
    assert resp.output.count("Read-only file system") == 4, resp.output
    assert "hi" in resp.output and "\nt" in resp.output
    res = box.upload_files([("/opt/tfl/cfl_system/evil.py", b"x"), ("/workspace/ok.txt", b"ok")])
    assert res[0].error == "permission_denied" and res[1].error is None
    assert box.download_files(["/workspace/ok.txt"])[0].content == b"ok"
    assert not (REPO / "cfl_system" / "evil.py").exists()


@needs_docker
def test_real_limits_user_and_capabilities(box):
    resp = _run_ok(box, "id -u; id -g; grep CapEff /proc/self/status; "
                        "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.max 2>/dev/null; "
                        "grep NoNewPrivs /proc/self/status")
    out = resp.output
    assert out.splitlines()[:2] == ["1000", "1000"]
    assert "CapEff:\t0000000000000000" in out
    assert re.search(r"NoNewPrivs:\s+1", out)
    if "1073741824" not in out:  # pragma: no cover - cgroup v1 host
        pytest.skip("cgroup v2 limits not visible")
    assert "\n256" in out


@needs_docker
def test_real_output_is_truncated(box):
    resp = _run_ok(box, "python -c \"print('x' * 500000)\"")
    assert resp.truncated and resp.exit_code == 0
    assert len(resp.output.encode()) < box.config.max_output_bytes + 200


CODER_SCRIPT = '''\
"""Brute force: CYK on S0 -> eps | A B | A C, S -> A B | A C, C -> S B, A -> a, B -> b
(CNF of S -> a S b | eps) against the predicate w = a^n b^n, all words over {a, b}
up to length 8."""
import itertools
import sys

sys.path.insert(0, "/opt/tfl")
from cfl_system.lib.cyk import cyk_parse

G = {
    "terminals": ["a", "b"], "nonterminals": ["S0", "S", "A", "B", "C"], "start": "S0",
    "rules": [
        {"lhs": "S0", "rhs": []}, {"lhs": "S0", "rhs": ["A", "B"]}, {"lhs": "S0", "rhs": ["A", "C"]},
        {"lhs": "S", "rhs": ["A", "B"]}, {"lhs": "S", "rhs": ["A", "C"]}, {"lhs": "C", "rhs": ["S", "B"]},
        {"lhs": "A", "rhs": ["a"]}, {"lhs": "B", "rhs": ["b"]},
    ],
}
BOUND = 8
words = mismatches = members = 0
for n in range(BOUND + 1):
    for t in itertools.product("ab", repeat=n):
        w = "".join(t)
        k = len(w) // 2
        expected = w == "a" * k + "b" * k
        got = cyk_parse(G, w)
        words += 1
        members += got
        mismatches += got != expected
print(f"bound={BOUND} words={words} members={members} mismatches={mismatches}")
print("cyk module:", cyk_parse.__module__, sys.modules["cfl_system.lib.cyk"].__file__)
'''


@needs_docker
def test_real_coder_style_script_imports_cyk_from_opt_tfl(box):
    assert box.write("/workspace/brute.py", CODER_SCRIPT).error is None
    resp = _run_ok(box, "python /workspace/brute.py", timeout=60)
    assert resp.exit_code == 0, resp.output
    assert "bound=8 words=511 members=5 mismatches=0" in resp.output
    assert "/opt/tfl/cfl_system/lib/cyk.py" in resp.output


@needs_docker
def test_real_secret_files_are_empty_inside(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / ".env").write_text("ANTHROPIC_API_KEY=sk-should-not-leak\n", encoding="utf-8")
    (pkg / "mod.py").write_text("VALUE = 7\n", encoding="utf-8")
    sb = DockerSandbox(SandboxConfig(mounts=("pkg",)), run_id="mask", repo_root=tmp_path)
    _start_or_skip(sb)
    try:
        resp = _run_ok(sb, "wc -c < /opt/tfl/pkg/.env; cat /opt/tfl/pkg/.env; "
                           "python -c 'import pkg.mod as m; print(\"value\", m.VALUE)'; "
                           "echo x > /opt/tfl/pkg/.env")
        assert "sk-should-not-leak" not in resp.output
        assert resp.output.splitlines()[0].strip() == "0"
        assert "value 7" in resp.output and "Read-only file system" in resp.output
    finally:
        sb.close()
    assert (pkg / ".env").read_text(encoding="utf-8").startswith("ANTHROPIC_API_KEY=")


@needs_docker
def test_real_eval_answers_and_tests_are_not_visible(box):
    resp = _run_ok(box, "test -e /opt/tfl/docs/EVAL_SET.md && echo LEAK-EVAL; "
                        "test -e /opt/tfl/docs/examples && echo LEAK-EXAMPLES; "
                        "ls -A /opt/tfl/cfl_system/tests | wc -l; "
                        "test -s /opt/tfl/docs/THEORY.md && echo theory-ok; "
                        "touch /opt/tfl/cfl_system/tests/x")
    assert "LEAK" not in resp.output
    assert resp.output.splitlines()[0].strip() == "0"
    assert "theory-ok" in resp.output and "Read-only file system" in resp.output


def _container_exists(name: str) -> bool:
    out = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^/{name}$"],
                         capture_output=True, text=True, timeout=30, check=False)
    return bool(out.stdout.strip())


@needs_docker
def test_real_close_and_interpreter_exit_remove_the_container():
    sb = DockerSandbox(run_id="cleanup")
    _start_or_skip(sb)
    assert _container_exists(sb.name)
    sb.close()
    assert not _container_exists(sb.name)

    code = ("from tfl_deep_agent.sandbox.docker_backend import DockerSandbox\n"
            "sb = DockerSandbox(run_id='atexit')\n"
            "try:\n"
            "    sb.start()\n"
            "except Exception as e:\n"
            "    print('SKIP', e)\n"
            "else:\n"
            "    print('NAME', sb.name, flush=True)\n")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180,
                          cwd=str(REPO), env=env, check=False)
    lines = dict(ln.split(" ", 1) for ln in proc.stdout.splitlines() if " " in ln)
    if "SKIP" in lines:  # pragma: no cover - flaky daemon
        pytest.skip(lines["SKIP"])
    assert proc.returncode == 0, proc.stderr
    name = lines["NAME"].strip()
    assert not _container_exists(name), f"{name} survived interpreter exit"


def _route_scoped_permissions(prefixes=("/skills/", "/input/")):
    """Write-deny rules whose every path starts with a route prefix.

    deepagents 0.7.34 refuses FilesystemPermission rules next to an execution
    backend unless every rule path starts with a CompositeBackend route
    (``_all_paths_scoped_to_routes``); ``agent.readonly_permissions`` also lists
    the bare roots ("/skills", "/input"), which never pass that check. Wiring the
    sandbox into ``agent.py`` (outside P5) needs this shape plus an ``/input/``
    route; see DEV_NOTES P5. Until then the acceptance test patches it."""
    from deepagents.middleware.filesystem import FilesystemPermission

    return [FilesystemPermission(operations=["write"], paths=[f"{p.rstrip('/')}/**" for p in prefixes],
                                 mode="deny")]


@needs_docker
def test_mock_coder_scenario_runs_cyk_in_the_sandbox(monkeypatch):
    """P5 acceptance: the coder role, driven by scripted models, writes a brute
    force script to /workspace and runs it; the script imports cyk from
    /opt/tfl inside the container."""
    from deepagents.backends import StateBackend

    from tfl_deep_agent import agent as A

    monkeypatch.setattr(A, "readonly_permissions", _route_scoped_permissions)

    settings = json.loads((REPO / "tfl_deep_agent" / "settings.example.json").read_text(encoding="utf-8"))
    script = {
        "task": "Проверь перебором, что грамматика S -> aSb | ε порождает ровно a^n b^n до длины 8.",
        "models": {
            "main": [
                {"tool_calls": [{"name": "task", "id": "call-coder", "args": {
                    "subagent_type": "coder",
                    "description": "Сверь CYK для S -> aSb | ε с предикатом a^n b^n на всех словах длины <= 8."}}]},
                {"content": "coder: 511 слов, расхождений нет (до длины 8).",
                 "expect": {"last_message_contains": "mismatches=0"}},
            ],
            "coder": [
                {"tool_calls": [{"name": "write_file", "id": "w1", "args": {
                    "file_path": "/workspace/brute.py", "content": CODER_SCRIPT}}]},
                {"tool_calls": [{"name": "execute", "id": "x1", "args": {
                    "command": "python /workspace/brute.py", "timeout": 60}}]},
                {"content": "bound=8 words=511 members=5 mismatches=0 (вывод execute выше).",
                 "expect": {"last_message_contains": "mismatches=0"}},
            ],
        },
    }
    sb = DockerSandbox(run_id="mock-coder")
    _start_or_skip(sb)
    try:
        factory = A.scripted_model_factory(script)
        graph = A.create_tfl_agent(settings, model_factory=factory,
                                   backend=A.make_backend(default=sb, routes={A.INPUT_ROUTE: StateBackend()}),
                                   hitl=False)
        state = A.run_task(graph, script["task"])
    finally:
        sb.close()
    coder = factory.models["coder"]
    assert coder.calls_made == 3
    assert "execute" in (coder.bound_tools or [])
    exec_msg = coder.received[-1][-1]
    text = str(exec_msg.content)
    if "OCI runtime" in text:  # pragma: no cover - flaky daemon
        pytest.skip(f"Docker daemon failure: {text[:200]}")
    assert getattr(exec_msg, "name", None) == "execute"
    assert "bound=8 words=511 members=5 mismatches=0" in text
    assert "/opt/tfl/cfl_system/lib/cyk.py" in text
    assert "succeeded with exit code 0" in text
    # main never had execute
    assert "execute" not in (factory.models["main"].bound_tools or [])
    assert state["messages"][-1].content.startswith("coder: 511")
    assert not _container_exists(sb.name)
