#!/usr/bin/env python3
"""Validate a v3 delivery across decision, evidence, budget, report and runbook."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import researchctl
from render_delivery import (
    _normalized_recommendation,
    typed_runbook_problems,
    validate_decision,
)


META_RE = re.compile(
    r'<script\s+id="research-delivery-meta"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{line_no} is not an object")
            records.append(value)
    return records


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_id(record: dict[str, Any]) -> str | None:
    for key in ("id", "finding_id", "claim_id", "cluster_id", "verdict_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _has_user_event(events: list[dict[str, Any]], seq: Any, event_type: str) -> bool:
    return any(
        event.get("actor") == "user" and event.get("event_type") == event_type
        and event.get("seq") == seq and isinstance(event.get("verbatim"), str)
        and bool(event["verbatim"])
        for event in events
    )


def _has_agent_event(
    events: list[dict[str, Any]], seq: Any, event_type: str, verbatim: Any,
) -> bool:
    return any(
        event.get("actor") in {"agent", "main-agent", "summary-agent"}
        and event.get("event_type") == event_type
        and event.get("seq") == seq
        and event.get("verbatim") == verbatim
        and isinstance(verbatim, str) and bool(verbatim)
        for event in events
    )


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _walk_sources(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in {"sources", "finding_ids", "evidence_ids"}:
                yield child_path, child
            yield from _walk_sources(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_sources(child, f"{path}[{index}]")


def _walk_named_fields(
    value: Any, names: set[str], path: str = "$",
) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in names:
                yield child_path, child
            yield from _walk_named_fields(child, names, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_named_fields(child, names, f"{path}[{index}]")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def _budget_problems(manifest: dict[str, Any], attempts: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    budget = manifest.get("budget") or {}
    if not isinstance(budget, dict):
        return ["manifest.v3.json budget must be an object"]
    fields = {
        "asr_seconds_limit": "ASR limit",
        "asr_seconds_reserved": "ASR reserved",
        "asr_seconds_spent": "ASR spent",
        "cost_limit": "cost limit",
        "cost_reserved": "cost reserved",
        "cost_spent": "cost spent",
    }
    numbers: dict[str, Decimal] = {}
    for field, label in fields.items():
        parsed = _decimal(budget.get(field))
        if parsed is None:
            problems.append(f"manifest budget {label} must be a non-negative number")
        else:
            numbers[field] = parsed
    if numbers.get("asr_seconds_spent", 0) > numbers.get("asr_seconds_limit", 0):
        problems.append("ASR budget exceeded")
    if numbers.get("cost_spent", 0) > numbers.get("cost_limit", 0):
        problems.append("cost budget exceeded")

    reserved_asr = Decimal(0)
    reserved_cost = Decimal(0)
    spent_asr = Decimal(0)
    spent_cost = Decimal(0)
    charged: dict[tuple[str, str], int] = {}
    for index, attempt in enumerate(attempts):
        prefix = f"attempts.jsonl[{index}]"
        status = attempt.get("status")
        if status not in {"reserved", "unknown", "settled", "released"}:
            problems.append(f"{prefix}.status is invalid")
            continue
        requested_asr = _decimal(attempt.get("requested_asr_seconds"))
        requested_cost = _decimal(attempt.get("requested_cost"))
        charged_asr = _decimal(attempt.get("charged_asr_seconds"))
        charged_cost = _decimal(attempt.get("charged_cost"))
        for field, value in (
            ("requested_asr_seconds", requested_asr), ("requested_cost", requested_cost),
            ("charged_asr_seconds", charged_asr), ("charged_cost", charged_cost),
        ):
            if value is None:
                problems.append(f"{prefix}.{field} must be a non-negative number")
        if None in {requested_asr, requested_cost, charged_asr, charged_cost}:
            continue
        assert requested_asr is not None and requested_cost is not None
        assert charged_asr is not None and charged_cost is not None
        if charged_asr > requested_asr or charged_cost > requested_cost:
            problems.append(f"{prefix} charges exceed its reservation")
        if status in {"reserved", "unknown"}:
            reserved_asr += requested_asr
            reserved_cost += requested_cost
            problems.append(f"{prefix} has unresolved {status} status")
            if charged_asr or charged_cost:
                problems.append(f"{prefix} unresolved attempts must not contain charges")
        elif status == "settled":
            spent_asr += charged_asr
            spent_cost += charged_cost
            kind = attempt.get("kind")
            key = attempt.get("idempotency_key")
            if not _nonempty_string(kind) or not _nonempty_string(key):
                problems.append(f"{prefix} requires kind and idempotency_key")
            elif charged_asr or charged_cost:
                charged[(kind, key)] = charged.get((kind, key), 0) + 1
        elif charged_asr or charged_cost:
            problems.append(f"{prefix} released attempts must not contain charges")
    for field, actual in (
        ("asr_seconds_reserved", reserved_asr), ("cost_reserved", reserved_cost),
        ("asr_seconds_spent", spent_asr), ("cost_spent", spent_cost),
    ):
        if field in numbers and numbers[field] != actual:
            problems.append(
                f"manifest budget {field} does not reconcile with attempts: "
                f"{numbers[field]} != {actual}"
            )
    duplicates = sorted(f"{kind}:{key}" for (kind, key), count in charged.items() if count > 1)
    if duplicates:
        problems.append(f"duplicate charged idempotency keys: {', '.join(duplicates[:10])}")
    return problems


def _expected_public_decision(
    revision: dict[str, Any], revisions: list[dict[str, Any]],
    manifest: dict[str, Any], claims: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Replay the state-layer public projection from immutable exported inputs."""
    payload = revision.get("payload")
    run = manifest.get("run")
    gate = manifest.get("gate")
    if not isinstance(payload, dict):
        return None, "decision revision payload must be an object"
    if not isinstance(run, dict) or not _nonempty_string(run.get("profile")):
        return None, "manifest.run is required to reconstruct decision.json"
    if not _nonempty_string(run.get("objective")):
        return None, "manifest.run.objective is required to reconstruct decision.json"
    if not isinstance(gate, dict) or gate.get("status") not in {
        "production-ready", "pilot-only", "blocked",
    }:
        return None, "manifest.gate.status is required to reconstruct decision.json"

    # JSON round-tripping avoids retaining references to the immutable payload object.
    public = json.loads(json.dumps(payload, ensure_ascii=False))
    effective_status = gate["status"]
    profile_task_types = {
        "technical": "implementation",
        "travel": "itinerary",
        "policy-forecast": "forecast",
    }
    task_type = public.get(
        "task_type", profile_task_types.get(run["profile"], "research-only")
    )
    if effective_status == "blocked":
        task_type = "research-only"
    title = public.get("title", run["objective"])
    summary = public.get(
        "summary",
        "The confirmed decision contract and evidence gate determine the readiness shown here.",
    )
    contract = public.get("decision_contract")
    contract = dict(contract) if isinstance(contract, dict) else {}
    contract.update({
        "confirmed": bool(revision.get("confirmed")),
        "verbatim": revision.get("contract_verbatim"),
        "sha256": revision.get("contract_sha256"),
        "user_confirmation_event_id": revision.get("user_confirmation_event_id"),
        "presentation_event_id": payload.get("contract_presentation_event_id"),
    })
    public_claims = []
    for claim in claims:
        projected = dict(claim)
        projected["sources"] = list(projected.get("evidence_cluster_ids") or [])
        public_claims.append(projected)
    first_created = next(
        (
            row.get("created_at") for row in sorted(
                (
                    row for row in revisions
                    if row.get("run_id") == revision.get("run_id")
                    and isinstance(row.get("revision"), int)
                ),
                key=lambda row: row["revision"],
            )
            if _nonempty_string(row.get("created_at"))
        ),
        revision.get("created_at"),
    )
    metadata = public.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata.update({
        "created_at": first_created,
        "updated_at": revision.get("created_at"),
        "contract_sha256": revision.get("contract_sha256"),
    })
    public.update({
        "schema_version": 3,
        "run_id": revision.get("run_id"),
        "plan_revision": revision.get("plan_revision"),
        "decision_revision": revision.get("revision"),
        "input_event_seq": revision.get("input_event_seq"),
        "decision_sha256": revision.get("decision_sha256"),
        "task_type": task_type,
        "readiness": effective_status,
        "title": title,
        "summary": summary,
        "decision_contract": contract,
        "claims": public_claims,
        "evidence_clusters": clusters,
        "metadata": metadata,
    })
    if effective_status == "blocked":
        public["recommendation"] = None
        blockers = public.get("blockers") if isinstance(public.get("blockers"), list) else []
        public["blockers"] = blockers or [
            "No findings were captured under the approved search plan."
        ]
    for key in (
        "contract_verbatim", "contract_presentation_event_id", "confirmed",
        "user_confirmation_event_id", "requested_status",
    ):
        public.pop(key, None)
    return public, None


def validate_directory(out_dir: str) -> list[str]:
    root = Path(out_dir)
    required = {
        "manifest": root / "manifest.v3.json",
        "plan": root / "plan.json",
        "decision": root / "decision.json",
        "runbook": root / "runbook.json",
        "report": root / "report.html",
        "delivery": root / "delivery-manifest.json",
    }
    history_required = {
        "plan-revisions": root / "plan-revisions.jsonl",
        "decision-revisions": root / "decision-revisions.jsonl",
    }
    problems = [
        f"missing required delivery file: {path.name}"
        for path in list(required.values()) + list(history_required.values()) if not path.is_file()
    ]
    if problems:
        return problems

    try:
        manifest = _read_json(required["manifest"])
        plan = _read_json(required["plan"])
        decision = _read_json(required["decision"])
        runbook = _read_json(required["runbook"])
        delivery = _read_json(required["delivery"])
        findings = _read_jsonl(root / "findings.jsonl")
        claims_export = _read_jsonl(root / "claims.jsonl")
        clusters = _read_jsonl(root / "evidence-clusters.jsonl")
        events = _read_jsonl(root / "events.jsonl")
        attempts = _read_jsonl(root / "attempts.jsonl")
        artifacts = _read_jsonl(root / "artifacts.jsonl")
        candidates = _read_jsonl(root / "candidates.jsonl")
        plan_revisions = _read_jsonl(history_required["plan-revisions"])
        decision_revisions = _read_jsonl(history_required["decision-revisions"])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"cannot parse delivery: {error}"]

    top_level = (
        ("manifest", manifest), ("plan", plan), ("decision", decision),
        ("runbook", runbook), ("delivery-manifest", delivery),
    )
    invalid_objects = [name for name, value in top_level if not isinstance(value, dict)]
    if invalid_objects:
        return [f"{name} must be an object" for name in invalid_objects]

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 3:
        problems.append("manifest.v3.json schema_version must be 3")
    try:
        validate_decision(decision)
    except (TypeError, ValueError) as error:
        return [str(error)]
    run_id = decision.get("run_id")
    for name, value in (("manifest", manifest), ("plan", plan), ("runbook", runbook), ("delivery-manifest", delivery)):
        if not isinstance(value, dict):
            problems.append(f"{name} must be an object")
        elif value.get("run_id") != run_id:
            problems.append(f"{name}.run_id does not match decision.run_id")
    manifest_gate = manifest.get("gate")
    manifest_gate_status = manifest_gate.get("status") if isinstance(manifest_gate, dict) else None
    if manifest_gate_status != decision.get("readiness"):
        problems.append("manifest gate status differs from decision.readiness")
    if delivery.get("gate_status") != decision.get("readiness"):
        problems.append("delivery-manifest gate_status differs from decision.readiness")

    if isinstance(plan, dict):
        projection_keys = {
            "schema_version", "run_id", "revision", "plan_sha256", "scope_sha256",
            "created_at", "updated_at",
        }
        plan_payload = {key: value for key, value in plan.items() if key not in projection_keys}
        try:
            expected_profile = (
                (manifest.get("run") or {}).get("profile")
                if isinstance(manifest.get("run"), dict) else None
            ) or str(plan_payload.get("profile", ""))
            researchctl._validate_plan_payload(
                plan_payload, expected_profile=expected_profile
            )
        except (TypeError, ValueError, researchctl.InputError) as error:
            problems.append(f"invalid plan.json: {error}")
        canonical_plan = researchctl.canonical_json(plan_payload)
        if plan.get("schema_version") != 3 or plan.get("plan_version") != 3:
            problems.append("plan.json schema_version and plan_version must be 3")
        if plan.get("plan_sha256") != hashlib.sha256(canonical_plan.encode()).hexdigest():
            problems.append("plan.json plan_sha256 does not match its canonical payload")
        if plan.get("scope_sha256") != researchctl._plan_scope_sha256(plan_payload):
            problems.append("plan.json scope_sha256 does not match its approved scope")
        if not isinstance(plan.get("revision"), int) or plan["revision"] < 1:
            problems.append("plan.json revision must be a positive integer")
        if decision.get("plan_revision") != plan.get("revision"):
            problems.append("decision was not produced from the exported plan revision")
        if manifest.get("plan_revision") != plan.get("revision"):
            problems.append("manifest plan_revision differs from plan.json")
        if manifest.get("decision_revision") != decision.get("decision_revision"):
            problems.append("manifest decision_revision differs from decision.json")
        current_plan_history = next(
            (row for row in plan_revisions if row.get("revision") == plan.get("revision")), None
        )
        if current_plan_history is None or any(
            current_plan_history.get(field) != expected for field, expected in (
                ("plan_sha256", plan.get("plan_sha256")),
                ("scope_sha256", plan.get("scope_sha256")),
                ("approval_event_id", plan.get("scope_approval_event_id")),
                ("payload", plan_payload),
            )
        ):
            problems.append("plan.json does not match its immutable plan revision")
    current_decision_history = next(
        (row for row in decision_revisions if row.get("revision") == decision.get("decision_revision")),
        None,
    )
    if current_decision_history is None:
        problems.append("decision.json lacks its immutable decision revision")
    else:
        revision_payload = current_decision_history.get("payload")
        input_event_seq = current_decision_history.get("input_event_seq")
        expected_decision_hash = hashlib.sha256(researchctl.canonical_json({
            "plan_revision": current_decision_history.get("plan_revision"),
            "input_event_seq": input_event_seq,
            "payload": revision_payload,
        }).encode()).hexdigest()
        if (
            current_decision_history.get("decision_sha256") != expected_decision_hash
            or decision.get("decision_sha256") != expected_decision_hash
            or current_decision_history.get("plan_revision") != decision.get("plan_revision")
            or current_decision_history.get("input_event_seq") != decision.get("input_event_seq")
        ):
            problems.append("decision.json does not match its immutable decision revision")
        if not isinstance(input_event_seq, int) or isinstance(input_event_seq, bool) or input_event_seq < 1:
            problems.append("decision revision has an invalid input_event_seq")
        elif any(
            isinstance(event.get("seq"), int)
            and event["seq"] > input_event_seq
            and event.get("event_type") not in {"decision.set", "delivery.exported"}
            for event in events
        ):
            problems.append("research inputs changed after the exported decision revision")
        expected_decision, reconstruction_problem = _expected_public_decision(
            current_decision_history, decision_revisions, manifest, claims_export, clusters,
        )
        if reconstruction_problem is not None:
            problems.append(reconstruction_problem)
        elif researchctl.canonical_json(expected_decision) != researchctl.canonical_json(decision):
            problems.append(
                "decision.json projection differs from immutable revision and canonical state"
            )
    if isinstance(runbook, dict):
        if runbook.get("schema_version") != 3:
            problems.append("runbook.schema_version must be 3")
        problems.extend(typed_runbook_problems(runbook, decision))
        if runbook.get("decision_revision") != decision.get("decision_revision"):
            problems.append("runbook.decision_revision differs from decision.json")

    valid_ids = set()
    for record in findings + claims_export + clusters:
        if isinstance(record, dict) and (record_id := _record_id(record)):
            valid_ids.add(record_id)

    candidates_by_id: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(candidates):
        candidate_id = _record_id(record)
        if candidate_id is None:
            problems.append(f"candidates.jsonl[{index}] lacks a canonical ID")
        elif candidate_id in candidates_by_id:
            problems.append(f"candidates.jsonl repeats canonical ID {candidate_id}")
        else:
            candidates_by_id[candidate_id] = record
        if record.get("run_id") != run_id:
            problems.append(f"candidates.jsonl[{index}].run_id differs from decision")
    for source_path, candidate_id in (
        list(_walk_named_fields(decision, {"selected_candidate_id", "candidate_id"}))
        + list(_walk_named_fields(runbook, {"selected_candidate_id", "candidate_id"}))
    ):
        if not _nonempty_string(candidate_id) or candidate_id not in candidates_by_id:
            problems.append(f"{source_path} references unknown candidate ID {candidate_id!r}")
    recommendation = decision.get("recommendation")
    if isinstance(recommendation, dict):
        selected_candidate_id = decision.get("selected_candidate_id")
        selected_candidate = candidates_by_id.get(selected_candidate_id)
        if selected_candidate is None:
            problems.append("decision.selected_candidate_id must reference a canonical candidate")
        elif selected_candidate.get("status") != "active":
            problems.append("decision.selected_candidate_id must reference an active candidate")
        recommendation_candidate_id = recommendation.get("candidate_id")
        if (
            recommendation_candidate_id is not None
            and recommendation_candidate_id != selected_candidate_id
        ):
            problems.append(
                "decision.recommendation.candidate_id differs from selected_candidate_id"
            )

    artifacts_by_id: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(artifacts):
        artifact_id = _record_id(record)
        if artifact_id is None:
            problems.append(f"artifacts.jsonl[{index}] lacks a canonical ID")
        elif artifact_id in artifacts_by_id:
            problems.append(f"artifacts.jsonl repeats canonical ID {artifact_id}")
        else:
            artifacts_by_id[artifact_id] = record
        if record.get("run_id") != run_id:
            problems.append(f"artifacts.jsonl[{index}].run_id differs from decision")
    referenced_artifact_ids: set[str] = set()
    for source_path, artifact_ids in (
        list(_walk_named_fields(decision, {"artifact_ids"}))
        + list(_walk_named_fields(runbook, {"artifact_ids"}))
    ):
        if (
            not isinstance(artifact_ids, list) or not artifact_ids
            or any(not _nonempty_string(artifact_id) for artifact_id in artifact_ids)
        ):
            problems.append(f"{source_path} must be a non-empty canonical artifact ID list")
            continue
        if len(artifact_ids) != len(set(artifact_ids)):
            problems.append(f"{source_path} repeats an artifact ID")
        for artifact_id in artifact_ids:
            if artifact_id not in artifacts_by_id:
                problems.append(f"{source_path} references unknown artifact ID {artifact_id}")
            else:
                referenced_artifact_ids.add(artifact_id)
    referenced_artifact_paths: set[str] = set()
    for artifact_id in sorted(referenced_artifact_ids):
        record = artifacts_by_id[artifact_id]
        raw_path = record.get("path")
        digest = record.get("sha256")
        if record.get("kind") != "poc-result":
            problems.append(f"artifact {artifact_id} is not a poc-result artifact")
        if not _nonempty_string(raw_path):
            problems.append(f"artifact {artifact_id} has no relative path")
            continue
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            problems.append(f"artifact {artifact_id} path is not a safe relative path")
            continue
        relative_name = relative.as_posix()
        artifact_path = (root / Path(*relative.parts)).resolve()
        try:
            artifact_path.relative_to(root.resolve())
        except ValueError:
            problems.append(f"artifact {artifact_id} path escapes the delivery directory")
            continue
        if not artifact_path.is_file() or artifact_path.stat().st_size == 0:
            problems.append(f"artifact {artifact_id} does not resolve to a non-empty file")
        elif not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            problems.append(f"artifact {artifact_id} has an invalid SHA-256")
        elif _sha256(artifact_path) != digest.lower():
            problems.append(f"artifact {artifact_id} SHA-256 does not match its file")
        else:
            referenced_artifact_paths.add(relative_name)

    claims_by_id = {
        _record_id(record): record for record in claims_export
        if isinstance(record, dict) and _record_id(record)
    }
    clusters_by_id = {
        _record_id(record): record for record in clusters
        if isinstance(record, dict) and _record_id(record)
    }
    decision_claims = {
        _record_id(record): record for record in decision.get("claims") or []
        if isinstance(record, dict) and _record_id(record)
    }
    decision_clusters = {
        _record_id(record): record for record in decision.get("evidence_clusters") or []
        if isinstance(record, dict) and _record_id(record)
    }
    if set(decision_claims) != set(claims_by_id):
        problems.append("decision claims differ from canonical claims.jsonl IDs")
    if set(decision_clusters) != set(clusters_by_id):
        problems.append("decision evidence clusters differ from canonical evidence-clusters.jsonl IDs")
    for claim_id, canonical_claim in claims_by_id.items():
        projected = decision_claims.get(claim_id)
        if projected is None:
            continue
        expected_sources = list(canonical_claim.get("evidence_cluster_ids") or [])
        for field in ("text", "critical", "sufficiency"):
            if projected.get(field) != canonical_claim.get(field):
                problems.append(f"decision claim {claim_id} differs from canonical {field}")
        if projected.get("sources") != expected_sources:
            problems.append(f"decision claim {claim_id} sources differ from canonical evidence links")
    for cluster_id, canonical_cluster in clusters_by_id.items():
        projected = decision_clusters.get(cluster_id)
        if projected is not None and projected != canonical_cluster:
            problems.append(f"decision evidence cluster {cluster_id} differs from canonical export")

    findings_by_fingerprint = {
        record.get("fingerprint"): record for record in findings
        if isinstance(record.get("fingerprint"), str)
    }
    used_cluster_ids = {
        cluster_id for claim in claims_export
        for cluster_id in (claim.get("evidence_cluster_ids") or [])
        if isinstance(cluster_id, str)
    }
    cluster_independence_keys: dict[str, set[str]] = {}
    for cluster_id in sorted(used_cluster_ids):
        cluster = clusters_by_id.get(cluster_id)
        if cluster is None:
            problems.append(f"claim references missing evidence cluster {cluster_id}")
            continue
        sources = cluster.get("source_fingerprints")
        payload = cluster.get("payload") if isinstance(cluster.get("payload"), dict) else cluster
        members = payload.get("members") if isinstance(payload, dict) else None
        if not isinstance(sources, list) or not sources or len(sources) != len(set(sources)):
            problems.append(f"evidence cluster {cluster_id} has invalid source_fingerprints")
            continue
        independent_count = cluster.get("independent_source_count")
        if (
            not isinstance(independent_count, int) or isinstance(independent_count, bool)
            or independent_count < 0 or independent_count > len(sources)
        ):
            problems.append(f"evidence cluster {cluster_id} has invalid independent_source_count")
        if not isinstance(members, list) or len(members) != len(sources):
            problems.append(f"evidence cluster {cluster_id} members do not exactly cover sources")
            continue
        member_map = {
            member.get("source_fingerprint"): member for member in members
            if isinstance(member, dict)
        }
        if set(member_map) != set(sources) or any(
            not _nonempty_string(member_map[source].get("quote"))
            or not _nonempty_string(member_map[source].get("locator"))
            or not _nonempty_string(member_map[source].get("independence_key"))
            for source in sources
        ):
            problems.append(
                f"evidence cluster {cluster_id} lacks exact quote/locator/independence members"
            )
        else:
            independence_keys = {
                member_map[source]["independence_key"] for source in sources
            }
            cluster_independence_keys[cluster_id] = independence_keys
            if independent_count != len(independence_keys):
                problems.append(
                    f"evidence cluster {cluster_id} independent_source_count differs "
                    "from unique independence_key values"
                )
        for source in sources:
            finding = findings_by_fingerprint.get(source)
            if finding is None or finding.get("disposition") != "consumed":
                problems.append(
                    f"evidence cluster {cluster_id} uses a missing, pending, or excluded finding"
                )
    for claim_id, claim in claims_by_id.items():
        required_count = claim.get("required_evidence_count", 0)
        if not isinstance(required_count, int) or isinstance(required_count, bool) or required_count < 0:
            problems.append(f"claim {claim_id} has invalid required_evidence_count")
            continue
        if claim.get("critical") is True and required_count < 1:
            problems.append(f"critical claim {claim_id} requires no evidence")
        seen_sources: set[str] = set()
        seen_independence_keys: set[str] = set()
        source_overlap = False
        independence_overlap = False
        for cluster_id in claim.get("evidence_cluster_ids") or []:
            cluster = clusters_by_id.get(cluster_id) or {}
            sources = cluster.get("source_fingerprints") or []
            if seen_sources.intersection(sources):
                source_overlap = True
            seen_sources.update(sources)
            independence_keys = cluster_independence_keys.get(cluster_id, set())
            if seen_independence_keys.intersection(independence_keys):
                independence_overlap = True
            seen_independence_keys.update(independence_keys)
        if source_overlap:
            problems.append(f"claim {claim_id} evidence clusters overlap source findings")
        if independence_overlap:
            problems.append(f"claim {claim_id} evidence clusters overlap independence keys")
        if (
            claim.get("sufficiency") == "sufficient"
            and len(seen_independence_keys) < required_count
        ):
            problems.append(f"claim {claim_id} is marked sufficient without enough independent evidence")
    for source_path, sources in list(_walk_sources(decision)) + list(_walk_sources(runbook)):
        if not isinstance(sources, list) or any(not _nonempty_string(source) for source in sources):
            problems.append(f"{source_path} must be a canonical source ID list")
            continue
        for source in sources:
            if source not in valid_ids:
                problems.append(f"{source_path} references unknown evidence ID {source}")

    pending = [record_id for record in findings if record.get("disposition") not in {"consumed", "excluded"} if (record_id := _record_id(record))]
    if pending:
        problems.append(f"findings not consumed/excluded: {', '.join(pending[:10])}")
    if decision.get("readiness") != "blocked" and not any(
        finding.get("disposition") == "consumed" for finding in findings
    ):
        problems.append("non-blocked delivery requires at least one consumed finding")
    contract = decision.get("decision_contract") or {}
    if not isinstance(contract, dict) or contract.get("confirmed") is not True:
        problems.append("delivery requires a confirmed decision contract")
        confirmation_id = None
    else:
        confirmation_id = contract.get("user_confirmation_event_id")
    if not _has_user_event(events, confirmation_id, "user.decision-confirmation"):
        problems.append("delivery requires its referenced verbatim user confirmation event")
    if current_decision_history is not None:
        expected_contract_sha = current_decision_history.get("contract_sha256")
        revision_payload = current_decision_history.get("payload")
        revision_payload = revision_payload if isinstance(revision_payload, dict) else {}
        presentation_id = revision_payload.get("contract_presentation_event_id")
        if any((
            contract.get("confirmed") != current_decision_history.get("confirmed"),
            contract.get("verbatim") != current_decision_history.get("contract_verbatim"),
            contract.get("sha256") != expected_contract_sha,
            confirmation_id != current_decision_history.get("user_confirmation_event_id"),
            contract.get("presentation_event_id") != presentation_id,
        )):
            problems.append("decision contract differs from its immutable decision revision")
        if confirmation_id != current_decision_history.get("input_event_seq"):
            problems.append("decision confirmation is not the latest research input")
        if not _has_agent_event(
            events, presentation_id, "agent.decision-contract",
            current_decision_history.get("contract_verbatim"),
        ):
            problems.append(
                "delivery requires its exact referenced agent.decision-contract presentation event"
            )
        if not (
            isinstance(presentation_id, int) and not isinstance(presentation_id, bool)
            and isinstance(confirmation_id, int) and presentation_id < confirmation_id
        ):
            problems.append("decision confirmation must follow its contract presentation event")
        try:
            displayed_contract = json.loads(current_decision_history.get("contract_verbatim", ""))
        except (TypeError, json.JSONDecodeError):
            displayed_contract = None
        if displayed_contract != revision_payload.get("decision_contract"):
            problems.append(
                "immutable contract_verbatim does not exactly represent decision_contract"
            )
        try:
            researchctl._validate_decision_contract(
                revision_payload,
                current_decision_history.get("contract_verbatim", ""),
                current_decision_history.get("requested_status", ""),
            )
        except (researchctl.InputError, TypeError, ValueError) as error:
            problems.append(f"invalid structured decision contract: {error}")
        if (
            isinstance(contract.get("verbatim"), str)
            and isinstance(expected_contract_sha, str)
            and hashlib.sha256(contract["verbatim"].encode()).hexdigest() != expected_contract_sha
        ):
            problems.append("decision contract hash does not match its verbatim text")
    plan_approval_id = plan.get("scope_approval_event_id") if isinstance(plan, dict) else None
    if not _has_user_event(events, plan_approval_id, "user.search-scope-approval"):
        problems.append("delivery requires its referenced verbatim search-scope approval event")
    if isinstance(plan, dict):
        plan_budgets = plan.get("budgets") or {}
        manifest_budget = manifest.get("budget") or {}
        asr_limit = manifest_budget.get("asr_seconds_limit", manifest_budget.get("asr_limit_seconds"))
        cost_limit = manifest_budget.get("cost_limit", manifest_budget.get("cost_limit_cny"))
        if plan_budgets.get("asr_seconds") != asr_limit:
            problems.append("plan ASR duration limit differs from manifest budget")
        if plan_budgets.get("asr_cost_cny") != cost_limit:
            problems.append("plan ASR cost limit differs from manifest budget")
        budget_event_id = plan.get("budget_authorization_event_id")
        if (plan_budgets.get("asr_seconds", 0) or plan_budgets.get("asr_cost_cny", 0)) and not _has_user_event(
            events, budget_event_id, "user.asr-authorization"
        ):
            problems.append("non-zero plan ASR budget lacks its verbatim user authorization event")
        account_event_id = plan.get("account_authorization_event_id")
        if plan_budgets.get("account_actions") is True and not _has_user_event(
            events, account_event_id, "user.account-authorization"
        ):
            problems.append("plan account actions lack their verbatim user authorization event")
    if decision.get("readiness") == "production-ready":
        load_bearing_claims = {
            claim_id for claim_id, claim in claims_by_id.items()
            if claim.get("critical") is True and claim.get("sufficiency") == "sufficient"
            and bool(claim.get("evidence_cluster_ids"))
        }
        load_bearing_clusters = {
            cluster_id for claim_id in load_bearing_claims
            for cluster_id in claims_by_id[claim_id].get("evidence_cluster_ids") or []
        }
        if not load_bearing_claims:
            problems.append("production-ready requires at least one sufficient canonical critical claim")
        insufficient = [
            _record_id(claim) or "<missing-id>"
            for claim in claims_export + list(decision.get("claims") or [])
            if claim.get("critical") is True and claim.get("sufficiency") != "sufficient"
        ]
        if insufficient:
            problems.append(f"critical claims insufficient for production-ready: {', '.join(sorted(set(insufficient)))}")
        recommendation = _normalized_recommendation(decision) or {}
        for index, rationale in enumerate(recommendation.get("rationale") or []):
            sources = rationale.get("sources") if isinstance(rationale, dict) else None
            if not isinstance(sources, list) or not any(
                isinstance(source, str) and source.startswith(("clm_", "clm-", "evc_", "evc-"))
                for source in sources
            ):
                problems.append(
                    f"production recommendation rationale[{index}] must cite a claim or evidence cluster"
                )
            elif any(source not in valid_ids for source in sources if source.startswith(("clm_", "clm-", "evc_", "evc-"))):
                problems.append(
                    f"production recommendation rationale[{index}] references unknown evidence"
                )
            elif not any(source in load_bearing_claims or source in load_bearing_clusters for source in sources):
                problems.append(
                    f"production recommendation rationale[{index}] does not cite sufficient load-bearing evidence"
                )

    problems.extend(_budget_problems(manifest, attempts))

    try:
        report_text = required["report"].read_text(encoding="utf-8")
        match = META_RE.search(report_text)
        if not match:
            problems.append("report.html lacks research-delivery-meta")
        else:
            embedded = json.loads(match.group(1).replace("<\\/", "</"))
            if embedded.get("run_id") != run_id or embedded.get("readiness") != decision.get("readiness"):
                problems.append("report metadata differs from decision")
            canonical = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            if embedded.get("decision_sha256") != hashlib.sha256(canonical).hexdigest():
                problems.append("report was not rendered from the current decision.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        problems.append(f"cannot validate report metadata: {error}")

    if isinstance(delivery, dict):
        files = delivery.get("files")
        if not isinstance(files, dict):
            problems.append("delivery-manifest.files must be an object")
        else:
            for relative in (
                "manifest.v3.json", "plan.json", "decision.json", "findings.jsonl",
                "events.jsonl", "plan-revisions.jsonl", "decision-revisions.jsonl",
                "finding-revisions.jsonl", "claims.jsonl", "evidence-clusters.jsonl", "attempts.jsonl",
                "artifacts.jsonl", "candidates.jsonl", "report.html", "runbook.json",
            ):
                if relative not in files:
                    problems.append(f"delivery manifest does not hash required file: {relative}")
            for relative in sorted(referenced_artifact_paths):
                if relative not in files:
                    problems.append(
                        f"delivery manifest does not hash referenced POC artifact: {relative}"
                    )
            for relative, expected in files.items():
                path = (root / relative).resolve()
                try:
                    path.relative_to(root.resolve())
                except ValueError:
                    problems.append(f"delivery manifest path escapes output directory: {relative}")
                    continue
                if isinstance(expected, dict):
                    expected_hash = expected.get("sha256")
                    expected_bytes = expected.get("bytes")
                else:
                    expected_hash = expected
                    expected_bytes = None
                if not path.is_file():
                    problems.append(f"delivery manifest file missing: {relative}")
                elif not isinstance(expected_hash, str) or _sha256(path) != expected_hash:
                    problems.append(f"delivery manifest hash mismatch: {relative}")
                elif expected_bytes is not None and expected_bytes != path.stat().st_size:
                    problems.append(f"delivery manifest byte count mismatch: {relative}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    problems = validate_directory(args.out_dir)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"ok": True, "problems": []}, ensure_ascii=False))


if __name__ == "__main__":
    main()
