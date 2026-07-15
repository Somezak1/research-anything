#!/usr/bin/env python3
"""Download/localize images, recognize Chinese/English text with macOS Vision, and save JSON + text."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
MAX_BYTES = 30 * 1024 * 1024
SWIFT_HELPER = os.path.join(os.path.dirname(__file__), "ocr_images.swift")


def download(url, dest, opener=urllib.request.urlopen):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只接受 http/https 图片 URL")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener(req, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise ValueError("图片为空或超过 30MB")
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _run_vision(paths, runner=subprocess.run):
    proc = runner(["/usr/bin/swift", SWIFT_HELPER, *paths], capture_output=True, text=True)
    if not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or "图片文字识别没有返回结果")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"图片文字识别返回了无效 JSON: {exc}") from exc


def recognize(inputs, out, runner=subprocess.run, fetcher=download):
    results = []
    local_to_source = {}
    with tempfile.TemporaryDirectory(prefix="research_scout_ocr_") as tmp:
        for index, source in enumerate(inputs):
            if source.startswith(("http://", "https://")):
                suffix = os.path.splitext(urllib.parse.urlparse(source).path)[1] or ".img"
                local = os.path.join(tmp, f"image_{index}{suffix}")
                try:
                    fetcher(source, local)
                except Exception as exc:
                    results.append({"source": source, "status": "failed", "text": "", "error": str(exc)})
                    continue
            else:
                local = os.path.abspath(source)
                if not os.path.isfile(local):
                    results.append({"source": source, "status": "failed", "text": "", "error": "图片文件不存在"})
                    continue
            local_to_source[local] = source

        if local_to_source:
            try:
                vision_results = _run_vision(list(local_to_source), runner)
                for item in vision_results:
                    source = local_to_source.get(item.get("path"), item.get("path", ""))
                    results.append({"source": source, "status": item.get("status", "failed"),
                                    "text": item.get("text", ""), "error": item.get("error")})
            except Exception as exc:
                for local, source in local_to_source.items():
                    results.append({"source": source, "status": "failed", "text": "", "error": str(exc)})

    json_path = out + ".ocr.json"
    text_path = out + ".ocr.txt"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    with open(text_path, "w", encoding="utf-8") as f:
        blocks = [f"## 图片 {i}: {r['source']}\n{r.get('text') or '（未识别到文字）'}"
                  for i, r in enumerate(results, 1) if r["status"] == "ocr"]
        f.write("\n\n".join(blocks))
    return {"status": "FAILED" if any(r["status"] == "failed" for r in results) else "SUCCEEDED",
            "requested": len(inputs), "processed": sum(r["status"] == "ocr" for r in results),
            "failed": sum(r["status"] == "failed" for r in results),
            "json": json_path, "text": text_path, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="本地图片路径或 http/https 图片 URL")
    parser.add_argument("--out", required=True, help="输出前缀，产出 .ocr.json 和 .ocr.txt")
    args = parser.parse_args()
    summary = recognize(args.inputs, args.out)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))
    if summary["status"] != "SUCCEEDED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
