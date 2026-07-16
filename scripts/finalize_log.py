#!/usr/bin/env python3
"""Backfill the first-line meta of a findings JSONL: count / finished / failures.

Collection agents append findings incrementally (log-format.md 落盘纪律), then run this
once at the end so meta matches reality; validate_log.py checks the match afterwards.
"""
import argparse, datetime, json, os, tempfile


def _atomic_write(path: str, text: str) -> None:
    """Replace *path* only after a complete, durable write in the same directory."""
    directory = os.path.dirname(os.path.abspath(path))
    original_mode = os.stat(path).st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, original_mode or 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def finalize(path: str, failures=None, finished=None) -> dict:
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    if not lines:
        raise SystemExit(f"文件为空，无法回填：{path}")
    try:
        meta = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise SystemExit(f"第1行 JSON 损坏，拒绝回填：{exc}") from exc
    if not isinstance(meta, dict):
        raise SystemExit("第1行必须是 JSON 对象，拒绝回填")
    if meta.get("type") != "meta":
        raise SystemExit("第1行不是 meta，无法回填（文件是不是没按 log-format.md 写？）")
    count = 0
    for line_no, l in enumerate(lines[1:], 2):
        try:
            row = json.loads(l)
            if not isinstance(row, dict):
                raise SystemExit(f"第{line_no}行必须是 JSON 对象，拒绝回填")
            if row.get("type") == "finding":
                count += 1
        except json.JSONDecodeError as exc:
            raise SystemExit(f"第{line_no}行 JSON 损坏，拒绝回填：{exc}") from exc
    meta["count"] = count
    meta["finished"] = finished or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if failures:
        merged = list(meta.get("failures") or [])
        for x in failures:
            if x not in merged:
                merged.append(x)
        meta["failures"] = merged
    lines[0] = json.dumps(meta, ensure_ascii=False)
    _atomic_write(path, "\n".join(lines) + "\n")
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
