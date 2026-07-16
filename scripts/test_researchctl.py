import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

import researchctl


SCRIPT = os.path.join(os.path.dirname(__file__), "researchctl.py")


def _init(tmp_path, **overrides):
    db = tmp_path / "research.db"
    config = {
        "run_id": "run_test",
        "objective": "Choose a production workflow",
        "profile": "technical",
        "asr_seconds_limit": 100,
        "asr_cost_limit": 10,
        "require_critical_claims": True,
    }
    config.update(overrides)
    asr_seconds = config.pop("asr_seconds_limit", 0)
    asr_cost = config.pop("asr_cost_limit", 0)
    result = researchctl.init_database(db, config)
    assert result["run_id"] == config["run_id"]
    if asr_seconds or asr_cost:
        event = researchctl.record_event(db, {
            "event_type": "user.asr-authorization", "actor": "user",
            "verbatim": f"Authorize at most {asr_seconds} seconds and {asr_cost} CNY for tests.",
        })
        researchctl.authorize_budget(db, {
            "asr_seconds_limit": asr_seconds, "asr_cost_limit": asr_cost,
            "user_authorization_event_id": event["seq"],
        })
    return db


def _plan_payload(db, approval_event_id, **overrides):
    current = researchctl.status(db)
    budget = current["gate"]["budget"]
    authorization_id = current["run"]["config"].get("budget_authorization_event_id")
    value = {
        "plan_version": 3,
        "profile": current["run"]["profile"],
        "risk_overlays": [],
        "dimensions": ["quality", "latency", "cost", "license"],
        "source_requirements": ["official", "independent-test", "failure-signal"],
        "estimates": {
            "p50_minutes": 30, "p90_minutes": 90,
            "basis": ["doctor capability snapshot and declared probe caps; no probe has run"],
        },
        "budgets": {
            "wall_minutes": 120,
            "asr_seconds": budget["asr_seconds_limit"],
            "asr_cost_cny": budget["cost_limit"],
            "account_actions": False,
        },
        "scope_approval_event_id": approval_event_id,
        "budget_authorization_event_id": authorization_id,
        "account_authorization_event_id": None,
        "channels": [{
            "name": name, "signals": ["candidate", "failure"],
            "probe": {"queries": [f"production options {name}"], "limit_per_query": 3},
        } for name in sorted(researchctl.KNOWN_CHANNELS)],
        "deepening": [],
    }
    value.update(overrides)
    return value


def _approve_plan(db, verbatim="批准上述搜索范围。"):
    event = researchctl.record_event(db, {
        "event_type": "user.search-scope-approval", "actor": "user", "verbatim": verbatim,
    })
    result = researchctl.set_plan(db, _plan_payload(db, event["seq"]))
    return result, event


def _decision_contract(**overrides):
    value = {
        "hard_constraints": [{"name": "budget", "operator": "<=", "value": "confirmed"}],
        "preferences": [],
        "success_metrics": [{"metric": "acceptance", "threshold": "passes representative test"}],
        "risk_tolerance": "bounded rollout",
        "time_horizon": "current run",
        "approved_costs": [],
        "unresolved": [],
    }
    value.update(overrides)
    return value


def _confirmed_decision_fields(db, *, contract=None, user_verbatim="确认上述决策契约。"):
    contract = contract or _decision_contract()
    contract_verbatim = researchctl.canonical_json(contract)
    presentation = researchctl.record_event(db, {
        "event_type": "agent.decision-contract", "actor": "main-agent",
        "verbatim": contract_verbatim,
    })
    confirmation = researchctl.record_event(db, {
        "event_type": "user.decision-confirmation", "actor": "user",
        "verbatim": user_verbatim,
    })
    return {
        "contract_verbatim": contract_verbatim,
        "decision_contract": contract,
        "contract_presentation_event_id": presentation["seq"],
        "confirmed": True,
        "user_confirmation_event_id": confirmation["seq"],
    }


def _poc_artifact(db, tmp_path, name="poc-result.json"):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    path = artifact_dir / name
    content = b'{"result":"passed","latency_ms":120}\n'
    path.write_bytes(content)
    return researchctl.upsert_artifact(db, {
        "kind": "poc-result", "path": f"artifacts/{name}",
        "sha256": hashlib.sha256(content).hexdigest(),
    })["id"]


def _candidate(db, name="Measured option"):
    return researchctl.upsert_candidate(db, {
        "name": name, "candidate_type": "workflow", "status": "active",
    })["id"]


def _passed_poc(claim_id, artifact_id):
    return {
        "result": "passed", "sample": "representative production-shaped sample",
        "baseline": {"latency_ms": 250},
        "metrics": {"latency_ms": 120}, "thresholds": {"latency_ms": "<= 200"},
        "budget": {"wall_minutes": 10, "cost_cny": 0},
        "failure_tests": ["provider timeout returns to the prior version"],
        "sources": [claim_id], "artifact_ids": [artifact_id],
        "rollback": "Restore the prior production version.",
    }


def _finding(**overrides):
    value = {
        "channel": "youtube",
        "source_url": "https://example.com/watch?v=42",
        "media_url": "https://cdn.example.com/video/42.mp4?X-Amz-Signature=first&expires=1",
        "title": "A production walkthrough",
        "headline": "The workflow is viable under the measured constraints",
        "note": "The author reports the complete setup, cost, latency, and failure mode.",
        "published_at": "2026-07-01",
        "unknown_terms": ["adapter cache"],
    }
    value.update(overrides)
    return value


def test_init_doctor_and_restart(tmp_path):
    db = _init(tmp_path)
    health = researchctl.doctor(db)
    assert health["ok"] is True
    assert set(("web", "github", "youtube", "twitter", "douyin", "xiaohongshu", "zhihu", "bilibili")) <= set(health["details"]["connectors"])
    first = researchctl.status(db)
    # Every API call opens a fresh connection, so this is also a restart/readback check.
    second = researchctl.status(str(db))
    assert first["run"] == second["run"]
    assert second["counts"]["events"] == 3
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_verbatim_event_hash_and_append_only_triggers(tmp_path):
    db = _init(tmp_path)
    verbatim = "  用户原话，保留空格。\n第二行\\n  "
    event = researchctl.record_event(db, {
        "event_type": "user.constraints",
        "actor": "user",
        "verbatim": verbatim,
    })
    assert event["verbatim"] == verbatim
    assert event["sha256"] == hashlib.sha256(verbatim.encode()).hexdigest()
    with sqlite3.connect(db) as conn:
        stored = conn.execute(
            "SELECT verbatim,sha256 FROM events WHERE seq=?", (event["seq"],)
        ).fetchone()
        assert stored == (verbatim, event["sha256"])
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE events SET verbatim='changed' WHERE seq=?", (event["seq"],))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM events WHERE seq=?", (event["seq"],))


def test_budget_authorization_requires_preserved_user_event(tmp_path):
    db = _init(tmp_path, asr_seconds_limit=0, asr_cost_limit=0)
    event = researchctl.record_event(db, {
        "event_type": "user.asr-authorization", "actor": "user",
        "verbatim": "同意最多转写 60 秒，费用上限 2 元。",
    })
    result = researchctl.authorize_budget(db, {
        "asr_seconds_limit": 60, "asr_cost_limit": 2,
        "user_authorization_event_id": event["seq"],
    })
    assert result["budget"]["asr_seconds_limit"] == 60
    assert researchctl.status(db)["run"]["config"]["budget_authorization_event_id"] == event["seq"]
    system_event = researchctl.record_event(db, {
        "event_type": "test", "actor": "system", "verbatim": "not user authorization",
    })
    with pytest.raises(researchctl.InputError, match="user event"):
        researchctl.authorize_budget(db, {
            "asr_seconds_limit": 70, "asr_cost_limit": 3,
            "user_authorization_event_id": system_event["seq"],
        })


def test_init_rejects_nonzero_budget_without_user_event(tmp_path):
    with pytest.raises(researchctl.InputError, match="must start with zero"):
        researchctl.init_database(tmp_path / "unsafe.db", {
            "objective": "unsafe", "profile": "technical",
            "asr_seconds_limit": 1, "asr_cost_limit": 0.1,
        })
    with pytest.raises(researchctl.InputError, match="unsupported fields"):
        researchctl.init_database(tmp_path / "ambiguous.db", {
            "objective": "ambiguous", "profile": "technical",
            "limits": {"asr_seconds": 9, "asr_cost": 1.5},
        })


def test_plan_is_validated_revisioned_and_bound_to_scope_approval(tmp_path):
    db = _init(tmp_path)
    first, event = _approve_plan(db)
    replay = researchctl.set_plan(db, _plan_payload(db, event["seq"]))
    assert first["revision"] == replay["revision"] == 1
    assert replay["idempotent_replay"] is True

    deepened = _plan_payload(db, event["seq"])
    deepened["deepening"] = [{
        "channel": "github", "reason": "critical-gap",
        "decision_gap": "License for the selected version",
        "queries": ["project license current version"], "candidate_ids": [],
        "claim_ids": [], "limit": 5,
    }]
    second = researchctl.set_plan(db, deepened)
    assert second["revision"] == 2
    changed_scope = _plan_payload(db, event["seq"], dimensions=["quality", "cost"])
    with pytest.raises(researchctl.InputError, match="materially different"):
        researchctl.set_plan(db, changed_scope)
    status = researchctl.status(db)
    assert status["plan"]["revision"] == 2
    assert status["counts"]["plan_revisions"] == 2


def test_plan_requires_all_discovery_entries_and_canonical_budget(tmp_path):
    db = _init(tmp_path)
    event = researchctl.record_event(db, {
        "event_type": "user.search-scope-approval", "actor": "user",
        "verbatim": "批准八入口计划。",
    })
    missing = _plan_payload(db, event["seq"])
    missing["channels"].pop()
    with pytest.raises(researchctl.InputError, match="eight discovery"):
        researchctl.set_plan(db, missing)
    unexplained = _plan_payload(db, event["seq"])
    unexplained["channels"][0]["enabled"] = False
    with pytest.raises(researchctl.InputError, match="disabled_reason"):
        researchctl.set_plan(db, unexplained)
    wrong_budget = _plan_payload(db, event["seq"])
    wrong_budget["budgets"]["asr_seconds"] += 1
    with pytest.raises(researchctl.InputError, match="budget broker"):
        researchctl.set_plan(db, wrong_budget)


def test_authorization_event_types_cannot_be_reused_across_capabilities(tmp_path):
    db = _init(tmp_path)
    generic = researchctl.record_event(db, {
        "event_type": "user.requirement", "actor": "user", "verbatim": "继续。",
    })
    with pytest.raises(researchctl.InputError, match="user.asr-authorization"):
        researchctl.authorize_budget(db, {
            "asr_seconds_limit": 100, "asr_cost_limit": 10,
            "user_authorization_event_id": generic["seq"],
        })
    with pytest.raises(researchctl.InputError, match="user.search-scope-approval"):
        researchctl.set_plan(db, _plan_payload(db, generic["seq"]))

    _, scope = _approve_plan(db)
    wrong_account = _plan_payload(db, scope["seq"])
    wrong_account["budgets"]["account_actions"] = True
    wrong_account["account_action_scope"] = ["Use a dedicated test account for one login attempt"]
    wrong_account["account_authorization_event_id"] = generic["seq"]
    with pytest.raises(researchctl.InputError, match="user.account-authorization"):
        researchctl.set_plan(db, wrong_account)
    contract = _decision_contract(unresolved=["No decision evidence."])
    contract_verbatim = researchctl.canonical_json(contract)
    presentation = researchctl.record_event(db, {
        "event_type": "agent.decision-contract", "actor": "main-agent",
        "verbatim": contract_verbatim,
    })
    with pytest.raises(researchctl.InputError, match="user.decision-confirmation"):
        researchctl.set_decision(db, {
            "contract_verbatim": contract_verbatim, "decision_contract": contract,
            "contract_presentation_event_id": presentation["seq"], "confirmed": True,
            "user_confirmation_event_id": generic["seq"],
            "requested_status": "blocked", "recommendation": None,
            "blockers": ["No decision evidence."],
        })


def test_decision_contract_must_be_structured_and_previously_displayed(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    contract = _decision_contract(unresolved=["No evidence captured."])
    contract_verbatim = researchctl.canonical_json(contract)
    presentation = researchctl.record_event(db, {
        "event_type": "agent.decision-contract", "actor": "main-agent",
        "verbatim": researchctl.canonical_json(_decision_contract()),
    })
    confirmation = researchctl.record_event(db, {
        "event_type": "user.decision-confirmation", "actor": "user", "verbatim": "确认。",
    })
    with pytest.raises(researchctl.InputError, match="exact displayed"):
        researchctl.set_decision(db, {
            "contract_verbatim": contract_verbatim, "decision_contract": contract,
            "contract_presentation_event_id": presentation["seq"], "confirmed": True,
            "user_confirmation_event_id": confirmation["seq"], "requested_status": "blocked",
            "recommendation": None, "blockers": ["No evidence captured."],
        })


def test_recommendation_must_select_a_canonical_candidate(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    fields = _confirmed_decision_fields(db)
    with pytest.raises(researchctl.InputError, match="active candidate"):
        researchctl.set_decision(db, {
            **fields, "requested_status": "production-ready",
            "selected_candidate_id": "cand_not_registered",
            "recommendation": {
                "name": "Invented option", "rationale": [{"text": "Invented", "sources": ["clm_x"]}],
            },
        })


def test_plan_revision_invalidates_an_older_decision(tmp_path):
    db = _init(tmp_path)
    _, approval = _approve_plan(db)
    decision_payload = {
        **_confirmed_decision_fields(
            db, contract=_decision_contract(unresolved=["No evidence captured."]),
            user_verbatim="确认当前无证据，只交付解阻清单。",
        ),
        "requested_status": "blocked",
        "recommendation": None, "blockers": ["No evidence captured."],
    }
    first_decision = researchctl.set_decision(db, decision_payload)
    replay = researchctl.set_decision(db, decision_payload)
    assert first_decision["decision_revision"] == replay["decision_revision"] == 1
    assert replay["idempotent_replay"] is True
    assert researchctl.evaluate_gate(db)["ok"] is True
    revised = _plan_payload(db, approval["seq"])
    revised["deepening"] = [{
        "channel": "web", "reason": "critical-gap", "decision_gap": "Obtain a source",
        "queries": ["official source"], "limit": 1,
    }]
    researchctl.set_plan(db, revised)
    stale = researchctl.evaluate_gate(db)
    assert stale["ok"] is False
    assert stale["checks"]["decision_uses_current_plan"] is False
    decision_payload.update(_confirmed_decision_fields(
        db, contract=_decision_contract(unresolved=["No evidence captured."]),
        user_verbatim="确认修订后的计划仍无证据，只交付解阻清单。",
    ))
    revised_decision = researchctl.set_decision(db, decision_payload)
    assert revised_decision["decision_revision"] == 2
    assert researchctl.status(db)["counts"]["decision_revisions"] == 2
    assert researchctl.evaluate_gate(db)["ok"] is True


def test_finding_and_media_ids_ignore_signed_url_rotation(tmp_path):
    db = _init(tmp_path)
    first = researchctl.upsert_finding(db, _finding())
    second = researchctl.upsert_finding(db, _finding(
        source_url="https://EXAMPLE.com/watch?v=42#comments",
        media_url="https://cdn.example.com/video/42.mp4?expires=999&X-Amz-Signature=rotated",
        note="Updated note from a retry.",
    ))
    assert first["id"] == second["id"]
    assert first["fingerprint"] == second["fingerprint"]
    assert first["media_fingerprint"] == second["media_fingerprint"]
    assert first["created"] is True and second["created"] is False
    assert first["revision"] == 1 and second["revision"] == 2
    projected = researchctl.project_notes(db)
    assert len(projected["items"]) == 1
    assert projected["items"][0]["note"] == "Updated note from a retry."


def test_artifact_registration_requires_real_matching_content(tmp_path):
    db = _init(tmp_path)
    with pytest.raises(researchctl.InputError, match="real non-empty"):
        researchctl.upsert_artifact(db, {
            "kind": "poc-result", "path": "artifacts/missing.json", "sha256": "0" * 64,
        })
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "result.json").write_text('{"result":"passed"}\n')
    with pytest.raises(researchctl.InputError, match="does not match"):
        researchctl.upsert_artifact(db, {
            "kind": "poc-result", "path": "artifacts/result.json", "sha256": "0" * 64,
        })


def test_changed_finding_invalidates_prior_consumption(tmp_path):
    db = _init(tmp_path, require_critical_claims=False)
    finding = researchctl.upsert_finding(db, _finding())
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "consumed", "reason": "Initial review",
    }]})
    assert researchctl.evaluate_gate(db)["checks"]["all_findings_disposed"] is True
    updated = researchctl.upsert_finding(db, _finding(note="The retry added a material failure mode."))
    assert updated["revision"] == 2 and updated["changed"] is True
    projected = researchctl.project_notes(db)
    assert projected["items"][0]["disposition"] == "pending"
    assert researchctl.evaluate_gate(db)["checks"]["all_findings_disposed"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM finding_revisions").fetchone()[0] == 2


def test_budget_reservation_is_concurrency_safe(tmp_path):
    db = _init(tmp_path, asr_seconds_limit=100, asr_cost_limit=10)
    _approve_plan(db)
    findings = [researchctl.upsert_finding(db, _finding(
        source_url=f"https://example.com/watch?v=budget-{index}",
        media_url=f"https://cdn.example.com/video/budget-{index}.mp4?signature=old",
    )) for index in (1, 2)]

    def reserve(index):
        try:
            value = researchctl.reserve_budget(db, {
                "idempotency_key": f"job-{index}",
                "finding_id": findings[index - 1]["id"],
                "requested_asr_seconds": 60,
                "requested_cost": 4,
            })
            return value["status"]
        except researchctl.BudgetError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, (1, 2)))
    assert sorted(outcomes) == ["rejected", "reserved"]
    current = researchctl.status(db)["gate"]["budget"]
    assert current["asr_seconds_reserved"] == 60
    assert current["cost_reserved"] == 4


def test_new_paid_reservation_requires_scope_approved_plan(tmp_path):
    db = _init(tmp_path)
    finding = researchctl.upsert_finding(db, _finding())
    with pytest.raises(researchctl.InputError, match="user-approved current plan"):
        researchctl.reserve_budget(db, {
            "idempotency_key": "no-plan", "finding_id": finding["id"],
            "requested_asr_seconds": 1, "requested_cost": 0.1,
        })


def test_idempotency_key_can_never_charge_twice(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    reservation = {
        "idempotency_key": "same-media-model-options",
        "finding_id": finding["id"],
        "requested_asr_seconds": 20.25,
        "requested_cost": 2.5,
    }
    first = researchctl.reserve_budget(db, reservation)
    replay = researchctl.reserve_budget(db, reservation)
    assert first["id"] == replay["id"]
    assert replay["idempotent_replay"] is True
    settlement = {
        "idempotency_key": reservation["idempotency_key"],
        "status": "settled",
        "charged_asr_seconds": 18.5,
        "charged_cost": 2,
        "provider_task_id": "provider-1",
    }
    researchctl.settle_budget(db, settlement)
    settled_replay = researchctl.settle_budget(db, settlement)
    assert settled_replay["idempotent_replay"] is True
    budget = researchctl.status(db)["gate"]["budget"]
    assert budget["asr_seconds_reserved"] == 0
    assert budget["asr_seconds_spent"] == 18.5
    assert budget["cost_spent"] == 2
    with pytest.raises(researchctl.InputError):
        researchctl.reserve_budget(db, {**reservation, "requested_cost": 3})


def test_media_request_fingerprint_deduplicates_different_keys(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    first = researchctl.reserve_budget(db, {
        "idempotency_key": "caller-key-one",
        "finding_id": finding["id"],
        "model": "paraformer-v2",
        "options": {"language": "zh"},
        "requested_asr_seconds": 20,
        "requested_cost": 1,
    })
    replay = researchctl.reserve_budget(db, {
        "idempotency_key": "caller-key-two",
        "finding_id": finding["id"],
        "model": "paraformer-v2",
        "options": {"language": "zh"},
        "requested_asr_seconds": 20,
        "requested_cost": 1,
    })
    assert replay["id"] == first["id"]
    assert replay["replayed_by_request_fingerprint"] is True
    assert researchctl.status(db)["counts"]["attempts"] == 1


def test_unknown_attempt_holds_reservation_and_blocks_gate(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    researchctl.reserve_budget(db, {
        "idempotency_key": "uncertain-provider-task",
        "finding_id": finding["id"],
        "requested_asr_seconds": 10,
        "requested_cost": 1,
    })
    result = researchctl.settle_budget(db, {
        "idempotency_key": "uncertain-provider-task",
        "status": "unknown",
        "provider_task_id": "task-maybe-running",
    })
    assert result["status"] == "unknown"
    assert researchctl.evaluate_gate(db)["checks"]["budget_sane"] is False
    researchctl.settle_budget(db, {
        "idempotency_key": "uncertain-provider-task",
        "status": "released",
        "provider_task_id": "task-maybe-running",
    })
    assert researchctl.status(db)["gate"]["budget"]["asr_seconds_reserved"] == 0


def test_note_consumption_and_gate_failure_then_success(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    before = researchctl.evaluate_gate(db)
    assert before["ok"] is False
    assert before["checks"] == {
        "search_plan_approved": True,
        "decision_contract_confirmed": False,
        "decision_revision_intact": False,
        "decision_inputs_current": False,
        "decision_uses_current_plan": False,
        "all_findings_disposed": False,
        "evidence_present": False,
        "critical_claims_sufficient": False,
        "production_poc_passed": True,
        "production_recommendation_present": True,
        "production_rationale_evidence_sufficient": True,
        "selected_candidate_canonical": True,
        "canonical_references_resolve": True,
        "declared_artifacts_current": True,
        "requested_delivery_shape_valid": True,
        "budget_sane": True,
    }

    cluster = researchctl.upsert_evidence_cluster(db, {
        "label": "Independent measured production evidence",
        "source_fingerprints": [finding["fingerprint"]],
        "independent_source_count": 1,
        "members": [{"source_fingerprint": finding["fingerprint"],
                     "independence_key": "author:production-walkthrough",
                     "quote": "The measured latency meets the target.",
                     "locator": "transcript 00:10-00:24"}],
    })
    claim = researchctl.upsert_claim(db, {
        "text": "The selected workflow meets the confirmed latency target.",
        "critical": True,
        "sufficiency": "sufficient",
        "required_evidence_count": 1,
        "evidence_cluster_ids": [cluster["id"]],
    })
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"],
        "disposition": "consumed",
        "reason": "Used to validate latency.",
        "claim_ids": [claim["id"]],
    }]})
    candidate_id = _candidate(db, "candidate-demo")
    artifact_id = _poc_artifact(db, tmp_path)
    confirmation_fields = _confirmed_decision_fields(
        db, user_verbatim="确认：月预算 500 元，单条十分钟内，可先小流量上线。",
    )
    researchctl.set_decision(db, {
        **confirmation_fields,
        "requested_status": "production-ready",
        "selected_candidate_id": candidate_id,
        "recommendation": {
            "candidate_id": candidate_id, "name": "candidate-demo",
            "rationale": [{"text": "Measured latency passed.", "sources": [claim["id"]]}],
            "steps": [{"action": "Deploy", "expect": "Latency remains below target",
                       "sources": [claim["id"]], "rollback": "Restore prior version"}],
        },
        "poc": _passed_poc(claim["id"], artifact_id),
    })
    after = researchctl.evaluate_gate(db)
    assert after["ok"] is True
    assert after["status"] == "production-ready"
    assert all(after["checks"].values())
    (tmp_path / "artifacts" / "poc-result.json").unlink()
    stale_poc = researchctl.evaluate_gate(db)
    assert stale_poc["status"] == "blocked"
    assert stale_poc["ok"] is False
    assert stale_poc["checks"]["production_poc_passed"] is False
    assert stale_poc["checks"]["declared_artifacts_current"] is False


def test_zero_evidence_can_only_be_delivered_as_blocked(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    researchctl.set_decision(db, {
        **_confirmed_decision_fields(
            db, contract=_decision_contract(),
            user_verbatim="确认当前所有连接器均不可用，先交付解阻清单。",
        ),
        "requested_status": "production-ready", "task_type": "implementation",
        "recommendation": None, "blockers": ["No authorized connector is available."],
        "next_research": [{"action": "Authorize one primary-source connector."}],
    })
    gate = researchctl.evaluate_gate(db)
    assert gate["ok"] is True
    assert gate["status"] == "blocked"
    assert gate["checks"]["all_findings_disposed"] is True
    assert gate["checks"]["evidence_present"] is False
    out = tmp_path / "blocked-delivery"
    researchctl.export_state(db, out)
    decision = json.loads((out / "decision.json").read_text())
    assert decision["readiness"] == "blocked"
    assert decision["task_type"] == "research-only"


def test_sufficient_claim_cannot_exceed_recorded_independent_evidence(tmp_path):
    db = _init(tmp_path)
    finding = researchctl.upsert_finding(db, _finding())
    cluster = researchctl.upsert_evidence_cluster(db, {
        "label": "One upstream source", "source_fingerprints": [finding["fingerprint"]],
        "independent_source_count": 1,
        "members": [{"source_fingerprint": finding["fingerprint"],
                     "independence_key": "upstream:one-measurement",
                     "quote": "One measured result.", "locator": "artifact result.json"}],
    })
    with pytest.raises(researchctl.InputError, match="requires 2 independent"):
        researchctl.upsert_claim(db, {
            "text": "Two independent confirmations exist", "critical": True,
            "sufficiency": "sufficient", "required_evidence_count": 2,
            "evidence_cluster_ids": [cluster["id"]],
        })
    with pytest.raises(researchctl.InputError, match="unique evidence member"):
        researchctl.upsert_evidence_cluster(db, {
            "label": "Inflated count", "source_fingerprints": [finding["fingerprint"]],
            "independent_source_count": 0,
            "members": [{
                "source_fingerprint": finding["fingerprint"],
                "independence_key": "upstream:one-measurement",
                "quote": "One measured result.", "locator": "artifact result.json",
            }],
        })
    with pytest.raises(researchctl.InputError, match="reference findings"):
        researchctl.upsert_evidence_cluster(db, {
            "label": "Invented source", "source_fingerprints": ["not-in-database"],
            "members": [{"source_fingerprint": "not-in-database",
                         "independence_key": "unknown:invented",
                         "quote": "Invented", "locator": "none"}],
        })


def test_decision_cannot_inject_claims_and_excluded_sources_cannot_support_production(tmp_path):
    db = _init(tmp_path)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    cluster = researchctl.upsert_evidence_cluster(db, {
        "label": "Measured evidence", "source_fingerprints": [finding["fingerprint"]],
        "independent_source_count": 1,
        "members": [{"source_fingerprint": finding["fingerprint"],
                     "independence_key": "artifact:measured-latency",
                     "quote": "The measured latency meets the target.",
                     "locator": "result.json latency_ms"}],
    })
    claim = researchctl.upsert_claim(db, {
        "text": "The measured latency meets the target.", "critical": True,
        "sufficiency": "sufficient", "required_evidence_count": 1,
        "evidence_cluster_ids": [cluster["id"]],
    })
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "consumed",
        "reason": "Supports measured latency", "claim_ids": [claim["id"]],
    }]})
    candidate_id = _candidate(db)
    artifact_id = _poc_artifact(db, tmp_path)
    researchctl.set_decision(db, {
        **_confirmed_decision_fields(db, user_verbatim="确认按实测证据进入生产。"),
        "requested_status": "production-ready", "task_type": "implementation",
        "selected_candidate_id": candidate_id,
        "recommendation": {
            "candidate_id": candidate_id, "name": "Measured option",
            "rationale": [{"text": "Latency passed.", "sources": [claim["id"]]}],
            "steps": [{"action": "Deploy", "expect": "Latency stays within target",
                       "sources": [claim["id"]], "rollback": "Restore prior version"}],
        },
        "poc": _passed_poc(claim["id"], artifact_id),
        "claims": [{"id": "clm_fabricated", "text": "Invented", "critical": True,
                    "sufficiency": "sufficient", "sources": ["evc_fabricated"]}],
        "evidence_clusters": [{"id": "evc_fabricated"}],
    })
    assert researchctl.evaluate_gate(db)["status"] == "production-ready"
    out = tmp_path / "canonical-delivery"
    researchctl.export_state(db, out)
    exported = json.loads((out / "decision.json").read_text())
    assert {item["id"] for item in exported["claims"]} == {claim["id"]}
    assert {item["id"] for item in exported["evidence_clusters"]} == {cluster["id"]}

    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "excluded",
        "reason": "The source was later found inapplicable.",
    }]})
    downgraded = researchctl.evaluate_gate(db)
    assert downgraded["status"] == "blocked"
    assert downgraded["ok"] is False
    assert downgraded["checks"]["decision_inputs_current"] is False
    assert downgraded["checks"]["evidence_present"] is False
    assert downgraded["checks"]["critical_claims_sufficient"] is False


@pytest.mark.parametrize("requested,expected", [
    ("blocked", "blocked"),
    ("production-ready", "pilot-only"),
])
def test_consistent_nonproduction_decision_is_deliverable(tmp_path, requested, expected):
    db = _init(tmp_path, require_critical_claims=True)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "consumed",
        "reason": "Reviewed; production proof is still missing",
    }]})
    researchctl.set_decision(db, {
        **_confirmed_decision_fields(db, user_verbatim="确认当前证据缺口和试点边界。"),
        "requested_status": requested,
        "title": "Blocked decision", "summary": "Critical production proof is missing.",
        "task_type": "implementation", "recommendation": None,
        "blockers": ["Critical production proof is missing."],
        "to_test": [{"action": "Run the missing production proof."}],
    })
    gate = researchctl.evaluate_gate(db)
    assert gate["ok"] is True and gate["status"] == expected
    assert gate["checks"]["critical_claims_sufficient"] is False
    out = tmp_path / "delivery"
    researchctl.export_state(db, out)
    exported = json.loads((out / "decision.json").read_text())
    assert exported["readiness"] == expected
    assert exported["task_type"] == ("research-only" if expected == "blocked" else "implementation")


def test_gate_downgrades_incomplete_typed_travel_and_forecast_decisions(tmp_path):
    travel_db = _init(
        tmp_path / "travel", run_id="run_travel", profile="travel",
        require_critical_claims=False, asr_seconds_limit=0, asr_cost_limit=0,
    )
    _approve_plan(travel_db)
    travel_finding = researchctl.upsert_finding(travel_db, _finding())
    researchctl.acknowledge_notes(travel_db, {"items": [{
        "finding_id": travel_finding["id"], "disposition": "consumed", "reason": "Travel input",
    }]})
    candidate_id = _candidate(travel_db, "Incomplete itinerary")
    researchctl.set_decision(travel_db, {
        **_confirmed_decision_fields(travel_db),
        "requested_status": "production-ready", "task_type": "itinerary",
        "selected_candidate_id": candidate_id,
        "recommendation": {
            "candidate_id": candidate_id, "name": "Incomplete itinerary",
            "rationale": [{"text": "Candidate route", "sources": [travel_finding["id"]]}],
        },
    })
    travel_gate = researchctl.evaluate_gate(travel_db)
    assert travel_gate["status"] == "pilot-only"
    assert travel_gate["checks"]["requested_delivery_shape_valid"] is False

    forecast_db = _init(
        tmp_path / "forecast", run_id="run_forecast", profile="policy-forecast",
        require_critical_claims=False, asr_seconds_limit=0, asr_cost_limit=0,
    )
    _approve_plan(forecast_db)
    forecast_finding = researchctl.upsert_finding(forecast_db, _finding())
    researchctl.acknowledge_notes(forecast_db, {"items": [{
        "finding_id": forecast_finding["id"], "disposition": "consumed", "reason": "Forecast input",
    }]})
    researchctl.set_decision(forecast_db, {
        **_confirmed_decision_fields(forecast_db),
        "requested_status": "pilot-only", "task_type": "forecast",
        "recommendation": None,
    })
    forecast_gate = researchctl.evaluate_gate(forecast_db)
    assert forecast_gate["status"] == "blocked"
    assert forecast_gate["checks"]["requested_delivery_shape_valid"] is False


def test_gate_rejects_noncanonical_execution_sources(tmp_path):
    db = _init(tmp_path, require_critical_claims=False)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "consumed", "reason": "Reviewed",
    }]})
    researchctl.set_decision(db, {
        **_confirmed_decision_fields(db),
        "requested_status": "pilot-only", "task_type": "implementation",
        "recommendation": None,
        "to_test": [{"action": "Run a pilot", "sources": ["not-a-canonical-id"]}],
    })
    gate = researchctl.evaluate_gate(db)
    assert gate["ok"] is False
    assert gate["status"] == "blocked"
    assert gate["checks"]["canonical_references_resolve"] is False


def test_exclusion_requires_reason_and_counts_as_consumed(tmp_path):
    db = _init(tmp_path, require_critical_claims=False)
    finding = researchctl.upsert_finding(db, _finding())
    with pytest.raises(researchctl.InputError, match="require a non-empty reason"):
        researchctl.acknowledge_notes(db, {"items": [{
            "finding_id": finding["id"], "disposition": "excluded",
        }]})
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "excluded", "reason": "Duplicate repost",
    }]})
    assert researchctl.project_notes(db)["items"] == []
    assert researchctl.project_notes(db, disposition="excluded")["items"][0]["id"] == finding["id"]


def test_project_notes_cursor_is_stable(tmp_path):
    db = _init(tmp_path)
    for value in range(3):
        researchctl.upsert_finding(db, _finding(
            source_url=f"https://example.com/watch?v={value}",
            title=f"Item {value}",
        ))
    page1 = researchctl.project_notes(db, limit=2)
    page2 = researchctl.project_notes(db, cursor=page1["next_cursor"], limit=2)
    assert len(page1["items"]) == 2
    assert page1["remaining"] == 1
    assert len(page2["items"]) == 1
    assert page2["remaining"] == 0
    assert {item["id"] for item in page1["items"]}.isdisjoint(
        {item["id"] for item in page2["items"]}
    )


def test_atomic_exports_are_hashed_and_parseable(tmp_path):
    db = _init(tmp_path)
    researchctl.upsert_finding(db, _finding())
    out = tmp_path / "export"
    delivery = researchctl.export_state(db, out, allow_incomplete=True)
    assert delivery["gate_status"] == "blocked"
    exported = json.loads((out / "delivery-manifest.json").read_text())
    assert exported == delivery
    for name, metadata in delivery["files"].items():
        content = (out / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == metadata["sha256"]
        assert metadata["bytes"] == len(content)
    manifest = json.loads((out / "manifest.v3.json").read_text())
    assert manifest["schema_version"] == 3
    assert manifest["run_id"] == "run_test"
    assert manifest["run"]["id"] == "run_test"
    lines = (out / "findings.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["note"]
    assert researchctl.status(db)["counts"]["deliveries"] == 1


def test_database_is_private_by_default(tmp_path):
    db = _init(tmp_path)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_decision_export_is_public_delivery_shape(tmp_path):
    db = _init(tmp_path, require_critical_claims=False)
    _approve_plan(db)
    finding = researchctl.upsert_finding(db, _finding())
    cluster = researchctl.upsert_evidence_cluster(db, {
        "label": "Official source", "source_fingerprints": [finding["fingerprint"]],
        "members": [{"source_fingerprint": finding["fingerprint"],
                     "independence_key": "publisher:official-api",
                     "quote": "The operation is supported.", "locator": "API reference #operation"}],
    })
    claim = researchctl.upsert_claim(db, {
        "text": "The official API supports the required operation.",
        "critical": False,
        "sufficiency": "sufficient",
        "evidence_cluster_ids": [cluster["id"]],
    })
    researchctl.acknowledge_notes(db, {"items": [{
        "finding_id": finding["id"], "disposition": "consumed",
        "reason": "Used in the exported API claim", "claim_ids": [claim["id"]],
    }]})
    confirmation_fields = _confirmed_decision_fields(db, user_verbatim="确认预算和时限。")
    researchctl.set_decision(db, {
        **confirmation_fields,
        "requested_status": "pilot-only",
        "task_type": "implementation",
        "title": "Production workflow selection",
        "summary": "One candidate is ready for a measured pilot.",
        "recommendation": None,
        "to_test": [{"action": "Run a bounded integration pilot."}],
    })
    out = tmp_path / "delivery"
    researchctl.export_state(db, out)
    decision = json.loads((out / "decision.json").read_text())
    assert decision["schema_version"] == 3
    assert decision["run_id"] == "run_test"
    assert decision["readiness"] == "pilot-only"
    assert decision["decision_contract"] == {
        **confirmation_fields["decision_contract"],
        "confirmed": True,
        "sha256": hashlib.sha256(confirmation_fields["contract_verbatim"].encode()).hexdigest(),
        "presentation_event_id": confirmation_fields["contract_presentation_event_id"],
        "user_confirmation_event_id": confirmation_fields["user_confirmation_event_id"],
        "verbatim": confirmation_fields["contract_verbatim"],
    }
    exported_claim = next(value for value in decision["claims"] if value["id"] == claim["id"])
    assert exported_claim["sources"] == [cluster["id"]]
    assert decision["evidence_clusters"][0]["id"] == cluster["id"]


def test_cli_type_validation_and_gate_exit_code(tmp_path):
    db = _init(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"channel": 7}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, SCRIPT, "upsert-finding", "--db", str(db), "--input", str(bad)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error_type"] == "InputError"
    gate = subprocess.run(
        [sys.executable, SCRIPT, "gate", "--db", str(db)],
        capture_output=True, text=True,
    )
    assert gate.returncode == 2
    assert json.loads(gate.stdout)["status"] == "blocked"
