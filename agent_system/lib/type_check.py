"""Lean 4 type checker wrapper (§4.13 of TFL Agent System spec).

Runs Lean 4 inside a Docker container to type-check code snippets.

Note for Windows with Docker Desktop:
    Volume mounts from temp directories (e.g. C:\\Users\\...\\AppData\\Local\\Temp)
    may require that the drive or directory is shared in Docker Desktop settings.
    Go to Docker Desktop -> Settings -> Resources -> File Sharing and ensure
    the temp directory (or its parent drive) is listed.
"""

import json
import re
import subprocess
import tempfile
import time
import shutil
import os
from pathlib import Path
from typing import Any

DOCKER_IMAGE = "tfl-lean4"
DEFAULT_TIMEOUT = 300  # seconds — Mathlib imports are much slower to elaborate than the old, dependency-free templates
DOCKER_DIR = Path(__file__).resolve().parent.parent / "docker"
COMPOSE_FILE = DOCKER_DIR / "docker-compose.yml"
# Working directory for `lake env lean` inside the container: the tfl_lean
# lake project baked into the image at build time (see Dockerfile.lean4),
# which has Mathlib.Computability.DFA / Mathlib.Computability.RegularExpressions
# pre-built. Running `lean` from here (via `lake env`) puts Mathlib's .olean
# cache on LEAN_PATH so imports resolve without recompiling Mathlib.
LEAN_PROJECT_DIR = "/home/lean/tfl_lean"

# docs/VERDICT_POLICY.md R-Lean: a Lean file only earns `proved` when
# `#print axioms <name>` reports nothing outside this set (`native_decide`/
# `ofReduceBool` are explicitly NOT allowed -- they would let a proof
# "compile" without actually checking anything).
ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})


def _windows_docker_path(p: str) -> str:
    """Convert a Windows path to a form Docker Desktop accepts for -v mounts.

    Docker Desktop on Windows accepts paths like ``/c/Users/...`` or the
    raw Windows path ``C:\\Users\\...``.  We convert to the forward-slash
    POSIX form so the volume mount works reliably across shells.
    """
    posix = Path(p).as_posix()
    # Turn  C:/Users/...  into  /c/Users/...
    if len(posix) >= 2 and posix[1] == ":":
        posix = "/" + posix[0].lower() + posix[2:]
    return posix


def _docker_is_running() -> bool:
    """Check that the Docker daemon is actually responsive."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def is_docker_available() -> bool:
    """Check if Docker is installed, running, and the tfl-lean4 image exists."""
    if shutil.which("docker") is None:
        return False
    if not _docker_is_running():
        return False
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", DOCKER_IMAGE],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def check_lean(code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Type-check Lean 4 code via Docker container.

    If Docker is not available, returns status="skipped".

    Parameters
    ----------
    code : str
        Lean 4 source code to check.
    timeout : int
        Maximum seconds to allow the Lean process inside Docker.

    Returns
    -------
    dict
        Keys: status, errors, warnings, time_seconds, message.
    """
    if not is_docker_available():
        return {
            "status": "skipped",
            "errors": None,
            "warnings": None,
            "time_seconds": 0.0,
            "message": "Docker not available",
        }

    # Write code to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".lean", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        # On Windows, convert the path for Docker volume mounts.
        if os.name == "nt":
            mount_src = _windows_docker_path(tmp_path)
        else:
            mount_src = tmp_path

        start = time.monotonic()

        # Run lean (via `lake env`, from the tfl_lean project dir, so Mathlib
        # is on LEAN_PATH) in the Docker container.
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--memory=4g", "--cpus=2",
                "-v", f"{mount_src}:/home/lean/check.lean:ro",
                "-w", LEAN_PROJECT_DIR,
                DOCKER_IMAGE,
                "timeout", str(timeout), "lake", "env", "lean", "/home/lean/check.lean",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 30,  # extra buffer for Docker overhead
        )

        elapsed = time.monotonic() - start

        output = result.stdout + result.stderr

        # Parse output for errors and warnings
        errors: list[str] = []
        warnings: list[str] = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "error" in stripped.lower():
                errors.append(stripped)
            elif "warning" in stripped.lower() or "sorry" in stripped.lower():
                warnings.append(stripped)

        # Determine status
        if result.returncode == 124:
            status = "timeout"
        elif result.returncode == 0 and not errors:
            status = "valid"
        else:
            status = "invalid"

        return {
            "status": status,
            "errors": errors if errors else None,
            "warnings": warnings if warnings else None,
            "time_seconds": round(elapsed, 2),
            "message": None,
        }

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return {
            "status": "timeout",
            "errors": None,
            "warnings": None,
            "time_seconds": round(elapsed, 2),
            "message": f"Process killed after {timeout}s",
        }
    except OSError as exc:
        elapsed = time.monotonic() - start
        return {
            "status": "skipped",
            "errors": None,
            "warnings": None,
            "time_seconds": round(elapsed, 2),
            "message": f"Docker error: {exc}",
        }
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# v2 (docs/VERDICT_POLICY.md R-Lean): compose a statement (rendered
# deterministically from the IR by lib.lean_ir.render_statement, never by
# the LLM) with an LLM-written proof body, and check the result with
# `lake env lean --json` for a structured verdict -- proved / has_sorry /
# error / timeout / unavailable -- instead of the old free-text grep.
# ---------------------------------------------------------------------------

def _stmt_field(statement: Any, name: str, default: Any = "") -> Any:
    """Read *name* off *statement*, which may be the dataclass-like object
    `lean_ir.render_statement` returns or a plain dict (tests build both)."""
    if isinstance(statement, dict):
        return statement.get(name, default)
    return getattr(statement, name, default)


def compose_lean_file(statement: Any, proof_body: str) -> str:
    """Substitute *proof_body* into the statement's proof and append
    ``#print axioms tfl_main`` for the axiom check.

    *statement* is whatever ``lib.lean_ir.render_statement(ir, direction)``
    returns -- a frozen ``LeanStatement`` (``imports``, ``alphabet_decl``,
    ``language_decl``, a bare ``theorem_decl`` with no ``:=``, and its own
    ``.render(proof) -> str`` that assembles the full file and appends
    ``:= by\\n  <proof>``) -- the formulation, generated deterministically
    from the IR, never by the LLM (docs/VERDICT_POLICY.md R-Lean). Only
    the proof itself is the LLM-written *proof_body*; nothing here ever
    touches ``imports``/``alphabet_decl``/``language_decl``/``theorem_decl``.

    Falls back to assembling the same shape by hand for a plain dict (as
    used by tests) or an object without ``.render`` -- also accepting a
    ``theorem_decl`` with a literal ``<PROOF>`` placeholder or one that is
    already a complete ``:=`` statement, for backward compatibility.
    """
    render = getattr(statement, "render", None)
    if callable(render):
        text = render(proof_body)
    else:
        imports = _stmt_field(statement, "imports", "")
        if isinstance(imports, (list, tuple)):
            imports_text = "\n".join(imports)
        else:
            imports_text = str(imports or "")

        alphabet_decl = str(_stmt_field(statement, "alphabet_decl", "") or "")
        language_decl = str(_stmt_field(statement, "language_decl", "") or "")
        theorem_decl = str(_stmt_field(statement, "theorem_decl", "") or "")

        if "<PROOF>" in theorem_decl:
            theorem_text = theorem_decl.replace("<PROOF>", proof_body)
        elif ":=" in theorem_decl:
            theorem_text = theorem_decl  # already a complete statement
        else:
            theorem_text = f"{theorem_decl} := by\n  {proof_body}"

        text = "\n\n".join(
            part for part in (imports_text, alphabet_decl, language_decl, theorem_text)
            if part.strip()
        ) + "\n"

    # The theorem is always named `tfl_main` by contract
    # (lean_ir.LeanStatement.name); this print is how proved/error/
    # axiom-violation get told apart in check_lean_file below.
    return f"{text.rstrip(chr(10))}\n\n#print axioms tfl_main\n"


def _parse_lean_json_messages(raw: str) -> list[dict]:
    """`lake env lean --json` prints one JSON object per line. Non-JSON
    lines (e.g. stray tool banners) are skipped rather than failing the
    whole parse."""
    messages: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and "severity" in msg:
            messages.append(msg)
    return messages


_PRINT_AXIOMS_RE = re.compile(r"#print axioms\s+(\S+)")
_AXIOMS_LIST_RE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")
_NO_AXIOMS_RE = re.compile(r"does not depend on any axioms")


def _parse_axioms(messages: list[dict], theorem_name: str) -> list[str] | None:
    """Extract the axiom list from the ``#print axioms <theorem_name>``
    message. Returns ``None`` when no such message is present at all
    (typically because compilation failed before Lean got to it) --
    distinct from ``[]``, which means the axiom check itself ran and
    found nothing beyond the kernel."""
    for msg in messages:
        data = msg.get("data")
        if not isinstance(data, str) or theorem_name not in data:
            continue
        if _NO_AXIOMS_RE.search(data):
            return []
        m = _AXIOMS_LIST_RE.search(data)
        if m:
            return [item.strip() for item in m.group(1).split(",") if item.strip()]
    return None


def check_lean_file(text: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Type-check a composed Lean 4 file via `lake env lean --json` and
    classify the result (docs/VERDICT_POLICY.md R-Lean).

    Returns a dict with keys ``status`` (``proved`` | ``has_sorry`` |
    ``error`` | ``timeout`` | ``unavailable``), ``errors``, ``warnings``,
    ``axioms``, ``elapsed`` and ``message``.

    ``proved`` requires: no ``error``-severity message, no ``sorry``
    warning, and the axioms printed for ``tfl_main`` are a subset of
    ``ALLOWED_AXIOMS``. Everything else that would stop that -- a
    compile error, a `sorry`, a timeout, or Docker/the image being
    unavailable -- yields one of the other statuses, never `proved`.
    """
    if not is_docker_available():
        return {
            "status": "unavailable",
            "errors": [],
            "warnings": [],
            "axioms": [],
            "elapsed": 0.0,
            "message": "Docker not available",
        }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".lean", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(text)
        tmp_path = f.name

    start = time.monotonic()
    try:
        mount_src = _windows_docker_path(tmp_path) if os.name == "nt" else tmp_path
        env = dict(os.environ)
        # Only matters when this call itself goes through a POSIX shell
        # (Git Bash) that would otherwise mangle the container path; a
        # harmless no-op for the plain subprocess.run() below.
        env["MSYS_NO_PATHCONV"] = "1"

        try:
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--memory=4g", "--cpus=2",
                    "-v", f"{mount_src}:/home/lean/check.lean:ro",
                    "-w", LEAN_PROJECT_DIR,
                    DOCKER_IMAGE,
                    "timeout", str(timeout), "lake", "env", "lean", "--json",
                    "/home/lean/check.lean",
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 30,
                env=env,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return {
                "status": "timeout",
                "errors": [],
                "warnings": [],
                "axioms": [],
                "elapsed": round(elapsed, 2),
                "message": f"Process killed after {timeout}s",
            }
        except OSError as exc:
            elapsed = time.monotonic() - start
            return {
                "status": "unavailable",
                "errors": [],
                "warnings": [],
                "axioms": [],
                "elapsed": round(elapsed, 2),
                "message": f"Docker error: {exc}",
            }

        elapsed = time.monotonic() - start
        messages = _parse_lean_json_messages(result.stdout)

        errors = [m for m in messages if m.get("severity") == "error"]
        warnings = [m for m in messages if m.get("severity") == "warning"]
        sorry_warnings = [
            w for w in warnings
            if isinstance(w.get("data"), str) and "sorry" in w["data"].lower()
        ]

        name_match = _PRINT_AXIOMS_RE.search(text)
        theorem_name = name_match.group(1) if name_match else "tfl_main"
        axioms = _parse_axioms(messages, theorem_name)

        if result.returncode == 124:
            status = "timeout"
        elif not messages and result.returncode != 0:
            # `--json` produced nothing parseable at all (e.g. Lean crashed
            # before emitting any message) -- a non-zero exit is still a
            # hard failure, not a silent `proved`.
            status = "error"
            errors = [{"severity": "error", "data": (result.stdout + result.stderr).strip()}]
        elif errors:
            status = "error"
        elif sorry_warnings:
            status = "has_sorry"
        elif axioms is not None and not (set(axioms) <= ALLOWED_AXIOMS):
            status = "error"
            disallowed = sorted(set(axioms) - ALLOWED_AXIOMS)
            errors = errors + [{
                "severity": "error",
                "data": f"disallowed axioms for {theorem_name}: {disallowed}",
            }]
        else:
            status = "proved"

        return {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "axioms": axioms if axioms is not None else [],
            "elapsed": round(elapsed, 2),
            "message": None,
        }
    finally:
        Path(tmp_path).unlink(missing_ok=True)
