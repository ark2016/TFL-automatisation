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


# ---------------------------------------------------------------------------
# Proof-body safety gate (code review 2026-09-28, both blockers on this
# file). `compose_lean_file` is the one place the LLM-written proof body
# meets the deterministically-generated theorem statement, so it is also
# the one place a barrier against two classes of exploit belongs, rather
# than trusting every caller to remember to check separately:
#
# (a) Elaborator-level escape hatches. A proof body can reach Lean's own
#     metaprogramming API (`run_tac` + `Lean.addDecl` with
#     `debug.skipKernelTC`, or `elab`/`macro`/`run_cmd`/`run_meta`) to add a
#     declaration the *kernel* never actually checked, then have `tfl_main`
#     reference it -- `lake env lean --json` then reports zero errors, no
#     `sorry`, and `#print axioms tfl_main` prints an axiom-free result for
#     a *false* theorem. `_scan_proof_body` below lexically rejects the
#     tokens this needs before the text ever reaches the compiler.
# (b) `#print axioms` message spoofing/suppression. `#exit` after the proof
#     truncates the file before the appended `#print axioms` line runs at
#     all (silently returning "no message" rather than an error); a `trace`
#     call can print a fake "does not depend on any axioms" line *earlier*
#     than the real one. Both are why `check_lean_file` below matches the
#     axioms message (and the independent-replay message) by the *line
#     position* of the commands `compose_lean_file` itself appends, not by
#     "first message that mentions the theorem's name" -- and why `#exit`,
#     a `#`-command, and a zero-indented line are lexically rejected too.
#
# Defense in depth for whatever this lexical list misses: `compose_lean_file`
# also appends an independent kernel re-check (`Lean.Environment.replay`,
# `_REPLAY_CHECK_BLOCK` below) after the theorem. Unlike the compiler's own
# elaboration pipeline -- which a `debug.skipKernelTC` declaration can
# bypass for *that one declaration*, and which then trusts it forever after
# purely because it is sitting in the environment -- `replay` re-derives a
# *fresh* trusted environment from only the file's imports (already
# kernel-checked when those modules were built) and re-sends every
# constant the file itself added to the kernel from scratch, so a
# `skipKernelTC`-added fake declaration fails here even if it slipped past
# every lexical check (verified against the exact PoC from the review: see
# agent_system/tests/test_type_check_security.py).
# ---------------------------------------------------------------------------

_FORBIDDEN_PROOF_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, re.compile(pattern))
    for label, pattern in (
        ("run_tac", r"\brun_tac\b"),
        ("run_cmd", r"\brun_cmd\b"),
        ("run_meta", r"\brun_meta\b"),
        ("by_elab", r"\bby_elab\b"),
        ("elab", r"\belab\b"),
        ("macro", r"\bmacro\b"),
        ("syntax", r"\bsyntax\b"),
        ("Lean namespace reference (Lean.*)", r"\bLean\."),
        ("set_option", r"\bset_option\b"),
        ("unsafe", r"\bunsafe\b"),
        ("implemented_by", r"\bimplemented_by\b"),
        ("extern attribute", r"\bextern\b"),
        ("native_decide", r"\bnative_decide\b"),
        ("trace", r"\btrace\b"),
        ("#exit", r"#exit\b"),
    )
)
_COMMAND_TOKEN_RE = re.compile(r"(?:^|\s)#\S")


def _strip_lean_comments(text: str) -> str:
    """Blank out Lean ``--`` line comments and (possibly nested) ``/- -/``
    block comments, replacing their contents with spaces and keeping every
    line break exactly where it was.

    A real, hand-verified proof legitimately has comments that mention a
    banned word in passing (e.g. ``-- length #a + #b + #c``, or a comment
    line at column 0), and comments are invisible to Lean's own parser --
    they carry no indentation-sensitivity and can't smuggle in a command.
    `scan_proof_body` below scans (and measures indentation on) this
    stripped form so such comments are never mistaken for code.
    """
    out: list[str] = []
    i, n, depth = 0, len(text), 0
    while i < n:
        if depth == 0 and text.startswith("--", i):
            j = text.find("\n", i)
            end = n if j == -1 else j
            out.append(" " * (end - i))
            i = end
        elif text.startswith("/-", i):
            depth += 1
            out.append("  ")
            i += 2
        elif depth > 0 and text.startswith("-/", i):
            depth -= 1
            out.append("  ")
            i += 2
        elif depth > 0:
            out.append(text[i] if text[i] == "\n" else " ")
            i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def scan_proof_body(proof_body: Any) -> list[str]:
    """Lexical pre-compile gate on the LLM-authored proof body (see the
    module-level note above). Returns the list of violated pattern labels
    (``[]`` when clean); never raises.

    Only the LLM-written *proof_body* is scanned here -- never the
    deterministically-generated statement text or the ``#print axioms`` /
    replay-check code `compose_lean_file` appends itself, both of which
    legitimately use some of these tokens. Comments are stripped first
    (see `_strip_lean_comments`) so a comment merely mentioning a banned
    word, or sitting at column 0, is never itself a violation.
    """
    if not isinstance(proof_body, str):
        return ["proof body is not a string"]
    code = _strip_lean_comments(proof_body)
    violations = [
        label for label, pattern in _FORBIDDEN_PROOF_TOKEN_PATTERNS
        if pattern.search(code)
    ]
    if _COMMAND_TOKEN_RE.search(code):
        violations.append("'#' command token")
    # The first line inherits compose_lean_file's own "  " indentation
    # (LeanStatement.render appends ":= by\n  " before it); only a *later*
    # line starting at column 0 would actually dedent out of the `by`
    # block and be parsed as a new top-level command.
    for line in code.split("\n")[1:]:
        if line and not line[0].isspace():
            violations.append("line at zero indentation")
            break
    return violations


_REPLAY_IMPORT = "import Lean.Replay\nimport Lean.Elab.Command"

# Independent kernel re-check, appended after the theorem by
# compose_lean_file (see the module docstring above). `const2ModIdx`
# reliably identifies every constant the *file itself* added (anything
# from an import has an entry there; anything declared in this file does
# not), regardless of how it was added -- including a `tfl_main.fake`
# smuggled in via `debug.skipKernelTC`, which is exactly the point: those
# never went through the kernel the normal way, so replaying them into a
# freshly-imported (trusted) environment sends them to the kernel for the
# first time. `logInfo`/`logError` here are reported by Lean at the
# position of this `run_cmd` command itself, which is what
# `check_lean_file` matches against.
_REPLAY_CHECK_BLOCK = """open Lean in
run_cmd do
  let env ← getEnv
  let mut newConsts : Std.HashMap Name ConstantInfo := {}
  for (n, ci) in env.constants.toList do
    if !env.const2ModIdx.contains n then
      newConsts := newConsts.insert n ci
  try
    let importEnv ← importModules env.imports {} (trustLevel := 0)
    let _ ← Lean.Environment.replay newConsts importEnv
    logInfo "TFL_REPLAY_OK"
  catch e =>
    logError s!"TFL_REPLAY_FAIL: {(← e.toMessageData.toString)}\""""

_REJECTED_MARKER_PREFIX = "-- TFL_PROOF_BODY_REJECTED: "


def compose_lean_file(statement: Any, proof_body: str) -> str:
    """Substitute *proof_body* into the statement's proof and append the
    independent kernel replay check plus ``#print axioms tfl_main`` for
    the axiom check.

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

    If *proof_body* trips ``scan_proof_body``'s lexical gate, it is never
    substituted at all: the composed text carries a leading
    ``-- TFL_PROOF_BODY_REJECTED: <reasons>`` marker line that
    ``check_lean_file`` recognizes and turns into ``status="error"``
    without invoking Docker, and the theorem's proof is replaced by a
    reference to an undefined name (so that even a caller which skips
    `check_lean_file`'s marker check still gets a hard compile error, never
    a silent pass) -- this still returns an ordinary ``str`` and never
    raises, keeping ``compose_lean_file``'s existing contract.
    """
    violations = scan_proof_body(proof_body)
    effective_body = proof_body
    reason = None
    if violations:
        reason = "; ".join(violations)
        marker = "TFL_PROOF_BODY_REJECTED_" + re.sub(r"[^A-Za-z0-9_]+", "_", reason).strip("_")[:120]
        effective_body = (
            "-- R-Lean safety gate rejected this proof body before it reached "
            f"the compiler ({reason}); forcing a compile error.\n  exact {marker}"
        )

    render = getattr(statement, "render", None)
    if callable(render):
        text = render(effective_body)
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
            theorem_text = theorem_decl.replace("<PROOF>", effective_body)
        elif ":=" in theorem_decl:
            theorem_text = theorem_decl  # already a complete statement
        else:
            theorem_text = f"{theorem_decl} := by\n  {effective_body}"

        text = "\n\n".join(
            part for part in (imports_text, alphabet_decl, language_decl, theorem_text)
            if part.strip()
        ) + "\n"

    # The theorem is always named `tfl_main` by contract
    # (lean_ir.LeanStatement.name); this print, and the independent replay
    # check just before it, are how proved/error/axiom-violation/tampered
    # get told apart in check_lean_file below -- both matched there by the
    # *line position* of the commands appended here, not by message
    # content alone (see the module note above).
    composed = (
        f"{_REPLAY_IMPORT}\n{text.rstrip(chr(10))}\n\n"
        f"{_REPLAY_CHECK_BLOCK}\n\n"
        "#print axioms tfl_main\n"
    )
    if violations:
        composed = f"{_REJECTED_MARKER_PREFIX}{reason}\n{composed}"
    return composed


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
_REPLAY_MARKER = "run_cmd do"
_REPLAY_OK_MARKER = "TFL_REPLAY_OK"
_REPLAY_FAIL_MARKER = "TFL_REPLAY_FAIL"


def _line_of(text: str, index: int) -> int:
    """1-indexed line number of *index* within *text* -- Lean's own
    convention for a `--json` message's ``pos.line``."""
    return text.count("\n", 0, index) + 1


def _parse_axioms(messages: list[dict], theorem_name: str, expected_line: int | None) -> list[str] | None:
    """Extract the axiom list from the ``#print axioms <theorem_name>``
    message *compose_lean_file itself appended*, identified by matching
    both severity ``"information"`` and ``pos.line == expected_line`` (the
    real line of that command in the composed text) -- not merely "some
    message that happens to mention the theorem's name".

    Code review 2026-09-28 (this file, blocker): matching by name alone let
    a `trace "'tfl_main' does not depend on any axioms"` call inside the
    proof body -- or any other message coincidentally naming the theorem --
    forge this result *earlier* in the message stream than the real
    command's output. `#exit` in the proof body has the opposite effect:
    it truncates the file before the real ``#print axioms`` line ever
    runs, so no matching message exists at all; `expected_line=None` (the
    command's marker wasn't found in the text to begin with) is handled
    the same way. Both now return ``None`` here rather than silently
    falling through -- `check_lean_file` below never treats ``None`` as
    the empty axiom list.

    Returns ``None`` when no matching message is present -- distinct from
    ``[]``, which means the axiom check itself ran (at the right line) and
    found nothing beyond the kernel."""
    if expected_line is None:
        return None
    for msg in messages:
        if msg.get("severity") != "information":
            continue
        pos = msg.get("pos")
        if not isinstance(pos, dict) or pos.get("line") != expected_line:
            continue
        data = msg.get("data")
        if not isinstance(data, str) or theorem_name not in data:
            continue
        if _NO_AXIOMS_RE.search(data):
            return []
        m = _AXIOMS_LIST_RE.search(data)
        if m:
            return [item.strip() for item in m.group(1).split(",") if item.strip()]
    return None


def _parse_replay_status(messages: list[dict], expected_line: int | None) -> str | None:
    """``"ok"`` / ``"fail"`` / ``None`` (no message at the independent
    kernel-replay command's own line -- e.g. it never ran at all) for
    `compose_lean_file`'s appended ``run_cmd`` block, matched the same way
    as `_parse_axioms`: by the line position of the command that produced
    it, not by content alone."""
    if expected_line is None:
        return None
    for msg in messages:
        pos = msg.get("pos")
        if not isinstance(pos, dict) or pos.get("line") != expected_line:
            continue
        data = msg.get("data")
        if not isinstance(data, str):
            continue
        if _REPLAY_OK_MARKER in data:
            return "ok"
        if _REPLAY_FAIL_MARKER in data:
            return "fail"
    return None


def check_lean_file(text: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Type-check a composed Lean 4 file via `lake env lean --json` and
    classify the result (docs/VERDICT_POLICY.md R-Lean).

    Returns a dict with keys ``status`` (``proved`` | ``has_sorry`` |
    ``error`` | ``timeout`` | ``unavailable``), ``errors``, ``warnings``,
    ``axioms``, ``elapsed`` and ``message``.

    ``proved`` requires: no ``error``-severity message, no ``sorry``
    warning, and a ``#print axioms`` result (at its own known line -- see
    ``_parse_axioms``) that is a subset of ``ALLOWED_AXIOMS``. When *text*
    also carries the independent kernel-replay check `compose_lean_file`
    appends (i.e. it went through `compose_lean_file` at all -- the one
    path an LLM-written proof body can reach) that check must additionally
    report success at its own known line; a trusted, hand-written file
    checked directly (no replay marker present at all) isn't held to it.
    Everything else that would stop `proved` -- a compile error, a
    `sorry`, a disallowed axiom, a replay failure, a timeout, or Docker/the
    image being unavailable -- yields one of the other statuses, never
    `proved`.

    If *text* carries `compose_lean_file`'s own
    ``-- TFL_PROOF_BODY_REJECTED: ...`` marker (its proof body tripped
    ``scan_proof_body``'s lexical gate), this returns ``status="error"``
    immediately without invoking Docker at all.
    """
    if isinstance(text, str) and text.lstrip().startswith(_REJECTED_MARKER_PREFIX):
        reason = text.lstrip().splitlines()[0][len(_REJECTED_MARKER_PREFIX):]
        return {
            "status": "error",
            "errors": [{
                "severity": "error",
                "data": f"proof body rejected before compiling (R-Lean safety gate): {reason}",
            }],
            "warnings": [],
            "axioms": [],
            "elapsed": 0.0,
            "message": "rejected by scan_proof_body; Docker was not invoked",
        }

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
        axioms_line = _line_of(text, name_match.start()) if name_match else None
        axioms = _parse_axioms(messages, theorem_name, axioms_line)

        replay_idx = text.find(_REPLAY_MARKER)
        replay_line = _line_of(text, replay_idx) if replay_idx != -1 else None
        replay_status = _parse_replay_status(messages, replay_line)

        if result.returncode == 124:
            status = "timeout"
        elif not messages and result.returncode != 0:
            # `--json` produced nothing parseable at all (e.g. Lean crashed
            # before emitting any message) -- a non-zero exit is still a
            # hard failure, not a silent `proved`.
            status = "error"
            errors = [{"severity": "error", "data": (result.stdout + result.stderr).strip()}]
        elif result.returncode != 0:
            # Code review 2026-09-28 (blocker): a non-zero exit that *did*
            # emit some JSON messages (e.g. an OOM kill, code 137, after
            # `--memory=4g`, partway through) must not fall through to
            # `proved` just because none of what was printed before the
            # process died happened to be `error`-severity.
            status = "error"
            if not errors:
                errors = [{
                    "severity": "error",
                    "data": f"lean process exited with code {result.returncode}",
                }]
        elif errors:
            status = "error"
        elif sorry_warnings:
            status = "has_sorry"
        elif axioms is None:
            # Code review 2026-09-28 (blocker): no axiom-check result at
            # the expected line at all -- typically `#exit` in the proof
            # body truncated the file before `#print axioms` ran, or a
            # forged message sits at the wrong position. Previously this
            # fell straight through to `proved`.
            status = "error"
            errors = errors + [{
                "severity": "error",
                "data": (
                    f"no #print axioms result for {theorem_name!r} at its "
                    "expected line (possible #exit, or a crash before it ran)"
                ),
            }]
        elif not (set(axioms) <= ALLOWED_AXIOMS):
            status = "error"
            disallowed = sorted(set(axioms) - ALLOWED_AXIOMS)
            errors = errors + [{
                "severity": "error",
                "data": f"disallowed axioms for {theorem_name}: {disallowed}",
            }]
        elif replay_line is not None and replay_status != "ok":
            # Independent kernel replay (compose_lean_file's appended
            # `run_cmd`, docs/VERDICT_POLICY.md R-Lean) did not confirm
            # the result: either it actively failed (a declaration that
            # only "passed" via an elaborator-level escape hatch like
            # `debug.skipKernelTC` fails the kernel here even when the
            # plain compile-and-axiom-check above saw nothing wrong), or
            # it never ran / never reported at its expected line at all.
            #
            # Only enforced when *text* actually carries the marker (i.e.
            # it went through `compose_lean_file`, the one path an
            # LLM-written proof body can reach): a trusted, hand-written
            # file checked directly (TflLean/Lemmas.lean, a template, an
            # old-format caller) has nothing to replay against and is not
            # part of that adversarial-input trust boundary.
            status = "error"
            errors = errors + [{
                "severity": "error",
                "data": f"independent kernel replay check did not confirm the proof (status={replay_status!r})",
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
