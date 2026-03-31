"""Allow running the agent system as ``python -m agent_system <args>``."""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so that ``agent_system`` is
# importable as a package even when invoked directly.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _cli() -> None:
    from agent_system.orchestrator import main  # noqa: E402
    main()


if __name__ == "__main__":
    _cli()
