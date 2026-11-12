## What and why

<!-- One topic per pull request. -->

## What I ran

- [ ] `python -m pytest tests`
- [ ] `python tools/validate_skill.py`
- [ ] `ruff check .`
- [ ] k6 was / was not available (say which; k6 tests skip without it)

## Checklist

- [ ] The safety gate is unchanged or stricter (load, stress, spike, soak, chaos and fuzz need a confirmed non-production target)
- [ ] No secrets are printed, logged or written to disk; new output goes through `_common.redact_text`
- [ ] New input paths have a hostile-input test
- [ ] CHANGELOG.md updated under the unreleased version
- [ ] Eval claims say what actually ran ("mock-based, real target not run" is a valid label)
