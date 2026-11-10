import sys

from tfl_eval.false_proofs.runner import main

# Downgrade reasons contain non-ASCII text; a cp1251 Windows console would raise.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

raise SystemExit(main())
