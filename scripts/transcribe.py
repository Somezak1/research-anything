#!/usr/bin/env python3
"""Budgeted, idempotent fun-asr transcription.

The caller must provide (or place beside the research artifacts) a manifest with
an explicit ``asr_authorization`` and an estimated media duration. Budget is
reserved under a file lock before each remote task. A timed-out task remains an
active reservation and is never followed by a second paid task until reconciled.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

BASE = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_COST_PER_HOUR = 0.8
TERMINAL_TASK_STATUSES = {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELED", "CANCELLED"}
SETTLED_LEDGER_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED", "CANCELLED"}


class AuthorizationError(RuntimeError):
    pass


class BudgetError(RuntimeError):
    pass


class LedgerError(RuntimeError):
    pass


def _key():
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("缺少环境变量 DASHSCOPE_API_KEY（阿里云百炼 API Key，建议放 ~/.zshrc）")
    return key


def api(path, method="GET", body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", f"Bearer {_key()}")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, data, timeout=60) as response:
        return json.load(response)


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def download(url, dest, runner=subprocess.run):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise RuntimeError("媒体下载只允许 http/https URL")
    result = runner(["curl", "-L", "-sS", "--fail", "--max-time", "600", "-o", dest, url])
    if result.returncode != 0 or not os.path.exists(dest) or not os.path.getsize(dest):
        try:
            os.remove(dest)
        except OSError:
            pass
        raise RuntimeError(f"下载失败: {url[:80]}")
    os.chmod(dest, 0o600)
    return dest


def temp_upload(path, api_fn=api, runner=subprocess.run):
    if not os.path.isfile(path) or not os.path.getsize(path):
        raise RuntimeError(f"待上传文件不存在或为空: {path}")
    if os.path.getsize(path) > 1024 * 1024 * 1024:
        raise RuntimeError("待上传文件超过临时通道 1GB 限制")
    policy = api_fn("/uploads?action=getPolicy&model=fun-asr")["data"]
    key = f"{policy['upload_dir']}/{os.path.basename(path)}"
    result = runner([
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", policy["upload_host"],
        "-F", f"OSSAccessKeyId={policy['oss_access_key_id']}", "-F", f"Signature={policy['signature']}",
        "-F", f"policy={policy['policy']}", "-F", f"key={key}", "-F", "x-oss-object-acl=private",
        "-F", "x-oss-forbid-overwrite=true", "-F", "success_action_status=200", "-F", f"file=@{path}",
    ], capture_output=True, text=True)
    if result.stdout != "200":
        raise RuntimeError(f"临时通道上传失败 HTTP {result.stdout}")
    return "oss://" + key


def submit(url, language, api_fn=api):
    headers = {"X-DashScope-Async": "enable"}
    if url.startswith("oss://"):
        headers["X-DashScope-OssResourceResolve"] = "enable"
    parameters = {"language_hints": [language]} if language else {}
    body = {"model": "fun-asr", "input": {"file_urls": [url]}, "parameters": parameters}
    return api_fn("/services/audio/asr/transcription", "POST", body, headers)["output"]["task_id"]


def wait(task_id, timeout, api_fn=api, sleep=time.sleep, interval=3):
    """Poll at least once; ``None`` means the task is still non-terminal."""
    started = time.monotonic()
    while True:
        sleep(interval)
        state = api_fn(f"/tasks/{task_id}")["output"]
        if state.get("task_status") in TERMINAL_TASK_STATUSES:
            return state
        if time.monotonic() - started >= timeout:
            return None


def pick(state):
    if not state or state.get("task_status") != "SUCCEEDED":
        return False, (state or {}).get("code") or "TIMEOUT"
    result = (state.get("results") or [{}])[0]
    substatus = result.get("subtask_status") or result.get("output", {}).get("subtask_status")
    if substatus == "FAILED":
        return False, result.get("code") or result.get("output", {}).get("code") or "SUBTASK_FAILED"
    transcription_url = result.get("transcription_url") or result.get("output", {}).get("transcription_url")
    if not transcription_url:
        return False, "NO_TRANSCRIPTION_URL"
    return True, {"transcription_url": transcription_url,
                  "billed_seconds": (result.get("usage") or {}).get("duration")}


def _billed_seconds(state) -> float:
    total = 0.0
    for result in (state or {}).get("results") or []:
        duration = (result.get("usage") or {}).get("duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
            total += float(duration)
    return total


def srt_ts(ms):
    hours, rest = divmod(int(ms), 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def build_srt(sentences):
    return "\n".join(
        f"{index}\n{srt_ts(sentence['begin_time'])} --> {srt_ts(sentence['end_time'])}\n{sentence['text']}\n"
        for index, sentence in enumerate(sentences, 1)
    )


def _canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    return urllib.parse.urlunsplit((parsed.scheme.lower(), host + port, parsed.path, "", ""))


def source_fingerprint(url=None, file=None, media_fingerprint=None) -> str:
    """Fingerprint media identity without volatile CDN query signatures."""
    if media_fingerprint:
        seed = f"declared:{media_fingerprint}"
    elif file:
        digest = hashlib.sha256()
        with open(file, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        seed = f"sha256:{digest.hexdigest()}"
    else:
        seed = f"source:{_canonical_url(url)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _find_manifest(out: str, explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    directory = os.path.dirname(os.path.abspath(out))
    for candidate in (os.path.join(directory, "manifest.json"),
                      os.path.join(os.path.dirname(directory), "manifest.json")):
        if os.path.isfile(candidate):
            return candidate
    raise AuthorizationError("找不到 manifest.json；请传 --manifest。未验证 ASR 授权，拒绝调用付费服务")


def _load_budget(manifest_path: str, cost_per_hour: float) -> tuple[dict, float]:
    try:
        with open(manifest_path, encoding="utf-8") as stream:
            manifest = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorizationError(f"无法读取 manifest：{exc}") from exc
    authorization = manifest.get("asr_authorization")
    if not isinstance(authorization, dict) or authorization.get("authorized") is not True:
        raise AuthorizationError("manifest 未明确授权付费 ASR")
    hours = authorization.get("max_hours")
    cost = authorization.get("max_cost_cny")
    if (not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0
            or not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost <= 0):
        raise AuthorizationError("ASR 授权必须同时包含正数 max_hours 和 max_cost_cny")
    if cost_per_hour <= 0:
        raise AuthorizationError("cost_per_hour 必须为正数")
    max_seconds = min(float(hours) * 3600, float(cost) / cost_per_hour * 3600)
    return authorization, max_seconds


@contextlib.contextmanager
def _ledger_lock(path: str):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    lock_path = path + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_ledger_unlocked(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("record is not an object")
                rows.append(row)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LedgerError(f"ASR 台账损坏（line {line_no if 'line_no' in locals() else '?'}）：{exc}") from exc
    return rows


def _append_unlocked(path: str, row: dict) -> None:
    payload = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.fchmod(fd, 0o600)
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError("short ledger write")
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise LedgerError(f"ASR 台账写入失败，拒绝继续：{exc}") from exc


def ledger_append(path, meta):
    """Durably append one event; unlike v2, failure is fatal."""
    row = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), **meta}
    with _ledger_lock(path):
        _read_ledger_unlocked(path)
        _append_unlocked(path, row)
    return row


def _latest_attempts(rows: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    attempts, legacy = {}, []
    for row in rows:
        attempt_id = row.get("attempt_id")
        if attempt_id:
            attempts[str(attempt_id)] = row
        elif row.get("billed_seconds") is not None:
            legacy.append(row)
    return attempts, legacy


def _fingerprint_for_row(row: dict) -> str | None:
    if row.get("source_fingerprint"):
        return row["source_fingerprint"]
    source = row.get("source")
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        return source_fingerprint(url=source)
    return None


def _reserve(ledger: str, fingerprint: str, source: str, expected_seconds: float,
             max_seconds: float, language: str | None, stage: str) -> dict:
    attempt_id = uuid.uuid4().hex
    with _ledger_lock(ledger):
        rows = _read_ledger_unlocked(ledger)
        attempts, legacy = _latest_attempts(rows)
        for row in list(attempts.values()) + legacy:
            if _fingerprint_for_row(row) != fingerprint:
                continue
            if row.get("status") == "SUCCEEDED":
                reuse = {"event": "REUSED", "status": "REUSED", "attempt_id": attempt_id,
                         "reused_attempt_id": row.get("attempt_id"), "source": source,
                         "source_fingerprint": fingerprint, "reserved_seconds": 0,
                         "language": language, "stage": stage,
                         "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
                _append_unlocked(ledger, reuse)
                return {"attempt_id": attempt_id, "reuse": row}
            if row.get("status") not in SETTLED_LEDGER_STATUSES | {"REUSED"}:
                if row.get("task_id"):
                    return {"attempt_id": row.get("attempt_id"), "resume": row}
                raise BudgetError("相同媒体已有未终结且无 task_id 的 ASR 尝试；拒绝重复计费，请先核对台账")

        billed = sum(float(row.get("billed_seconds") or 0) for row in legacy)
        active_reserved = 0.0
        for row in attempts.values():
            status = row.get("status")
            if status in SETTLED_LEDGER_STATUSES:
                billed += float(row.get("billed_seconds") or 0)
            elif status != "REUSED":
                active_reserved += float(row.get("reserved_seconds") or 0)
        if billed + active_reserved + expected_seconds > max_seconds + 1e-9:
            raise BudgetError(
                f"ASR 预算不足：已结算 {billed:.1f}s，活跃预留 {active_reserved:.1f}s，"
                f"本次需 {expected_seconds:.1f}s，上限 {max_seconds:.1f}s")
        row = {"event": "RESERVED", "status": "RESERVED", "attempt_id": attempt_id,
               "source": source, "source_fingerprint": fingerprint,
               "reserved_seconds": expected_seconds, "language": language, "stage": stage,
               "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        _append_unlocked(ledger, row)
    return {"attempt_id": attempt_id}


def _event(ledger: str, attempt_id: str, **fields) -> dict:
    row = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "attempt_id": attempt_id, **fields}
    with _ledger_lock(ledger):
        attempts, _ = _latest_attempts(_read_ledger_unlocked(ledger))
        current = attempts.get(attempt_id)
        if current and current.get("status") in SETTLED_LEDGER_STATUSES:
            return current
        _append_unlocked(ledger, row)
    return row


def _settle(ledger: str, attempt_id: str, **fields) -> dict:
    """Append billing exactly once even when multiple processes resume one task."""
    row = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "attempt_id": attempt_id, **fields}
    with _ledger_lock(ledger):
        attempts, _ = _latest_attempts(_read_ledger_unlocked(ledger))
        current = attempts.get(attempt_id)
        if current and current.get("status") in SETTLED_LEDGER_STATUSES:
            return current
        _append_unlocked(ledger, row)
    return row


def _state_reserve(db: str | None, run_id: str | None, finding_id: str | None,
                   fingerprint: str, stage: str, language: str | None,
                   expected_seconds: float, cost_per_hour: float) -> dict | None:
    """Reserve the canonical v3 budget before touching the provider."""
    if not db:
        return None
    if not finding_id:
        raise AuthorizationError("v3 ASR 调用必须传 --finding-id，使预算和证据能关联")
    import researchctl
    material = json.dumps({"media": fingerprint, "model": "fun-asr", "stage": stage,
                           "language": language}, sort_keys=True, separators=(",", ":"))
    key = "fun-asr:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    result = researchctl.reserve_budget(db, {
        "idempotency_key": key,
        "finding_id": finding_id,
        "model": "fun-asr",
        "options": {"stage": stage, "language": language},
        "requested_asr_seconds": expected_seconds,
        "requested_cost": expected_seconds / 3600 * cost_per_hour,
    }, run_id)
    result["state_idempotency_key"] = key
    return result


def _state_unknown(db: str | None, run_id: str | None, state: dict | None,
                   task_id: str | None, provider_status: str) -> None:
    if not db or not state:
        return
    import researchctl
    researchctl.settle_budget(db, {
        "idempotency_key": state["state_idempotency_key"],
        "status": "unknown",
        "provider_task_id": task_id,
        "provider_status": provider_status,
    }, run_id)


def _state_settle(db: str | None, run_id: str | None, state: dict | None,
                  task_id: str | None, billed_seconds: float,
                  cost_per_hour: float, provider_status: str) -> None:
    if not db or not state:
        return
    import researchctl
    researchctl.settle_budget(db, {
        "idempotency_key": state["state_idempotency_key"],
        "status": "settled",
        "provider_task_id": task_id,
        "charged_asr_seconds": billed_seconds,
        "charged_cost": billed_seconds / 3600 * cost_per_hour,
        "provider_status": provider_status,
    }, run_id)


def _run_paid_attempt(url: str, source: str, fingerprint: str, expected_seconds: float,
                      max_seconds: float, language, timeout, ledger, stage, api_fn, sleep,
                      db=None, run_id=None, finding_id=None,
                      cost_per_hour=DEFAULT_COST_PER_HOUR):
    state_budget = _state_reserve(
        db, run_id, finding_id, fingerprint, stage, language, expected_seconds, cost_per_hour
    )
    reservation = _reserve(ledger, fingerprint, source, expected_seconds, max_seconds, language, stage)
    attempt_id = reservation["attempt_id"]
    reuse = reservation.get("reuse")
    if reuse is not None:
        if state_budget and state_budget.get("status") not in {"settled", "released"}:
            _state_settle(db, run_id, state_budget, reuse.get("task_id"), 0,
                          cost_per_hour, "REUSED")
        return {"ok": True, "reused": True, "terminal": True, "attempt_id": attempt_id,
                "task_id": reuse.get("task_id"), "payload": reuse, "state": None}
    if state_budget and state_budget.get("status") in {"settled", "released"}:
        _settle(ledger, attempt_id, event="STATE_REPLAY", status="FAILED",
                task_id=state_budget.get("provider_task_id"), source=source,
                source_fingerprint=fingerprint, reserved_seconds=expected_seconds,
                stage=stage, billed_seconds=0, code="STATE_ATTEMPT_ALREADY_FINAL")
        return {"ok": False, "terminal": True, "attempt_id": attempt_id,
                "task_id": state_budget.get("provider_task_id"),
                "code": "STATE_ATTEMPT_ALREADY_FINAL"}
    resume = reservation.get("resume")
    reserved_seconds = float((resume or {}).get("reserved_seconds") or expected_seconds)
    attempt_stage = (resume or {}).get("stage") or stage
    if resume is not None:
        task_id = resume["task_id"]
        _event(ledger, attempt_id, event="RESUMED", status="SUBMITTED", task_id=task_id,
               source=source, source_fingerprint=fingerprint, reserved_seconds=reserved_seconds,
               stage=attempt_stage)
    else:
        try:
            task_id = submit(url, language, api_fn)
        except Exception as exc:
            _event(ledger, attempt_id, event="SUBMIT_ERROR", status="ERROR_UNKNOWN",
                   source=source, source_fingerprint=fingerprint, reserved_seconds=reserved_seconds,
                   stage=attempt_stage, code=type(exc).__name__, error=str(exc))
            _state_unknown(db, run_id, state_budget, None, "SUBMIT_ERROR_UNKNOWN")
            return {"ok": False, "terminal": False, "attempt_id": attempt_id,
                    "task_id": None, "code": type(exc).__name__}
        _event(ledger, attempt_id, event="SUBMITTED", status="SUBMITTED", task_id=task_id,
               source=source, source_fingerprint=fingerprint, reserved_seconds=reserved_seconds,
               stage=attempt_stage)
        _state_unknown(db, run_id, state_budget, task_id, "SUBMITTED")
    try:
        state = wait(task_id, timeout, api_fn, sleep)
    except Exception as exc:
        _event(ledger, attempt_id, event="POLL_ERROR", status="ERROR_UNKNOWN", task_id=task_id,
               source=source, source_fingerprint=fingerprint, reserved_seconds=reserved_seconds,
               stage=attempt_stage, code=type(exc).__name__, error=str(exc))
        _state_unknown(db, run_id, state_budget, task_id, "POLL_ERROR_UNKNOWN")
        return {"ok": False, "terminal": False, "attempt_id": attempt_id,
                "task_id": task_id, "code": type(exc).__name__}
    if state is None:
        _event(ledger, attempt_id, event="TIMEOUT", status="TIMEOUT_NON_TERMINAL", task_id=task_id,
               source=source, source_fingerprint=fingerprint, reserved_seconds=reserved_seconds,
               stage=attempt_stage,
               code="TIMEOUT_NON_TERMINAL")
        _state_unknown(db, run_id, state_budget, task_id, "TIMEOUT_NON_TERMINAL")
        return {"ok": False, "terminal": False, "attempt_id": attempt_id,
                "task_id": task_id, "code": "TIMEOUT_NON_TERMINAL"}
    if state.get("task_status") == "UNKNOWN":
        _event(ledger, attempt_id, event="UNKNOWN", status="ERROR_UNKNOWN", task_id=task_id,
               source=source, source_fingerprint=fingerprint, reserved_seconds=reserved_seconds,
               stage=attempt_stage, code=state.get("code") or "TASK_STATUS_UNKNOWN")
        _state_unknown(db, run_id, state_budget, task_id, "TASK_STATUS_UNKNOWN")
        return {"ok": False, "terminal": False, "attempt_id": attempt_id,
                "task_id": task_id, "code": state.get("code") or "TASK_STATUS_UNKNOWN"}
    ok, payload = pick(state)
    task_status = state.get("task_status")
    billed = payload.get("billed_seconds") if ok else _billed_seconds(state)
    final_status = "SUCCEEDED" if ok else (task_status if task_status in {"CANCELED", "CANCELLED"} else "FAILED")
    event = _settle(ledger, attempt_id, event="TERMINAL", status=final_status, task_id=task_id,
                    source=source, source_fingerprint=fingerprint, reserved_seconds=reserved_seconds,
                    stage=attempt_stage, billed_seconds=billed or 0,
                    code=None if ok else payload, task_status=task_status,
                    transcription_url=payload.get("transcription_url") if ok else None)
    _state_settle(db, run_id, state_budget, task_id, float(billed or 0),
                  cost_per_hour, final_status)
    return {"ok": ok, "terminal": True, "attempt_id": attempt_id, "task_id": task_id,
            "payload": payload, "state": state, "event": event,
            "code": None if ok else payload}


def _atomic_output(path: str, data: bytes) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def transcribe(url=None, file=None, out="out", language="zh", timeout=180, ledger=None,
               manifest=None, estimated_seconds=None, media_fingerprint=None,
               cost_per_hour=DEFAULT_COST_PER_HOUR, db=None, run_id=None, finding_id=None,
               api_fn=api, fetch=fetch_json, dl=download, up=temp_upload, sleep=time.sleep):
    started = time.monotonic()
    if bool(url) == bool(file):
        raise ValueError("必须且只能提供 url/file 之一")
    if not isinstance(estimated_seconds, (int, float)) or isinstance(estimated_seconds, bool) or estimated_seconds <= 0:
        raise AuthorizationError("调用付费 ASR 前必须提供正数 --estimated-seconds 以原子预留预算")
    if db:
        import researchctl
        max_seconds = researchctl.status(db, run_id)["gate"]["budget"]["asr_seconds_limit"]
    else:
        manifest_path = _find_manifest(out, manifest)
        _, max_seconds = _load_budget(manifest_path, cost_per_hour)
    ledger_path = ledger or os.path.join(os.path.dirname(os.path.abspath(out)), "asr_ledger.jsonl")
    source = url or os.path.abspath(file)
    fingerprint = source_fingerprint(url=url, file=file, media_fingerprint=media_fingerprint)

    if url:
        result = _run_paid_attempt(url, source, fingerprint, float(estimated_seconds), max_seconds,
                                   language, timeout, ledger_path, "direct", api_fn, sleep,
                                   db, run_id, finding_id, cost_per_hour)
        path_used = "direct"
        # A fallback is permitted only after the provider has reported a terminal
        # failure. TIMEOUT/POLL_ERROR keeps its reservation and stops here.
        if not result["ok"] and result["terminal"]:
            fd, temp_path = tempfile.mkstemp(prefix="transcribe_dl_", suffix=".media")
            os.close(fd)
            os.chmod(temp_path, 0o600)
            try:
                uploaded = up(dl(url, temp_path), api_fn)
                result = _run_paid_attempt(uploaded, source, fingerprint, float(estimated_seconds), max_seconds,
                                           language, timeout, ledger_path, "fallback_download_upload", api_fn, sleep,
                                           db, run_id, finding_id, cost_per_hour)
                path_used = "fallback_download_upload"
            finally:
                try:
                    os.remove(temp_path)
                except FileNotFoundError:
                    pass
    else:
        uploaded = up(file, api_fn)
        result = _run_paid_attempt(uploaded, source, fingerprint, float(estimated_seconds), max_seconds,
                                   language, timeout, ledger_path, "upload", api_fn, sleep,
                                   db, run_id, finding_id, cost_per_hour)
        path_used = "upload"

    meta = {"source": source, "source_fingerprint": fingerprint, "path_used": path_used,
            "task_id": result.get("task_id"), "attempt_id": result.get("attempt_id"),
            "wall_seconds": round(time.monotonic() - started, 1)}
    if not result["ok"]:
        meta.update(status="FAILED", code=result.get("code"), terminal=result.get("terminal", False))
        return meta

    payload = result["payload"]
    if result.get("reused") and not payload.get("transcription_url"):
        # Legacy ledger rows predate durable result URLs. Reuse their existing
        # artifacts if present, but never submit a duplicate paid task.
        meta.update(status="SUCCEEDED", reused=True, billed_seconds=0,
                    original_task_id=payload.get("task_id"), txt=payload.get("txt"), srt=payload.get("srt"))
        return meta
    data = fetch(payload["transcription_url"])
    transcript = data["transcripts"][0]
    _atomic_output(out + ".txt", transcript["text"].encode("utf-8"))
    _atomic_output(out + ".srt", build_srt(transcript["sentences"]).encode("utf-8"))
    _atomic_output(out + ".json", json.dumps(data, ensure_ascii=False).encode("utf-8"))
    meta.update(status="SUCCEEDED", reused=bool(result.get("reused")),
                billed_seconds=0 if result.get("reused") else payload.get("billed_seconds"),
                sentences=len(transcript["sentences"]), chars=len(transcript["text"]),
                txt=out + ".txt", srt=out + ".srt")
    # Delivery is a separate informational event. It intentionally has no
    # attempt_id/billed_seconds, so append-only billing consumers count the paid
    # terminal event exactly once and the task state remains authoritative.
    ledger_append(ledger_path, {"event": "OUTPUT_WRITTEN", "status": "DELIVERED",
                                "parent_attempt_id": result["attempt_id"],
                                "task_id": result.get("task_id"), "source": source,
                                "source_fingerprint": fingerprint, "stage": path_used,
                                "txt": out + ".txt", "srt": out + ".srt", "json": out + ".json"})
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="公网媒体直链（http/https）")
    group.add_argument("--file", help="本地音视频文件")
    parser.add_argument("--out", required=True, help="输出前缀")
    parser.add_argument("--language", default="zh", help="语种提示；传空串关闭")
    parser.add_argument("--timeout", type=int, default=180, help="单次轮询秒数；超时不自动启动第二任务")
    parser.add_argument("--ledger", help="费用台账 JSONL；默认 <out 所在目录>/asr_ledger.jsonl")
    parser.add_argument("--manifest", help="授权 manifest；默认从 out 所在目录或其父目录发现")
    parser.add_argument("--db", help="v3 research.db；提供后由 canonical budget broker 授权并结算")
    parser.add_argument("--run-id", help="v3 run id；数据库只有一个 run 时可省略")
    parser.add_argument("--finding-id", help="v3 finding id；使用 --db 时必填")
    parser.add_argument("--estimated-seconds", type=float, required=True,
                        help="媒体预计计费秒数；调用前据此原子预留预算")
    parser.add_argument("--media-fingerprint", help="稳定 finding/media ID；用于跨签名 URL 幂等")
    parser.add_argument("--cost-per-hour", type=float, default=DEFAULT_COST_PER_HOUR,
                        help=f"预算换算单价，默认 {DEFAULT_COST_PER_HOUR} CNY/hour")
    args = parser.parse_args()
    try:
        meta = transcribe(url=args.url, file=args.file, out=args.out, language=args.language or None,
                          timeout=args.timeout, ledger=args.ledger, manifest=args.manifest,
                          estimated_seconds=args.estimated_seconds, media_fingerprint=args.media_fingerprint,
                          cost_per_hour=args.cost_per_hour, db=args.db,
                          run_id=args.run_id, finding_id=args.finding_id)
    except Exception as exc:
        meta = {"source": args.url or args.file, "status": "FAILED",
                "code": type(exc).__name__, "error": str(exc)}
    print(json.dumps(meta, ensure_ascii=False))
    if meta["status"] != "SUCCEEDED":
        sys.exit(2)


if __name__ == "__main__":
    main()
