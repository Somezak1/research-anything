import json
import os
from pathlib import Path

import pytest

from audit_v2 import audit


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")


def _run(tmp_path, large_note=False):
    finding = {
        "type": "finding", "id": "web-001", "channel": "web", "source_url": "https://example.test/a",
        "headline": "A", "note": "x" * (60_000 if large_note else 20),
        "capture": {"video": {"artifact": "artifacts/web-001.txt"}},
    }
    _jsonl(tmp_path / "raw" / "findings.web.jsonl", [
        {"type": "meta", "channel": "web", "count": 1}, finding,
    ])
    _json(tmp_path / "runbook.json", {"default_plan": {"sources": ["web-001"]}})
    _json(tmp_path / "coverage.json", {"video": {"asr": 0, "failed": 0, "subtitle": 0}})
    (tmp_path / "qa.md").write_text("用户原话 A1: ok", encoding="utf-8")
    (tmp_path / "report.html").write_text("asr: 0 failed: 0 subtitle: 0", encoding="utf-8")
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    (tmp_path / "artifacts" / "web-001.txt").write_text("evidence", encoding="utf-8")


def test_clean_minimal_run_has_no_high_or_blocker(tmp_path):
    _run(tmp_path)
    result = audit(str(tmp_path))
    assert result["counts"]["blocker"] == 0
    assert result["counts"]["high"] == 0


def test_project_root_discovers_its_single_nested_run(tmp_path):
    run = tmp_path / "docs" / "research" / "one-run"
    _run(run)
    result = audit(str(tmp_path))
    assert result["out_dir"] == str(run.resolve())
    assert result["requested_out_dir"] == str(tmp_path.resolve())
    assert result["counts"]["blocker"] == 0


def test_missing_or_ambiguous_run_path_fails_explicitly(tmp_path):
    missing = audit(str(tmp_path / "missing"))
    assert missing["issues"][0]["code"] == "missing-run-directory"
    _run(tmp_path / "run-a")
    _run(tmp_path / "run-b")
    ambiguous = audit(str(tmp_path))
    assert ambiguous["issues"][0]["code"] == "ambiguous-run-directory"


def test_large_projection_without_verbatim_gate_is_flagged(tmp_path):
    _run(tmp_path, large_note=True)
    result = audit(str(tmp_path))
    assert any(issue["code"] == "missing-context-authorization" for issue in result["issues"])


def test_duplicate_signed_media_and_bad_reference_are_flagged(tmp_path):
    _run(tmp_path)
    _jsonl(tmp_path / "artifacts" / "asr_ledger.jsonl", [
        {"source": "https://cdn.test/path/media.mp4?sig=a", "status": "SUCCEEDED", "billed_seconds": 20},
        {"source": "https://cdn.test/path/media.mp4?sig=b", "status": "SUCCEEDED", "billed_seconds": 20},
    ])
    _json(tmp_path / "runbook.json", {"default_plan": {"sources": ["C3"]}})
    codes = {issue["code"] for issue in audit(str(tmp_path))["issues"]}
    assert "duplicate-asr-charge" in codes
    assert "invalid-source-reference" in codes


def test_candidate_excluded_in_narrative_cannot_remain_in_runbook(tmp_path):
    _run(tmp_path)
    _json(tmp_path / "runbook.json", {
        "default_plan": {"lodging": {"candidates": ["舟山新城曼居酒店(约¥200)"]},
                         "sources": ["web-001"]},
    })
    (tmp_path / "report.html").write_text("最终决定：曼居离目的地更远，本次不选。", encoding="utf-8")
    codes = {issue["code"] for issue in audit(str(tmp_path))["issues"]}
    assert "retained-excluded-candidate" in codes


def test_newer_input_makes_report_stale(tmp_path):
    _run(tmp_path)
    report = tmp_path / "report.html"
    raw = tmp_path / "raw" / "findings.web.jsonl"
    old = report.stat().st_mtime - 10
    os.utime(report, (old, old))
    os.utime(raw, None)
    assert any(issue["code"] == "stale-report" for issue in audit(str(tmp_path))["issues"])


@pytest.mark.parametrize(("case_path", "expected_codes"), [
    (
        Path("/Users/chenshangweidut/research-waic/docs/research/waic-2026-keynote"),
        {"missing-context-authorization", "invalid-source-reference", "truncated-source"},
    ),
    (
        Path("/Users/chenshangweidut/travel_to_zhoushan/docs/research/zhoushan-family-roadtrip"),
        {"duplicate-asr-charge", "coverage-report-drift", "retained-excluded-candidate"},
    ),
])
def test_known_local_v2_cases(case_path, expected_codes):
    if not case_path.is_dir():
        pytest.skip("private regression case is not present")
    result = audit(str(case_path))
    codes = {issue["code"] for issue in result["issues"]}
    assert expected_codes <= codes
    assert result["production_usable"] is False
