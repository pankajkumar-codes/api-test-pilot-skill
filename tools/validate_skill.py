#!/usr/bin/env python3
"""Validate a skill folder (and, optionally, the plugin/marketplace manifests) against the Agent Skills format.

    python tools/validate_skill.py                      # skills/api-testing + the repo's plugin files + version consistency
    python tools/validate_skill.py path/to/skill-dir    # one skill folder only
    python tools/validate_skill.py --json               # machine-readable result

Exit code 0 = no errors (warnings are printed), 1 = errors, 2 = usage problem. Standard library only, offline.

Rules come from the Agent Skills specification (https://agentskills.io/specification): required `name` and `description`,
`name` 1-64 chars of a-z 0-9 and single hyphens and equal to the folder name, `description` 1-1024 chars, optional `license`,
`compatibility` (max 500), `metadata` (string to string map) and `allowed-tools`. Two stricter checks are ours: the description
may not contain angle brackets (the Claude app's skill upload rejects them) and every file SKILL.md points to must exist.
"""
from __future__ import annotations

import argparse
import json
import py_compile
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")
MAX_SKILL_MD_LINES = 500
RESERVED_PLUGIN_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")


class Result:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


# ------------------------------------------------------------------ frontmatter
def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(frontmatter text, body). None when the file does not start with a `---` block."""
    lines = text.lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, text


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if raw[0] == '"' else inner.replace("''", "'")
    return re.sub(r"\s+#.*$", "", raw)  # trailing comment on a plain scalar


def parse_frontmatter(fm: str) -> dict:
    """The small YAML subset skills use: `key: value`, quoted values, `|`/`>` blocks, one-level maps, plain continuation lines.
    Raises ValueError on anything it cannot read, so a file this reader accepts is one every YAML reader accepts."""
    out: dict = {}
    lines = fm.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            raise ValueError(f"unexpected indentation: {line!r}")
        m = re.fullmatch(r"([A-Za-z0-9_-]+):(?:\s+(.*))?", line.rstrip())
        if not m:
            raise ValueError(f"cannot read line: {line!r}")
        key, rest = m.group(1), (m.group(2) or "")
        if key in out:
            raise ValueError(f"duplicate key: {key}")
        i += 1
        block: list[str] = []
        while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
            block.append(lines[i])
            i += 1
        while block and not block[-1].strip():
            block.pop()
        if rest[:1] in ("|", ">"):
            body = [b.strip() for b in block]
            out[key] = ("\n" if rest[0] == "|" else " ").join(body).strip()
        elif rest == "" and block:
            sub: dict = {}
            for b in block:
                if not b.strip():
                    continue
                mm = re.fullmatch(r"\s+([A-Za-z0-9_.-]+):\s*(.*)", b.rstrip())
                if not mm:
                    raise ValueError(f"cannot read nested line under {key}: {b!r}")
                raw = mm.group(2).strip()
                # an unquoted number, boolean or null is not a string in YAML: keep that visible so the type check can fail it
                if re.fullmatch(r"[-+]?\d+(?:\.\d+)?|true|false|null|~|True|False|Null", raw):
                    sub[mm.group(1)] = float(raw) if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw) else (raw in ("true", "True"))
                else:
                    sub[mm.group(1)] = _scalar(raw)
            out[key] = sub
        elif block and rest:  # plain multi-line scalar
            out[key] = _scalar(rest) + " " + " ".join(b.strip() for b in block)
        else:
            out[key] = _scalar(rest)
    return out


# ------------------------------------------------------------------ skill folder
def check_skill(skill_dir: Path, res: Result) -> dict:
    """Validate one skill folder; returns the parsed frontmatter ({} when unreadable)."""
    label = skill_dir.name
    md = skill_dir / "SKILL.md"
    if not skill_dir.is_dir():
        res.error(f"{skill_dir}: not a directory")
        return {}
    if not md.is_file():
        res.error(f"{label}: SKILL.md is missing (the file name is case-sensitive)")
        return {}
    text = md.read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(text)
    if fm_text is None:
        res.error(f"{label}/SKILL.md: must start with a YAML frontmatter block between two `---` lines")
        return {}
    try:
        fm = parse_frontmatter(fm_text)
    except ValueError as e:
        res.error(f"{label}/SKILL.md: frontmatter is not readable: {e}")
        return {}

    for k in fm:
        if k not in SKILL_KEYS:
            res.warn(f"{label}/SKILL.md: unknown frontmatter key '{k}' (spec keys: {', '.join(sorted(SKILL_KEYS))})")
    name = fm.get("name")
    if not isinstance(name, str) or not name:
        res.error(f"{label}/SKILL.md: 'name' is required")
    else:
        if len(name) > 64:
            res.error(f"{label}/SKILL.md: name is {len(name)} characters, the limit is 64")
        if not NAME_RE.fullmatch(name):
            res.error(f"{label}/SKILL.md: name '{name}' must be lowercase letters, digits and single hyphens, not starting or ending with a hyphen")
        if name != skill_dir.name:
            res.error(f"{label}/SKILL.md: name '{name}' must equal the folder name '{skill_dir.name}'")
        if any(w in name for w in ("anthropic", "claude")):
            res.warn(f"{label}/SKILL.md: names containing 'claude' or 'anthropic' are reserved by some marketplaces")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        res.error(f"{label}/SKILL.md: 'description' is required and must not be empty")
    else:
        if len(desc) > 1024:
            res.error(f"{label}/SKILL.md: description is {len(desc)} characters, the limit is 1024")
        if "<" in desc or ">" in desc:
            res.error(f"{label}/SKILL.md: description must not contain angle brackets (the Claude app rejects them)")
        if len(desc) < 40:
            res.warn(f"{label}/SKILL.md: description is very short; say what the skill does and when to use it")
        if not re.search(r"\b[Uu]se when\b|\b[Uu]se this\b|\bwhen the user\b|\bWhen\b", desc):
            res.warn(f"{label}/SKILL.md: description should also say WHEN to use the skill (for example 'Use when ...')")
    if "compatibility" in fm:
        c = fm["compatibility"]
        if not isinstance(c, str) or not (1 <= len(c) <= 500):
            res.error(f"{label}/SKILL.md: compatibility must be 1-500 characters")
    if "license" in fm and not (isinstance(fm["license"], str) and fm["license"].strip()):
        res.error(f"{label}/SKILL.md: license must be a non-empty string")
    if "metadata" in fm:
        md_ = fm["metadata"]
        if not isinstance(md_, dict) or not all(isinstance(v, str) for v in md_.values()):
            res.error(f"{label}/SKILL.md: metadata must be a map of string keys to string values (quote numbers: version: \"0.1.0\")")
    if "allowed-tools" in fm and not isinstance(fm["allowed-tools"], str):
        res.error(f"{label}/SKILL.md: allowed-tools must be one space-separated string")

    n_lines = len(text.splitlines())
    if n_lines > MAX_SKILL_MD_LINES:
        res.warn(f"{label}/SKILL.md: {n_lines} lines; keep it under {MAX_SKILL_MD_LINES} and move detail into references/")
    if not body.strip():
        res.error(f"{label}/SKILL.md: the body after the frontmatter is empty")

    # every relative file SKILL.md points to must exist (markdown links and `backticked/paths`)
    refs: set[str] = set()
    for m in re.finditer(r"\]\(([^)#\s]+)\)", body):
        refs.add(m.group(1))
    for m in re.finditer(r"`((?:references|scripts|templates|assets)/[A-Za-z0-9_./-]+)`", body):
        refs.add(m.group(1))
    for m in re.finditer(r"(?<![\w/.-])((?:scripts|references|templates|assets)/[A-Za-z0-9_-]+\.[A-Za-z0-9]+)", body):
        refs.add(m.group(1))
    for ref in sorted(refs):
        if re.match(r"[a-z]+://", ref) or ref.startswith(("mailto:", "/")):
            continue
        target = (skill_dir / ref).resolve()
        if skill_dir.resolve() not in target.parents and target != skill_dir.resolve():
            res.error(f"{label}/SKILL.md: '{ref}' points outside the skill folder")
        elif not target.exists():
            res.error(f"{label}/SKILL.md: '{ref}' does not exist")
    refd = skill_dir / "references"
    if refd.is_dir():
        for f in sorted(refd.glob("*.md")):
            if f"references/{f.name}" not in body:
                res.warn(f"{label}: references/{f.name} is not mentioned in SKILL.md, so the agent will never know to load it")
    # layout hygiene
    for p in skill_dir.rglob("*"):
        rel = p.relative_to(skill_dir)
        if "__pycache__" in rel.parts or p.suffix == ".pyc":
            continue  # build artifacts; the zip builder leaves them out
        if p.is_symlink():
            res.error(f"{label}: {rel} is a symbolic link (not portable in a zip)")
        elif p.name.startswith(".") and p.name != ".gitkeep":
            res.warn(f"{label}: hidden file {rel}")
    # scripts must at least compile (no execution, no imports)
    with tempfile.TemporaryDirectory() as tmp:
        for py in sorted((skill_dir).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            try:
                py_compile.compile(str(py), cfile=str(Path(tmp) / "x.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                res.error(f"{label}: {py.relative_to(skill_dir)} does not compile: {str(e).splitlines()[-1]}")
    return fm


# ------------------------------------------------------------------ plugin / marketplace manifests
def _load_json(path: Path, res: Result) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        res.error(f"{path.relative_to(REPO)}: missing")
        return None
    except (OSError, ValueError) as e:
        res.error(f"{path.relative_to(REPO)}: not valid JSON: {e}")
        return None
    if not isinstance(data, dict):
        res.error(f"{path.relative_to(REPO)}: top level must be an object")
        return None
    return data


def check_plugin_files(root: Path, res: Result) -> dict[str, str]:
    """Validate .claude-plugin/plugin.json and marketplace.json. Returns {where: version} for the consistency check."""
    versions: dict[str, str] = {}
    pj = _load_json(root / ".claude-plugin" / "plugin.json", res)
    if pj is not None:
        n = pj.get("name")
        if not isinstance(n, str) or not NAME_RE.fullmatch(n):
            res.error(".claude-plugin/plugin.json: 'name' is required and must be kebab-case")
        elif n.startswith(RESERVED_PLUGIN_PREFIXES) or n in ("claude", "anthropic", "claude-code"):
            res.error(f".claude-plugin/plugin.json: name '{n}' is reserved for Anthropic's own plugins")
        v = pj.get("version")
        if isinstance(v, str) and SEMVER_RE.fullmatch(v):
            versions["plugin.json"] = v
        else:
            res.error(".claude-plugin/plugin.json: 'version' must be a semantic version such as 0.1.0")
        for k in ("description", "author", "license", "repository"):
            if k not in pj:
                res.warn(f".claude-plugin/plugin.json: no '{k}'")
        if isinstance(pj.get("author"), dict) and not pj["author"].get("name"):
            res.error(".claude-plugin/plugin.json: author.name is required when author is set")
        for k in ("skills", "commands", "agents", "hooks", "mcpServers"):
            val = pj.get(k)
            for p in ([val] if isinstance(val, str) else val if isinstance(val, list) else []):
                if isinstance(p, str):
                    if not p.startswith("./") and p not in (".",):
                        res.error(f".claude-plugin/plugin.json: path '{p}' in '{k}' must start with ./")
                    elif not (root / p).exists():
                        res.error(f".claude-plugin/plugin.json: path '{p}' in '{k}' does not exist")
        if not (root / "skills").is_dir() and "skills" not in pj:
            res.error("plugin has no skills/ directory and no 'skills' key")
    mj = _load_json(root / ".claude-plugin" / "marketplace.json", res)
    if mj is not None:
        if not isinstance(mj.get("name"), str) or not mj["name"] or re.search(r"[\s/\\]", mj["name"]):
            res.error(".claude-plugin/marketplace.json: 'name' is required (no spaces or slashes)")
        elif mj["name"].startswith(("claude", "anthropic")) or mj["name"] in ("agent-skills", "life-sciences", "knowledge-work-plugins"):
            res.error(f".claude-plugin/marketplace.json: marketplace name '{mj['name']}' is reserved")
        if not (isinstance(mj.get("owner"), dict) and mj["owner"].get("name")):
            res.error(".claude-plugin/marketplace.json: owner.name is required")
        plugins = mj.get("plugins")
        if not isinstance(plugins, list) or not plugins:
            res.error(".claude-plugin/marketplace.json: 'plugins' must be a non-empty list")
        else:
            for i, p in enumerate(plugins):
                if not isinstance(p, dict) or not p.get("name") or "source" not in p:
                    res.error(f".claude-plugin/marketplace.json: plugins[{i}] needs 'name' and 'source'")
                    continue
                src = p["source"]
                if isinstance(src, str):
                    if src != "." and not src.startswith("./"):
                        res.error(f".claude-plugin/marketplace.json: plugins[{i}].source '{src}' must be '.' or start with ./")
                    elif not (root / src).is_dir():
                        res.error(f".claude-plugin/marketplace.json: plugins[{i}].source '{src}' is not a directory")
                if pj and pj.get("name") and p["name"] != pj["name"]:
                    res.warn(f".claude-plugin/marketplace.json: entry name '{p['name']}' differs from plugin.json name '{pj['name']}'")
                if isinstance(p.get("version"), str):
                    versions["marketplace.json"] = p["version"]
    return versions


def changelog_version(root: Path, res: Result) -> str | None:
    cl = root / "CHANGELOG.md"
    if not cl.is_file():
        res.error("CHANGELOG.md: missing")
        return None
    versions = re.findall(r"(?m)^## \[(\d+\.\d+\.\d+[^\]]*)\]", cl.read_text(encoding="utf-8"))
    if not versions:
        res.error("CHANGELOG.md: no '## [x.y.z]' heading found (Keep a Changelog format)")
        return None
    return versions[0]


def check_repo(root: Path, res: Result) -> None:
    skills_root = root / "skills"
    skill_dirs = sorted(p for p in skills_root.iterdir() if p.is_dir()) if skills_root.is_dir() else []
    if not skill_dirs:
        res.error("skills/: no skill folder found")
    versions: dict[str, str] = {}
    for sd in skill_dirs:
        fm = check_skill(sd, res)
        v = (fm.get("metadata") or {}).get("version") if isinstance(fm.get("metadata"), dict) else None
        if v:
            versions[f"{sd.name}/SKILL.md metadata.version"] = v
    versions.update(check_plugin_files(root, res))
    cv = changelog_version(root, res)
    if cv and cv.lower() != "unreleased":
        versions["CHANGELOG.md (latest)"] = cv
    if len(set(versions.values())) > 1:
        res.error("version mismatch: " + ", ".join(f"{k}={v}" for k, v in sorted(versions.items())))
    for f in ("LICENSE", "README.md", "CONTRIBUTING.md"):
        if not (root / f).is_file():
            res.error(f"{f}: missing")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("skill_dir", nargs="?", help="validate only this skill folder (default: the whole repo)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--root", default=str(REPO), help="repository root for the default whole-repo check")
    a = ap.parse_args(argv)
    res = Result()
    if a.skill_dir:
        check_skill(Path(a.skill_dir).resolve(), res)
    else:
        check_repo(Path(a.root).resolve(), res)
    if a.json:
        print(json.dumps({"ok": not res.errors, "errors": res.errors, "warnings": res.warnings}, indent=2))
    else:
        for w in res.warnings:
            print("WARN  " + w)
        for e in res.errors:
            print("ERROR " + e)
        print(f"{'FAIL' if res.errors else 'OK'}: {len(res.errors)} error(s), {len(res.warnings)} warning(s)")
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
