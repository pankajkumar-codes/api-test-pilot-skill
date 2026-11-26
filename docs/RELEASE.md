# Release checklist

Version **0.1.0**. Nothing below has been done yet: the steps marked **(outward-facing)** publish something and need the
maintainer's explicit go-ahead each time. Run the "Before tagging" block as often as you like; it changes nothing outside this checkout.

## Before tagging (safe, local)
- [ ] `git status` clean on `main`, branch up to date with `origin/main`.
- [ ] `python -m pytest tests` passes. With k6 installed (`K6_BIN` or PATH) the generated k6 scripts run too; record whether k6 was present.
- [ ] `python tools/validate_skill.py` prints `OK` (frontmatter, layout, plugin files, version consistency across
      `SKILL.md` metadata, `plugin.json`, CHANGELOG).
- [ ] `claude plugin validate ./` passes. The icon is included now; see "Icon" below.
- [ ] Install from a clean directory: `claude plugin marketplace add ./` then `claude plugin install api-test-pilot@api-test-pilot-skill`
      with a scratch `HOME`; `claude plugin list` shows version 0.1.0.
- [ ] `python tools/build_skill_zip.py`; unzip into an empty folder, create a fresh virtualenv with `pip install pytest requests`,
      and run the README example (intake, generate, run, report) against `examples/demo_server.py`.
- [ ] Scan for secrets: `git grep -nEi "(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}"` shows only test fixtures; run
      the GitHub secret scan on the branch.
- [ ] CHANGELOG: the heading reads `## [0.1.0] - 2026-11-19 (preview)`; change the date if you tag on another day (and the string in `tests/test_packaging.py`); "Known gaps" still accurate. Tag and release it as a **preview** (GitHub: tick "Set as a pre-release").
- [ ] PLAN.md Phase 6 boxes ticked for what is actually done; DECISIONS.md updated.
- [ ] Optional but recommended: person runs the real Petstore, VAmPI and DVGA evals (`evals/README.md`) and records results in
      `docs/evals.md`. Until then every doc says "mock-based, real target not run" for them; keep it that way.

## Tag and release (outward-facing, maintainer only)
- [ ] Repository is public and its visibility, description and topics are set (suggested topics: `agent-skills`, `claude-code`,
      `api-testing`, `openapi`, `graphql`, `pytest`, `k6`, `owasp`).
- [ ] Tag: `git tag -a v0.1.0 -m "api-test-pilot-skill 0.1.0" && git push origin v0.1.0`. (`claude plugin tag` would create
      `api-test-pilot--v0.1.0` (named after the plugin), which is only needed when other plugins depend on a version range.)
- [ ] GitHub release for `v0.1.0`: notes from CHANGELOG; attach `dist/api-testing-0.1.0.zip` and `dist/SHA256SUMS` (both written by the zip tool), and paste the SHA-256 into the notes.
- [ ] Bump `version` in `SKILL.md` metadata, `.claude-plugin/plugin.json` and CHANGELOG together for every later release.
      `plugin.json` sets `version`, so users only receive an update when it changes.

## Marketplace submissions (outward-facing, maintainer only)
Copy for every listing is in [LISTING.md](LISTING.md). Do not submit before the tag exists and the README install lines work.
- [ ] **Own marketplace:** nothing to submit. Once the repository is public, `/plugin marketplace add pankajkumar-codes/api-test-pilot-skill` works.
- [ ] **Anthropic plugin directory:** see "Marketplace requirements, checked 2026-11-19" below. Submit from the developer portal
      (`claude.ai/directory/manage`, Submit new, Plugin bundle).
- [ ] **Claude app skill upload:** upload `dist/api-testing-0.1.0.zip` in Settings, Capabilities, Skills (personal use; this is not a public listing).
- [ ] **Other Agent Skills marketplaces and directories:** each has its own form or pull-request flow; check its current rules,
      then reuse the LISTING.md copy. Do not claim features the README does not.

## Marketplace requirements, checked 2026-11-19
Read from Anthropic's pages. Rules change; re-read them on the day you submit.

**Anthropic plugin directory** ([Publish to the directory](https://claude.com/docs/directory/publish), [Submit your plugin](https://claude.com/docs/plugins/submit),
[pre-submission checklist](https://claude.com/docs/plugins/pre-submission-checklist), [status page](https://claude.com/docs/directory/submission-status)):
- Where: the developer portal at `claude.ai/directory/manage`, Submit new, Plugin bundle. This replaces the earlier Console form. There is no separate "Claude Marketplace" submission.
- Who: Pro, Max, Ultra, Team or Enterprise plan (free accounts cannot submit); on Team and Enterprise an Owner submits. The first organization to submit a repository folder owns the listing.
- What: a GitHub repository (may stay private while validating, must be public before the listing goes live) with your GitHub account connected on claude.ai.
  Run `claude plugin validate ./` first, then the portal's Validate button, which runs more checks. Every plugin must follow the Anthropic Software Directory Terms and Policy.
- Checks that matter here: `.claude-plugin/plugin.json` with name, description, author, version; a README of at least 40 words outside code blocks; a LICENSE; a name
  that is not taken and cannot be confused with a known brand or presented as official; regular files only (no symbolic links, submodules or LFS pointers); no real credentials in any file.
  Held for a human reviewer (not rejected): files over 256 KiB that are not images or fonts, more than 512 files, any binary other than PNG, JPEG, GIF, WebP and fonts.
  The icon is a square PNG or JPEG, 512 to 2048 px, under 2 MB; `claude plugin validate` only advises when it is missing. The icon may become the listing icon only the first time the plugin is saved or submitted, so set it before the first submission.
- No privacy-policy or support URL is listed among the plugin checks I read (the connector form is different). The README section "Privacy and support" is there in case a form asks.
- Review time: **not fixed**; Anthropic's status page says so and gives no number. Statuses: Draft, Scanning, Needs changes, In review, Approved, Published (installable only then). Contact: the portal's Get help, or `directory@anthropic.com` (replies can be slow). Limit: 10 submissions per organization per 24 hours.
- This repository on 2026-11-24 (171 tracked files with `git ls-files | wc -l`): a valid manifest, README, MIT licence and icon. Two sample report files
  (`examples/sample-report.html`, `examples/spring-boot-demo/report.html`) are over 256 KiB, so expect a reviewer hold on them. Installs copy the whole plugin folder (see "Plugin size" below).
- Policy risk to answer: the policy says software must not "evade or enable users to circumvent" Claude's safety guardrails. This is a security-testing tool that sends attack-shaped requests only to targets the user confirms; keep the safety gate wording plain and conservative in the listing.

**Claude app skill upload** (Settings, Capabilities, Skills): personal use, no review. Not yet installed cold on claude.ai; the zip was unpacked and run from a clean directory instead.

**Other Agent Skills hubs and directories** (for example skills.sh, SkillsMP): not checked. Those hosts are blocked from the build environment (HTTP 403 from the egress proxy), so their current rules and review times are unknown. Check each one's page on the day; most index public GitHub repositories automatically or by pull request.

## Cold install check, 2026-11-19 (main 4c8b12f plus the report and icon changes)
- `claude plugin marketplace add ./` and `claude plugin install api-test-pilot-skill@api-test-pilot-skill` with a scratch `HOME` (the plugin was still named `api-test-pilot-skill` then): installed, `claude plugin list` showed 0.1.0 enabled (Claude Code 2.1.329).
  The install copied the whole repository (7.2 MB, `tests/`, `evals/`, `docs/` included), as described under "Plugin size".
- `python tools/build_skill_zip.py`, unzipped into an empty directory with a fresh virtualenv and `pip install pytest requests`: intake, generation (134 tests), run and report worked
  against `examples/demo_server.py` (240 passed, 9 failed on the seeded flaws, 20 skipped). The demo server needs this repository checkout (it imports `tests/mock_rest.py`); the zip does not contain it.
- What broke: nothing in the skill. Two rough edges: the README example numbers were out of date (fixed), and running the whole generated suite against a target that is down takes minutes
  (every test retries), though the first smoke test fails at once with "target unreachable".
- Trimming the plugin to `skills/api-testing` was tried: a plugin folder that links to it by symbolic link installs correctly with Claude Code, but the directory's checks block symbolic
  links, and Windows checkouts may not create them. A real trim means moving the skill under a plugin subfolder, which touches the paths in the tests, README and zip builder. Not done for 0.1.0.

## Open items for the maintainer
- **Icon.** `.claude-plugin/icon.png` is a plain placeholder (white check mark on teal, drawn by `tools/make_icon.py`) and `plugin.json` points to it. Replace it with a design of your own before the first directory submission, because the listing icon is only taken once. Do not use a third party's logo.
- **Privacy policy and support URLs** if a directory asks for them: see the README section "Privacy and support" and `docs/LISTING.md`. The skill sends nothing anywhere except HTTP requests to
  the target API the user names; it collects no telemetry. The GitHub Issues link serves as the support URL.
- **Copyright holder.** LICENSE names Pankaj Kumar (changed from the GitHub handle on 2026-11-23).
- **Plugin size.** The plugin source is the whole repository (`source: "./"`), so an install also copies `tests/`, `evals/` and
  `docs/`. If that matters, move the plugin into a subfolder and point `source` at it.
