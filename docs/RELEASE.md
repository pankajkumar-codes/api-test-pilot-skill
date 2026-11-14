# Release checklist

Version **0.1.0**. Nothing below has been done yet: the steps marked **(outward-facing)** publish something and need the
maintainer's explicit go-ahead each time. Run the "Before tagging" block as often as you like; it changes nothing outside this checkout.

## Before tagging (safe, local)
- [ ] `git status` clean on `main`, branch up to date with `origin/main`.
- [ ] `python -m pytest tests` passes. With k6 installed (`K6_BIN` or PATH) the generated k6 scripts run too; record whether k6 was present.
- [ ] `python tools/validate_skill.py` prints `OK` (frontmatter, layout, plugin files, version consistency across
      `SKILL.md` metadata, `plugin.json`, CHANGELOG).
- [ ] `claude plugin validate .` passes. Known, accepted output: a warning that `CLAUDE.md` at the plugin root is not loaded
      (it is repository instructions, not plugin content), and a directory advice that no `icon` is set (see "Icon" below).
- [ ] Install from a clean directory: `claude plugin marketplace add ./` then `claude plugin install api-test-pilot-skill@api-test-pilot-skill`
      with a scratch `HOME`; `claude plugin list` shows version 0.1.0.
- [ ] `python tools/build_skill_zip.py`; unzip into an empty folder, create a fresh virtualenv with `pip install pytest requests`,
      and run the README example (intake, generate, run, report) against `examples/demo_server.py`.
- [ ] Scan for secrets: `git grep -nEi "(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}"` shows only test fixtures; run
      the GitHub secret scan on the branch.
- [ ] CHANGELOG: rename `## [0.1.0] - unreleased` to the release date; "Known gaps" still accurate.
- [ ] PLAN.md Phase 6 boxes ticked for what is actually done; DECISIONS.md updated.
- [ ] Optional but recommended: person runs the real Petstore, VAmPI and DVGA evals (`evals/README.md`) and records results in
      `docs/evals.md`. Until then every doc says "mock-based, real target not run" for them; keep it that way.

## Tag and release (outward-facing, maintainer only)
- [ ] Repository is public and its visibility, description and topics are set (suggested topics: `agent-skills`, `claude-code`,
      `api-testing`, `openapi`, `graphql`, `pytest`, `k6`, `owasp`).
- [ ] Tag: `git tag -a v0.1.0 -m "api-test-pilot-skill 0.1.0" && git push origin v0.1.0`. (`claude plugin tag` would create
      `api-test-pilot-skill--v0.1.0`, which is only needed when other plugins depend on a version range.)
- [ ] GitHub release for `v0.1.0`: notes from CHANGELOG; attach `dist/api-testing-0.1.0.zip` and its SHA-256 (printed by the zip tool).
- [ ] Bump `version` in `SKILL.md` metadata, `.claude-plugin/plugin.json` and CHANGELOG together for every later release.
      `plugin.json` sets `version`, so users only receive an update when it changes.

## Marketplace submissions (outward-facing, maintainer only)
Copy for every listing is in [LISTING.md](LISTING.md). Do not submit before the tag exists and the README install lines work.
- [ ] **Own marketplace:** nothing to submit. Once the repository is public, `/plugin marketplace add pankajkumar-codes/api-test-pilot-skill` works.
- [ ] **Anthropic community plugin marketplace:** submit through the plugin directory form
      (claude.ai: Directory submissions, needs a Team or Enterprise organization; or the Console form at
      `platform.claude.com/plugins/submit` for individuals). Re-check the current requirements first; the form may ask for a
      privacy policy URL, support URL and icon.
- [ ] **Claude app skill upload:** upload `dist/api-testing-0.1.0.zip` in Settings, Capabilities, Skills (personal use; this is not a public listing).
- [ ] **Other Agent Skills marketplaces and directories:** each has its own form or pull-request flow; check its current rules,
      then reuse the LISTING.md copy. Do not claim features the README does not.

## Open items for the maintainer
- **Icon.** The Anthropic directory wants a square PNG or JPEG (512 to 2048 px, under 2 MB), set as `icon` in `plugin.json`.
  No icon is included; the skill works without one. Do not use a third party's logo.
- **Privacy policy and support URLs** if a directory asks for them. The skill itself sends nothing anywhere except HTTP requests to
  the target API the user names; it collects no telemetry. A GitHub Issues link can serve as the support URL.
- **Copyright holder.** LICENSE names `pankajkumar-codes`; change it to your legal name or organization if you prefer.
- **Plugin size.** The plugin source is the whole repository (`source: "./"`), so an install also copies `tests/`, `evals/` and
  `docs/`. If that matters, move the plugin into a subfolder and point `source` at it.
