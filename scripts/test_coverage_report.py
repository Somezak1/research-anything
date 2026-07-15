import json

import coverage_report as C


def test_summarize_capture(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    records = [
        {"type": "meta", "schema_version": 2, "channel": "xiaohongshu", "count": 1,
         "queries": ["视频转文字", "语音识别踩坑"],
         "failures": ["关键词'语音识别踩坑'无相关结果"], "skipped": []},
        {"type": "finding", "query": "视频转文字", "capture": {
            "content_sources": ["post_text", "asr", "image_ocr"],
            "video": {"present": True, "status": "asr"},
            "comments": {"status": "captured", "count": 10},
            "images": {"present": True, "status": "ocr", "total": 3, "processed": 3},
            "license": {"status": "not_applicable"},
        }},
    ]
    path = raw / "findings.xiaohongshu.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    report = C.summarize(str(raw))
    assert report["log_schema_versions"] == {"2": 1}
    assert report["overall"]["video"] == {"asr": 1}
    assert report["overall"]["comment_count"] == 10
    assert report["overall"]["query_total"] == 2
    assert report["overall"]["query_with_findings"] == 1
    assert report["overall"]["query_failed_or_skipped"] == 1
    assert report["channels"]["xiaohongshu"]["image_processed"] == 3
    assert report["channels"]["xiaohongshu"]["query_coverage"] == [
        {"query": "视频转文字", "findings": 1, "outcome": "findings"},
        {"query": "语音识别踩坑", "findings": 0, "outcome": "failed_or_skipped"},
    ]
    assert report["channels"]["xiaohongshu"]["failures"] == ["关键词'语音识别踩坑'无相关结果"]
