import json
import subprocess

import ocr_images as O


def test_recognize_local_and_url(tmp_path):
    local = tmp_path / "local.png"
    local.write_bytes(b"image")

    def fetcher(_url, dest):
        open(dest, "wb").write(b"remote")
        return dest

    def runner(cmd, **_kwargs):
        payload = [{"path": path, "status": "ocr", "text": f"文字-{i}", "error": None}
                   for i, path in enumerate(cmd[2:], 1)]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    summary = O.recognize([str(local), "https://example.com/a.webp"], str(tmp_path / "out"),
                          runner=runner, fetcher=fetcher)
    assert summary["status"] == "SUCCEEDED" and summary["processed"] == 2
    assert "文字-1" in (tmp_path / "out.ocr.txt").read_text()
    saved = json.loads((tmp_path / "out.ocr.json").read_text())
    assert saved[1]["source"] == "https://example.com/a.webp"


def test_missing_image_is_reported(tmp_path):
    summary = O.recognize([str(tmp_path / "missing.png")], str(tmp_path / "out"))
    assert summary["status"] == "FAILED" and summary["failed"] == 1
    assert summary["results"][0]["error"] == "图片文件不存在"


def test_successful_blank_image_still_has_auditable_text_block(tmp_path):
    local = tmp_path / "blank.png"
    local.write_bytes(b"image")

    def runner(cmd, **_kwargs):
        payload = [{"path": cmd[2], "status": "ocr", "text": "", "error": None}]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    summary = O.recognize([str(local)], str(tmp_path / "out"), runner=runner)
    assert summary["status"] == "SUCCEEDED" and summary["processed"] == 1
    assert "未识别到文字" in (tmp_path / "out.ocr.txt").read_text()
