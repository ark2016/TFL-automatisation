"""Machine-built proof artefacts for specific DCFL tasks (docs/VERDICT_POLICY.md R2':
"конструктивный сертификат для DCFL"). Each submodule is self-contained: brute-force
oracle + NPDA + determinization + certificate export for one task, runnable as
``python -m dcfl_system.tools.<name> --out PATH [--check N]``.
"""
