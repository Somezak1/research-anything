#!/usr/bin/env python3
"""Download safe raster report images into <out>/assets/.

Only public HTTP(S) endpoints are accepted. Downloads are bounded, checked against
their declared MIME type and file signature, and published with an atomic 0600
write. A failed asset degrades to ``None`` without weakening those boundaries.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


class UnsafeAssetURL(ValueError):
    pass


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


def validate_url(url: str, *, resolve: bool = True) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise UnsafeAssetURL(f"invalid URL: {exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeAssetURL("only http/https asset URLs are allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeAssetURL("asset URL must have a host and no embedded credentials")
    host = parsed.hostname.rstrip(".")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        is_literal = False
    else:
        is_literal = True
    if is_literal:
        if not _is_public_ip(host):
            raise UnsafeAssetURL("private, loopback, link-local, and reserved hosts are blocked")
        return parsed
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise UnsafeAssetURL("localhost is blocked")
    if resolve:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeAssetURL(f"host resolution failed: {exc}") from exc
        if not infos or any(not _is_public_ip(info[4][0]) for info in infos):
            raise UnsafeAssetURL("host resolves to a non-public address")
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _image_type(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def filename_for(url: str, ext: str | None = None) -> str:
    parsed = urllib.parse.urlsplit(url)
    base = urllib.parse.urlunsplit((parsed.scheme.lower(), (parsed.hostname or "").lower(),
                                    parsed.path, "", ""))
    if ext is None:
        ext = ".jpg"
        for candidate in (".png", ".webp", ".gif", ".jpeg", ".jpg"):
            if parsed.path.lower().endswith(candidate):
                ext = ".jpg" if candidate == ".jpeg" else candidate
                break
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]
    return f"img_{digest}{ext}"


def default_fetcher(url: str, max_bytes: int = DEFAULT_MAX_BYTES):
    """Return validated image bytes, or ``None`` on transport/policy failure."""
    try:
        validate_url(url)
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "image/*"})
        with opener.open(req, timeout=20) as response:
            validate_url(response.geturl())
            mime = response.headers.get_content_type().lower()
            if mime not in _MIME_EXT:
                return None
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > max_bytes:
                        return None
                except ValueError:
                    return None
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                return None
            detected = _image_type(data)
            if not detected or detected[0] != mime:
                return None
            return data
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _atomic_write(path: str, data: bytes) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".asset.", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            fd = -1
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def localize(specs, out_dir, fetcher=default_fetcher, max_bytes: int = DEFAULT_MAX_BYTES):
    assets = os.path.join(out_dir, "assets")
    try:
        os.makedirs(assets, mode=0o700, exist_ok=True)
        assets_ready = True
    except OSError:
        assets_ready = False
    result = {}
    for spec in specs:
        url = spec["url"]
        if not assets_ready:
            result[url] = None
            continue
        try:
            # Always reject dangerous schemes/literals. DNS checks are performed by
            # the network fetcher so injected offline test fetchers remain usable.
            validate_url(url, resolve=False)
            try:
                data = fetcher(url, max_bytes=max_bytes)
            except TypeError:
                data = fetcher(url)  # compatibility for existing one-argument fetchers
            detected = _image_type(data or b"")
            if not data or len(data) > max_bytes or not detected:
                result[url] = None
                continue
            name = filename_for(url, detected[1])
            _atomic_write(os.path.join(assets, name), data)
            result[url] = f"assets/{name}"
        except (OSError, ValueError):
            result[url] = None
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, help="JSON list of {url, source_post_url}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"maximum bytes per image (default {DEFAULT_MAX_BYTES})")
    a = ap.parse_args()
    if a.max_bytes <= 0:
        ap.error("--max-bytes must be positive")
    with open(a.manifest, encoding="utf-8") as f:
        specs = json.load(f)
    result = localize(specs, a.out, max_bytes=a.max_bytes)
    os.makedirs(a.out, exist_ok=True)
    payload = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_write(os.path.join(a.out, "assets-manifest.json"), payload)
    ok = sum(1 for value in result.values() if value)
    print(f"localized {ok}/{len(result)} images -> {a.out}/assets/")


if __name__ == "__main__":
    main()
