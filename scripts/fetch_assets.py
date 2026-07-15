#!/usr/bin/env python3
"""Download report images into <out>/assets/, degrade to None on failure."""
import argparse, hashlib, json, os, urllib.request

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

def filename_for(url: str) -> str:
    base = url.split("?", 1)[0]
    ext = ".jpg"
    for e in (".png", ".webp", ".jpeg", ".jpg"):
        if base.lower().endswith(e):
            ext = ".jpg" if e == ".jpeg" else e; break
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
    return f"img_{h}{ext}"

def default_fetcher(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except Exception:
        return None

def localize(specs, out_dir, fetcher=default_fetcher):
    assets = os.path.join(out_dir, "assets")
    try:
        os.makedirs(assets, exist_ok=True)
        assets_ready = True
    except OSError:
        assets_ready = False
    result = {}
    for spec in specs:
        url = spec["url"]
        if not assets_ready:
            result[url] = None
            continue
        data = fetcher(url)
        if not data:
            result[url] = None
            continue
        name = filename_for(url)
        try:
            with open(os.path.join(assets, name), "wb") as f:
                f.write(data)
            result[url] = f"assets/{name}"
        except OSError:
            result[url] = None
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, help="JSON list of {url, source_post_url}")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    specs = json.load(open(a.manifest))
    result = localize(specs, a.out)
    json.dump(result, open(os.path.join(a.out, "assets-manifest.json"), "w"), ensure_ascii=False, indent=2)
    ok = sum(1 for v in result.values() if v)
    print(f"localized {ok}/{len(result)} images -> {a.out}/assets/")

if __name__ == "__main__":
    main()
