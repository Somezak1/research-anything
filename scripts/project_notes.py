#!/usr/bin/env python3
"""Field-projection reader for findings JSONL — 总结层唯一推荐的读取入口.

finding 是单行 JSON、行内嵌原文全文（可达数万字）；agent 的 Read 工具对超长行会截断，
所以总结 agent 不用 Read 读原始文件，而用本脚本投影出要读的字段：

  --mode stats      各渠道条数/失败 + headline+note 字符量与保守 token 估算（熔断判断用）
  --mode headlines  全部 id + headline（第一遍速览）
  --mode notes      全部 id/channel/title/…/note，不含 content（第二遍通读）
  --mode get --id xhs-007   按 id 取整条记录，含 content 原文全文与 raw（"从笔记反查原帖"的正规通道）
"""
import argparse, glob, json, os, sys

NOTE_FIELDS = ("id", "channel", "title", "author", "published_at", "metrics",
               "source_url", "unknown_terms", "headline", "note")


def iter_records(raw_dir: str):
    files = sorted(glob.glob(os.path.join(raw_dir, "findings.*.jsonl")))
    if not files:
        raise SystemExit(f"未找到任何 findings.*.jsonl -> {raw_dir}")
    for path in files:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print(f"[warn] 跳过坏行：{os.path.basename(path)}（validate_log.py 会报详情）", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True, help="含 findings.*.jsonl 的 raw/ 目录")
    ap.add_argument("--mode", required=True, choices=["stats", "headlines", "notes", "get"])
    ap.add_argument("--id", help="--mode get 用：要反查的 finding id，如 xhs-007")
    a = ap.parse_args()

    if a.mode == "stats":
        channels, chars, findings = {}, 0, 0
        for r in iter_records(a.raw_dir):
            if r.get("type") == "meta":
                channels[r.get("channel", "?")] = {
                    "count": r.get("count"), "failures": r.get("failures") or []}
            elif r.get("type") == "finding":
                findings += 1
                chars += len(str(r.get("headline", ""))) + len(str(r.get("note", "")))
        print(json.dumps({
            "channels": channels,
            "findings_total": findings,
            "headline_note_chars": chars,
            # 中文约 1 token/字符——按 1 计是最保守（偏高）的估算，熔断判断用它
            "approx_tokens_conservative": chars,
        }, ensure_ascii=False, indent=1))
    elif a.mode == "headlines":
        for r in iter_records(a.raw_dir):
            if r.get("type") == "finding":
                print(f"{r.get('id')}\t{r.get('headline', '')}")
    elif a.mode == "notes":
        for r in iter_records(a.raw_dir):
            if r.get("type") == "finding":
                out = {k: r.get(k) for k in NOTE_FIELDS if r.get(k) not in (None, "", [])}
                print(json.dumps(out, ensure_ascii=False))
    elif a.mode == "get":
        if not a.id:
            raise SystemExit("--mode get 需要 --id")
        for r in iter_records(a.raw_dir):
            if r.get("type") == "finding" and r.get("id") == a.id:
                print(json.dumps(r, ensure_ascii=False, indent=1))
                return
        raise SystemExit(f"未找到 id={a.id}")


if __name__ == "__main__":
    main()
