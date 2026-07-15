#!/usr/bin/env python3
"""Summarize evidence completion from schema-version-2 findings logs."""
import argparse
import glob
import json
import os
from collections import Counter


def _bucket():
    return {"findings": 0, "content_sources": Counter(), "video": Counter(),
            "comments": Counter(), "images": Counter(), "license": Counter(),
            "comment_count": 0, "image_total": 0, "image_processed": 0,
            "queries": [], "query_findings": Counter(), "failures": [],
            "skipped": [], "discovery": {}}


def summarize(raw_dir):
    channels = {}
    overall = _bucket()
    schema_versions = Counter()
    for path in sorted(glob.glob(os.path.join(raw_dir, "findings.*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        if not records:
            continue
        schema_versions[str(records[0].get("schema_version") or "legacy")] += 1
        channel = records[0].get("channel") or os.path.basename(path).split(".")[1]
        bucket = channels.setdefault(channel, _bucket())
        meta = records[0]
        bucket["queries"] = list(meta.get("queries") or [])
        bucket["failures"] = list(meta.get("failures") or [])
        bucket["skipped"] = list(meta.get("skipped") or [])
        bucket["discovery"] = meta.get("discovery") or {}
        for rec in records[1:]:
            if rec.get("type") != "finding":
                continue
            bucket["findings"] += 1
            overall["findings"] += 1
            query = rec.get("query")
            if isinstance(query, str) and query.strip():
                bucket["query_findings"][query] += 1
            capture = rec.get("capture") or {}
            for source in capture.get("content_sources") or ["missing"]:
                bucket["content_sources"][source] += 1
                overall["content_sources"][source] += 1
            for key in ("video", "comments", "images", "license"):
                status = (capture.get(key) or {}).get("status", "missing")
                bucket[key][status] += 1
                overall[key][status] += 1
            comments = capture.get("comments") or {}
            images = capture.get("images") or {}
            bucket["comment_count"] += int(comments.get("count") or 0)
            overall["comment_count"] += int(comments.get("count") or 0)
            bucket["image_total"] += int(images.get("total") or 0)
            overall["image_total"] += int(images.get("total") or 0)
            bucket["image_processed"] += int(images.get("processed") or 0)
            overall["image_processed"] += int(images.get("processed") or 0)

    def plain(bucket):
        result = {k: dict(v) if isinstance(v, Counter) else v for k, v in bucket.items()
                  if k != "query_findings"}
        counts = bucket["query_findings"]
        result["query_coverage"] = [
            {"query": query, "findings": counts.get(query, 0),
             "outcome": "findings" if counts.get(query, 0) else "failed_or_skipped"}
            for query in bucket["queries"]
        ]
        return result

    channel_reports = {name: plain(bucket) for name, bucket in channels.items()}
    overall_report = plain(overall)
    query_rows = [row for report in channel_reports.values() for row in report["query_coverage"]]
    overall_report["query_total"] = len(query_rows)
    overall_report["query_with_findings"] = sum(row["findings"] > 0 for row in query_rows)
    overall_report["query_failed_or_skipped"] = sum(
        row["outcome"] == "failed_or_skipped" for row in query_rows
    )
    # Overall has no single-channel meta, so these empty placeholders are noise.
    for key in ("queries", "query_coverage", "failures", "skipped", "discovery"):
        overall_report.pop(key, None)
    return {"report_version": 1, "log_schema_versions": dict(schema_versions),
            "overall": overall_report, "channels": channel_reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--out", help="可选：同时把 JSON 写入该路径")
    args = parser.parse_args()
    report = summarize(args.raw_dir)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload + "\n")


if __name__ == "__main__":
    main()
