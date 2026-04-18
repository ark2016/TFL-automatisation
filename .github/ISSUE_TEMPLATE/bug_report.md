---
name: Bug report
about: Something in the pipeline or UI behaves incorrectly
title: "[BUG] "
labels: bug
assignees: ''
---

## Which pipeline / component

- [ ] REG (`agent_system/`)
- [ ] CFL (`cfl_system/`)
- [ ] DCFL (`dcfl_system/`)
- [ ] LL (`ll_system/`)
- [ ] TFL Lab UI (`ui_server/`)
- [ ] Renderer (`*/lib/*renderer.py`)
- [ ] Other (please specify)

## Run mode

- [ ] Mock (no LLM)
- [ ] Live (Anthropic API)
  - Model: <!-- Opus 4.7 / Sonnet 4.6 / Haiku 4.5 -->

## What happened

<!-- Short description of the wrong behavior. -->

## What you expected

<!-- Short description of the correct behavior. -->

## Reproducer

### Task IR

<details>
<summary>`path/to/task.json` (paste or attach)</summary>

```json
{
  "task_type": "...",
  "source_text": "...",
  "language_spec": { ... }
}
```

</details>

### Command

```bash
.venv/Scripts/python -m <project>.orchestrator path/to/task.json --verbose [--live] --save ...
```

### Output

<details>
<summary>stderr log (relevant portion or full)</summary>

```
paste here
```

</details>

<details>
<summary>`_result.json` if produced</summary>

```json
paste here
```

</details>

## Environment

- OS: <!-- e.g. Windows 11 / macOS 15 / Ubuntu 24.04 -->
- Python: <!-- output of `python --version` -->
- Commit: <!-- output of `git rev-parse HEAD` -->
- `dot -V`: <!-- Graphviz version, or "not installed" -->

## Additional context

<!-- Anything else — screenshots of the rendered HTML report, bisect results, suspicion about the root cause, ... -->
