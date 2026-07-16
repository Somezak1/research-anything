import json
import os
import pytest
import finalize_log as F
from finalize_log import finalize


def _lines(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _write(tmp_path, rows):
    p = tmp_path / "findings.xiaohongshu.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    return str(p)


_META0 = {"type": "meta", "channel": "xiaohongshu", "slug": "t", "count": 0,
          "queries": ["q"], "started": "2026-07-11T10:00+08:00", "failures": []}
_F = {"type": "finding", "id": "xhs-001", "note": "n", "content": "c"}


def test_backfills_count_finished_failures(tmp_path):
    path = _write(tmp_path, [_META0, _F, dict(_F, id="xhs-002")])
    meta = finalize(path, failures=["关键词'x'触发验证码"])
    assert meta["count"] == 2
    assert meta["finished"]
    assert meta["failures"] == ["关键词'x'触发验证码"]
    rows = _lines(path)
    assert rows[0]["type"] == "meta" and rows[0]["count"] == 2  # 第 1 行仍是 meta
    assert len(rows) == 3  # findings 原样保留


def test_merges_failures_without_duplicates(tmp_path):
    meta0 = dict(_META0, failures=["占位阶段已记的失败"])
    path = _write(tmp_path, [meta0, _F])
    meta = finalize(path, failures=["占位阶段已记的失败", "新失败"])
    assert meta["failures"] == ["占位阶段已记的失败", "新失败"]


def test_zero_findings_ok(tmp_path):
    path = _write(tmp_path, [_META0])
    meta = finalize(path, failures=["全部关键词无命中"])
    assert meta["count"] == 0 and meta["failures"]


def test_empty_file_fails(tmp_path):
    p = tmp_path / "findings.xiaohongshu.jsonl"
    p.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        finalize(str(p))


def test_first_line_not_meta_fails(tmp_path):
    path = _write(tmp_path, [_F])
    with pytest.raises(SystemExit):
        finalize(path)


def test_malformed_finding_refuses_without_modifying_file(tmp_path):
    path = tmp_path / "findings.xiaohongshu.jsonl"
    original = json.dumps(_META0) + "\n{broken\n"
    path.write_text(original)
    with pytest.raises(SystemExit, match="第2行 JSON 损坏"):
        finalize(str(path))
    assert path.read_text() == original


def test_replace_failure_keeps_original(monkeypatch, tmp_path):
    path = _write(tmp_path, [_META0, _F])
    original = open(path, encoding="utf-8").read()
    monkeypatch.setattr(F.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError, match="crash"):
        finalize(path)
    assert open(path, encoding="utf-8").read() == original
    assert not list(tmp_path.glob(".findings.*.tmp"))
