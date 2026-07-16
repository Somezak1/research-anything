#!/usr/bin/env python3
"""Stable, paged field projection for findings JSONL.

Legacy invocations still emit the original line-oriented output. Supplying
``--limit``, ``--max-chars`` or ``--cursor`` switches headlines/notes to a JSON
page envelope with a dataset-bound cursor. ``--receipt`` appends a durable proof
of exactly which projection was consumed.
"""
import argparse
import base64
import datetime
import fcntl
import glob
import hashlib
import json
import os
import sys

NOTE_FIELDS = ("id", "channel", "title", "author", "published_at", "metrics",
               "source_url", "unknown_terms", "headline", "note")


def source_files(raw_dir: str) -> list[str]:
    files = sorted(glob.glob(os.path.join(raw_dir, "findings.*.jsonl")))
    if not files:
        raise SystemExit(f"未找到任何 findings.*.jsonl -> {raw_dir}")
    return files


def dataset_hash(raw_dir: str) -> str:
    digest = hashlib.sha256()
    for path in source_files(raw_dir):
        digest.update(os.path.basename(path).encode("utf-8") + b"\0")
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def iter_records(raw_dir: str):
    for path in source_files(raw_dir):
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"JSON 损坏：{os.path.basename(path)}:{line_no}: {exc}") from exc
                if not isinstance(row, dict):
                    raise SystemExit(f"记录必须是 JSON 对象：{os.path.basename(path)}:{line_no}")
                yield row


def note_projection(record: dict) -> dict:
    return {key: record.get(key) for key in NOTE_FIELDS if record.get(key) not in (None, "", [])}


def _json_line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def projected_lines(raw_dir: str, mode: str) -> list[tuple[str, str]]:
    values = []
    for record in iter_records(raw_dir):
        if record.get("type") != "finding":
            continue
        if mode == "headlines":
            values.append((str(record.get("id", "")), f"{record.get('id')}\t{record.get('headline', '')}"))
        elif mode == "notes":
            values.append((str(record.get("id", "")), _json_line(note_projection(record))))
    return values


def _measure(lines: list[str]) -> dict:
    text = "".join(line + "\n" for line in lines)
    return {"chars": len(text), "bytes": len(text.encode("utf-8")),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _encode_cursor(mode: str, digest: str, offset: int) -> str:
    raw = json.dumps({"v": 1, "mode": mode, "dataset_hash": digest, "offset": offset},
                     separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str, mode: str, digest: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        value = json.loads(raw)
    except Exception as exc:
        raise SystemExit(f"无效 cursor：{exc}") from exc
    if value.get("v") != 1 or value.get("mode") != mode:
        raise SystemExit("cursor 版本或 mode 不匹配")
    if value.get("dataset_hash") != digest:
        raise SystemExit("cursor 对应的原始数据已变化；拒绝跳过或重复读取")
    offset = value.get("offset")
    if not isinstance(offset, int) or offset < 0:
        raise SystemExit("cursor offset 无效")
    return offset


def make_page(raw_dir: str, mode: str, cursor: str | None = None,
              limit: int | None = None, max_chars: int | None = None) -> dict:
    digest = dataset_hash(raw_dir)
    values = projected_lines(raw_dir, mode)
    start = _decode_cursor(cursor, mode, digest) if cursor else 0
    if start > len(values):
        raise SystemExit("cursor offset 超出数据集")
    selected = []
    used_chars = 0
    for finding_id, line in values[start:]:
        if limit is not None and len(selected) >= limit:
            break
        line_chars = len(line) + 1
        if max_chars is not None and selected and used_chars + line_chars > max_chars:
            break
        selected.append((finding_id, line))
        used_chars += line_chars
        if max_chars is not None and used_chars >= max_chars:
            break
    end = start + len(selected)
    lines = [line for _, line in selected]
    measure = _measure(lines)
    return {
        "schema_version": 1,
        "mode": mode,
        "dataset_hash": digest,
        "start": start,
        "end": end,
        "total": len(values),
        "record_ids": [finding_id for finding_id, _ in selected],
        "projected_chars": measure["chars"],
        "projected_bytes": measure["bytes"],
        "projection_sha256": measure["sha256"],
        "records": ([{"id": finding_id, "headline": line.split("\t", 1)[1]}
                     for finding_id, line in selected] if mode == "headlines"
                    else [json.loads(line) for line in lines]),
        "next_cursor": _encode_cursor(mode, digest, end) if end < len(values) else None,
    }


def append_receipt(path: str, page: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    row = {key: page[key] for key in ("mode", "dataset_hash", "start", "end", "total",
                                      "record_ids", "projected_chars", "projected_bytes",
                                      "projection_sha256", "next_cursor")}
    row.update(schema_version=1, read_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    payload = (_json_line(row) + "\n").encode("utf-8")
    lock_path = path + ".lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def stats(raw_dir: str) -> dict:
    channels, headline_note_chars, findings = {}, 0, 0
    for record in iter_records(raw_dir):
        if record.get("type") == "meta":
            channels[record.get("channel", "?")] = {
                "count": record.get("count"), "failures": record.get("failures") or []}
        elif record.get("type") == "finding":
            findings += 1
            headline_note_chars += len(str(record.get("headline", ""))) + len(str(record.get("note", "")))
    headline_lines = [line for _, line in projected_lines(raw_dir, "headlines")]
    note_lines = [line for _, line in projected_lines(raw_dir, "notes")]
    headline_size, note_size = _measure(headline_lines), _measure(note_lines)
    return {
        "channels": channels,
        "findings_total": findings,
        "dataset_hash": dataset_hash(raw_dir),
        "headline_note_chars": headline_note_chars,
        "approx_tokens_conservative": headline_note_chars,
        "headlines_projected_chars": headline_size["chars"],
        "headlines_projected_bytes": headline_size["bytes"],
        "notes_projected_chars": note_size["chars"],
        "notes_projected_bytes": note_size["bytes"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True, help="含 findings.*.jsonl 的 raw/ 目录")
    ap.add_argument("--mode", required=True, choices=["stats", "headlines", "notes", "get"])
    ap.add_argument("--id", help="--mode get 用：要反查的 finding id，如 xhs-007")
    ap.add_argument("--limit", type=int, help="分页最多返回多少条；启用 JSON page envelope")
    ap.add_argument("--max-chars", "--chunk-chars", dest="max_chars", type=int,
                    help="分页投影字符上限，不拆分单条；启用 JSON page envelope")
    ap.add_argument("--cursor", help="上一页 next_cursor；绑定输入 hash，输入变化时拒绝继续")
    ap.add_argument("--receipt", help="追加读取回执 JSONL（分页读取时推荐）")
    args = ap.parse_args()

    if args.limit is not None and args.limit <= 0:
        ap.error("--limit 必须大于 0")
    if args.max_chars is not None and args.max_chars <= 0:
        ap.error("--max-chars 必须大于 0")
    paged = args.cursor is not None or args.limit is not None or args.max_chars is not None
    if paged and args.mode not in {"headlines", "notes"}:
        ap.error("分页只支持 --mode headlines/notes")

    if args.mode == "stats":
        print(json.dumps(stats(args.raw_dir), ensure_ascii=False, indent=1))
    elif paged:
        page = make_page(args.raw_dir, args.mode, args.cursor, args.limit, args.max_chars)
        if args.receipt:
            append_receipt(args.receipt, page)
        print(json.dumps(page, ensure_ascii=False, indent=1))
    elif args.mode in {"headlines", "notes"}:
        lines = [line for _, line in projected_lines(args.raw_dir, args.mode)]
        if args.receipt:
            page = make_page(args.raw_dir, args.mode, limit=len(lines) or 1)
            append_receipt(args.receipt, page)
        if lines:
            print("\n".join(lines))
    elif args.mode == "get":
        if not args.id:
            raise SystemExit("--mode get 需要 --id")
        for record in iter_records(args.raw_dir):
            if record.get("type") == "finding" and record.get("id") == args.id:
                print(json.dumps(record, ensure_ascii=False, indent=1))
                return
        raise SystemExit(f"未找到 id={args.id}")


if __name__ == "__main__":
    main()
