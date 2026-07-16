#!/usr/bin/env python3
"""Read-only auditor for legacy v2 research runs.

This tool never rewrites a legacy run.  It surfaces protocol violations that the old
shape validator could not detect, so a v2 report cannot be mistaken for a v3 decision.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlsplit


ID_PATTERN = re.compile(r"^(?:dy|xhs|zh|bili|yt|gh|tw|web)-\d+$|^vd-\d+$")
SOURCE_KEYS = {"sources", "finding_ids"}


def _json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records, errors = [], []
    if not path.is_file():
        return records, errors
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(f"{path.name}:{line_no}: malformed JSON: {error.msg}")
                continue
            if not isinstance(value, dict):
                errors.append(f"{path.name}:{line_no}: record is not an object")
                continue
            records.append(value)
    return records, errors


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _media_key(source: object) -> str | None:
    if not isinstance(source, str) or not source:
        return None
    if os.path.isfile(source):
        digest = hashlib.sha256()
        with open(source, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    parsed = urlsplit(source)
    query = parse_qs(parsed.query)
    for parameter in ("video_id", "file_id", "vid", "item_id"):
        values = query.get(parameter)
        if values and values[0]:
            return f"{parsed.netloc.lower()}:{parameter}:{values[0]}"
    path = unquote(parsed.path).rstrip("/")
    basename = os.path.basename(path)
    if basename:
        # CDN hosts rotate while the long asset basename remains stable.  Short generic
        # endpoints such as Douyin's `/play/` must retain their query identity instead.
        if len(basename) >= 16 and re.search(r"[0-9a-f]{12}", basename, re.IGNORECASE):
            return f"asset:{basename.lower()}"
        return source.split("?", 1)[0]
    return source.split("?", 1)[0]


def _projected_note_chars(findings: list[dict[str, Any]]) -> int:
    fields = ("id", "channel", "title", "author", "published_at", "metrics", "source_url", "unknown_terms", "headline", "note")
    total = 0
    for finding in findings:
        projected = {key: finding.get(key) for key in fields if finding.get(key) not in (None, "", [])}
        total += len(json.dumps(projected, ensure_ascii=False)) + 1
    return total


def _candidate_strings(value: Any, path: str = "$") -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if "candidate" in key.lower() and isinstance(child, list):
                for item in child:
                    if isinstance(item, str):
                        yield item
            yield from _candidate_strings(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _candidate_strings(child, f"{path}[{index}]")


def _candidate_aliases(value: str) -> set[str]:
    aliases = set()
    for part in re.split(r"[/、,，]", value):
        name = re.split(r"[（(]", part, maxsplit=1)[0].strip()
        if len(name) >= 2:
            aliases.add(name)
            compact = re.sub(r"(?:国际大)?酒店$", "", name)
            if len(compact) >= 2:
                aliases.add(compact[-2:])
    return aliases


def _terminal_audit(root: Path, code: str, message: str, evidence: Any = None) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "severity": "blocker", "message": message}
    if evidence is not None:
        issue["evidence"] = evidence
    return {
        "schema_version": 1,
        "audit_type": "research-anything-v2-read-only",
        "out_dir": str(root),
        "findings": 0,
        "projected_note_chars": 0,
        "issues": [issue],
        "counts": {"blocker": 1, "high": 0, "medium": 0, "low": 0},
        "production_usable": False,
    }


def _resolve_run_root(requested: Path) -> tuple[Path | None, dict[str, Any] | None]:
    if not requested.is_dir():
        return None, _terminal_audit(
            requested, "missing-run-directory", "The requested v2 run directory does not exist."
        )
    if any((requested / "raw").glob("findings.*.jsonl")):
        return requested, None
    candidates = sorted({path.parent.parent for path in requested.glob("**/raw/findings.*.jsonl")})
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, _terminal_audit(
            requested,
            "ambiguous-run-directory",
            "The requested directory contains multiple legacy runs; audit one run directory at a time.",
            [str(path) for path in candidates],
        )
    return None, _terminal_audit(
        requested,
        "not-a-v2-run",
        "No raw/findings.<channel>.jsonl files were found in this directory or its descendants.",
    )


def audit(out_dir: str) -> dict[str, Any]:
    requested = Path(out_dir).expanduser().resolve()
    root, terminal = _resolve_run_root(requested)
    if terminal is not None:
        return terminal
    assert root is not None
    findings, errors, metas = [], [], []
    for name in sorted(glob.glob(str(root / "raw" / "findings.*.jsonl"))):
        records, parse_errors = _jsonl(Path(name))
        errors.extend(parse_errors)
        for record in records:
            if record.get("type") == "finding":
                findings.append(record)
            elif record.get("type") == "meta":
                metas.append(record)
    qa = (root / "qa.md").read_text(encoding="utf-8", errors="replace") if (root / "qa.md").is_file() else ""
    report = (root / "report.html").read_text(encoding="utf-8", errors="replace") if (root / "report.html").is_file() else ""
    runbook = _json(root / "runbook.json", {})
    coverage = _json(root / "coverage.json", {})
    verdicts, verdict_errors = _jsonl(root / "verify" / "verdicts.jsonl")
    errors.extend(verdict_errors)
    ledger, ledger_errors = _jsonl(root / "artifacts" / "asr_ledger.jsonl")
    errors.extend(ledger_errors)

    issues: list[dict[str, Any]] = []

    def add(code: str, severity: str, message: str, evidence: Any = None) -> None:
        item = {"code": code, "severity": severity, "message": message}
        if evidence is not None:
            item["evidence"] = evidence
        issues.append(item)

    for error in errors:
        add("malformed-json", "blocker", error)

    projected_chars = _projected_note_chars(findings)
    if projected_chars > 50_000 and not (
        "熔断" in qa and any(marker in qa for marker in ("一次性", "完整处理", "拆分", "缩小范围"))
    ):
        add("missing-context-authorization", "high", "Projected notes exceed the legacy 50k gate but qa.md does not preserve a matching user decision.", projected_chars)

    duplicate_media: dict[str, list[dict[str, Any]]] = {}
    for entry in ledger:
        if entry.get("status") != "SUCCEEDED" and not entry.get("billed_seconds"):
            continue
        key = entry.get("idempotency_key") or _media_key(entry.get("source"))
        if key:
            duplicate_media.setdefault(str(key), []).append(entry)
    duplicates = {key: rows for key, rows in duplicate_media.items() if len(rows) > 1}
    if duplicates:
        add("duplicate-asr-charge", "blocker", "The ASR ledger contains repeated charged media identities.", {key: len(rows) for key, rows in duplicates.items()})

    for finding in findings:
        for path, value in _walk(finding):
            key = path.rsplit(".", 1)[-1].lower()
            if key in {"truncated", "readme_truncated", "content_truncated"} and value is True:
                add("truncated-source", "high", "A finding explicitly records truncated source content.", {"finding_id": finding.get("id"), "path": path})

    known_ids = {finding.get("id") for finding in findings if isinstance(finding.get("id"), str)}
    known_ids.update(verdict.get("id") for verdict in verdicts if isinstance(verdict.get("id"), str))
    if isinstance(runbook, dict):
        for path, value in _walk(runbook):
            if path.rsplit(".", 1)[-1] not in SOURCE_KEYS:
                continue
            if not isinstance(value, list):
                add("invalid-source-contract", "high", f"{path} is not an array of evidence IDs.")
                continue
            for source in value:
                if not isinstance(source, str) or not ID_PATTERN.fullmatch(source) or source not in known_ids:
                    add("invalid-source-reference", "high", f"{path} contains an unknown or non-contract source.", source)

        narrative = report + "\n" + "\n".join(
            str(value) for path, value in _walk(runbook)
            if isinstance(value, str) and "candidate" not in path.lower()
        )
        for candidate in _candidate_strings(runbook):
            for alias in sorted(_candidate_aliases(candidate), key=len, reverse=True):
                if re.search(re.escape(alias) + r"[^。；;\n]{0,60}(?:剔除|排除|不选|已改牌)", narrative):
                    add("retained-excluded-candidate", "blocker",
                        "A structured runbook candidate is explicitly excluded by the decision narrative.",
                        {"candidate": candidate, "matched_alias": alias})
                    break

    report_path = root / "report.html"
    input_paths = [Path(path) for path in glob.glob(str(root / "raw" / "*.jsonl"))]
    input_paths += [root / "coverage.json", root / "artifacts" / "asr_ledger.jsonl", root / "qa.md"]
    newer = [str(path.relative_to(root)) for path in input_paths if path.is_file() and report_path.is_file() and path.stat().st_mtime > report_path.stat().st_mtime]
    if newer:
        add("stale-report", "blocker", "Inputs changed after report.html was generated.", newer)

    if isinstance(coverage, dict) and report:
        video = (coverage.get("overall") or {}).get("video") or coverage.get("video") or (coverage.get("totals") or {}).get("video") or {}
        if isinstance(video, dict):
            report_text = re.sub(r"<[^>]+>", " ", report)
            summaries = re.findall(
                r"(?i)(?:视频|video)[^;；\n]{0,120}?subtitle\s*[:=：]?\s*(\d+)"
                r"[^;；\n]{0,80}?asr\s*[:=：]?\s*(\d+)"
                r"[^;；\n]{0,80}?failed\s*[:=：]?\s*(\d+)",
                report_text,
            )
            canonical = (video.get("subtitle", 0), video.get("asr", 0), video.get("failed", 0))
            for summary in summaries:
                reported = tuple(map(int, summary))
                if reported != canonical:
                    add("coverage-report-drift", "blocker", "report.html video totals differ from coverage.json.", {"coverage": canonical, "report": reported})

    if not qa:
        add("missing-verbatim-log", "high", "qa.md is absent; user decisions cannot be audited.")
    elif "用户原话" not in qa and "A1" not in qa:
        add("weak-verbatim-log", "medium", "qa.md does not visibly label preserved user utterances.")

    artifact_paths = {path.resolve() for path in (root / "artifacts").glob("**/*") if path.is_file()}
    referenced = set()
    for finding in findings:
        for path, value in _walk(finding.get("capture") or {}):
            if path.endswith("artifact") and isinstance(value, str):
                referenced.add((root / value).resolve())
    orphans = sorted(str(path.relative_to(root)) for path in artifact_paths - referenced if path.name != "asr_ledger.jsonl")
    if orphans:
        add("orphan-artifacts", "medium", "Artifacts exist without a finding capture reference.", orphans[:100])

    severity_order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda item: (severity_order[item["severity"]], item["code"]))
    counts = {severity: sum(issue["severity"] == severity for issue in issues) for severity in severity_order}
    result = {
        "schema_version": 1,
        "audit_type": "research-anything-v2-read-only",
        "out_dir": str(root),
        "findings": len(findings),
        "projected_note_chars": projected_chars,
        "issues": issues,
        "counts": counts,
        "production_usable": counts["blocker"] == 0 and counts["high"] == 0,
    }
    if root != requested:
        result["requested_out_dir"] = str(requested)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--out")
    parser.add_argument("--strict", action="store_true", help="exit non-zero for blocker/high issues")
    args = parser.parse_args()
    result = audit(args.out_dir)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    print(payload, end="")
    if args.strict and not result["production_usable"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
