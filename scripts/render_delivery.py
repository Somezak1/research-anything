#!/usr/bin/env python3
"""Render a v3 decision into deterministic, escaped HTML and a typed runbook.

The renderer deliberately accepts structured JSON only.  Agents must not hand-write the
final HTML or runbook because those copies otherwise drift after retries.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import os
import tempfile
from pathlib import Path
from typing import Any


READINESS = {"production-ready", "pilot-only", "blocked"}
TASK_TYPES = {"implementation", "itinerary", "forecast", "research-only"}


def _read_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    records = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no} must be a JSON object")
            records.append(value)
    return records


def _atomic_write(path: str | Path, data: str, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_id(record: dict[str, Any]) -> str | None:
    for key in ("id", "finding_id", "claim_id", "cluster_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _normalized_recommendation(decision: dict[str, Any]) -> dict[str, Any] | None:
    value = decision.get("recommendation")
    if not isinstance(value, dict):
        return None
    result = dict(value)
    result["name"] = value.get("name") or value.get("candidate_name") or value.get("candidate_id")
    rationale = value.get("rationale")
    if not isinstance(rationale, list):
        rationale = []
        for item in value.get("why") or []:
            if isinstance(item, dict) and isinstance(item.get("claim_id"), str):
                rationale.append({
                    "text": item.get("text") or f"Supported by {item['claim_id']}",
                    "sources": [item["claim_id"]],
                })
    result["rationale"] = rationale
    return result


def validate_decision(decision: dict[str, Any]) -> None:
    if decision.get("schema_version") != 3:
        raise ValueError("decision.schema_version must be 3")
    if not isinstance(decision.get("run_id"), str) or not decision["run_id"]:
        raise ValueError("decision.run_id must be a non-empty string")
    for field in ("plan_revision", "decision_revision"):
        if not isinstance(decision.get(field), int) or isinstance(decision.get(field), bool) or decision[field] < 1:
            raise ValueError(f"decision.{field} must be a positive integer")
    if decision.get("task_type") not in TASK_TYPES:
        raise ValueError(f"decision.task_type must be one of {sorted(TASK_TYPES)}")
    if decision.get("readiness") not in READINESS:
        raise ValueError(f"decision.readiness must be one of {sorted(READINESS)}")
    if not isinstance(decision.get("title"), str) or not decision["title"].strip():
        raise ValueError("decision.title must be a non-empty string")
    if not isinstance(decision.get("summary"), str) or not decision["summary"].strip():
        raise ValueError("decision.summary must be a non-empty string")
    recommendation = _normalized_recommendation(decision)
    if decision["readiness"] == "production-ready" and not isinstance(recommendation, dict):
        raise ValueError("production-ready requires decision.recommendation")
    if decision["readiness"] == "blocked" and decision.get("recommendation") is not None:
        raise ValueError("blocked decision.recommendation must be null")
    if isinstance(recommendation, dict):
        if not isinstance(recommendation.get("name"), str) or not recommendation["name"].strip():
            raise ValueError("recommendation.name must be a non-empty string")
        rationale = recommendation.get("rationale")
        if not isinstance(rationale, list) or not rationale:
            raise ValueError("recommendation.rationale must be a non-empty list")
        for item in rationale:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("each rationale item must contain text")
            sources = item.get("sources")
            if not isinstance(sources, list) or not sources or not all(
                isinstance(source, str) and source for source in sources
            ):
                raise ValueError("each rationale item must cite one or more source IDs")


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (not nonempty or bool(value))
        and all(_nonempty_string(item) for item in value)
    )


def typed_runbook_problems(
    runbook: dict[str, Any], decision: dict[str, Any],
) -> list[str]:
    """Return minimum usability-contract violations for a typed v3 runbook."""
    problems: list[str] = []
    expected_type = (
        "research-only" if decision.get("readiness") == "blocked"
        else decision.get("task_type")
    )
    task_type = runbook.get("task_type")
    readiness = runbook.get("readiness")
    if task_type not in TASK_TYPES or task_type != expected_type:
        problems.append(f"runbook.task_type must be {expected_type!r} for this decision")
    if readiness not in READINESS or readiness != decision.get("readiness"):
        problems.append("runbook.readiness is invalid or differs from decision")
    if runbook.get("default_plan") != decision.get("recommendation"):
        problems.append("runbook.default_plan differs from decision.recommendation")
    if not isinstance(runbook.get("decision_revision"), int) or runbook["decision_revision"] < 1:
        problems.append("runbook.decision_revision must be a positive integer")
    if not isinstance(runbook.get("constraints"), list):
        problems.append("runbook.constraints must be a list")

    if task_type == "implementation":
        for field, kind in (
            ("environment", dict), ("steps", list), ("fallbacks", list),
            ("poc", dict), ("monitoring", list), ("security_and_license", list),
        ):
            if not isinstance(runbook.get(field), kind):
                problems.append(f"implementation runbook.{field} must be a {kind.__name__}")
        steps = runbook.get("steps") if isinstance(runbook.get("steps"), list) else []
        if readiness == "production-ready" and not steps:
            problems.append("production-ready implementation requires at least one runbook step")
        if readiness == "production-ready":
            poc = runbook.get("poc")
            if not isinstance(poc, dict) or not (
                poc.get("result") == "passed"
                and bool(poc.get("sample"))
                and isinstance(poc.get("baseline"), dict) and bool(poc["baseline"])
                and bool(poc.get("metrics"))
                and bool(poc.get("thresholds"))
                and isinstance(poc.get("budget"), dict) and bool(poc["budget"])
                and isinstance(poc.get("failure_tests"), list) and bool(poc["failure_tests"])
                and _string_list(poc.get("sources"), nonempty=True)
                and _string_list(poc.get("artifact_ids"), nonempty=True)
                and _nonempty_string(poc.get("rollback"))
            ):
                problems.append(
                    "production-ready implementation requires a passed representative POC "
                    "with sample, baseline, metrics, thresholds, budget, failure_tests, "
                    "sources, artifact_ids, and rollback"
                )
        if readiness == "pilot-only" and not (
            steps or runbook.get("poc") or runbook.get("to_test")
        ):
            problems.append("pilot-only implementation requires steps, a POC, or tests")
        for index, step in enumerate(steps):
            prefix = f"runbook.steps[{index}]"
            if not isinstance(step, dict):
                problems.append(f"{prefix} must be an object")
                continue
            for field in ("action", "expect"):
                if not _nonempty_string(step.get(field)):
                    problems.append(f"{prefix}.{field} must be a non-empty string")
            command = step.get("command")
            if command is not None and not _string_list(command, nonempty=True):
                problems.append(f"{prefix}.command must be a non-empty argv string list")
            sources = step.get("sources")
            if not _string_list(sources, nonempty=readiness == "production-ready"):
                problems.append(f"{prefix}.sources must cite evidence for production steps")
            if readiness == "production-ready" and not _nonempty_string(step.get("rollback")):
                problems.append(f"{prefix}.rollback is required for production-ready steps")

    elif task_type == "itinerary":
        days = runbook.get("days")
        if not isinstance(days, list):
            problems.append("itinerary runbook.days must be a list")
            days = []
        if readiness == "production-ready" and not days:
            problems.append("production-ready itinerary requires at least one day")
        if readiness == "production-ready" and not _nonempty_string(runbook.get("timezone")):
            problems.append("production-ready itinerary requires a timezone")
        for index, day in enumerate(days):
            prefix = f"runbook.days[{index}]"
            if not isinstance(day, dict):
                problems.append(f"{prefix} must be an object")
                continue
            if not _nonempty_string(day.get("date")):
                problems.append(f"{prefix}.date must be a non-empty string")
            segments = day.get("segments")
            if not isinstance(segments, list) or (readiness == "production-ready" and not segments):
                problems.append(f"{prefix}.segments must be a non-empty list for production")
                continue
            for segment_index, segment in enumerate(segments):
                segment_prefix = f"{prefix}.segments[{segment_index}]"
                if not isinstance(segment, dict):
                    problems.append(f"{segment_prefix} must be an object")
                    continue
                for field in ("start", "end", "place_id"):
                    if not _nonempty_string(segment.get(field)):
                        problems.append(f"{segment_prefix}.{field} must be a non-empty string")
                if segment.get("reservation") not in {"required", "recommended", "none", "unknown"}:
                    problems.append(f"{segment_prefix}.reservation is invalid")
                if not _string_list(segment.get("sources"), nonempty=readiness == "production-ready"):
                    problems.append(f"{segment_prefix}.sources must cite evidence for production segments")
                if readiness == "production-ready" and not _nonempty_string(segment.get("fallback")):
                    problems.append(f"{segment_prefix}.fallback is required for production itineraries")

    elif task_type == "forecast":
        scenarios = runbook.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            problems.append("forecast runbook.scenarios must be a non-empty list")
            scenarios = []
        for index, scenario in enumerate(scenarios):
            prefix = f"runbook.scenarios[{index}]"
            if not isinstance(scenario, dict):
                problems.append(f"{prefix} must be an object")
                continue
            for field in ("id", "label", "observation_window"):
                if not _nonempty_string(scenario.get(field)):
                    problems.append(f"{prefix}.{field} must be a non-empty string")
            for field in ("assumptions", "signals", "falsifiers"):
                if not isinstance(scenario.get(field), list):
                    problems.append(f"{prefix}.{field} must be a list")
        for field in ("known_facts", "unknowns", "prohibited_actions"):
            if not isinstance(runbook.get(field), list):
                problems.append(f"forecast runbook.{field} must be a list")

    elif task_type == "research-only":
        if runbook.get("default_plan") is not None:
            problems.append("research-only runbook.default_plan must be null")
        if not (runbook.get("blockers") or runbook.get("next_research")):
            problems.append("research-only runbook requires blockers or next_research")
        for field in ("steps", "days", "scenarios"):
            if runbook.get(field):
                problems.append(f"research-only runbook must not contain executable {field}")
    return problems


def _sources_index(findings: list[dict[str, Any]], decision: dict[str, Any]) -> dict[str, str | None]:
    index: dict[str, str | None] = {}
    for record in findings + list(decision.get("claims") or []) + list(decision.get("evidence_clusters") or []):
        if not isinstance(record, dict):
            continue
        source_id = _source_id(record)
        if source_id:
            url = record.get("source_url")
            index[source_id] = url if isinstance(url, str) and url.startswith(("http://", "https://")) else None
    return index


def _source_links(source_ids: list[str], index: dict[str, str | None]) -> str:
    links = []
    for source_id in source_ids:
        label = html.escape(source_id)
        url = index.get(source_id)
        links.append(f'<a class="source" href="{html.escape(url, quote=True)}">{label}</a>' if url else f'<span class="source">{label}</span>')
    return " ".join(links)


def _render_list(items: list[Any]) -> str:
    if not items:
        return '<p class="muted">None recorded.</p>'
    values = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("text") or item.get("name") or item.get("claim") or json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            text = str(item)
        values.append(f"<li>{html.escape(str(text))}</li>")
    return "<ul>" + "".join(values) + "</ul>"


def _render_evidence_details(decision: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    by_fingerprint = {
        record.get("fingerprint"): record for record in findings
        if isinstance(record.get("fingerprint"), str)
    }
    rows = []
    for cluster in decision.get("evidence_clusters") or []:
        if not isinstance(cluster, dict):
            continue
        payload = cluster.get("payload") if isinstance(cluster.get("payload"), dict) else cluster
        for member in payload.get("members") or []:
            if not isinstance(member, dict):
                continue
            finding = by_fingerprint.get(member.get("source_fingerprint")) or {}
            finding_id = _source_id(finding) or str(member.get("source_fingerprint", ""))[:16]
            source_url = finding.get("source_url")
            source = (f'<a href="{html.escape(source_url, quote=True)}">{html.escape(finding_id)}</a>'
                      if isinstance(source_url, str) and source_url.startswith(("http://", "https://"))
                      else html.escape(finding_id))
            rows.append(
                "<tr>"
                f'<td>{html.escape(str(cluster.get("id", "")))}</td>'
                f'<td>{source}</td>'
                f'<td>{html.escape(str(member.get("quote", "")))}</td>'
                f'<td>{html.escape(str(member.get("locator", "")))}</td>'
                "</tr>"
            )
    if not rows:
        return '<p class="muted">No evidence excerpts exported.</p>'
    return "<table><thead><tr><th>Cluster</th><th>Source</th><th>Excerpt / observation</th><th>Locator</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def build_runbook(decision: dict[str, Any]) -> dict[str, Any]:
    recommendation = decision.get("recommendation")
    normalized = _normalized_recommendation(decision)
    task_type = "research-only" if decision["readiness"] == "blocked" else decision["task_type"]
    source_ids = []
    for rationale in (normalized or {}).get("rationale") or []:
        source_ids.extend(source for source in rationale.get("sources") or [] if isinstance(source, str))
    contract = decision.get("decision_contract") or {}
    common = {
        "schema_version": 3,
        "run_id": decision["run_id"],
        "task_type": task_type,
        "decision_revision": decision.get("decision_revision") or (decision.get("metadata") or {}).get("decision_revision", 1),
        "readiness": decision["readiness"],
        "decision_contract": contract,
        "constraints": list(contract.get("hard_constraints") or []),
        "sources": list(dict.fromkeys(source_ids)),
        "default_plan": recommendation,
        "alternatives": decision.get("alternatives") or [],
        "critical_claims": [claim for claim in decision.get("claims") or [] if isinstance(claim, dict) and claim.get("critical") is True],
        "to_test": decision.get("to_test") or [],
        "blockers": decision.get("blockers") or [],
        "open_questions": decision.get("open_questions") or [],
    }
    if task_type == "implementation":
        typed = decision.get("implementation") or (normalized or {}).get("implementation") or {}
        common.update({
            "environment": typed.get("environment") or decision.get("environment") or {},
            "steps": typed.get("steps") or (normalized or {}).get("steps") or [],
            "fallbacks": typed.get("fallbacks") or (normalized or {}).get("fallbacks") or [],
            "poc": typed.get("poc") or decision.get("poc") or {},
            "monitoring": typed.get("monitoring") or decision.get("monitoring") or [],
            "security_and_license": typed.get("security_and_license") or decision.get("security_and_license") or [],
        })
    elif task_type == "itinerary":
        typed = decision.get("itinerary") or (normalized or {}).get("itinerary") or {}
        for key, default in (("timezone", ""), ("days", []), ("lodging", {}),
                             ("weather_checks", []), ("mobility_and_family_constraints", []),
                             ("booking_deadlines", [])):
            common[key] = typed.get(key, decision.get(key, default))
    elif task_type == "forecast":
        typed = decision.get("forecast") or (normalized or {}).get("forecast") or {}
        for key, default in (("as_of", ""), ("scenarios", []), ("known_facts", []),
                             ("unknowns", []), ("prohibited_actions", [])):
            common[key] = typed.get(key, decision.get(key, default))
    else:
        typed = decision.get("research_only") or {}
        common.update({
            "blockers": typed.get("blockers") or decision.get("blockers") or [],
            "next_research": typed.get("next_research") or decision.get("next_research") or [],
            "decision_branches": typed.get("decision_branches") or decision.get("decision_branches") or [],
            "default_plan": None,
        })
    return common


def render_html(decision: dict[str, Any], findings: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    index = _sources_index(findings, decision)
    recommendation = _normalized_recommendation(decision)
    if isinstance(recommendation, dict):
        rationale = "".join(
            f'<li>{html.escape(item["text"])}<div>{_source_links(item["sources"], index)}</div></li>'
            for item in recommendation["rationale"]
        )
        steps = _render_list(recommendation.get("steps") or [])
        plans = f'<h3>{html.escape(recommendation["name"])}</h3><ol>{rationale}</ol><h3>Execution</h3>{steps}'
    else:
        plans = '<p class="blocked">No default plan passed the evidence gate.</p>'

    claims = []
    for claim in decision.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        source_ids = [source for source in claim.get("sources") or [] if isinstance(source, str)]
        claims.append(
            "<tr>"
            f'<td>{html.escape(str(claim.get("id", "")))}</td>'
            f'<td>{html.escape(str(claim.get("text", "")))}</td>'
            f'<td>{html.escape(str(claim.get("sufficiency", claim.get("status", ""))))}</td>'
            f'<td>{_source_links(source_ids, index)}</td>'
            "</tr>"
        )
    claims_html = "".join(claims) or '<tr><td colspan="4">No claims exported.</td></tr>'
    contract = decision.get("decision_contract") or {}
    event_count = len([event for event in events if event.get("actor") == "user"])
    embedded = json.dumps({
        "schema_version": 3,
        "run_id": decision["run_id"],
        "task_type": decision["task_type"],
        "readiness": decision["readiness"],
        "decision_sha256": hashlib.sha256(json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(decision["title"])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 20px;color:#202124}}h1,h2{{line-height:1.25}}.status{{display:inline-block;padding:3px 8px;border:1px solid #777;border-radius:4px;font-weight:700}}.blocked{{border-left:4px solid #b42318;padding:10px 14px;background:#fff4f2}}.muted{{color:#666}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;vertical-align:top}}th{{background:#f5f5f5;text-align:left}}.source{{font:12px ui-monospace,monospace;margin-right:8px}}code{{background:#f5f5f5;padding:1px 4px}}</style></head><body>
<h1>{html.escape(decision["title"])}</h1>
<p><span class="status">{html.escape(decision["readiness"])}</span> &nbsp; {html.escape(decision["task_type"])}</p>
<section id="scope"><h2>Decision contract</h2><pre>{html.escape(json.dumps(contract, ensure_ascii=False, indent=2))}</pre><p class="muted">Verbatim user events: {event_count}</p></section>
<section id="summary"><h2>Executive summary</h2><p>{html.escape(decision["summary"])}</p>{_render_list(decision.get("blockers") or [])}</section>
<section id="plans"><h2>Plan</h2>{plans}<h3>Alternatives</h3>{_render_list(decision.get("alternatives") or [])}</section>
<section id="evidence"><h2>Critical evidence</h2><table><thead><tr><th>ID</th><th>Claim</th><th>Status</th><th>Sources</th></tr></thead><tbody>{claims_html}</tbody></table><h3>Exact excerpts and locators</h3>{_render_evidence_details(decision, findings)}</section>
<section id="reco"><h2>Next gate</h2><h3>Validation work</h3>{_render_list(decision.get("to_test") or [])}<h3>Open questions</h3>{_render_list(decision.get("open_questions") or [])}</section>
<script id="research-delivery-meta" type="application/json">{embedded}</script>
</body></html>'''


def render(decision_path: str, findings_path: str | None, events_path: str | None,
           report_path: str, runbook_path: str, manifest_path: str) -> dict[str, Any]:
    decision = _read_json(decision_path)
    validate_decision(decision)
    findings = _read_jsonl(findings_path)
    events = _read_jsonl(events_path)
    _atomic_write(report_path, render_html(decision, findings, events))
    _atomic_write(runbook_path, json.dumps(build_runbook(decision), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    existing: dict[str, Any] = {}
    if os.path.isfile(manifest_path):
        try:
            candidate = _read_json(manifest_path)
            if candidate.get("schema_version") == 3 and candidate.get("run_id") == decision["run_id"]:
                existing = candidate
        except (OSError, ValueError, json.JSONDecodeError):
            existing = {}
    files = dict(existing.get("files") or {})
    root = Path(manifest_path).parent
    canonical_inputs = [root / name for name in (
        "manifest.v3.json", "plan.json", "plan-revisions.jsonl",
        "finding-revisions.jsonl", "claims.jsonl", "evidence-clusters.jsonl",
        "attempts.jsonl", "artifacts.jsonl", "candidates.jsonl",
        "decision-revisions.jsonl",
    )]
    artifact_records = _read_jsonl(str(root / "artifacts.jsonl")) if (root / "artifacts.jsonl").is_file() else []
    artifacts_by_id = {
        record.get("id"): record for record in artifact_records
        if isinstance(record.get("id"), str) and record["id"]
    }
    recommendation = decision.get("recommendation")
    recommendation = recommendation if isinstance(recommendation, dict) else {}
    implementation = decision.get("implementation")
    implementation = implementation if isinstance(implementation, dict) else {}
    poc = decision.get("poc") or implementation.get("poc") or recommendation.get("poc")
    referenced_artifacts: list[Path] = []
    if isinstance(poc, dict) and isinstance(poc.get("artifact_ids"), list):
        for artifact_id in poc["artifact_ids"]:
            record = artifacts_by_id.get(artifact_id)
            artifact_path = record.get("path") if isinstance(record, dict) else None
            if not isinstance(artifact_path, str) or not artifact_path:
                continue
            candidate = (root / artifact_path).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                referenced_artifacts.append(candidate)
    for path in (decision_path, findings_path, events_path, report_path, runbook_path,
                 *(str(value) for value in canonical_inputs if value.is_file()),
                 *(str(value) for value in referenced_artifacts)):
        if path:
            files[os.path.relpath(path, os.path.dirname(manifest_path))] = {
                "sha256": _sha256(path), "bytes": os.path.getsize(path),
            }
    manifest = {
        **existing,
        "schema_version": 3,
        "run_id": decision["run_id"],
        "gate_status": decision["readiness"],
        "rendered_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "files": files,
    }
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--findings")
    parser.add_argument("--events")
    parser.add_argument("--report", required=True)
    parser.add_argument("--runbook", required=True)
    parser.add_argument("--delivery-manifest", required=True)
    args = parser.parse_args()
    result = render(args.decision, args.findings, args.events, args.report, args.runbook, args.delivery_manifest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
