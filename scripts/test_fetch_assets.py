import json, os
from fetch_assets import localize, filename_for

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
