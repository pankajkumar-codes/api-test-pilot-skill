"""Phase 6: SKILL.md / folder validation, plugin manifests, zip builder, version consistency, doc claims."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import build_skill_zip as bz  # noqa: E402
import validate_skill as vs  # noqa: E402

GOOD = '''---
name: demo-skill
description: Does a useful thing with files. Use when the user asks for the useful thing.
license: MIT
metadata:
  version: "1.0.0"
---

# Demo
See `references/guide.md` and [more](references/more.md).
'''


def make_skill(tmp_path, text=GOOD, name="demo-skill", files=("references/guide.md", "references/more.md")):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text("# x\n", encoding="utf-8")
    return d


def check(d):
    r = vs.Result()
    fm = vs.check_skill(d, r)
    return r, fm


# ---------------------------------------------------------------- the real repository
def test_the_repository_validates_clean():
    r = vs.Result()
    vs.check_repo(ROOT, r)
    assert r.errors == [] and r.warnings == [], (r.errors, r.warnings)


def test_cli_exit_codes(tmp_path):
    ok = subprocess.run([sys.executable, str(ROOT / "tools/validate_skill.py")], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip().endswith("OK: 0 error(s), 0 warning(s)")
    bad = make_skill(tmp_path, GOOD.replace("name: demo-skill", "name: Wrong_Name"))
    p = subprocess.run([sys.executable, str(ROOT / "tools/validate_skill.py"), str(bad), "--json"], capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["ok"] is False


def test_real_skill_frontmatter_fields_and_limits():
    r, fm = check(ROOT / "skills" / "api-testing")
    assert fm["name"] == "api-testing" and fm["license"] == "MIT" and fm["metadata"]["version"] == "0.1.0"
    assert len(fm["description"]) <= 1024 and len(fm["compatibility"]) <= 500
    lines = (ROOT / "skills/api-testing/SKILL.md").read_text().count("\n")
    assert lines < 500, "SKILL.md is a router; detail belongs in references/"


def test_every_reference_file_is_reachable_from_skill_md():
    body = (ROOT / "skills/api-testing/SKILL.md").read_text()
    for f in (ROOT / "skills/api-testing/references").glob("*.md"):
        assert f"references/{f.name}" in body, f.name


# ---------------------------------------------------------------- validator rules
def test_good_skill_passes(tmp_path):
    r, _ = check(make_skill(tmp_path))
    assert r.errors == [] and r.warnings == []


@pytest.mark.parametrize("old,new,needle", [
    ("name: demo-skill", "name: Demo-Skill", "lowercase"),
    ("name: demo-skill", "name: -demo-skill", "lowercase"),
    ("name: demo-skill", "name: demo--skill", "lowercase"),
    ("name: demo-skill", "name: other-name", "must equal the folder name"),
    ("name: demo-skill", "name: " + "a" * 65, "limit is 64"),
    ("description: Does a useful thing with files. Use when the user asks for the useful thing.", "description:", "'description' is required"),
    ("description: Does a useful thing with files. Use when the user asks for the useful thing.",
     "description: Use when <this> happens, do the useful thing with files", "angle brackets"),
    ("description: Does a useful thing with files. Use when the user asks for the useful thing.",
     "description: " + "x" * 1025, "limit is 1024"),
    ("license: MIT\n", "license: MIT\ncompatibility: " + "c" * 501 + "\n", "compatibility"),
    ('version: "1.0.0"', "version: 1", "string"),
    ("license: MIT\n", "license: MIT\nlicense: GPL\n", "duplicate key"),
])
def test_invalid_frontmatter_is_an_error(tmp_path, old, new, needle):
    d = make_skill(tmp_path, GOOD.replace(old, new))
    r, _ = check(d)
    assert any(needle in e for e in r.errors), r.errors


def test_missing_or_misnamed_skill_md_and_missing_frontmatter(tmp_path):
    d = tmp_path / "demo-skill"
    d.mkdir()
    (d / "skill.md").write_text(GOOD)  # wrong case
    r, _ = check(d)
    if (d / "SKILL.md").is_file():  # case-insensitive file system
        pytest.skip("case-insensitive file system")
    assert any("SKILL.md is missing" in e for e in r.errors)
    d2 = make_skill(tmp_path / "x", "# no frontmatter\n", name="demo-skill") if (tmp_path / "x").mkdir() is None else None
    r2, _ = check(d2)
    assert any("frontmatter block" in e for e in r2.errors)


def test_dangling_references_and_path_escape_are_errors(tmp_path):
    d = make_skill(tmp_path, GOOD + "\nAlso `scripts/missing.py` and [x](../outside.md).\n")
    r, _ = check(d)
    assert any("scripts/missing.py" in e and "does not exist" in e for e in r.errors)
    assert any("outside the skill folder" in e for e in r.errors)


def test_orphan_reference_file_is_a_warning(tmp_path):
    d = make_skill(tmp_path, files=("references/guide.md", "references/more.md", "references/orphan.md"))
    r, _ = check(d)
    assert r.errors == [] and any("orphan.md" in w for w in r.warnings)


def test_script_that_does_not_compile_is_an_error(tmp_path):
    d = make_skill(tmp_path, files=("references/guide.md", "references/more.md", "scripts/broken.py"))
    (d / "scripts" / "broken.py").write_text("def (:\n")
    r, _ = check(d)
    assert any("does not compile" in e for e in r.errors)


def test_symlink_is_an_error(tmp_path):
    d = make_skill(tmp_path)
    (d / "link.md").symlink_to(d / "SKILL.md")
    r, _ = check(d)
    assert any("symbolic link" in e for e in r.errors)


def test_frontmatter_reader_supports_block_scalars_and_quotes():
    fm = vs.parse_frontmatter('name: a\ndescription: >\n  one\n  two\nmetadata:\n  k: "v: 1"\n  n: \'x\'\n')
    assert fm == {"name": "a", "description": "one two", "metadata": {"k": "v: 1", "n": "x"}}
    with pytest.raises(ValueError):
        vs.parse_frontmatter("name a")


# ---------------------------------------------------------------- plugin and marketplace files
def _repo_copy(tmp_path):
    dst = tmp_path / "repo"
    (dst / ".claude-plugin").mkdir(parents=True)
    for f in ("plugin.json", "marketplace.json"):
        shutil.copy(ROOT / ".claude-plugin" / f, dst / ".claude-plugin" / f)
    for f in ("CHANGELOG.md", "LICENSE", "README.md", "CONTRIBUTING.md"):
        shutil.copy(ROOT / f, dst / f)
    shutil.copytree(ROOT / "skills", dst / "skills", ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def test_plugin_files_are_valid_and_consistent():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert plugin["name"] == "api-test-pilot" and plugin["version"] == "0.1.0" and plugin["license"] == "MIT"
    entry = market["plugins"][0]
    assert entry["name"] == plugin["name"] and entry["source"] == "./" and market["owner"]["name"]
    assert not market["name"].startswith(("claude", "anthropic"))
    assert (ROOT / "skills" / "api-testing" / "SKILL.md").is_file(), "the plugin root holds skills/<name>/SKILL.md"


@pytest.mark.parametrize("mutate,needle", [
    (lambda pj, mj: pj.update(version="0.2.0"), "version mismatch"),
    (lambda pj, mj: pj.update(name="claude-tools"), "reserved"),
    (lambda pj, mj: pj.update(name="Bad Name"), "kebab-case"),
    (lambda pj, mj: pj.update(version="one"), "semantic version"),
    (lambda pj, mj: mj["plugins"][0].update(source="plugins/x"), "must be '.' or start with ./"),
    (lambda pj, mj: mj["plugins"][0].update(source="./nope"), "not a directory"),
    (lambda pj, mj: mj.update(name="claude-plugins-official"), "reserved"),
    (lambda pj, mj: mj.pop("owner"), "owner.name"),
    (lambda pj, mj: mj.update(plugins=[]), "non-empty"),
])
def test_broken_plugin_files_are_caught(tmp_path, mutate, needle):
    dst = _repo_copy(tmp_path)
    pj = json.loads((dst / ".claude-plugin/plugin.json").read_text())
    mj = json.loads((dst / ".claude-plugin/marketplace.json").read_text())
    mutate(pj, mj)
    (dst / ".claude-plugin/plugin.json").write_text(json.dumps(pj))
    (dst / ".claude-plugin/marketplace.json").write_text(json.dumps(mj))
    r = vs.Result()
    vs.check_repo(dst, r)
    assert any(needle in e for e in r.errors), r.errors


def test_missing_release_files_are_caught(tmp_path):
    dst = _repo_copy(tmp_path)
    (dst / "LICENSE").unlink()
    (dst / "CHANGELOG.md").write_text("# Changelog\nnothing here\n")
    r = vs.Result()
    vs.check_repo(dst, r)
    assert any("LICENSE: missing" in e for e in r.errors) and any("CHANGELOG.md: no" in e for e in r.errors)


# ---------------------------------------------------------------- zip builder
def test_zip_has_one_top_level_folder_no_pycache_and_is_reproducible(tmp_path):
    z1 = bz.build(ROOT / "skills" / "api-testing", tmp_path / "a")
    z2 = bz.build(ROOT / "skills" / "api-testing", tmp_path / "b")
    assert z1.name == "api-testing-0.1.0.zip"
    assert z1.read_bytes() == z2.read_bytes(), "same files give the same zip"
    with zipfile.ZipFile(z1) as z:
        names = z.namelist()
        assert "api-testing/SKILL.md" in names
        assert {n.split("/")[0] for n in names} == {"api-testing"}
        assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or ".." in n or n.startswith("/")]
        assert z.testzip() is None
        out = tmp_path / "unz"
        z.extractall(out)
    r = vs.Result()
    vs.check_skill(out / "api-testing", r)
    assert r.errors == []


def test_zip_builder_refuses_an_invalid_skill(tmp_path):
    d = make_skill(tmp_path, GOOD.replace("name: demo-skill", "name: nope"))
    with pytest.raises(SystemExit, match="does not validate"):
        bz.build(d, tmp_path / "out")


# ---------------------------------------------------------------- documentation promises
def test_docs_name_every_release_and_safety_item():
    readme = (ROOT / "README.md").read_text()
    for needle in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED=yes", "production is always refused", "destructive", "xdist",
                   "tools/validate_skill.py", "tools/build_skill_zip.py", "/plugin install api-test-pilot@api-test-pilot", "LICENSE"):
        assert needle in readme, needle
    release = (ROOT / "docs/RELEASE.md").read_text()
    assert "outward-facing" in release and "maintainer" in release and "v0.1.0" in release
    assert (ROOT / "docs/LISTING.md").is_file() and (ROOT / "examples/sample-report.md").is_file()


def test_release_docs_do_not_claim_a_release_happened():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "[0.1.0] - unreleased" in text
    assert "Nothing has been tagged" in text


def test_license_is_mit_and_names_a_holder():
    lic = (ROOT / "LICENSE").read_text()
    assert lic.startswith("MIT License") and "Copyright (c) 2026 pankajkumar-codes" in lic
