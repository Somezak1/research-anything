import json
import hashlib

import researchctl
from render_delivery import render
from validate_delivery import validate_directory


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, values):
    path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")


def _plan_payload(profile, approval_event_id, *, asr_seconds=0, asr_cost=0, budget_event_id=None):
    return {
        "plan_version": 3, "profile": profile, "risk_overlays": [],
        "dimensions": ["quality", "cost", "latency"],
        "source_requirements": ["official", "independent-test", "failure-signal"],
        "estimates": {"p50_minutes": 30, "p90_minutes": 90,
                      "basis": ["doctor capabilities and declared probe caps"]},
        "budgets": {"wall_minutes": 120, "asr_seconds": asr_seconds,
                    "asr_cost_cny": asr_cost, "account_actions": False},
        "scope_approval_event_id": approval_event_id,
        "budget_authorization_event_id": budget_event_id,
        "account_authorization_event_id": None,
        "channels": [{
            "name": name, "signals": ["candidate", "failure"],
            "probe": {"queries": [f"current options {name}"], "limit_per_query": 3},
        } for name in sorted(researchctl.KNOWN_CHANNELS)],
        "deepening": [],
    }


def _approve_plan(db, *, asr_seconds=0, asr_cost=0, budget_event_id=None):
    event = researchctl.record_event(db, {
        "event_type": "user.search-scope-approval", "actor": "user",
        "verbatim": "Approve the displayed eight-entry search scope.",
    })
    researchctl.set_plan(db, _plan_payload(
        "technical", event["seq"], asr_seconds=asr_seconds,
        asr_cost=asr_cost, budget_event_id=budget_event_id,
    ))
    return event


def _public_plan(run_id, approval_event_id):
    payload = _plan_payload("technical", approval_event_id)
    digest = hashlib.sha256(researchctl.canonical_json(payload).encode()).hexdigest()
    scope_digest = researchctl._plan_scope_sha256(payload)
    return {
        **payload, "schema_version": 3, "run_id": run_id, "revision": 1,
        "plan_sha256": digest, "scope_sha256": scope_digest,
        "created_at": "2026-07-16T00:00:00Z", "updated_at": "2026-07-16T00:00:00Z",
    }


def _passed_poc(claim_id="clm_a", artifact_id="art_poc"):
    return {
        "result": "passed", "sample": "representative production-shaped sample",
        "baseline": {"latency_ms": 180},
        "metrics": {"latency_ms": 120}, "thresholds": {"latency_ms": "<= 200"},
        "budget": {"asr_seconds": 0, "cost_cny": 0},
        "failure_tests": [{"name": "provider timeout", "result": "passed"}],
        "sources": [claim_id], "rollback": "Restore the prior production version.",
        "artifact_ids": [artifact_id],
    }


def _fixture(tmp_path, readiness="production-ready"):
    fixture_contract_value = {
        "hard_constraints": [{"name": "latency", "operator": "<=", "value": 200}],
        "preferences": [{"name": "operability", "value": "simple"}],
        "success_metrics": [{"name": "latency_ms", "operator": "<=", "value": 200}],
        "approved_costs": [], "unresolved": [],
        "risk_tolerance": "low", "time_horizon": "current production cycle",
    }
    fixture_contract = researchctl.canonical_json(fixture_contract_value)
    fixture_contract_sha = hashlib.sha256(fixture_contract.encode()).hexdigest()
    recommendation = ({
        "candidate_id": "cand_a", "name": "Tool A",
        "rationale": [{"text": "It meets the hard constraint.", "sources": ["clm_a"]}],
        "steps": [{
            "id": "step-01", "action": "Run the representative pilot.",
            "command": ["tool-a", "pilot"], "expect": "The acceptance threshold passes.",
            "sources": ["clm_a"], "rollback": "Stop the pilot and restore the prior configuration.",
        }],
        "fallbacks": [],
    } if readiness != "blocked" else None)
    poc = _passed_poc() if readiness == "production-ready" else {}
    blockers = [] if readiness == "production-ready" else ["Pilot not complete"]
    decision_revision_payload = {
        "contract_verbatim": fixture_contract,
        "decision_contract": fixture_contract_value,
        "contract_presentation_event_id": 2,
        "confirmed": True,
        "user_confirmation_event_id": 3,
        "requested_status": readiness,
        "task_type": "implementation",
        "title": "Choose <tool>",
        "summary": "Evidence-gated choice.",
        **({"selected_candidate_id": "cand_a"} if recommendation is not None else {}),
        "recommendation": recommendation,
        "alternatives": [],
        "to_test": [],
        "poc": poc,
        "blockers": blockers,
        "open_questions": [],
    }
    decision_digest = hashlib.sha256(researchctl.canonical_json({
        "plan_revision": 1, "input_event_seq": 3, "payload": decision_revision_payload,
    }).encode()).hexdigest()
    decision = {
        **{key: value for key, value in decision_revision_payload.items() if key not in {
            "contract_verbatim", "contract_presentation_event_id", "confirmed",
            "user_confirmation_event_id", "requested_status",
        }},
        "schema_version": 3,
        "run_id": "run-1",
        "plan_revision": 1,
        "decision_revision": 1,
        "input_event_seq": 3,
        "decision_sha256": decision_digest,
        "task_type": "research-only" if readiness == "blocked" else "implementation",
        "readiness": readiness,
        "decision_contract": {
            **fixture_contract_value,
            "confirmed": True, "user_confirmation_event_id": 3,
            "verbatim": fixture_contract, "sha256": fixture_contract_sha,
            "presentation_event_id": 2,
        },
        "claims": [{
            "id": "clm_a", "text": "Tool A meets the limit", "critical": True,
            "sufficiency": "sufficient" if readiness == "production-ready" else "insufficient",
            "required_evidence_count": 1, "evidence_cluster_ids": ["evc_a"],
            "sources": ["evc_a"],
        }],
        "evidence_clusters": [{
            "id": "evc_a", "label": "Independent evidence",
            "independent_source_count": 1, "source_fingerprints": ["fp_a"],
            "payload": {"members": [{
                "source_fingerprint": "fp_a", "quote": "Evidence",
                "locator": "example source paragraph 1",
                "independence_key": "publisher:example.test",
            }]},
        }],
        "metadata": {
            "created_at": "2026-07-16T00:00:00Z",
            "updated_at": "2026-07-16T00:00:00Z",
            "contract_sha256": fixture_contract_sha,
        },
    }
    findings = [{
        "id": "fnd_a", "source_url": "https://example.test/a", "channel": "web",
        "fingerprint": "fp_a", "headline": "A", "note": "Evidence", "disposition": "consumed",
    }]
    events = [{
        "seq": 1, "event_type": "user.search-scope-approval", "actor": "user", "verbatim": "批准搜索范围",
        "sha256": "irrelevant-for-renderer",
    }, {
        "seq": 2, "event_type": "agent.decision-contract", "actor": "summary-agent",
        "verbatim": fixture_contract,
        "sha256": fixture_contract_sha,
    }, {
        "seq": 3, "event_type": "user.decision-confirmation", "actor": "user", "verbatim": "确认",
        "sha256": "irrelevant-for-renderer",
    }]
    manifest = {
        "schema_version": 3, "run_id": "run-1",
        "plan_revision": 1,
        "decision_revision": 1,
        "run": {"id": "run-1", "objective": "Choose a tool", "profile": "technical"},
        "gate": {"status": readiness},
        "budget": {
            "currency": "CNY", "asr_seconds_limit": 0, "asr_seconds_reserved": 0,
            "asr_seconds_spent": 0, "cost_limit": 0, "cost_reserved": 0,
            "cost_spent": 0,
        },
    }
    _write_json(tmp_path / "decision.json", decision)
    plan = _public_plan("run-1", 1)
    _write_json(tmp_path / "plan.json", plan)
    plan_payload = {key: value for key, value in plan.items() if key not in {
        "schema_version", "run_id", "revision", "plan_sha256", "scope_sha256",
        "created_at", "updated_at",
    }}
    _write_jsonl(tmp_path / "plan-revisions.jsonl", [{
        "run_id": "run-1", "revision": 1, "plan_sha256": plan["plan_sha256"],
        "scope_sha256": plan["scope_sha256"], "approval_event_id": 1,
        "payload": plan_payload, "created_at": "2026-07-16T00:00:00Z",
    }])
    _write_jsonl(tmp_path / "decision-revisions.jsonl", [{
        "run_id": "run-1", "revision": 1, "plan_revision": 1,
        "input_event_seq": 3,
        "decision_sha256": decision_digest, "contract_verbatim": fixture_contract,
        "contract_sha256": fixture_contract_sha,
        "confirmed": True, "user_confirmation_event_id": 3,
        "requested_status": readiness, "payload": decision_revision_payload,
        "created_at": "2026-07-16T00:00:00Z",
    }])
    _write_json(tmp_path / "manifest.v3.json", manifest)
    _write_jsonl(tmp_path / "findings.jsonl", findings)
    _write_jsonl(tmp_path / "finding-revisions.jsonl", [])
    _write_jsonl(tmp_path / "events.jsonl", events)
    _write_jsonl(tmp_path / "claims.jsonl", decision["claims"])
    _write_jsonl(tmp_path / "evidence-clusters.jsonl", decision["evidence_clusters"])
    _write_jsonl(tmp_path / "attempts.jsonl", [])
    _write_jsonl(tmp_path / "candidates.jsonl", [{
        "id": "cand_a", "run_id": "run-1", "name": "Tool A",
        "canonical_name": "tool a", "candidate_type": "tool", "status": "active",
        "payload": {}, "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
    }])
    artifact_path = tmp_path / "artifacts" / "poc-result.json"
    artifact_path.parent.mkdir(exist_ok=True)
    artifact_path.write_text('{"result":"passed"}\n', encoding="utf-8")
    artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    _write_jsonl(tmp_path / "artifacts.jsonl", [{
        "id": "art_poc", "run_id": "run-1", "finding_id": None,
        "kind": "poc-result", "path": "artifacts/poc-result.json",
        "sha256": artifact_sha, "media_fingerprint": None, "payload": {},
        "created_at": "2026-07-16T00:00:00Z",
    }])
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    return decision


def _commit_payload_updates(tmp_path, decision, *keys):
    revisions = [
        json.loads(line)
        for line in (tmp_path / "decision-revisions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    revision = revisions[-1]
    payload = revision["payload"]
    for key in keys:
        if key in decision:
            payload[key] = decision[key]
        else:
            payload.pop(key, None)
    digest = hashlib.sha256(researchctl.canonical_json({
        "plan_revision": revision["plan_revision"],
        "input_event_seq": revision["input_event_seq"],
        "payload": payload,
    }).encode()).hexdigest()
    revision["decision_sha256"] = digest
    decision["decision_sha256"] = digest
    _write_jsonl(tmp_path / "decision-revisions.jsonl", revisions)
    _write_json(tmp_path / "decision.json", decision)


def _rerender(tmp_path):
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )


def test_rendered_delivery_validates_and_escapes_html(tmp_path):
    _fixture(tmp_path)
    assert validate_directory(str(tmp_path)) == []
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Choose &lt;tool&gt;" in report
    assert '<span class="source">clm_a</span>' in report
    delivery = json.loads((tmp_path / "delivery-manifest.json").read_text(encoding="utf-8"))
    assert "artifacts/poc-result.json" in delivery["files"]


def test_public_decision_projection_cannot_drift_from_immutable_payload(tmp_path):
    decision = _fixture(tmp_path)
    decision["recommendation"]["name"] = "Injected winner"
    _write_json(tmp_path / "decision.json", decision)
    _rerender(tmp_path)
    assert any(
        "projection differs" in problem for problem in validate_directory(str(tmp_path))
    )


def test_unknown_candidate_and_noncanonical_source_are_rejected(tmp_path):
    decision = _fixture(tmp_path)
    decision["selected_candidate_id"] = "cand_missing"
    decision["recommendation"]["candidate_id"] = "cand_missing"
    decision["recommendation"]["rationale"][0]["sources"] = ["human-readable-citation"]
    _commit_payload_updates(
        tmp_path, decision, "selected_candidate_id", "recommendation",
    )
    _rerender(tmp_path)
    problems = validate_directory(str(tmp_path))
    assert any("unknown candidate ID" in problem for problem in problems)
    assert any("unknown evidence ID human-readable-citation" in problem for problem in problems)


def test_poc_artifact_must_exist_match_registry_and_be_manifest_hashed(tmp_path):
    _fixture(tmp_path)
    artifact_path = tmp_path / "artifacts" / "poc-result.json"
    artifact_path.write_text('{"result":"tampered"}\n', encoding="utf-8")
    problems = validate_directory(str(tmp_path))
    assert any("SHA-256 does not match" in problem for problem in problems)

    _fixture(tmp_path)
    delivery = json.loads((tmp_path / "delivery-manifest.json").read_text(encoding="utf-8"))
    delivery["files"].pop("artifacts/poc-result.json")
    _write_json(tmp_path / "delivery-manifest.json", delivery)
    problems = validate_directory(str(tmp_path))
    assert any("does not hash referenced POC artifact" in problem for problem in problems)


def test_unknown_poc_artifact_id_is_rejected(tmp_path):
    decision = _fixture(tmp_path)
    decision["poc"]["artifact_ids"] = ["art_missing"]
    _commit_payload_updates(tmp_path, decision, "poc")
    _rerender(tmp_path)
    assert any(
        "unknown artifact ID art_missing" in problem
        for problem in validate_directory(str(tmp_path))
    )


def test_decision_change_invalidates_report_and_manifest(tmp_path):
    decision = _fixture(tmp_path)
    decision["summary"] = "Changed after rendering"
    _write_json(tmp_path / "decision.json", decision)
    problems = validate_directory(str(tmp_path))
    assert any("current decision" in problem for problem in problems)
    assert any("hash mismatch" in problem for problem in problems)


def test_malformed_top_level_delivery_is_rejected_without_crashing(tmp_path):
    _fixture(tmp_path)
    _write_json(tmp_path / "decision.json", ["not", "an", "object"])
    assert validate_directory(str(tmp_path)) == ["decision must be an object"]


def test_plan_revision_invalidates_an_older_decision_and_report(tmp_path):
    _fixture(tmp_path)
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    plan["revision"] = 2
    plan["deepening"] = [{
        "channel": "web", "reason": "critical-gap", "decision_gap": "Current price",
        "queries": ["official current price"], "limit": 1,
    }]
    payload = {key: value for key, value in plan.items() if key not in {
        "schema_version", "run_id", "revision", "plan_sha256", "scope_sha256",
        "created_at", "updated_at",
    }}
    plan["plan_sha256"] = hashlib.sha256(researchctl.canonical_json(payload).encode()).hexdigest()
    plan["scope_sha256"] = researchctl._plan_scope_sha256(payload)
    _write_json(tmp_path / "plan.json", plan)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    problems = validate_directory(str(tmp_path))
    assert any("exported plan revision" in problem for problem in problems)
    assert any("manifest plan_revision" in problem for problem in problems)


def test_unknown_reference_is_rejected(tmp_path):
    decision = _fixture(tmp_path)
    decision["claims"][0]["sources"] = ["fnd_missing"]
    _write_json(tmp_path / "decision.json", decision)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any("unknown evidence ID" in problem for problem in validate_directory(str(tmp_path)))


def test_authorization_event_type_is_part_of_the_delivery_contract(tmp_path):
    _fixture(tmp_path)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    events[0]["event_type"] = "user.requirement"
    _write_jsonl(tmp_path / "events.jsonl", events)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any("search-scope approval event" in problem for problem in validate_directory(str(tmp_path)))


def test_delivery_rejects_research_input_after_decision_confirmation(tmp_path):
    _fixture(tmp_path)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    events.append({
        "seq": 4, "event_type": "user.requirement", "actor": "user",
        "verbatim": "新增约束", "sha256": "irrelevant-for-renderer",
    })
    _write_jsonl(tmp_path / "events.jsonl", events)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any(
        "research inputs changed" in problem for problem in validate_directory(str(tmp_path))
    )


def test_delivery_contract_must_match_immutable_decision_revision(tmp_path):
    decision = _fixture(tmp_path)
    decision["decision_contract"]["verbatim"] = "tampered contract"
    _write_json(tmp_path / "decision.json", decision)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    problems = validate_directory(str(tmp_path))
    assert any("contract differs" in problem for problem in problems)
    assert any("contract hash" in problem for problem in problems)


def test_excluded_finding_cannot_back_a_delivery_claim(tmp_path):
    _fixture(tmp_path)
    findings = [json.loads(line) for line in (tmp_path / "findings.jsonl").read_text().splitlines()]
    findings[0]["disposition"] = "excluded"
    _write_jsonl(tmp_path / "findings.jsonl", findings)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any("excluded finding" in problem for problem in validate_directory(str(tmp_path)))


def test_decision_cannot_self_authorize_fabricated_claims(tmp_path):
    decision = _fixture(tmp_path)
    decision["claims"] = [{
        "id": "clm_fabricated", "text": "Invented", "critical": True,
        "sufficiency": "sufficient", "sources": ["evc_fabricated"],
    }]
    decision["evidence_clusters"] = [{"id": "evc_fabricated"}]
    decision["recommendation"]["rationale"][0]["sources"] = ["clm_fabricated"]
    _write_json(tmp_path / "decision.json", decision)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    problems = validate_directory(str(tmp_path))
    assert any("canonical claims.jsonl IDs" in problem for problem in problems)
    assert any("canonical evidence-clusters.jsonl IDs" in problem for problem in problems)


def test_blocked_delivery_may_omit_default_plan(tmp_path):
    _fixture(tmp_path, readiness="blocked")
    assert validate_directory(str(tmp_path)) == []
    runbook = json.loads((tmp_path / "runbook.json").read_text(encoding="utf-8"))
    assert runbook["default_plan"] is None
    assert runbook["task_type"] == "research-only"


def test_production_step_requires_observable_evidence_and_rollback(tmp_path):
    decision = _fixture(tmp_path)
    decision["recommendation"]["steps"] = [{"action": "Deploy now", "command": "tool-a deploy"}]
    _commit_payload_updates(tmp_path, decision, "recommendation")
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    problems = validate_directory(str(tmp_path))
    assert any(".expect" in problem for problem in problems)
    assert any("argv" in problem for problem in problems)
    assert any(".sources" in problem for problem in problems)
    assert any(".rollback" in problem for problem in problems)


def test_production_implementation_requires_a_passed_representative_poc(tmp_path):
    decision = _fixture(tmp_path)
    decision["poc"] = {}
    _commit_payload_updates(tmp_path, decision, "poc")
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any("representative POC" in problem for problem in validate_directory(str(tmp_path)))


def test_itinerary_runbook_uses_typed_schedule(tmp_path):
    decision = _fixture(tmp_path)
    decision["task_type"] = "itinerary"
    decision["recommendation"]["itinerary"] = {
        "timezone": "Asia/Shanghai",
        "days": [{
            "date": "2026-10-01",
            "segments": [{
                "start": "09:00", "end": "11:00", "place_id": "place-a",
                "transport": "taxi", "reservation": "required",
                "sources": ["clm_a"], "fallback": "Use the indoor alternative if closed.",
            }],
        }],
        "lodging": {"selected": "place-hotel", "alternatives": []},
    }
    _commit_payload_updates(tmp_path, decision, "task_type", "recommendation")
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert validate_directory(str(tmp_path)) == []
    runbook = json.loads((tmp_path / "runbook.json").read_text(encoding="utf-8"))
    assert runbook["task_type"] == "itinerary"
    assert runbook["days"][0]["segments"][0]["reservation"] == "required"


def test_forecast_runbook_requires_scenarios(tmp_path):
    decision = _fixture(tmp_path, readiness="pilot-only")
    decision["task_type"] = "forecast"
    decision["forecast"] = {
        "as_of": "2026-07-16",
        "scenarios": [{
            "id": "scenario-base", "label": "base",
            "assumptions": [{"claim_id": "clm_a"}],
            "signals": ["Official announcement"], "falsifiers": ["Cancellation"],
            "observation_window": "Through 2026-08-01",
        }],
        "known_facts": ["clm_a"], "unknowns": ["Final schedule"],
        "prohibited_actions": ["Do not treat this scenario as a guaranteed outcome."],
    }
    _commit_payload_updates(tmp_path, decision, "task_type", "forecast")
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert validate_directory(str(tmp_path)) == []
    runbook = json.loads((tmp_path / "runbook.json").read_text(encoding="utf-8"))
    assert runbook["task_type"] == "forecast"
    assert runbook["scenarios"][0]["id"] == "scenario-base"


def test_why_claim_id_must_reference_exported_evidence(tmp_path):
    decision = _fixture(tmp_path)
    decision["recommendation"] = {
        "candidate_id": "cand_a",
        "name": "Tool A",
        "why": [{"claim_id": "clm_missing", "text": "An invented claim."}],
        "steps": [{
            "action": "Run the rollout.", "expect": "The threshold passes.",
            "sources": ["clm_a"], "rollback": "Restore the prior tool.",
        }],
    }
    _commit_payload_updates(tmp_path, decision, "recommendation")
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any("unknown evidence" in problem for problem in validate_directory(str(tmp_path)))


def test_duplicate_charge_is_rejected(tmp_path):
    _fixture(tmp_path)
    _write_jsonl(tmp_path / "attempts.jsonl", [
        {"kind": "asr", "idempotency_key": "media:model", "status": "settled",
         "requested_asr_seconds": 1, "requested_cost": 0,
         "charged_asr_seconds": 1, "charged_cost": 0},
        {"kind": "asr", "idempotency_key": "media:model", "status": "settled",
         "requested_asr_seconds": 1, "requested_cost": 0,
         "charged_asr_seconds": 1, "charged_cost": 0},
    ])
    assert any("duplicate charged" in problem for problem in validate_directory(str(tmp_path)))


def test_delivery_manifest_gate_status_must_match_decision(tmp_path):
    _fixture(tmp_path)
    delivery = json.loads((tmp_path / "delivery-manifest.json").read_text())
    delivery["gate_status"] = "blocked"
    _write_json(tmp_path / "delivery-manifest.json", delivery)
    assert any(
        "gate_status differs" in problem for problem in validate_directory(str(tmp_path))
    )


def test_nonblocked_delivery_requires_consumed_evidence(tmp_path):
    _fixture(tmp_path)
    _write_jsonl(tmp_path / "findings.jsonl", [])
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any(
        "non-blocked delivery requires" in problem
        for problem in validate_directory(str(tmp_path))
    )


def test_production_poc_artifact_must_have_poc_result_kind(tmp_path):
    _fixture(tmp_path)
    artifacts = [
        json.loads(line) for line in (tmp_path / "artifacts.jsonl").read_text().splitlines()
    ]
    artifacts[0]["kind"] = "unrelated-log"
    _write_jsonl(tmp_path / "artifacts.jsonl", artifacts)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert any(
        "not a poc-result" in problem for problem in validate_directory(str(tmp_path))
    )


def test_unresolved_attempt_is_rejected_even_when_reserved_total_reconciles(tmp_path):
    _fixture(tmp_path)
    manifest = json.loads((tmp_path / "manifest.v3.json").read_text(encoding="utf-8"))
    manifest["budget"]["asr_seconds_limit"] = 10
    manifest["budget"]["asr_seconds_reserved"] = 2
    _write_json(tmp_path / "manifest.v3.json", manifest)
    _write_jsonl(tmp_path / "attempts.jsonl", [{
        "kind": "asr", "idempotency_key": "media:model", "status": "unknown",
        "requested_asr_seconds": 2, "requested_cost": 0,
        "charged_asr_seconds": 0, "charged_cost": 0,
    }])
    _rerender(tmp_path)
    assert any(
        "unresolved unknown status" in problem
        for problem in validate_directory(str(tmp_path))
    )


def test_attempt_overcharge_and_manifest_reconciliation_are_enforced(tmp_path):
    _fixture(tmp_path)
    manifest = json.loads((tmp_path / "manifest.v3.json").read_text(encoding="utf-8"))
    manifest["budget"]["asr_seconds_limit"] = 10
    manifest["budget"]["asr_seconds_spent"] = 1
    _write_json(tmp_path / "manifest.v3.json", manifest)
    _write_jsonl(tmp_path / "attempts.jsonl", [{
        "kind": "asr", "idempotency_key": "media:model", "status": "settled",
        "requested_asr_seconds": 2, "requested_cost": 0,
        "charged_asr_seconds": 3, "charged_cost": 0,
    }])
    _rerender(tmp_path)
    problems = validate_directory(str(tmp_path))
    assert any("charges exceed its reservation" in problem for problem in problems)
    assert any("does not reconcile with attempts" in problem for problem in problems)


def test_researchctl_export_renders_and_validates_end_to_end(tmp_path):
    db = tmp_path / "research.db"
    researchctl.init_database(db, {
        "run_id": "run-e2e", "objective": "Choose a tool", "profile": "technical",
        "require_critical_claims": True,
    })
    budget_event = researchctl.record_event(db, {
        "event_type": "user.asr-authorization", "actor": "user",
        "verbatim": "Authorize up to 60 seconds and 3 CNY of ASR.",
    })
    researchctl.authorize_budget(db, {
        "asr_seconds_limit": 60, "asr_cost_limit": 3,
        "user_authorization_event_id": budget_event["seq"],
    })
    _approve_plan(db, asr_seconds=60, asr_cost=3, budget_event_id=budget_event["seq"])
    finding = researchctl.upsert_finding(db, {
        "channel": "web", "source_url": "https://example.test/tool", "title": "Official API",
        "headline": "The API exposes the required operation", "note": "The official reference documents the operation.",
    })
    cluster = researchctl.upsert_evidence_cluster(db, {
        "label": "Official API evidence", "source_fingerprints": [finding["fingerprint"]],
        "independent_source_count": 1,
        "members": [{"source_fingerprint": finding["fingerprint"],
                     "quote": "The API exposes the required operation.",
                     "locator": "official API reference #operation",
                     "independence_key": "publisher:example.test"}],
    })
    claim = researchctl.upsert_claim(db, {
        "text": "The operation is available", "critical": True, "sufficiency": "sufficient",
        "required_evidence_count": 1, "evidence_cluster_ids": [cluster["id"]],
    })
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "consumed", "reason": "Supports the API claim",
        "claim_ids": [claim["id"]],
    }]})
    candidate = researchctl.upsert_candidate(db, {
        "name": "Tool A", "candidate_type": "tool", "status": "active",
    })
    artifact_file = tmp_path / "artifacts" / "poc-result.json"
    artifact_file.parent.mkdir()
    artifact_file.write_text('{"result":"passed"}\n', encoding="utf-8")
    artifact = researchctl.upsert_artifact(db, {
        "kind": "poc-result", "path": "artifacts/poc-result.json",
        "sha256": hashlib.sha256(artifact_file.read_bytes()).hexdigest(),
    })
    decision_contract = {
        "hard_constraints": [{"name": "operation", "operator": "=", "value": "available"}],
        "preferences": [],
        "success_metrics": [{"name": "operation_success", "operator": "=", "value": True}],
        "approved_costs": [{"currency": "CNY", "amount": 3}], "unresolved": [],
        "risk_tolerance": "low", "time_horizon": "current production cycle",
    }
    contract_verbatim = researchctl.canonical_json(decision_contract)
    presentation = researchctl.record_event(db, {
        "event_type": "agent.decision-contract", "actor": "summary-agent",
        "verbatim": contract_verbatim,
    })
    event = researchctl.record_event(db, {
        "event_type": "user.decision-confirmation", "actor": "user", "verbatim": "确认预算和上线条件。",
    })
    researchctl.set_decision(db, {
        "contract_verbatim": contract_verbatim,
        "decision_contract": decision_contract,
        "contract_presentation_event_id": presentation["seq"], "confirmed": True,
        "user_confirmation_event_id": event["seq"], "requested_status": "production-ready",
        "task_type": "implementation", "title": "Tool decision", "summary": "Tool A passes the gate.",
        "selected_candidate_id": candidate["id"],
        "recommendation": {
            "candidate_id": candidate["id"], "name": "Tool A",
            "rationale": [{"text": "The API fits.", "sources": [claim["id"]]}],
            "steps": [{
                "id": "step-01", "action": "Run the measured rollout.",
                "command": ["tool-a", "rollout", "--sample", "representative"],
                "expect": "The required operation succeeds within the confirmed limits.",
                "sources": [claim["id"]], "rollback": "Stop the rollout and restore the previous tool.",
            }],
            "fallbacks": [],
        },
        "poc": _passed_poc(claim["id"], artifact["id"]),
    })
    researchctl.export_state(db, tmp_path)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert validate_directory(str(tmp_path)) == []
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "The API exposes the required operation." in report
    assert "official API reference #operation" in report


def test_zero_evidence_blocked_export_is_a_valid_research_only_delivery(tmp_path):
    db = tmp_path / "research.db"
    researchctl.init_database(db, {
        "run_id": "run-no-access", "objective": "Choose a tool", "profile": "technical",
        "require_critical_claims": True,
    })
    _approve_plan(db)
    decision_contract = {
        "hard_constraints": [], "preferences": [], "success_metrics": [],
        "approved_costs": [],
        "unresolved": [{"question": "Which primary-source connector can be authorized?"}],
        "risk_tolerance": "low", "time_horizon": "next research pass",
    }
    contract_verbatim = researchctl.canonical_json(decision_contract)
    presentation = researchctl.record_event(db, {
        "event_type": "agent.decision-contract", "actor": "summary-agent",
        "verbatim": contract_verbatim,
    })
    event = researchctl.record_event(db, {
        "event_type": "user.decision-confirmation", "actor": "user",
        "verbatim": "确认没有可用连接器，先列解阻动作。",
    })
    researchctl.set_decision(db, {
        "contract_verbatim": contract_verbatim,
        "decision_contract": decision_contract,
        "contract_presentation_event_id": presentation["seq"], "confirmed": True,
        "user_confirmation_event_id": event["seq"], "requested_status": "blocked",
        "task_type": "implementation", "title": "Access-blocked research",
        "summary": "No source could be captured under the current permissions.",
        "recommendation": None, "blockers": ["No authorized connector is available."],
        "next_research": [{"action": "Authorize one primary-source connector."}],
    })
    researchctl.export_state(db, tmp_path)
    render(
        str(tmp_path / "decision.json"), str(tmp_path / "findings.jsonl"),
        str(tmp_path / "events.jsonl"), str(tmp_path / "report.html"),
        str(tmp_path / "runbook.json"), str(tmp_path / "delivery-manifest.json"),
    )
    assert validate_directory(str(tmp_path)) == []
    runbook = json.loads((tmp_path / "runbook.json").read_text(encoding="utf-8"))
    assert runbook["task_type"] == "research-only"
    assert runbook["default_plan"] is None
