#!/usr/bin/env python3
"""Validate collection-layer findings JSONL against references/log-format.md.

Fails loud instead of tolerating malformed logs: Stage 2 runs this before Stage 3 reads.
Checks per file: parseable JSONL, first line is meta, required finding fields (incl. raw),
id uniqueness, id prefix matches channel (per log-format.md prefix table), channel is a
canonical name, zero-count files must declare failures, meta.count matches reality.
Logs declaring schema_version=2 must also provide an auditable capture record for every
finding. Logs without schema_version keep the legacy validation rules.
With --channels, also fails when an expected channel file is missing entirely
(a channel agent that died before ever writing must not be silently skipped).
"""
import argparse, glob, json, os, re
from urllib.parse import urlparse

# Required on every finding record (see log-format.md field table).
FINDING_REQUIRED = ["id", "ts", "channel", "tool", "query",
                    "source_url", "title", "headline", "note", "metrics", "content", "raw"]
META_REQUIRED = ["type", "channel", "count"]
V2_META_REQUIRED = ["queries", "started", "finished", "failures", "skipped"]

# Canonical channel -> id prefix (must stay in sync with log-format.md 前缀表 and workflow.js PREFIX).
PREFIX = {"douyin": "dy", "xiaohongshu": "xhs", "zhihu": "zh", "bilibili": "bili",
          "youtube": "yt", "github": "gh", "twitter": "tw", "web": "web"}

SOCIAL_CHANNELS = {"douyin", "xiaohongshu", "zhihu", "bilibili", "youtube", "twitter"}
VIDEO_CHANNELS = {"bilibili", "youtube"}
VIDEO_STATUSES = {"not_present", "subtitle", "asr", "failed"}
COMMENT_STATUSES = {"captured", "not_available", "failed", "not_applicable"}
IMAGE_STATUSES = {"not_present", "ocr", "failed", "not_applicable"}
LICENSE_STATUSES = {"verified", "unknown", "not_applicable"}
DISCOVERY_ROUTES = ("keyword", "category", "related")
LICENSE_NAME = re.compile(r"^(?:licen[cs]e|copying)(?:[._-].*)?$", re.IGNORECASE)
FINDINGS_FILENAME = re.compile(r"^findings\.([^.]+)\.jsonl$")
GITHUB_RESERVED_ROOTS = {
    "about", "apps", "collections", "contact", "customer-stories", "enterprise",
    "events", "explore", "features", "issues", "marketplace", "new", "notifications",
    "orgs", "organizations", "pricing", "pulls", "search", "security", "settings",
    "sponsors", "topics", "trending", "users",
}
GENERIC_QUERY_EXPLANATIONS = {
    "fail", "failed", "skip", "skipped", "失败", "略", "略过", "跳过", "未处理", "未执行",
}
GENERIC_QUERY_EXPLANATION = re.compile(
    r"^(?:(?:已|未|因故|直接|暂时|暂|选择|操作|搜索|抓取|查询)?"
    r"(?:跳过|略过|失败|未执行|未处理)(?:了)?)$",
    re.IGNORECASE,
)


def _has_reason(status_record: dict) -> bool:
    return isinstance(status_record.get("reason"), str) and bool(status_record["reason"].strip())


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nonnegative_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _out_dir_for_log(path: str) -> str:
    log_dir = os.path.dirname(os.path.abspath(path))
    return os.path.dirname(log_dir) if os.path.basename(log_dir) == "raw" else log_dir


def _validate_artifact(path: str, line_no: int, label: str,
                       artifact: object, content: object) -> list:
    """Prove a successful extraction produced text and that text reached content."""
    problems = []
    if not isinstance(artifact, str) or not artifact.strip():
        return [f"第{line_no}行：{label} 成功时必须填写 artifact"]
    if os.path.isabs(artifact):
        return [f"第{line_no}行：{label}.artifact 必须是 OUT_DIR 内的相对路径"]

    out_dir = _out_dir_for_log(path)
    artifacts_dir = os.path.realpath(os.path.join(out_dir, "artifacts"))
    artifact_path = os.path.realpath(os.path.join(out_dir, artifact))
    try:
        inside_artifacts = os.path.commonpath([artifacts_dir, artifact_path]) == artifacts_dir
    except ValueError:
        inside_artifacts = False
    if not inside_artifacts:
        return [f"第{line_no}行：{label}.artifact 必须位于同一 OUT_DIR 的 artifacts/ 下"]
    if not os.path.isfile(artifact_path):
        return [f"第{line_no}行：{label}.artifact 文件不存在：{artifact}"]
    try:
        with open(artifact_path, encoding="utf-8") as f:
            artifact_text = f.read().strip()
    except (OSError, UnicodeError) as e:
        return [f"第{line_no}行：无法读取 {label}.artifact 文本：{e}"]
    if not artifact_text:
        return [f"第{line_no}行：{label}.artifact 文件为空"]
    if not isinstance(content, str) or artifact_text not in content:
        problems.append(f"第{line_no}行：{label}.artifact 的非空文本必须完整写入 finding.content")
    return problems


def _github_repo(url: object) -> tuple[str, str] | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.lower()
    if host in {"github.com", "raw.githubusercontent.com"} and len(parts) >= 2:
        return parts[0].lower(), parts[1].removesuffix(".git").lower()
    if host == "api.github.com" and len(parts) >= 3 and parts[0] == "repos":
        return parts[1].lower(), parts[2].removesuffix(".git").lower()
    return None


def _github_repository_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    return (parsed.scheme == "https" and parsed.netloc.lower() == "github.com"
            and len(parts) == 2 and parts[0].lower() not in GITHUB_RESERVED_ROOTS)


def _github_license_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.lower()
    file_parts = []
    if host == "github.com" and len(parts) >= 5 and parts[2] in {"blob", "raw"}:
        file_parts = parts[4:]
    elif host == "raw.githubusercontent.com" and len(parts) >= 4:
        file_parts = parts[3:]
    elif host == "api.github.com" and len(parts) >= 5 and parts[:1] == ["repos"] and parts[3] == "contents":
        file_parts = parts[4:]
    return bool(len(file_parts) == 1 and LICENSE_NAME.fullmatch(file_parts[0]))


def _valid_discovery_proof(route: str, proof: object) -> bool:
    if route == "related":
        return (isinstance(proof, dict)
                and set(proof) == {"from", "found", "via"}
                and _github_repository_url(proof.get("from"))
                and _github_repository_url(proof.get("found"))
                and _github_repo(proof["from"]) != _github_repo(proof["found"])
                and proof.get("via") in {"readme", "dependency", "related"})
    if not isinstance(proof, str) or not proof.strip():
        return False
    value = proof.strip()
    if route == "keyword" and value.lower() in DISCOVERY_ROUTES:
        return False
    if route == "category":
        parsed = urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        official_category = (parsed.scheme == "https" and parsed.netloc.lower() == "github.com"
                             and len(parts) >= 2 and parts[0] in {"topics", "collections"})
        repo = _github_repo(value)
        awesome_list = _github_repository_url(value) and repo is not None and "awesome" in repo[1]
        return official_category or awesome_list
    return True


def _value_item_count(value: object) -> int:
    """Count MediaCrawler image-list shapes without counting duplicate representations."""
    if isinstance(value, (list, tuple, set)):
        return sum(item not in (None, "", {}, []) for item in value)
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith("["):
            try:
                return _value_item_count(json.loads(text))
            except json.JSONDecodeError:
                pass
        return len([item for item in text.split(",") if item.strip()])
    return 0


def _xhs_expected_image_count(rec: dict) -> int:
    raw = rec.get("raw")
    raw_counts = []
    if isinstance(raw, dict):
        raw_counts = [_value_item_count(raw.get(key)) for key in (
            "image_list", "images", "image_urls", "note_download_url",
        )]
    media = rec.get("media")
    media_count = 0
    if isinstance(media, list):
        media_count = sum(
            isinstance(item, dict)
            and str(item.get("kind", item.get("type", ""))).lower() == "image"
            for item in media
        )
    return max([media_count, *raw_counts], default=0)


def _query_has_explanation(query: str, explanations: list[str]) -> bool:
    for explanation in explanations:
        if query not in explanation:
            continue
        remainder = explanation.replace(query, "", 1)
        remainder = re.sub(r"^[\s'\"“”‘’：:，,。.;；()（）\[\]【】_\-]+", "", remainder)
        normalized = remainder.strip().lower()
        compact = re.sub(r"[\s'\"“”‘’：:，,。.!！;；()（）\[\]【】_\-]+", "", normalized)
        if (len(compact) >= 2 and compact not in GENERIC_QUERY_EXPLANATIONS
                and not GENERIC_QUERY_EXPLANATION.fullmatch(compact)):
            return True
    return False


def _load_manifest(path: str, manifest_path: str | None) -> tuple[dict | None, str | None]:
    chosen = manifest_path or os.path.join(_out_dir_for_log(path), "manifest.json")
    if not os.path.isfile(chosen):
        return None, f"schema_version=2 找不到 manifest.json：{chosen}"
    try:
        with open(chosen, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return None, f"无法读取 manifest.json：{e}"
    if not isinstance(manifest, dict):
        return None, "manifest.json 顶层必须是对象"
    return manifest, None


def _manifest_keywords(manifest: dict, channel: str | None) -> tuple[list, list]:
    problems = []
    plan = manifest.get("plan")
    channels = plan.get("channels") if isinstance(plan, dict) else None
    if not isinstance(channels, list):
        return [], ["manifest.plan.channels 必须是数组"]
    matches = [item for item in channels
               if isinstance(item, dict) and item.get("name") == channel]
    if len(matches) != 1:
        return [], [f"manifest.plan.channels 必须且只能有一个渠道 '{channel}'"]
    keywords = matches[0].get("keywords")
    if (not isinstance(keywords, list) or not keywords
            or any(not isinstance(item, str) or not item.strip() for item in keywords)):
        problems.append(f"manifest 渠道 '{channel}' 的 keywords 必须是非空字符串数组")
        return [], problems
    return keywords, problems


def _manifest_asr_authorized(manifest: dict) -> tuple[bool, list]:
    """Validate the separately approved cloud-ASR spending boundary."""
    problems = []
    authorization = manifest.get("asr_authorization")
    if not isinstance(authorization, dict):
        return False, ["manifest.asr_authorization 必须是对象"]
    authorized = authorization.get("authorized")
    if not isinstance(authorized, bool):
        problems.append("manifest.asr_authorization.authorized 必须是布尔值")
        authorized = False
    max_hours = authorization.get("max_hours")
    max_cost = authorization.get("max_cost_cny")
    if not _is_nonnegative_number(max_hours):
        problems.append("manifest.asr_authorization.max_hours 必须是非负数")
    if not _is_nonnegative_number(max_cost):
        problems.append("manifest.asr_authorization.max_cost_cny 必须是非负数")
    if authorized and _is_nonnegative_number(max_hours) and max_hours == 0:
        problems.append("ASR 获授权时 max_hours 必须大于 0")
    if authorized is False and (
            (_is_nonnegative_number(max_hours) and max_hours != 0)
            or (_is_nonnegative_number(max_cost) and max_cost != 0)):
        problems.append("ASR 未获授权时 max_hours/max_cost_cny 必须均为 0")
    return authorized is True, problems


def _media_has_kind(rec: dict, kind: str) -> bool:
    media = rec.get("media")
    return isinstance(media, list) and any(
        isinstance(item, dict)
        and str(item.get("kind", item.get("type", ""))).lower() == kind
        for item in media
    )


def _raw_has_video(raw: object) -> bool:
    """Recognize common channel-native video markers without treating titles as evidence."""
    if not isinstance(raw, dict):
        return False
    if str(raw.get("type", "")).lower() in {"video", "video_note"}:
        return True
    if str(raw.get("kind", "")).lower() == "video":
        return True
    video_keys = {"video", "videos", "video_url", "video_download_url", "video_play_url", "play_url"}
    for key, value in raw.items():
        if str(key).lower() in video_keys and value not in (None, "", [], {}):
            return True
        if isinstance(value, (dict, list)):
            children = value.values() if isinstance(value, dict) else value
            if any(_raw_has_video(child) for child in children if isinstance(child, dict)):
                return True
    return False


def _is_video_finding(rec: dict, capture: dict) -> bool:
    video = capture.get("video")
    return (
        isinstance(video, dict) and video.get("present") is True
        or _media_has_kind(rec, "video")
        or _raw_has_video(rec.get("raw"))
    )


def _is_xhs_image_finding(rec: dict, capture: dict) -> bool:
    images = capture.get("images")
    if isinstance(images, dict) and images.get("present") is True:
        return True
    raw = rec.get("raw")
    if isinstance(raw, dict):
        # Video image_list is a cover, not a multi-image note that requires OCR.
        if str(raw.get("type", "")).lower() in {"video", "video_note"}:
            return False
        if str(raw.get("type", "")).lower() in {"normal", "image", "images"}:
            return True
        for key in ("image_list", "images", "image_urls", "note_download_url"):
            if raw.get(key) not in (None, "", [], {}):
                return True
    return _media_has_kind(rec, "image")


def _validate_status_record(line_no: int, capture: dict, name: str,
                            allowed: set, problems: list) -> dict | None:
    record = capture.get(name)
    if not isinstance(record, dict):
        problems.append(f"第{line_no}行：capture.{name} 必须是对象")
        return None
    status = record.get("status")
    if status not in allowed:
        choices = "/".join(sorted(allowed))
        problems.append(f"第{line_no}行：capture.{name}.status 必须是 {choices} 之一")
    return record


def validate_capture_v2(path: str, line_no: int, rec: dict,
                        meta_channel: str | None, asr_authorized: bool) -> list:
    """Validate the schema v2 capture/audit contract for one finding."""
    problems = []
    capture = rec.get("capture")
    if not isinstance(capture, dict):
        return [f"第{line_no}行：schema_version=2 的 finding 必须有 capture 对象"]

    sources = capture.get("content_sources")
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(source, str) or not source.strip() for source in sources)):
        problems.append(f"第{line_no}行：capture.content_sources 必须是非空字符串数组")

    video = _validate_status_record(line_no, capture, "video", VIDEO_STATUSES, problems)
    comments = _validate_status_record(line_no, capture, "comments", COMMENT_STATUSES, problems)
    images = _validate_status_record(line_no, capture, "images", IMAGE_STATUSES, problems)
    license_record = _validate_status_record(line_no, capture, "license", LICENSE_STATUSES, problems)

    if video is not None:
        if not isinstance(video.get("present"), bool):
            problems.append(f"第{line_no}行：capture.video.present 必须是布尔值")
        if video.get("status") == "failed" and not (
                isinstance(video.get("error"), str) and video["error"].strip()):
            problems.append(f"第{line_no}行：capture.video.status=failed 时必须填写 error")
        if video.get("status") in {"subtitle", "asr"}:
            problems.extend(_validate_artifact(
                path, line_no, "capture.video", video.get("artifact"), rec.get("content")
            ))
        if video.get("status") == "asr" and not asr_authorized:
            problems.append(f"第{line_no}行：manifest 未授权付费 ASR，capture.video.status 不能是 asr")
        if video.get("status") in {"subtitle", "asr"} and video.get("status") not in (sources or []):
            problems.append(f"第{line_no}行：capture.content_sources 必须包含视频来源 {video.get('status')}")
        if video.get("present") is False and video.get("status") != "not_present":
            problems.append(f"第{line_no}行：capture.video.present=false 时 status 必须是 not_present")
        if video.get("present") is True and video.get("status") == "not_present":
            problems.append(f"第{line_no}行：capture.video.present=true 时 status 不能是 not_present")
        video_required = meta_channel in VIDEO_CHANNELS or (
            meta_channel in {"douyin", "xiaohongshu", "twitter"}
            and _is_video_finding(rec, capture)
        )
        if video_required:
            if video.get("present") is not True:
                problems.append(f"第{line_no}行：该视频 finding 的 capture.video.present 必须为 true")
            if video.get("status") not in {"subtitle", "asr", "failed"}:
                problems.append(f"第{line_no}行：该视频必须取得字幕、完成 ASR，或以 failed 记录原因")

    if comments is not None:
        if not _is_nonnegative_int(comments.get("count")):
            problems.append(f"第{line_no}行：capture.comments.count 必须是非负整数")
        if comments.get("status") in {"failed", "not_available"} and not _has_reason(comments):
            problems.append(f"第{line_no}行：capture.comments 为 failed/not_available 时必须填写 reason")
        if (comments.get("status") != "captured"
                and _is_nonnegative_int(comments.get("count")) and comments["count"] != 0):
            problems.append(f"第{line_no}行：capture.comments 非 captured 状态时 count 必须为 0")
        if meta_channel in SOCIAL_CHANNELS and comments.get("status") not in {
                "captured", "failed", "not_available"}:
            problems.append(f"第{line_no}行：社交渠道必须抓取评论，或记录 failed/not_available 及原因")
        if (meta_channel in SOCIAL_CHANNELS and comments.get("status") == "captured"
                and not (_is_nonnegative_int(comments.get("count"))
                         and 1 <= comments["count"] <= 10)):
            problems.append(f"第{line_no}行：社交渠道 comments.status=captured 时 count 必须为 1..10；零条应记录 not_available/failed 及原因")
        if comments.get("status") == "captured":
            problems.extend(_validate_artifact(
                path, line_no, "capture.comments", comments.get("artifact"), rec.get("content")
            ))
            if "comments" not in (sources or []):
                problems.append(f"第{line_no}行：已抓取评论时 capture.content_sources 必须包含 comments")
            if (_is_nonnegative_int(comments.get("count"))
                    and comments["count"] < 10 and not _has_reason(comments)):
                problems.append(f"第{line_no}行：抓取评论少于 10 条时必须填写 reason 说明不足原因")

    if images is not None:
        if not isinstance(images.get("present"), bool):
            problems.append(f"第{line_no}行：capture.images.present 必须是布尔值")
        if images.get("status") == "failed" and not _has_reason(images):
            problems.append(f"第{line_no}行：capture.images.status=failed 时必须填写 reason")
        if images.get("status") == "ocr":
            problems.extend(_validate_artifact(
                path, line_no, "capture.images", images.get("artifact"), rec.get("content")
            ))
        if images.get("status") == "ocr" and "ocr" not in (sources or []):
            problems.append(f"第{line_no}行：capture.content_sources 必须包含 ocr")
        processed, total = images.get("processed"), images.get("total")
        valid_counts = (
            _is_nonnegative_int(processed) and _is_nonnegative_int(total)
            and processed <= total
        )
        if not valid_counts:
            problems.append(f"第{line_no}行：capture.images 必须记录有效的 processed/total 数量")
        if images.get("present") is False and images.get("status") not in {"not_present", "not_applicable"}:
            problems.append(f"第{line_no}行：capture.images.present=false 时 status 必须是 not_present/not_applicable")
        if images.get("present") is True and images.get("status") == "not_present":
            problems.append(f"第{line_no}行：capture.images.present=true 时 status 不能是 not_present")
        if meta_channel == "xiaohongshu" and _is_xhs_image_finding(rec, capture):
            expected_total = _xhs_expected_image_count(rec)
            if images.get("present") is not True:
                problems.append(f"第{line_no}行：小红书图片 finding 的 capture.images.present 必须为 true")
            if images.get("status") not in {"ocr", "failed"}:
                problems.append(f"第{line_no}行：小红书图片必须完成 OCR，或以 failed 记录原因")
            if not valid_counts or total == 0:
                problems.append(f"第{line_no}行：小红书图片必须记录有效的 processed/total 数量")
            elif expected_total == 0:
                problems.append(f"第{line_no}行：小红书图片无法从 raw/media 核对实际图片数")
            elif total != expected_total:
                problems.append(
                    f"第{line_no}行：capture.images.total={total} 与 raw/media 实际图片数 {expected_total} 不符"
                )
            elif images.get("status") == "ocr" and processed != total:
                problems.append(f"第{line_no}行：capture.images.status=ocr 时 processed 必须等于 total")

    if license_record is not None:
        if meta_channel == "github" and not _github_repository_url(rec.get("source_url")):
            problems.append(f"第{line_no}行：schema v2 GitHub finding.source_url 必须是 GitHub 仓库 URL")
        if license_record.get("status") == "unknown" and not _has_reason(license_record):
            problems.append(f"第{line_no}行：capture.license.status=unknown 时必须填写 reason")
        if meta_channel == "github" and license_record.get("status") not in {"verified", "unknown"}:
            problems.append(f"第{line_no}行：GitHub finding 的许可证必须 verified，或以 unknown 记录原因")
        if license_record.get("status") == "verified" and not (
                isinstance(license_record.get("source"), str) and license_record["source"].strip()):
            problems.append(f"第{line_no}行：许可证 verified 时必须填写实际许可证文件 source")
        if license_record.get("status") == "verified" and "license" not in (sources or []):
            problems.append(f"第{line_no}行：capture.content_sources 必须包含 license")
        if license_record.get("status") == "verified":
            source = license_record.get("source")
            if not _github_license_url(source):
                problems.append(f"第{line_no}行：verified license.source 必须是 GitHub 仓库的 LICENSE/COPYING 文件 URL")
            source_repo = _github_repo(source)
            finding_repo = _github_repo(rec.get("source_url"))
            if source_repo and finding_repo and source_repo != finding_repo:
                problems.append(f"第{line_no}行：verified license.source 与 finding.source_url 不是同一 GitHub 仓库")
            problems.extend(_validate_artifact(
                path, line_no, "capture.license", license_record.get("artifact"), rec.get("content")
            ))

    return problems


def validate_file(path: str, manifest_path: str | None = None) -> list:
    """Return a list of human-readable problems (empty list == valid)."""
    problems = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in (l.rstrip("\n") for l in f) if ln.strip()]
    except OSError as e:
        return [f"无法读取文件：{e}"]

    if not lines:
        return ["文件为空（至少应有一行 meta）"]

    # Line 1 must be meta.
    try:
        meta = json.loads(lines[0])
    except json.JSONDecodeError as e:
        return [f"第1行不是合法 JSON：{e}"]
    if meta.get("type") != "meta":
        problems.append("第1行的 type 必须是 'meta'")
    for k in META_REQUIRED:
        if k not in meta:
            problems.append(f"meta 缺字段：{k}")
    meta_channel = meta.get("channel")
    if meta_channel and meta_channel not in PREFIX:
        problems.append(f"未知渠道名 '{meta_channel}'（应为标准名之一：{'/'.join(PREFIX)}）")
    filename_match = FINDINGS_FILENAME.fullmatch(os.path.basename(path))
    if filename_match and meta_channel != filename_match.group(1):
        problems.append(
            f"文件名渠道 '{filename_match.group(1)}' 与 meta.channel '{meta_channel}' 不一致"
        )
    expected_prefix = PREFIX.get(meta_channel)
    schema_version = meta.get("schema_version")
    if schema_version is not None and (
            not isinstance(schema_version, int) or isinstance(schema_version, bool)
            or schema_version != 2):
        problems.append(f"不支持的 meta.schema_version：{schema_version!r}（当前仅支持 2；旧日志请省略该字段）")
    if manifest_path and schema_version != 2:
        problems.append("带 --manifest 的正式流程只接受 meta.schema_version=2；旧日志兼容检查请勿传 --manifest")
    planned_keywords = []
    asr_authorized = False
    if schema_version == 2:
        for key in V2_META_REQUIRED:
            if key not in meta:
                problems.append(f"schema v2 meta 缺字段：{key}")
        queries = meta.get("queries")
        if (not isinstance(queries, list) or not queries
                or any(not isinstance(item, str) or not item.strip() for item in queries)):
            problems.append("schema v2 meta.queries 必须是非空字符串数组")
        elif len(set(queries)) != len(queries):
            problems.append("schema v2 meta.queries 不得重复")
        for key in ("started", "finished"):
            if not isinstance(meta.get(key), str) or not meta[key].strip():
                problems.append(f"schema v2 meta.{key} 必须是非空字符串")
        for key in ("failures", "skipped"):
            value = meta.get(key)
            if (not isinstance(value, list)
                    or any(not isinstance(item, str) or not item.strip() for item in value)):
                problems.append(f"schema v2 meta.{key} 必须是字符串数组")

        manifest, manifest_error = _load_manifest(path, manifest_path)
        if manifest_error:
            problems.append(manifest_error)
        elif manifest is not None:
            planned_keywords, manifest_problems = _manifest_keywords(manifest, meta_channel)
            problems.extend(manifest_problems)
            asr_authorized, authorization_problems = _manifest_asr_authorized(manifest)
            problems.extend(authorization_problems)

    if schema_version == 2 and meta_channel == "github":
        discovery = meta.get("discovery")
        if not isinstance(discovery, dict):
            problems.append("GitHub schema v2 meta 必须有 discovery 对象")
        else:
            for route in DISCOVERY_ROUTES:
                item = discovery.get(route)
                if not isinstance(item, dict) or item.get("status") not in {"completed", "failed"}:
                    problems.append(f"GitHub meta.discovery.{route} 必须记录 completed/failed")
                    continue
                count = item.get("count")
                if not _is_nonnegative_int(count):
                    problems.append(f"GitHub meta.discovery.{route}.count 必须是非负整数")
                if item.get("status") == "completed":
                    if not _is_nonnegative_int(count) or count == 0:
                        problems.append(f"GitHub meta.discovery.{route} completed 时 count 必须大于 0")
                    proof = item.get("proof")
                    if not isinstance(proof, list) or not proof:
                        problems.append(f"GitHub meta.discovery.{route}.proof 必须是非空证据数组")
                    elif _is_nonnegative_int(count) and len(proof) != count:
                        problems.append(f"GitHub meta.discovery.{route}.count 必须等于 proof 条数")
                    else:
                        signatures = [json.dumps(entry, ensure_ascii=False, sort_keys=True) for entry in proof]
                        if len(set(signatures)) != len(signatures):
                            problems.append(f"GitHub meta.discovery.{route}.proof 不得重复")
                        if any(not _valid_discovery_proof(route, entry) for entry in proof):
                            problems.append(f"GitHub meta.discovery.{route}.proof 与该发现路线不匹配")
                        if (route == "keyword" and isinstance(meta.get("queries"), list)
                                and all(isinstance(entry, str) for entry in proof)
                                and set(proof) != set(meta["queries"])):
                            problems.append("GitHub meta.discovery.keyword.proof 必须与 meta.queries 完全一致")
                else:
                    if count != 0:
                        problems.append(f"GitHub meta.discovery.{route} failed 时 count 必须为 0")
                    if item.get("proof") != []:
                        problems.append(f"GitHub meta.discovery.{route} failed 时 proof 必须是空数组")
                    if not _has_reason(item):
                        problems.append(f"GitHub meta.discovery.{route} 失败时必须填写 reason")

    seen_ids = set()
    finding_queries = set()
    finding_repos = set()
    finding_count = 0
    for i, line in enumerate(lines[1:], start=2):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"第{i}行不是合法 JSON：{e}")
            continue
        rtype = rec.get("type")
        if rtype == "meta":
            problems.append(f"第{i}行：meta 只能出现在第1行")
            continue
        if rtype != "finding":
            problems.append(f"第{i}行：未知 type '{rtype}'（应为 finding）")
            continue
        finding_count += 1
        if isinstance(rec.get("query"), str) and rec["query"].strip():
            finding_queries.add(rec["query"])
            if (schema_version == 2 and isinstance(meta.get("queries"), list)
                    and rec["query"] not in meta["queries"]):
                problems.append(f"第{i}行：finding.query 未在 meta.queries 中申报：{rec['query']}")
        source_repo = _github_repo(rec.get("source_url"))
        if source_repo:
            finding_repos.add(source_repo)
        for k in FINDING_REQUIRED:
            if k not in rec or rec[k] in (None, ""):
                problems.append(f"第{i}行 finding 缺字段或为空：{k}")
        fid = rec.get("id")
        if fid:
            if fid in seen_ids:
                problems.append(f"第{i}行：id 重复 '{fid}'")
            seen_ids.add(fid)
            if expected_prefix and not str(fid).startswith(expected_prefix + "-"):
                problems.append(f"第{i}行：id '{fid}' 前缀应为 '{expected_prefix}-'（渠道 {meta_channel}，见 log-format.md 前缀表）")
        # channel consistency
        if meta_channel and rec.get("channel") and rec["channel"] != meta_channel:
            problems.append(f"第{i}行：channel '{rec['channel']}' 与 meta '{meta_channel}' 不一致")
        if schema_version == 2:
            problems.extend(validate_capture_v2(path, i, rec, meta_channel, asr_authorized))

    if schema_version == 2:
        meta_queries = meta.get("queries") if isinstance(meta.get("queries"), list) else []
        for keyword in planned_keywords:
            if keyword not in meta_queries:
                problems.append(f"计划关键词未出现在 meta.queries：{keyword}")
        for query in meta_queries:
            if planned_keywords and query not in planned_keywords:
                problems.append(f"meta.queries 含 manifest 计划外关键词：{query}")
        explanations = []
        for key in ("failures", "skipped"):
            if isinstance(meta.get(key), list):
                explanations.extend(item for item in meta[key] if isinstance(item, str))
        for query in meta_queries:
            if not isinstance(query, str) or not query.strip():
                continue
            if query not in finding_queries and not _query_has_explanation(query, explanations):
                problems.append(f"meta.query 无 finding 命中且 failures/skipped 未写具体原因：{query}")
        if meta_channel == "github" and isinstance(meta.get("discovery"), dict):
            related = meta["discovery"].get("related")
            if isinstance(related, dict) and related.get("status") == "completed":
                for proof in related.get("proof") or []:
                    if (isinstance(proof, dict) and _valid_discovery_proof("related", proof)
                            and _github_repo(proof["from"]) not in finding_repos):
                        problems.append(
                            "GitHub meta.discovery.related.proof.from 必须是本日志已入选仓库"
                        )

    # meta.count should match actual findings (fail loud, not warn).
    declared = meta.get("count")
    if not _is_nonnegative_int(declared):
        problems.append("meta.count 必须是非负整数")
    elif declared != finding_count:
        problems.append(f"meta.count={declared} 与实际 finding 数 {finding_count} 不符（收尾忘了跑 finalize_log.py？）")

    # Zero results must be explained — silence is the failure mode we refuse.
    if finding_count == 0 and not meta.get("failures"):
        problems.append("count=0 且 meta.failures 为空——零结果必须申报原因（账号未配置/关键词全部无命中/…），不许静默")

    return problems


def missing_channel_problems(raw_dir: str, channels: list) -> list:
    """Channels that were approved but never produced a findings file."""
    problems = []
    for c in channels:
        path = os.path.join(raw_dir, f"findings.{c}.jsonl")
        if not os.path.exists(path):
            problems.append(f"缺少渠道文件：findings.{c}.jsonl（该渠道 agent 可能从未落盘——按 SKILL.md 的单渠道重派 SOP 处理）")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", help="含 findings.*.jsonl 的 raw/ 目录")
    ap.add_argument("--file", help="只校验一个 findings 文件（并行证据复核时使用）")
    ap.add_argument("--manifest", help="manifest.json 路径；省略时自动读取 findings 所在 raw/ 的上一级")
    ap.add_argument("--channels", help="逗号分隔的期望渠道清单（英文标准名）；缺文件即报错，防死掉的渠道被静默跳过")
    a = ap.parse_args()
    if bool(a.raw_dir) == bool(a.file):
        ap.error("--raw-dir 与 --file 必须且只能提供一个")
    files = [a.file] if a.file else sorted(glob.glob(os.path.join(a.raw_dir, "findings.*.jsonl")))
    total_problems = 0

    if a.channels:
        if not a.raw_dir:
            ap.error("--channels 只能与 --raw-dir 一起使用")
        expected = [c.strip() for c in a.channels.split(",") if c.strip()]
        for p in missing_channel_problems(a.raw_dir, expected):
            print(f"✗ {p}")
            total_problems += 1

    if not files and not total_problems:
        print(f"未找到 findings 日志 -> {a.raw_dir or a.file}")
        raise SystemExit(1)

    for path in files:
        problems = validate_file(path, a.manifest)
        name = os.path.basename(path)
        if problems:
            total_problems += len(problems)
            print(f"✗ {name}: {len(problems)} 个问题")
            for p in problems:
                print(f"    - {p}")
        else:
            print(f"✓ {name}")
    if total_problems:
        print(f"\n共 {total_problems} 个问题——请修复后再进入总结阶段（不静默容忍）。")
        raise SystemExit(1)
    print(f"\n全部 {len(files)} 个渠道日志校验通过。")


if __name__ == "__main__":
    main()
