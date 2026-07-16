#!/usr/bin/env python3
"""Install, compare, or diagnose the public Claude Code skill bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Iterable


SKILL_NAME = "research-anything"
RUNTIME_DIRS = ("references", "scripts")


def _runtime_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    skill = root / "SKILL.md"
    if skill.is_file():
        yield skill
    for directory in RUNTIME_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.startswith("test_") or "__pycache__" in path.parts:
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            yield path


def tree_hash(root: str | pathlib.Path) -> str:
    root = pathlib.Path(root).resolve()
    digest = hashlib.sha256()
    for path in _runtime_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def default_target() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CLAUDE_SKILLS_DIR", "~/.claude/skills")).expanduser() / SKILL_NAME


def compare(source: pathlib.Path, target: pathlib.Path) -> dict:
    source_hash = tree_hash(source)
    installed_hash = tree_hash(target) if (target / "SKILL.md").is_file() else None
    return {
        "source": str(source), "target": str(target), "source_hash": source_hash,
        "installed_hash": installed_hash, "installed": installed_hash is not None,
        "in_sync": source_hash == installed_hash,
    }


def install(source: pathlib.Path, target: pathlib.Path, force: bool = False) -> dict:
    source = source.resolve()
    target = target.expanduser().resolve()
    if not (source / "SKILL.md").is_file():
        raise RuntimeError(f"source lacks SKILL.md: {source}")
    before = compare(source, target)
    if before["in_sync"]:
        return {**before, "changed": False, "backup": None}
    if target.exists() and not force:
        raise RuntimeError(f"target differs: {target}; inspect with check, then rerun install --force")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}.", dir=target.parent))
    backup = None
    try:
        shutil.copy2(source / "SKILL.md", staging / "SKILL.md")
        for directory in RUNTIME_DIRS:
            (staging / directory).mkdir(parents=True, exist_ok=True)
        for path in _runtime_files(source):
            relative = path.relative_to(source)
            if relative == pathlib.Path("SKILL.md"):
                continue
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        manifest = {"skill": SKILL_NAME, "source": str(source), "tree_hash": tree_hash(source),
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        (staging / "installed-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if target.exists():
            backup = target.with_name(f"{target.name}.backup-{time.strftime('%Y%m%d%H%M%S')}")
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except BaseException:
            if backup and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    result = compare(source, target)
    return {**result, "changed": True, "backup": str(backup) if backup else None}


def _command_version(command: str) -> dict:
    executable = shutil.which(command)
    if not executable:
        return {"available": False, "path": None, "version": None}
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
        version = (result.stdout or result.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError) as error:
        version = f"error: {error}"
    return {"available": True, "path": executable, "version": version}


def doctor(source: pathlib.Path, target: pathlib.Path) -> dict:
    tools = pathlib.Path(os.environ.get("RESEARCH_TOOLS_DIR", "~/tools")).expanduser()
    usage_mode = os.environ.get("RESEARCH_USAGE_MODE", "commercial").strip().lower()
    media_root = tools / "MediaCrawler"
    media_installed = media_root.is_dir()
    media_available = media_installed and usage_mode == "personal-noncommercial"
    connectors = {
        "mediacrawler": {
            "path": str(media_root),
            "installed": media_installed,
            "available": media_available,
            "commercial_default": False,
            "reason": (
                None if media_available else
                "MediaCrawler is restricted to non-commercial learning/research; "
                "set RESEARCH_USAGE_MODE=personal-noncommercial only when that license applies"
            ),
        },
        "yt_dlp": _command_version("yt-dlp"),
        "claude": _command_version("claude"),
    }
    sync = compare(source, target)
    required_ok = sys.version_info >= (3, 11) and connectors["claude"]["available"]
    return {
        "ok": required_ok,
        "python": ".".join(map(str, sys.version_info[:3])),
        "python_3_11_or_newer": sys.version_info >= (3, 11),
        "research_tools_dir": str(tools),
        "usage_mode": usage_mode,
        "connectors": connectors,
        "installation": sync,
        "notes": [
            "Optional connectors may be absent; each run must record the resulting capability gap.",
            "Do not enable MediaCrawler for commercial work without separate permission.",
        ],
    }


def main() -> None:
    source_default = pathlib.Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, default=source_default)
    parser.add_argument("--target", type=pathlib.Path, default=default_target())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--force", action="store_true")
    sub.add_parser("doctor")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = compare(args.source.resolve(), args.target)
        elif args.command == "install":
            result = install(args.source, args.target, args.force)
        else:
            result = doctor(args.source.resolve(), args.target)
    except RuntimeError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
