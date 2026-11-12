#!/usr/bin/env python3
"""Build dist/api-testing-<version>.zip for the Claude app's skill upload: one top-level `api-testing/` folder holding the skill.

    python tools/build_skill_zip.py            # validates first, then writes the zip and prints its SHA-256

Leaves out __pycache__ and *.pyc, writes entries in sorted order with a fixed timestamp (so the same files give the same zip),
and refuses to build when the validator reports errors. Stdlib only.
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_skill as vs  # noqa: E402

REPO = vs.REPO


def build(skill_dir: Path, out_dir: Path) -> Path:
    res = vs.Result()
    fm = vs.check_skill(skill_dir, res)
    if res.errors:
        raise SystemExit("refusing to build, the skill does not validate:\n  " + "\n  ".join(res.errors))
    version = (fm.get("metadata") or {}).get("version", "dev")
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"{skill_dir.name}-{version}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(skill_dir.rglob("*")):
            rel = p.relative_to(skill_dir)
            if p.is_dir() or "__pycache__" in rel.parts or p.suffix == ".pyc":
                continue
            info = zipfile.ZipInfo(f"{skill_dir.name}/{rel.as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if p.stat().st_mode & 0o111 else 0o644) << 16
            z.writestr(info, p.read_bytes())
    return zpath


def main() -> int:
    z = build(REPO / "skills" / "api-testing", REPO / "dist")
    print(f"{z}  ({z.stat().st_size} bytes)\nsha256 {hashlib.sha256(z.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
