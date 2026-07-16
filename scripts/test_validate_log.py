import json
from copy import deepcopy
from validate_log import validate_file, missing_channel_problems


def _write(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return str(p)


def _write_v2(tmp_path, name, meta, rec, artifacts=None, manifest=None):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    if manifest is None:
        manifest = {"plan": {"channels": [{
            "name": meta["channel"], "keywords": list(meta["queries"]),
        }]}, "asr_authorization": {
            "authorized": False, "max_hours": 0, "max_cost_cny": 0,
        }}
    else:
        manifest = deepcopy(manifest)
        manifest.setdefault("asr_authorization", {
            "authorized": False, "max_hours": 0, "max_cost_cny": 0,
        })
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    for relative_path, text in (artifacts or {}).items():
        artifact_path = tmp_path / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(text, encoding="utf-8")
    return _write(raw_dir, name, [meta, rec])


_META = {"type": "meta", "channel": "xiaohongshu", "slug": "t", "count": 1,
         "queries": ["视频转文字"], "failures": []}
_FINDING = {"type": "finding", "id": "xhs-001", "ts": "2026-07-11T10:30+08:00",
            "channel": "xiaohongshu", "tool": "mediacrawler/xhs-search", "query": "视频转文字",
            "source_url": "https://x/1", "title": "标题", "headline": "一句话",
            "note": "精华笔记", "metrics": {"likes": 10}, "content": "全文", "raw": {}}


def _capture(**overrides):
    result = {
        "content_sources": ["post"],
        "video": {"present": False, "status": "not_present"},
        "comments": {"status": "not_available", "count": 0,
                     "reason": "测试渠道未返回评论"},
        "images": {"present": False, "status": "not_present", "total": 0, "processed": 0},
        "license": {"status": "not_applicable"},
    }
    result.update(overrides)
    return result


def _v2(channel="xiaohongshu", finding=None):
    prefix = {"xiaohongshu": "xhs", "douyin": "dy", "bilibili": "bili",
              "youtube": "yt", "twitter": "tw", "github": "gh"}[channel]
    meta = dict(_META, channel=channel, schema_version=2,
                started="2026-07-11T10:00+08:00", finished="2026-07-11T10:10+08:00",
                skipped=[])
    rec = deepcopy(finding or _FINDING)
    rec.update(id=f"{prefix}-001", channel=channel)
    return meta, rec


def test_valid_file_passes(tmp_path):
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [_META, _FINDING])
    assert validate_file(path) == []


def test_missing_meta_first_line(tmp_path):
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [_FINDING, _FINDING])
    problems = validate_file(path)
    assert any("type 必须是 'meta'" in p for p in problems)


def test_missing_required_field(tmp_path):
    bad = dict(_FINDING); del bad["content"]
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [_META, bad])
    problems = validate_file(path)
    assert any("content" in p for p in problems)


def test_missing_raw_flagged(tmp_path):
    bad = dict(_FINDING); del bad["raw"]
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [_META, bad])
    problems = validate_file(path)
    assert any("raw" in p for p in problems)


def test_empty_raw_dict_ok(tmp_path):
    ok = dict(_FINDING, raw={})  # 必填但可空 {}
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [_META, ok])
    assert validate_file(path) == []


def test_empty_required_field_flagged(tmp_path):
    bad = dict(_FINDING, note="")
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [_META, bad])
    problems = validate_file(path)
    assert any("note" in p for p in problems)


def test_duplicate_id(tmp_path):
    meta = dict(_META, count=2)
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [meta, _FINDING, dict(_FINDING)])
    problems = validate_file(path)
    assert any("id 重复" in p for p in problems)


def test_id_prefix_mismatch_flagged(tmp_path):
    bad = dict(_FINDING, id="xia-001")  # workflow.js 旧 bug 风格的前缀，应被拦
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [_META, bad])
    problems = validate_file(path)
    assert any("前缀应为 'xhs-'" in p for p in problems)


def test_unknown_channel_flagged(tmp_path):
    meta = dict(_META, channel="xhs")  # 非标准名（缩写不是渠道名）
    bad = dict(_FINDING, channel="xhs")
    path = _write(tmp_path, "findings.xhs.jsonl", [meta, bad])
    problems = validate_file(path)
    assert any("未知渠道名" in p for p in problems)


def test_channel_mismatch(tmp_path):
    other = dict(_FINDING, id="xhs-002", channel="douyin")
    meta = dict(_META, count=2)
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [meta, _FINDING, other])
    problems = validate_file(path)
    assert any("不一致" in p for p in problems)


def test_filename_channel_mismatch_is_rejected(tmp_path):
    meta = dict(_META, channel="web")
    rec = dict(_FINDING, id="web-001", channel="web")
    path = _write(tmp_path, "findings.twitter.jsonl", [meta, rec])
    problems = validate_file(path)
    assert any("文件名渠道 'twitter' 与 meta.channel 'web' 不一致" in p for p in problems)


def test_count_mismatch(tmp_path):
    meta = dict(_META, count=5)
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [meta, _FINDING])
    problems = validate_file(path)
    assert any("不符" in p for p in problems)


def test_zero_count_without_failures_flagged(tmp_path):
    meta = dict(_META, count=0, failures=[])
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [meta])
    problems = validate_file(path)
    assert any("零结果必须申报原因" in p for p in problems)


def test_zero_count_with_failures_passes(tmp_path):
    meta = dict(_META, count=0, failures=["twitter 账号未配置，搜索静默返回空"], channel="twitter")
    path = _write(tmp_path, "findings.twitter.jsonl", [meta])
    assert validate_file(path) == []


def test_empty_file(tmp_path):
    p = tmp_path / "findings.xiaohongshu.jsonl"
    p.write_text("", encoding="utf-8")
    problems = validate_file(str(p))
    assert any("为空" in p for p in problems)


def test_malformed_json_line(tmp_path):
    p = tmp_path / "findings.xiaohongshu.jsonl"
    p.write_text(json.dumps(_META) + "\n{not json}\n", encoding="utf-8")
    problems = validate_file(str(p))
    assert any("不是合法 JSON" in p for p in problems)


def test_meta_and_finding_must_be_objects(tmp_path):
    meta_path = tmp_path / "meta-list.jsonl"
    meta_path.write_text("[]\n", encoding="utf-8")
    assert any("meta 必须是 JSON 对象" in problem for problem in validate_file(str(meta_path)))

    finding_path = tmp_path / "finding-list.jsonl"
    finding_path.write_text(json.dumps(_META) + "\n[]\n", encoding="utf-8")
    assert any("finding 必须是 JSON 对象" in problem for problem in validate_file(str(finding_path)))


def test_required_field_types_urls_and_timestamps_are_validated(tmp_path):
    bad = dict(
        _FINDING,
        ts="not-a-date",
        tool=[],
        source_url=7,
        title=[],
        headline="x" * 121,
        note={},
        metrics=[],
        content="   ",
        raw="not-an-object",
    )
    path = _write(tmp_path, "bad-types.jsonl", [_META, bad])
    problems = validate_file(path)
    for field in ("ts", "tool", "source_url", "title", "note", "content", "metrics", "raw", "headline"):
        assert any(field in problem for problem in problems), (field, problems)


def test_explicit_truncation_marker_is_rejected(tmp_path):
    bad = dict(_FINDING, raw={"readme_truncated": True})
    path = _write(tmp_path, "truncated.jsonl", [_META, bad])
    assert any("内容被截断" in problem for problem in validate_file(path))


def test_missing_channel_reported(tmp_path):
    _write(tmp_path, "findings.xiaohongshu.jsonl", [_META, _FINDING])
    problems = missing_channel_problems(str(tmp_path), ["xiaohongshu", "douyin"])
    assert len(problems) == 1 and "findings.douyin.jsonl" in problems[0]


def test_schema_v2_xhs_image_capture_passes(tmp_path):
    meta, rec = _v2()
    rec["raw"] = {"type": "normal", "image_list": ["https://x/1.jpg", "https://x/2.jpg"]}
    rec["capture"] = _capture(
        content_sources=["post", "ocr"],
        images={"present": True, "status": "ocr", "processed": 2, "total": 2,
                "artifact": "artifacts/xhs-001.ocr.txt"},
    )
    rec["content"] = "正文\n图一文字\n图二文字"
    path = _write_v2(
        tmp_path, "findings.xiaohongshu.jsonl", meta, rec,
        {"artifacts/xhs-001.ocr.txt": "图一文字\n图二文字"},
    )
    assert validate_file(path) == []


def test_schema_v2_xhs_image_total_must_match_raw_images(tmp_path):
    meta, rec = _v2()
    rec["raw"] = {"type": "normal", "image_list": "https://x/1.jpg,https://x/2.jpg"}
    rec["capture"] = _capture(
        content_sources=["post", "ocr"],
        images={"present": True, "status": "ocr", "processed": 1, "total": 1,
                "artifact": "artifacts/xhs-001.ocr.txt"},
    )
    rec["content"] = "正文\n只处理了一张图"
    path = _write_v2(
        tmp_path, "findings.xiaohongshu.jsonl", meta, rec,
        {"artifacts/xhs-001.ocr.txt": "只处理了一张图"},
    )
    assert any("实际图片数 2 不符" in problem for problem in validate_file(path))


def test_schema_v2_xhs_video_cover_does_not_force_image_ocr(tmp_path):
    meta, rec = _v2()
    rec["raw"] = {
        "type": "video", "video_url": "https://x/1.mp4",
        "image_list": "https://x/cover.jpg",
    }
    rec["capture"] = _capture(
        video={"present": True, "status": "failed", "error": "未获付费 ASR 授权"},
    )
    path = _write_v2(tmp_path, "findings.xiaohongshu.jsonl", meta, rec)
    assert validate_file(path) == []


def test_schema_v2_requires_capture_but_legacy_does_not(tmp_path):
    legacy_path = _write(tmp_path, "legacy.jsonl", [_META, _FINDING])
    assert validate_file(legacy_path) == []

    meta, rec = _v2()
    v2_path = _write_v2(tmp_path, "v2.jsonl", meta, rec)
    problems = validate_file(v2_path)
    assert any("必须有 capture 对象" in problem for problem in problems)


def test_explicit_manifest_prevents_legacy_schema_bypass(tmp_path):
    legacy_path = _write(tmp_path, "legacy.jsonl", [_META, _FINDING])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"plan": {"channels": [{
        "name": "xiaohongshu", "keywords": ["视频转文字"],
    }]}, "asr_authorization": {
        "authorized": False, "max_hours": 0, "max_cost_cny": 0,
    }}, ensure_ascii=False), encoding="utf-8")
    problems = validate_file(legacy_path, str(manifest))
    assert any("正式流程只接受 meta.schema_version=2" in problem for problem in problems)


def test_schema_v2_bilibili_requires_subtitle_asr_or_failed_reason(tmp_path):
    meta, rec = _v2("bilibili")
    rec["raw"] = {"video_id": "BV1x"}
    rec["capture"] = _capture()
    path = _write_v2(tmp_path, "findings.bilibili.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("必须取得字幕、完成 ASR" in problem for problem in problems)

    rec["capture"] = _capture(
        video={"present": True, "status": "failed", "error": "字幕不存在，视频下载被限流"},
        comments={"status": "not_available", "count": 0, "reason": "评论接口被限流"},
    )
    path = _write_v2(tmp_path, "findings.bilibili.jsonl", meta, rec)
    assert validate_file(path) == []


def test_schema_v2_youtube_subtitle_capture_passes(tmp_path):
    meta, rec = _v2("youtube")
    rec["capture"] = _capture(
        content_sources=["subtitle"],
        video={"present": True, "status": "subtitle", "artifact": "artifacts/yt-001.txt"},
    )
    rec["content"] = "字幕全文"
    path = _write_v2(
        tmp_path, "findings.youtube.jsonl", meta, rec,
        {"artifacts/yt-001.txt": "字幕全文"},
    )
    assert validate_file(path) == []


def test_schema_v2_infers_douyin_video_from_raw(tmp_path):
    meta, rec = _v2("douyin")
    rec["raw"] = {"video_download_url": "https://v.example/1.mp4"}
    rec["capture"] = _capture()
    path = _write_v2(tmp_path, "findings.douyin.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("capture.video.present 必须为 true" in problem for problem in problems)


def test_schema_v2_infers_twitter_video_from_media(tmp_path):
    meta, rec = _v2("twitter")
    rec["media"] = [{"type": "video", "url": "https://v.example/1.mp4"}]
    rec["capture"] = _capture()
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("必须取得字幕、完成 ASR" in problem for problem in problems)


def test_schema_v2_social_comment_failure_requires_reason(tmp_path):
    meta, rec = _v2("twitter")
    rec["capture"] = _capture(comments={"status": "not_available", "count": 0})
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("failed/not_available 时必须填写 reason" in problem for problem in problems)


def test_schema_v2_xhs_image_ocr_requires_processed_counts(tmp_path):
    meta, rec = _v2()
    rec["raw"] = {"type": "normal", "image_list": ["https://x/1.jpg"]}
    rec["capture"] = _capture(images={"present": True, "status": "ocr"})
    path = _write_v2(tmp_path, "findings.xiaohongshu.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("processed/total" in problem for problem in problems)


def test_schema_v2_github_license_unknown_requires_reason(tmp_path):
    meta, rec = _v2("github")
    rec["source_url"] = "https://github.com/acme/repo"
    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["视频转文字"]},
        "category": {"status": "completed", "count": 1, "proof": ["https://github.com/topics/asr"]},
        "related": {"status": "completed", "count": 1, "proof": [{
            "from": "https://github.com/acme/repo",
            "found": "https://github.com/openai/whisper", "via": "readme",
        }]},
    }
    rec["capture"] = _capture(
        comments={"status": "not_applicable", "count": 0},
        license={"status": "unknown"},
    )
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("license.status=unknown 时必须填写 reason" in problem for problem in problems)

    rec["capture"]["license"]["reason"] = "仓库没有 LICENSE 文件"
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    assert validate_file(path) == []


def test_schema_v2_github_requires_three_discovery_routes(tmp_path):
    meta, rec = _v2("github")
    rec["capture"] = _capture(
        comments={"status": "not_applicable", "count": 0},
        license={"status": "verified", "spdx": "MIT",
                 "source": "https://github.com/acme/repo/blob/main/LICENSE",
                 "artifact": "artifacts/gh-001.license.txt"},
        content_sources=["readme", "license"],
    )
    rec["source_url"] = "https://github.com/acme/repo"
    rec["content"] = "README\nMIT License"
    artifacts = {"artifacts/gh-001.license.txt": "MIT License"}
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec, artifacts)
    assert any("discovery" in problem for problem in validate_file(path))

    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["视频转文字"]},
        "category": {"status": "completed", "count": 1, "proof": ["https://github.com/topics/asr"]},
        "related": {"status": "failed", "count": 0, "proof": [], "reason": "API 限流"},
    }
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec, artifacts)
    assert validate_file(path) == []


def test_unknown_schema_version_is_rejected(tmp_path):
    meta = dict(_META, schema_version=3)
    path = _write(tmp_path, "findings.xiaohongshu.jsonl", [meta, _FINDING])
    problems = validate_file(path)
    assert any("不支持的 meta.schema_version" in problem for problem in problems)


def test_schema_v2_requires_complete_meta(tmp_path):
    meta, rec = _v2("twitter")
    del meta["finished"]
    rec["capture"] = _capture()
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("meta 缺字段：finished" in problem for problem in problems)


def test_schema_v2_manifest_and_query_coverage(tmp_path):
    meta, rec = _v2("twitter")
    meta["queries"] = ["视频转文字", "漏掉的计划词"]
    rec["capture"] = _capture()
    manifest = {"plan": {"channels": [{
        "name": "twitter", "keywords": ["视频转文字", "漏掉的计划词"],
    }]}}
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=manifest)
    problems = validate_file(path)
    assert any("无 finding 命中" in problem and "漏掉的计划词" in problem for problem in problems)

    meta["skipped"] = ["漏掉的计划词：平台搜索无结果"]
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=manifest)
    assert validate_file(path) == []


def test_schema_v2_query_skip_requires_a_concrete_reason(tmp_path):
    meta, rec = _v2("twitter")
    meta["queries"] = ["视频转文字", "q2"]
    meta["skipped"] = ["q2"]
    rec["capture"] = _capture()
    manifest = {"plan": {"channels": [{
        "name": "twitter", "keywords": ["视频转文字", "q2"],
    }]}}
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=manifest)
    problems = validate_file(path)
    assert any("未写具体原因" in problem and "q2" in problem for problem in problems)

    meta["skipped"] = ["q2：跳过"]
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=manifest)
    problems = validate_file(path)
    assert any("未写具体原因" in problem and "q2" in problem for problem in problems)

    for generic in ("已跳过", "跳过了", "因故跳过", "搜索失败"):
        meta["skipped"] = [f"q2：{generic}"]
        path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=manifest)
        assert any("未写具体原因" in problem and "q2" in problem
                   for problem in validate_file(path))


def test_schema_v2_manifest_requires_every_planned_keyword_in_meta(tmp_path):
    meta, rec = _v2("twitter")
    rec["capture"] = _capture()
    manifest = {"plan": {"channels": [{
        "name": "twitter", "keywords": ["视频转文字", "计划但未记录"],
    }]}}
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=manifest)
    problems = validate_file(path)
    assert any("计划关键词未出现在 meta.queries" in problem for problem in problems)


def test_schema_v2_meta_queries_cannot_exceed_manifest_plan(tmp_path):
    meta, rec = _v2("twitter")
    meta["queries"] = ["视频转文字", "计划外加词"]
    meta["skipped"] = ["计划外加词：无结果"]
    rec["capture"] = _capture()
    manifest = {"plan": {"channels": [{
        "name": "twitter", "keywords": ["视频转文字"],
    }]}}
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=manifest)
    problems = validate_file(path)
    assert any("manifest 计划外关键词" in problem for problem in problems)


def test_schema_v2_finding_query_must_be_declared_in_meta(tmp_path):
    meta, rec = _v2("twitter")
    meta["skipped"] = ["视频转文字：平台无结果"]
    rec["query"] = "未申报关键词"
    rec["capture"] = _capture()
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("finding.query 未在 meta.queries 中申报" in problem for problem in problems)


def test_validate_file_accepts_explicit_manifest(tmp_path):
    meta, rec = _v2("twitter")
    rec["capture"] = _capture()
    wrong = {"plan": {"channels": [{"name": "twitter", "keywords": ["错误计划"]}]}}
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, manifest=wrong)
    explicit = tmp_path / "approved-manifest.json"
    explicit.write_text(json.dumps({"plan": {"channels": [{
        "name": "twitter", "keywords": ["视频转文字"],
    }]}, "asr_authorization": {
        "authorized": False, "max_hours": 0, "max_cost_cny": 0,
    }}, ensure_ascii=False), encoding="utf-8")
    assert validate_file(path, str(explicit)) == []


def test_schema_v2_captured_comments_require_valid_count(tmp_path):
    meta, rec = _v2("twitter")
    rec["capture"] = _capture(
        content_sources=["post", "comments"],
        comments={"status": "captured", "count": 0,
                  "artifact": "artifacts/tw-001.comments.txt"},
    )
    rec["content"] = "正文\n评论文本"
    artifacts = {"artifacts/tw-001.comments.txt": "评论文本"}
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, artifacts)
    assert any("count 必须为 1..10" in problem for problem in validate_file(path))

    rec["capture"]["comments"].update(count=11)
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, artifacts)
    assert any("count 必须为 1..10" in problem for problem in validate_file(path))


def test_schema_v2_non_captured_comments_require_zero_count(tmp_path):
    meta, rec = _v2("twitter")
    rec["capture"] = _capture(
        comments={"status": "not_available", "count": 1, "reason": "接口不可用"},
    )
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("非 captured 状态时 count 必须为 0" in problem for problem in problems)


def test_schema_v2_captured_comments_require_artifact_and_shortfall_reason(tmp_path):
    meta, rec = _v2("twitter")
    rec["capture"] = _capture(
        content_sources=["post", "comments"],
        comments={"status": "captured", "count": 3,
                  "artifact": "artifacts/tw-001.comments.txt"},
    )
    rec["content"] = "正文\n三条评论"
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("artifact 文件不存在" in problem for problem in problems)
    assert any("少于 10 条" in problem for problem in problems)

    rec["capture"]["comments"]["reason"] = "该推文实际只返回 3 条回复"
    artifacts = {"artifacts/tw-001.comments.txt": "三条评论"}
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec, artifacts)
    assert validate_file(path) == []


def test_schema_v2_success_artifact_must_be_under_artifacts_and_in_content(tmp_path):
    meta, rec = _v2("youtube")
    rec["capture"] = _capture(
        content_sources=["subtitle"],
        video={"present": True, "status": "subtitle", "artifact": "raw/yt-001.txt"},
    )
    rec["content"] = "字幕全文"
    path = _write_v2(tmp_path, "findings.youtube.jsonl", meta, rec,
                     {"raw/yt-001.txt": "字幕全文"})
    assert any("必须位于同一 OUT_DIR" in problem for problem in validate_file(path))

    rec["capture"]["video"]["artifact"] = "artifacts/yt-001.txt"
    path = _write_v2(tmp_path, "findings.youtube.jsonl", meta, rec,
                     {"artifacts/yt-001.txt": "另一份字幕"})
    assert any("必须完整写入 finding.content" in problem for problem in validate_file(path))


def test_schema_v2_github_completed_discovery_proof_is_countable(tmp_path):
    meta, rec = _v2("github")
    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 0, "proof": []},
        "category": {"status": "completed", "count": 2,
                     "proof": ["https://github.com/topics/asr"]},
        "related": {"status": "failed", "count": 0, "proof": [], "reason": "API 限流"},
    }
    rec["capture"] = _capture(
        content_sources=["readme"],
        comments={"status": "not_applicable", "count": 0},
        license={"status": "unknown", "reason": "仓库没有 LICENSE 文件"},
    )
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("completed 时 count 必须大于 0" in problem for problem in problems)
    assert any("count 必须等于 proof 条数" in problem for problem in problems)


def test_schema_v2_github_discovery_proof_must_match_route(tmp_path):
    meta, rec = _v2("github")
    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["keyword"]},
        "category": {"status": "completed", "count": 1,
                     "proof": ["https://github.com/acme/repo"]},
        "related": {"status": "completed", "count": 1,
                    "proof": ["https://github.com/topics/asr"]},
    }
    rec["source_url"] = "https://github.com/acme/repo"
    rec["capture"] = _capture(
        content_sources=["readme"],
        comments={"status": "not_applicable", "count": 0},
        license={"status": "unknown", "reason": "仓库没有根许可证文件"},
    )
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    problems = validate_file(path)
    assert sum("proof 与该发现路线不匹配" in problem for problem in problems) == 3

    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["视频转文字"]},
        "category": {"status": "completed", "count": 1,
                     "proof": ["https://github.com/topics/speech-recognition"]},
        "related": {"status": "failed", "count": 0, "reason": "API 限流"},
    }
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    assert any("failed 时 proof 必须是空数组" in problem for problem in validate_file(path))

    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["xx"]},
        "category": {"status": "completed", "count": 1,
                     "proof": ["https://github.com/topics/speech-recognition"]},
        "related": {"status": "completed", "count": 1, "proof": [{
            "from": "https://github.com/other/parent",
            "found": "https://github.com/openai/whisper", "via": "dependency",
        }]},
    }
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("keyword.proof 必须与 meta.queries 完全一致" in problem for problem in problems)
    assert any("related.proof.from 必须是本日志已入选仓库" in problem for problem in problems)

    meta["discovery"]["keyword"]["proof"] = ["视频转文字"]
    meta["discovery"]["related"]["proof"] = [{
        "from": "https://github.com/acme/repo",
        "found": "https://github.com/ACME/REPO/", "via": "readme",
    }]
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    assert any("proof 与该发现路线不匹配" in problem for problem in validate_file(path))


def test_schema_v2_verified_license_requires_real_file_url_and_body(tmp_path):
    meta, rec = _v2("github")
    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["视频转文字"]},
        "category": {"status": "completed", "count": 1,
                     "proof": ["https://github.com/topics/speech-recognition"]},
        "related": {"status": "completed", "count": 1, "proof": [{
            "from": "https://github.com/acme/repo",
            "found": "https://github.com/openai/whisper", "via": "readme",
        }]},
    }
    rec["source_url"] = "https://github.com/acme/repo"
    rec["content"] = "README only"
    rec["capture"] = _capture(
        content_sources=["readme", "license"],
        comments={"status": "not_applicable", "count": 0},
        license={"status": "verified", "spdx": "MIT",
                 "source": "https://github.com/acme/repo/blob/main/README.md",
                 "artifact": "artifacts/gh-001.license.txt"},
    )
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec,
                     {"artifacts/gh-001.license.txt": "MIT License"})
    problems = validate_file(path)
    assert any("LICENSE/COPYING 文件 URL" in problem for problem in problems)
    assert any("必须完整写入 finding.content" in problem for problem in problems)


def test_schema_v2_github_requires_repository_source_url(tmp_path):
    meta, rec = _v2("github")
    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["视频转文字"]},
        "category": {"status": "completed", "count": 1,
                     "proof": ["https://github.com/topics/speech-recognition"]},
        "related": {"status": "completed", "count": 1, "proof": [{
            "from": "https://github.com/acme/repo",
            "found": "https://github.com/openai/whisper", "via": "readme",
        }]},
    }
    rec["source_url"] = "https://example.com/not-a-github-repo"
    rec["capture"] = _capture(
        content_sources=["readme"],
        comments={"status": "not_applicable", "count": 0},
        license={"status": "unknown", "reason": "仓库没有 LICENSE 文件"},
    )
    path = _write_v2(tmp_path, "findings.github.jsonl", meta, rec)
    problems = validate_file(path)
    assert any("finding.source_url 必须是 GitHub 仓库 URL" in problem for problem in problems)


def test_schema_v2_asr_requires_separate_manifest_authorization(tmp_path):
    meta, rec = _v2("douyin")
    rec["raw"] = {"video_download_url": "https://video.example/1.mp4"}
    rec["content"] = "视频转写全文"
    rec["capture"] = _capture(
        content_sources=["asr"],
        video={"present": True, "status": "asr", "artifact": "artifacts/dy-001.asr.txt"},
    )
    artifacts = {"artifacts/dy-001.asr.txt": "视频转写全文"}
    path = _write_v2(tmp_path, "findings.douyin.jsonl", meta, rec, artifacts)
    problems = validate_file(path)
    assert any("未授权付费 ASR" in problem for problem in problems)

    authorized = {"plan": {"channels": [{
        "name": "douyin", "keywords": ["视频转文字"],
    }]}, "asr_authorization": {
        "authorized": True, "max_hours": 1, "max_cost_cny": 1,
    }}
    path = _write_v2(
        tmp_path, "findings.douyin.jsonl", meta, rec, artifacts, manifest=authorized,
    )
    assert validate_file(path) == []


def test_schema_v2_requires_valid_asr_authorization_record(tmp_path):
    meta, rec = _v2("twitter")
    rec["capture"] = _capture()
    path = _write_v2(tmp_path, "findings.twitter.jsonl", meta, rec)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["asr_authorization"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert any("manifest.asr_authorization" in problem for problem in validate_file(path))


def test_meta_count_must_be_nonnegative_integer(tmp_path):
    for bad_count in ("1", True, -1):
        meta = dict(_META, count=bad_count)
        path = _write(tmp_path, "findings.xiaohongshu.jsonl", [meta, _FINDING])
        assert any("meta.count 必须是非负整数" in problem for problem in validate_file(path))


def test_schema_v2_github_license_must_be_at_repository_root(tmp_path):
    meta, rec = _v2("github")
    meta["discovery"] = {
        "keyword": {"status": "completed", "count": 1, "proof": ["视频转文字"]},
        "category": {"status": "completed", "count": 1,
                     "proof": ["https://github.com/topics/speech-recognition"]},
        "related": {"status": "completed", "count": 1, "proof": [{
            "from": "https://github.com/acme/repo",
            "found": "https://github.com/openai/whisper", "via": "readme",
        }]},
    }
    rec["source_url"] = "https://github.com/acme/repo"
    rec["content"] = "README\nVendored license"
    rec["capture"] = _capture(
        content_sources=["readme", "license"],
        comments={"status": "not_applicable", "count": 0},
        license={"status": "verified", "source":
                 "https://github.com/acme/repo/blob/main/vendor/pkg/LICENSE",
                 "artifact": "artifacts/gh-001.license.txt"},
    )
    path = _write_v2(
        tmp_path, "findings.github.jsonl", meta, rec,
        {"artifacts/gh-001.license.txt": "Vendored license"},
    )
    assert any("LICENSE/COPYING 文件 URL" in problem for problem in validate_file(path))

    rec["capture"]["license"]["source"] = "ftp://github.com/acme/repo/blob/main/LICENSE"
    path = _write_v2(
        tmp_path, "findings.github.jsonl", meta, rec,
        {"artifacts/gh-001.license.txt": "Vendored license"},
    )
    assert any("LICENSE/COPYING 文件 URL" in problem for problem in validate_file(path))
