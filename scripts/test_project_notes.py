import json
import subprocess
import sys
import os

SCRIPT = os.path.join(os.path.dirname(__file__), "project_notes.py")

_LONG = "原文全文" * 5000  # 2 万字，模拟单行超长 content

_META = {"type": "meta", "channel": "xiaohongshu", "count": 2, "failures": []}
_F1 = {"type": "finding", "id": "xhs-001", "channel": "xiaohongshu", "title": "帖1",
       "headline": "一句话1", "note": "笔记1", "metrics": {"likes": 1},
       "source_url": "https://x/1", "content": _LONG, "raw": {"xsec_token": "T"}}
_F2 = {"type": "finding", "id": "xhs-002", "channel": "xiaohongshu", "title": "帖2",
       "headline": "一句话2", "note": "笔记2", "metrics": {"likes": 2},
       "source_url": "https://x/2", "content": "短原文", "raw": {}}


def _setup(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "findings.xiaohongshu.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in (_META, _F1, _F2)), encoding="utf-8")
    return str(raw)


def _run(*argv):
    r = subprocess.run([sys.executable, SCRIPT, *argv], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_headlines_lists_all(tmp_path):
    out = _run("--raw-dir", _setup(tmp_path), "--mode", "headlines")
    assert "xhs-001\t一句话1" in out and "xhs-002\t一句话2" in out


def test_notes_excludes_content(tmp_path):
    out = _run("--raw-dir", _setup(tmp_path), "--mode", "notes")
    assert "笔记1" in out and "笔记2" in out
    assert "原文全文" not in out  # content 绝不进通读流


def test_get_returns_full_record(tmp_path):
    out = _run("--raw-dir", _setup(tmp_path), "--mode", "get", "--id", "xhs-001")
    rec = json.loads(out)
    assert rec["content"] == _LONG and rec["raw"]["xsec_token"] == "T"  # 反查拿得到全文与 raw


def test_get_unknown_id_fails(tmp_path):
    r = subprocess.run([sys.executable, SCRIPT, "--raw-dir", _setup(tmp_path),
                        "--mode", "get", "--id", "gh-999"], capture_output=True, text=True)
    assert r.returncode != 0


def test_stats_counts_only_headline_and_note(tmp_path):
    out = json.loads(_run("--raw-dir", _setup(tmp_path), "--mode", "stats"))
    expect = sum(len(f["headline"]) + len(f["note"]) for f in (_F1, _F2))
    assert out["findings_total"] == 2
    assert out["headline_note_chars"] == expect  # 2 万字 content 不计入熔断口径
    assert out["approx_tokens_conservative"] == expect
    assert out["channels"]["xiaohongshu"]["count"] == 2
