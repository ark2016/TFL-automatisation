# Security Policy

## Supported versions

Only the latest commit on `main` is supported. This is a small research project without stable releases; there is no backport policy.

## Reporting a vulnerability

If you believe you have found a security issue in this repository, please **do not open a public issue**. Instead, report it privately:

- Preferred: use GitHub's [private vulnerability reporting](https://github.com/Ark2016/TFL-automatisation/security/advisories/new) (Security tab → "Report a vulnerability")
- Alternative: contact the repository owner through the email on their GitHub profile

Please include:

- A short description of the issue
- Steps to reproduce (minimal task IR, command, observed result)
- The commit SHA where you observed the issue

You can expect an initial response within 7 days. Once a fix is prepared, it will be published as a regular commit on `main`; a GitHub Security Advisory may be opened depending on severity.

## Scope

This repository distributes code that calls the Anthropic API on behalf of the user. The main classes of concern:

- **Credential exposure.** `ANTHROPIC_API_KEY` must never be committed; `.env` is git-ignored. If you find a key accidentally committed to history, report privately and rotate the key immediately via the [Anthropic console](https://console.anthropic.com/).
- **Prompt-injection attacks.** The pipelines accept natural-language problem statements and pass them to an LLM. A crafted `source_text` that manipulates an agent into producing misleading output is a known risk and is in scope.
- **Arbitrary code execution.** The orchestrators spawn `python -m <project>.orchestrator` subprocesses based on user input in the UI. Command arguments are built from a fixed allow-list of project IDs, not from free-form input. Any path traversal or argument injection in `ui_server/server.py` is in scope.

Out of scope: general behavior of the underlying LLM, Anthropic API availability, and issues in third-party dependencies (report those upstream).
