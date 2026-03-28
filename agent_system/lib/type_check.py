"""Lean 4 type checker wrapper (§4.13 of TFL Agent System spec).

Runs Lean 4 inside a Docker container to type-check code snippets.

Note for Windows with Docker Desktop:
    Volume mounts from temp directories (e.g. C:\\Users\\...\\AppData\\Local\\Temp)
    may require that the drive or directory is shared in Docker Desktop settings.
    Go to Docker Desktop -> Settings -> Resources -> File Sharing and ensure
    the temp directory (or its parent drive) is listed.
"""

import subprocess
import tempfile
import time
import shutil
import os
from pathlib import Path

DOCKER_IMAGE = "tfl-lean4"
DEFAULT_TIMEOUT = 120  # seconds
DOCKER_DIR = Path(__file__).resolve().parent.parent / "docker"
COMPOSE_FILE = DOCKER_DIR / "docker-compose.yml"


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

        # Run lean in Docker container
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--memory=2g", "--cpus=2",
                "-v", f"{mount_src}:/home/lean/check.lean:ro",
                DOCKER_IMAGE,
                "timeout", str(timeout), "lean", "/home/lean/check.lean",
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
