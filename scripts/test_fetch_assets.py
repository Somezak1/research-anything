import json, os
import pytest
from urllib.request import Request
from fetch_assets import localize, filename_for, validate_url, UnsafeAssetURL, _SafeRedirectHandler

def test_filename_is_safe_and_stable():
    a = filename_for("http://x.cn/obj/1040g!nc_n_webp_mw_1?t=123")
    b = filename_for("http://x.cn/obj/1040g!nc_n_webp_mw_1?t=999")
    assert "/" not in a and "!" not in a and " " not in a
    assert a.endswith((".jpg", ".png", ".webp"))
    assert a == b  # 稳定：同一图不同时效戳 → 同名
    c = filename_for("http://x/foo.png?t=1")
    assert c.endswith(".png")  # 扩展名确实从 URL 派生，而非恒等兜底

def test_localize_success_and_failure(tmp_path):
    good_bytes = b"\xff\xd8\xff"
    def fake_fetcher(url):
        return good_bytes if "good" in url else None  # good→有字节，bad→失败
    specs = [{"url": "http://x/good.jpg", "source_post_url": "p1"},
             {"url": "http://x/bad.jpg", "source_post_url": "p2"}]
    out = str(tmp_path)
    result = localize(specs, out, fetcher=fake_fetcher)
    assert result["http://x/good.jpg"].startswith("assets/")
    good_path = os.path.join(out, result["http://x/good.jpg"])
    assert os.path.exists(good_path)
    with open(good_path, "rb") as f:
        assert f.read() == good_bytes  # 落盘内容与 fetcher 返回字节一致
    assert (os.stat(good_path).st_mode & 0o777) == 0o600
    assert result["http://x/bad.jpg"] is None  # 失败降级，不抛

def test_localize_disk_write_failure_degrades_to_none(tmp_path):
    # out_dir 指向一个已存在的文件路径，makedirs(out_dir/assets) 会因父路径是文件而失败
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a dir")
    def fake_fetcher(url):
        return b"\xff\xd8\xff"
    specs = [{"url": "http://x/good.jpg", "source_post_url": "p1"},
             {"url": "http://x/other.png", "source_post_url": "p2"}]
    result = localize(specs, str(blocked), fetcher=fake_fetcher)  # 不应抛异常
    assert result["http://x/good.jpg"] is None
    assert result["http://x/other.png"] is None


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://127.0.0.1/a.jpg", "http://[::1]/a.jpg",
    "http://169.254.169.254/latest/meta-data", "ftp://example.com/a.jpg",
])
def test_localize_rejects_non_http_and_private_urls(url, tmp_path):
    called = []
    result = localize([{"url": url}], str(tmp_path),
                      fetcher=lambda value: called.append(value) or b"\xff\xd8\xff")
    assert result[url] is None
    assert called == []


def test_redirect_handler_revalidates_destination():
    handler = _SafeRedirectHandler()
    with pytest.raises(UnsafeAssetURL):
        handler.redirect_request(Request("https://example.com/a.jpg"), None, 302, "found", {},
                                 "http://127.0.0.1/secret")


def test_rejects_non_image_and_oversize_payload(tmp_path):
    specs = [{"url": "https://example.com/not-image.jpg"},
             {"url": "https://example.com/huge.jpg"}]
    def fetcher(url):
        return b"<html>no</html>" if "not-image" in url else b"\xff\xd8\xff" + b"x" * 100
    result = localize(specs, str(tmp_path), fetcher=fetcher, max_bytes=20)
    assert all(value is None for value in result.values())
