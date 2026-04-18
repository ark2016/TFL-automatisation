<!-- Thanks for contributing! Please fill out the sections below. -->

## Summary

<!-- One or two sentences on what this PR changes and why. -->

## Scope

- [ ] Bug fix
- [ ] New test case / example
- [ ] New specialist agent or prompt
- [ ] New pipeline (new project folder)
- [ ] Renderer / UI improvement
- [ ] Refactor / infra (no behavior change)
- [ ] Docs

## Test plan

- [ ] `python -m pytest agent_system/tests cfl_system/tests dcfl_system/tests ll_system/tests -q` passes locally
- [ ] Manually tested the affected pipeline with `--mock` on a representative IR
- [ ] (if LLM behavior changed) Ran at least one `--live` task and reviewed the rendered HTML report
- [ ] (if UI changed) Loaded `http://127.0.0.1:8765/`, ran a mock task, all four result tabs render

## Related issues

<!-- Closes #X, part of #Y, etc. -->

## Notes for the reviewer

<!-- Anything non-obvious: tricky edge cases, alternative designs considered, open questions, follow-ups deferred. -->
