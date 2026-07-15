#!/usr/bin/env python3
"""Backfill the first-line meta of a findings JSONL: count / finished / failures.

Collection agents append findings incrementally (log-format.md 落盘纪律), then run this
once at the end so meta matches reality; validate_log.py checks the match afterwards.
"""
import argparse, datetime, json


def finalize(path: str, failures=None, finished=None) -> dict:
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    if not lines:
        raise SystemExit(f"文件为空，无法回填：{path}")
    meta = json.loads(lines[0])
    if meta.get("type") != "meta":
        raise SystemExit("第1行不是 meta，无法回填（文件是不是没按 log-format.md 写？）")
    count = 0
    for l in lines[1:]:
        try:
            if json.loads(l).get("type") == "finding":
                count += 1
        except json.JSONDecodeError:
            pass  # 坏行由 validate_log.py 负责报，这里只统计
    meta["count"] = count
    meta["finished"] = finished or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if failures:
        merged = list(meta.get("failures") or [])
        for x in failures:
            if x not in merged:
                merged.append(x)
        meta["failures"] = merged
    lines[0] = json.dumps(meta, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="findings.<渠道>.jsonl 路径")
    ap.add_argument("--failures", default="[]", help="本次收集的失败/跳过清单，JSON 数组字符串；无则 []")
    a = ap.parse_args()
    meta = finalize(a.file, failures=json.loads(a.failures))
    print(f"meta 回填完成：count={meta['count']} failures={len(meta.get('failures') or [])}")


if __name__ == "__main__":
    main()
