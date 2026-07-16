#!/usr/bin/env python3
"""Canonical, auditable state store for the v3 research workflow.

Python 3.11+ and the standard library are the only runtime requirements.  The
SQLite database is the source of truth; JSON/JSONL files created by ``export``
are projections and must never be edited as workflow state.

The CLI accepts structured input through ``--input PATH`` (or ``--input -`` for
stdin).  Common shapes are documented in each sub-command's ``--help`` output.
All monetary values in the public interface are decimal cost units (normally
CNY); internally they are integer millionths.  ASR duration is exposed as
seconds and stored as integer milliseconds.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import decimal
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
import uuid
from collections.abc import Iterable, Iterator, Mapping
from typing import Any


SCHEMA_VERSION = 3
APP_VERSION = "3.0.0"
DECISION_STATUSES = {"production-ready", "pilot-only", "blocked"}
DISPOSITIONS = {"pending", "consumed", "excluded"}
SUFFICIENCY_STATUSES = {"insufficient", "sufficient", "not_applicable"}
ATTEMPT_STATUSES = {"reserved", "unknown", "settled", "released"}
PROFILES = {"technical", "travel", "policy-forecast", "generic"}
KNOWN_CHANNELS = {
    "douyin", "xiaohongshu", "zhihu", "bilibili",
    "youtube", "github", "twitter", "web",
}
DEEPEN_REASONS = {
    "critical-gap", "contradiction", "new-candidate",
    "independence", "freshness", "user-constraint",
}
VOLATILE_QUERY_KEYS = {
    "auth", "auth_key", "authorization", "credential", "expires", "expiry",
    "policy", "signature", "sig", "token", "x-expires", "x-signature",
    "x-amz-algorithm", "x-amz-credential", "x-amz-date", "x-amz-expires",
    "x-amz-security-token", "x-amz-signature", "x-amz-signedheaders",
    "x-goog-algorithm", "x-goog-credential", "x-goog-date", "x-goog-expires",
    "x-goog-signature", "x-goog-signedheaders",
}
HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
UTC = dt.timezone.utc


class ResearchError(Exception):
    """Base class for expected CLI errors."""


class InputError(ResearchError):
    """Structured input is malformed or violates the public contract."""


class BudgetError(ResearchError):
    """A reservation would exceed the configured hard budget."""


def utc_now() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expect_object(value: Any, label: str = "input") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"{label} must be a JSON object")
    return value


def _string(
    data: Mapping[str, Any], key: str, *, required: bool = False,
    allow_empty: bool = False, default: str | None = None,
) -> str | None:
    value = data.get(key, default)
    if value is None:
        if required:
            raise InputError(f"{key} is required")
        return None
    if not isinstance(value, str):
        raise InputError(f"{key} must be a string")
    if not allow_empty and not value.strip():
        raise InputError(f"{key} must not be empty")
    return value


def _boolean(data: Mapping[str, Any], key: str, *, default: bool | None = None) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise InputError(f"{key} must be a boolean")
    return value


def _integer(
    data: Mapping[str, Any], key: str, *, default: int | None = None,
    minimum: int | None = None,
) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InputError(f"{key} must be an integer")
    if minimum is not None and value < minimum:
        raise InputError(f"{key} must be >= {minimum}")
    return value


def _number(data: Mapping[str, Any], key: str, *, default: float | int = 0) -> float:
    value = data.get(key, default)
    if not isinstance(value, (int, float, decimal.Decimal)) or isinstance(value, bool):
        raise InputError(f"{key} must be a finite non-negative number")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise InputError(f"{key} must be a finite non-negative number")
    return value


def _string_array(
    data: Mapping[str, Any], key: str, *, required: bool = False,
) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise InputError(f"{key} must be an array of non-empty strings")
    if required and not value:
        raise InputError(f"{key} must not be empty")
    if len(set(value)) != len(value):
        raise InputError(f"{key} must not contain duplicates")
    return value


def _seconds_to_ms(value: Any, label: str) -> int:
    return int((decimal.Decimal(str(_number({label: value}, label))) * 1000).quantize(
        decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP
    ))


def _cost_to_micros(value: Any, label: str) -> int:
    return int((decimal.Decimal(str(_number({label: value}, label))) * 1_000_000).quantize(
        decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP
    ))


def _ms_to_seconds(value: int) -> float:
    return value / 1000


def _micros_to_cost(value: int) -> float:
    return value / 1_000_000


def load_json_input(path: str) -> dict[str, Any]:
    try:
        if path == "-":
            value = json.load(sys.stdin)
        else:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read JSON input {path!r}: {exc}") from exc
    return _expect_object(value)


def _validate_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value):
        raise InputError(f"{label} must match {ID_RE.pattern}")
    return value


def canonicalize_url(value: str) -> str:
    """Remove fragments and volatile signatures without changing content identity."""
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError as exc:
        raise InputError(f"invalid URL: {exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise InputError("source and media URLs must use http or https")
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise InputError(f"invalid URL port: {exc}") from exc
    netloc = host
    if port and not ((parsed.scheme.lower() == "http" and port == 80)
                     or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path or "/"), safe="/%:@!$&'()*+,;=-._~")
    pairs = []
    for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.lower()
        if normalized_key in VOLATILE_QUERY_KEYS or "signature" in normalized_key:
            continue
        pairs.append((key, item))
    query = urllib.parse.urlencode(sorted(pairs), doseq=True)
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def finding_fingerprint(data: Mapping[str, Any]) -> str:
    channel = _string(data, "channel", required=True)
    source_id = _string(data, "source_id")
    if source_id is not None:
        locator = f"source-id:{source_id.strip()}"
    else:
        locator = f"url:{canonicalize_url(_string(data, 'source_url', required=True) or '')}"
    material = canonical_json({"v": 1, "channel": channel.strip().lower(), "locator": locator})
    return sha256_text(material)


def media_fingerprint(data: Mapping[str, Any]) -> str | None:
    digest = _string(data, "media_sha256")
    media_id = _string(data, "media_id")
    media_url = _string(data, "media_url")
    if digest is not None:
        if not HEX_64.fullmatch(digest):
            raise InputError("media_sha256 must contain exactly 64 hexadecimal characters")
        material = f"sha256:{digest.lower()}"
    elif media_id is not None:
        channel = _string(data, "channel", required=True) or ""
        material = f"media-id:{channel.strip().lower()}:{media_id.strip()}"
    elif media_url is not None:
        material = f"url:{canonicalize_url(media_url)}"
    else:
        return None
    return "med_" + sha256_text(material)[:32]


def _connect(path: str | os.PathLike[str], *, must_exist: bool = True) -> sqlite3.Connection:
    path = os.fspath(path)
    if must_exist and not os.path.isfile(path):
        raise ResearchError(f"database does not exist: {path}")
    try:
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    except sqlite3.Error as exc:
        raise ResearchError(f"cannot open database: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


SCHEMA_SQL = r"""
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    profile TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','complete','blocked')),
    require_critical_claims INTEGER NOT NULL CHECK (require_critical_claims IN (0,1)),
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    verbatim TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX events_run_seq ON events(run_id, seq);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE plans (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    plan_sha256 TEXT NOT NULL CHECK (length(plan_sha256) = 64),
    scope_sha256 TEXT NOT NULL CHECK (length(scope_sha256) = 64),
    approval_event_id INTEGER NOT NULL REFERENCES events(seq),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE plan_revisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    plan_sha256 TEXT NOT NULL CHECK (length(plan_sha256) = 64),
    scope_sha256 TEXT NOT NULL CHECK (length(scope_sha256) = 64),
    approval_event_id INTEGER NOT NULL REFERENCES events(seq),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, revision)
) STRICT;
CREATE INDEX plan_revisions_run ON plan_revisions(run_id, revision);
CREATE TRIGGER plan_revisions_no_update BEFORE UPDATE ON plan_revisions
BEGIN SELECT RAISE(ABORT, 'plan revisions are append-only'); END;
CREATE TRIGGER plan_revisions_no_delete BEFORE DELETE ON plan_revisions
BEGIN SELECT RAISE(ABORT, 'plan revisions are append-only'); END;

CREATE TABLE findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    fingerprint TEXT NOT NULL,
    channel TEXT NOT NULL,
    source_url TEXT NOT NULL,
    canonical_source_url TEXT NOT NULL,
    source_id TEXT,
    media_fingerprint TEXT,
    title TEXT NOT NULL,
    headline TEXT NOT NULL,
    note TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    disposition TEXT NOT NULL DEFAULT 'pending'
        CHECK (disposition IN ('pending','consumed','excluded')),
    disposition_reason TEXT,
    disposition_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, fingerprint)
) STRICT;
CREATE INDEX findings_run_disposition ON findings(run_id, disposition);
CREATE INDEX findings_run_media ON findings(run_id, media_fingerprint);

CREATE TABLE finding_revisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE(finding_id, revision)
) STRICT;
CREATE INDEX finding_revisions_run ON finding_revisions(run_id, finding_id, revision);
CREATE TRIGGER finding_revisions_no_update BEFORE UPDATE ON finding_revisions
BEGIN SELECT RAISE(ABORT, 'finding revisions are append-only'); END;
CREATE TRIGGER finding_revisions_no_delete BEFORE DELETE ON finding_revisions
BEGIN SELECT RAISE(ABORT, 'finding revisions are append-only'); END;

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    finding_id TEXT REFERENCES findings(id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    media_fingerprint TEXT,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, kind, sha256)
) STRICT;

CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    candidate_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, canonical_name, candidate_type)
) STRICT;

CREATE TABLE evidence_clusters (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    label TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    independent_source_count INTEGER NOT NULL CHECK (independent_source_count >= 0),
    source_fingerprints_json TEXT NOT NULL CHECK (json_valid(source_fingerprints_json)),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, fingerprint)
) STRICT;

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    text TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    critical INTEGER NOT NULL CHECK (critical IN (0,1)),
    sufficiency TEXT NOT NULL
        CHECK (sufficiency IN ('insufficient','sufficient','not_applicable')),
    required_evidence_count INTEGER NOT NULL CHECK (required_evidence_count >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, fingerprint)
) STRICT;

CREATE TABLE claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    evidence_cluster_id TEXT NOT NULL REFERENCES evidence_clusters(id),
    PRIMARY KEY (claim_id, evidence_cluster_id)
) STRICT;

CREATE TABLE finding_claims (
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    relation TEXT NOT NULL DEFAULT 'supports',
    PRIMARY KEY (finding_id, claim_id, relation)
) STRICT;

CREATE TABLE budgets (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    currency TEXT NOT NULL,
    asr_limit_ms INTEGER NOT NULL CHECK (asr_limit_ms >= 0),
    cost_limit_micros INTEGER NOT NULL CHECK (cost_limit_micros >= 0),
    reserved_asr_ms INTEGER NOT NULL DEFAULT 0 CHECK (reserved_asr_ms >= 0),
    spent_asr_ms INTEGER NOT NULL DEFAULT 0 CHECK (spent_asr_ms >= 0),
    reserved_cost_micros INTEGER NOT NULL DEFAULT 0 CHECK (reserved_cost_micros >= 0),
    spent_cost_micros INTEGER NOT NULL DEFAULT 0 CHECK (spent_cost_micros >= 0),
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT,
    finding_id TEXT REFERENCES findings(id),
    media_fingerprint TEXT,
    provider_task_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('reserved','unknown','settled','released')),
    requested_asr_ms INTEGER NOT NULL CHECK (requested_asr_ms >= 0),
    requested_cost_micros INTEGER NOT NULL CHECK (requested_cost_micros >= 0),
    charged_asr_ms INTEGER NOT NULL DEFAULT 0 CHECK (charged_asr_ms >= 0),
    charged_cost_micros INTEGER NOT NULL DEFAULT 0 CHECK (charged_cost_micros >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    settled_at TEXT,
    UNIQUE(run_id, kind, idempotency_key),
    UNIQUE(run_id, kind, request_fingerprint)
) STRICT;
CREATE INDEX attempts_run_status ON attempts(run_id, status);

CREATE TABLE decision_revisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    plan_revision INTEGER NOT NULL,
    input_event_seq INTEGER NOT NULL CHECK (input_event_seq >= 0),
    decision_sha256 TEXT NOT NULL CHECK (length(decision_sha256) = 64),
    contract_verbatim TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL CHECK (length(contract_sha256) = 64),
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0,1)),
    user_confirmation_event_id INTEGER REFERENCES events(seq),
    requested_status TEXT NOT NULL
        CHECK (requested_status IN ('production-ready','pilot-only','blocked')),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, revision),
    FOREIGN KEY (run_id, plan_revision) REFERENCES plan_revisions(run_id, revision)
) STRICT;
CREATE INDEX decision_revisions_run ON decision_revisions(run_id, revision);
CREATE TRIGGER decision_revisions_no_update BEFORE UPDATE ON decision_revisions
BEGIN SELECT RAISE(ABORT, 'decision revisions are append-only'); END;
CREATE TRIGGER decision_revisions_no_delete BEFORE DELETE ON decision_revisions
BEGIN SELECT RAISE(ABORT, 'decision revisions are append-only'); END;

CREATE TABLE decisions (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    plan_revision INTEGER NOT NULL,
    input_event_seq INTEGER NOT NULL CHECK (input_event_seq >= 0),
    decision_sha256 TEXT NOT NULL CHECK (length(decision_sha256) = 64),
    contract_verbatim TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL CHECK (length(contract_sha256) = 64),
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0,1)),
    user_confirmation_event_id INTEGER REFERENCES events(seq),
    requested_status TEXT NOT NULL
        CHECK (requested_status IN ('production-ready','pilot-only','blocked')),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id, revision) REFERENCES decision_revisions(run_id, revision),
    FOREIGN KEY (run_id, plan_revision) REFERENCES plan_revisions(run_id, revision)
) STRICT;

CREATE TABLE deliveries (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    gate_status TEXT NOT NULL
        CHECK (gate_status IN ('production-ready','pilot-only','blocked')),
    files_json TEXT NOT NULL CHECK (json_valid(files_json)),
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX deliveries_run_created ON deliveries(run_id, created_at);
"""


def _default_run_id(conn: sqlite3.Connection, explicit: str | None = None) -> str:
    if explicit:
        _validate_id(explicit, "run_id")
        row = conn.execute("SELECT 1 FROM runs WHERE id=?", (explicit,)).fetchone()
        if row is None:
            raise ResearchError(f"unknown run_id: {explicit}")
        return explicit
    row = conn.execute("SELECT value FROM metadata WHERE key='default_run_id'").fetchone()
    if row is None:
        raise ResearchError("database has no default_run_id metadata")
    return row[0]


def init_database(path: str | os.PathLike[str], data: Mapping[str, Any]) -> dict[str, Any]:
    data = _expect_object(dict(data))
    allowed_fields = {
        "run_id", "objective", "profile", "asr_seconds_limit", "asr_cost_limit",
        "currency", "require_critical_claims", "metadata",
    }
    unknown_fields = sorted(set(data) - allowed_fields)
    if unknown_fields:
        raise InputError(f"init contains unsupported fields: {unknown_fields}")
    path = os.fspath(path)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        raise ResearchError(f"refusing to initialize non-empty database: {path}")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    objective = _string(data, "objective", required=True) or ""
    profile = _string(data, "profile", default="generic") or "generic"
    if profile not in PROFILES:
        raise InputError(f"profile must be one of {sorted(PROFILES)}")
    run_id = _string(data, "run_id") or f"run_{uuid.uuid4().hex}"
    _validate_id(run_id, "run_id")
    require_critical = _boolean(data, "require_critical_claims", default=True)
    asr_limit_ms = _seconds_to_ms(data.get("asr_seconds_limit", 0), "asr_seconds_limit")
    cost_limit_micros = _cost_to_micros(data.get("asr_cost_limit", 0), "asr_cost_limit")
    if asr_limit_ms or cost_limit_micros:
        raise InputError(
            "new runs must start with zero ASR limits; preserve the user's numeric "
            "authorization event, then call authorize-budget"
        )
    currency = _string(data, "currency", default="CNY") or "CNY"
    now = utc_now()
    conn = _connect(path, must_exist=False)
    try:
        # The canonical store contains verbatim user messages and may contain private
        # constraints. Do not leave it readable under a permissive process umask.
        os.chmod(path, 0o600)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        with transaction(conn, immediate=True):
            conn.executescript(SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.executemany(
                "INSERT INTO metadata(key,value) VALUES (?,?)",
                [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("app_version", APP_VERSION),
                    ("default_run_id", run_id),
                    ("created_at", now),
                ],
            )
            config = dict(data)
            config.update({
                "asr_seconds_limit": _ms_to_seconds(asr_limit_ms),
                "asr_cost_limit": _micros_to_cost(cost_limit_micros),
                "currency": currency,
                "require_critical_claims": require_critical,
            })
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, objective, profile, "active", int(require_critical),
                 canonical_json(config), now, now),
            )
            conn.execute(
                "INSERT INTO budgets VALUES (?,?,?,?,0,0,0,0,?)",
                (run_id, currency, asr_limit_ms, cost_limit_micros, now),
            )
            _insert_event(conn, run_id, "run.initialized", "system", canonical_json(config), now)
    except BaseException:
        conn.close()
        with contextlib.suppress(OSError):
            if os.path.exists(path) and os.path.getsize(path) == 0:
                os.unlink(path)
        raise
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    return {"schema_version": SCHEMA_VERSION, "run_id": run_id, "db": os.path.abspath(path)}


def _insert_event(
    conn: sqlite3.Connection, run_id: str, event_type: str, actor: str,
    verbatim: str, created_at: str | None = None,
) -> dict[str, Any]:
    if not event_type.strip() or not actor.strip():
        raise InputError("event_type and actor must not be empty")
    created_at = created_at or utc_now()
    digest = sha256_text(verbatim)
    cur = conn.execute(
        "INSERT INTO events(run_id,event_type,actor,verbatim,sha256,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (run_id, event_type, actor, verbatim, digest, created_at),
    )
    return {
        "seq": cur.lastrowid, "run_id": run_id, "event_type": event_type,
        "actor": actor, "verbatim": verbatim, "sha256": digest,
        "created_at": created_at,
    }


def record_event(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    event_type = _string(data, "event_type", required=True) or ""
    actor = _string(data, "actor", required=True) or ""
    verbatim = _string(data, "verbatim", required=True, allow_empty=True)
    assert verbatim is not None
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            return _insert_event(conn, selected, event_type, actor, verbatim)
    finally:
        conn.close()


def authorize_budget(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    """Set or revise hard ASR limits only from a preserved user authorization."""
    data = _expect_object(dict(data))
    asr_limit_ms = _seconds_to_ms(data.get("asr_seconds_limit", 0), "asr_seconds_limit")
    cost_limit_micros = _cost_to_micros(data.get("asr_cost_limit", 0), "asr_cost_limit")
    event_id = _integer(data, "user_authorization_event_id", minimum=1)
    currency = _string(data, "currency", default="CNY") or "CNY"
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            event = _require_user_event(
                conn, selected, event_id, "user_authorization_event_id",
                expected_event_type="user.asr-authorization",
            )
            budget = conn.execute("SELECT * FROM budgets WHERE run_id=?", (selected,)).fetchone()
            assert budget is not None
            committed_asr = budget["spent_asr_ms"] + budget["reserved_asr_ms"]
            committed_cost = budget["spent_cost_micros"] + budget["reserved_cost_micros"]
            if asr_limit_ms < committed_asr or cost_limit_micros < committed_cost:
                raise BudgetError("new limits cannot be below already spent or reserved budget")
            conn.execute(
                "UPDATE budgets SET currency=?,asr_limit_ms=?,cost_limit_micros=?,updated_at=? "
                "WHERE run_id=?",
                (currency, asr_limit_ms, cost_limit_micros, now, selected),
            )
            run = conn.execute("SELECT config_json FROM runs WHERE id=?", (selected,)).fetchone()
            config = json.loads(run["config_json"])
            config.update({
                "asr_seconds_limit": _ms_to_seconds(asr_limit_ms),
                "asr_cost_limit": _micros_to_cost(cost_limit_micros),
                "currency": currency,
                "budget_authorization_event_id": event_id,
            })
            conn.execute(
                "UPDATE runs SET config_json=?,updated_at=? WHERE id=?",
                (canonical_json(config), now, selected),
            )
            _insert_event(conn, selected, "budget.authorized", "system", canonical_json({
                "user_authorization_event_id": event_id,
                "user_event_sha256": event["sha256"],
                "asr_seconds_limit": _ms_to_seconds(asr_limit_ms),
                "asr_cost_limit": _micros_to_cost(cost_limit_micros),
                "currency": currency,
            }), now)
            updated = conn.execute("SELECT * FROM budgets WHERE run_id=?", (selected,)).fetchone()
            return {"run_id": selected, "user_authorization_event_id": event_id,
                    "budget": _budget_public(updated)}
    finally:
        conn.close()


def _validate_plan_payload(data: Mapping[str, Any], *, expected_profile: str) -> dict[str, Any]:
    plan = _expect_object(dict(data), "plan")
    reserved = {
        "schema_version", "run_id", "revision", "plan_sha256", "scope_sha256",
        "created_at", "updated_at",
    }
    if reserved & plan.keys():
        raise InputError(f"plan input contains reserved projection fields: {sorted(reserved & plan.keys())}")
    if _integer(plan, "plan_version", minimum=1) != SCHEMA_VERSION:
        raise InputError(f"plan_version must be {SCHEMA_VERSION}")
    profile = _string(plan, "profile", required=True) or ""
    if profile not in PROFILES or profile != expected_profile:
        raise InputError(f"plan.profile must match run profile {expected_profile!r}")
    overlays = _string_array(plan, "risk_overlays")
    if set(overlays) - {"high-risk"}:
        raise InputError("risk_overlays only supports 'high-risk'")
    _string_array(plan, "dimensions", required=True)
    _string_array(plan, "source_requirements", required=True)
    _integer(plan, "scope_approval_event_id", minimum=1)

    estimates = _expect_object(plan.get("estimates"), "estimates")
    p50 = _number(estimates, "p50_minutes")
    p90 = _number(estimates, "p90_minutes")
    if p50 <= 0 or p90 < p50:
        raise InputError("estimates require 0 < p50_minutes <= p90_minutes")
    _string_array(estimates, "basis", required=True)

    budgets = _expect_object(plan.get("budgets"), "budgets")
    wall_minutes = _number(budgets, "wall_minutes")
    if wall_minutes <= 0:
        raise InputError("budgets.wall_minutes must be > 0")
    _number(budgets, "asr_seconds")
    _number(budgets, "asr_cost_cny")
    account_actions = _boolean(budgets, "account_actions", default=False)
    account_scope = _string_array(plan, "account_action_scope") if "account_action_scope" in plan else []
    if account_actions and not account_scope:
        raise InputError("account_actions=true requires non-empty account_action_scope")
    if not account_actions and account_scope:
        raise InputError("account_action_scope must be empty when account_actions=false")
    for field in ("budget_authorization_event_id", "account_authorization_event_id"):
        value = plan.get(field)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            raise InputError(f"{field} must be null or a positive integer")

    channels = plan.get("channels")
    if not isinstance(channels, list):
        raise InputError("channels must be an array")
    names: list[str] = []
    for index, channel_value in enumerate(channels):
        channel = _expect_object(channel_value, f"channels[{index}]")
        name = _string(channel, "name", required=True) or ""
        if name not in KNOWN_CHANNELS:
            raise InputError(f"channels[{index}].name is not a supported discovery entry")
        names.append(name)
        _string_array(channel, "signals", required=True)
        enabled = _boolean(channel, "enabled", default=True)
        if not enabled:
            _string(channel, "disabled_reason", required=True)
        probe = _expect_object(channel.get("probe"), f"channels[{index}].probe")
        _string_array(probe, "queries", required=True)
        limit = _integer(probe, "limit_per_query", minimum=1)
        if limit > 3:
            raise InputError(f"channels[{index}].probe.limit_per_query must be <= 3")
    if len(names) != len(KNOWN_CHANNELS) or set(names) != KNOWN_CHANNELS:
        missing = sorted(KNOWN_CHANNELS - set(names))
        extra_or_duplicate = sorted(name for name in set(names) if names.count(name) > 1)
        raise InputError(
            "channels must contain each of the eight discovery entries exactly once; "
            f"missing={missing}, duplicates={extra_or_duplicate}"
        )

    deepening = plan.get("deepening", [])
    if not isinstance(deepening, list):
        raise InputError("deepening must be an array")
    for index, batch_value in enumerate(deepening):
        batch = _expect_object(batch_value, f"deepening[{index}]")
        channel = _string(batch, "channel", required=True) or ""
        if channel not in KNOWN_CHANNELS:
            raise InputError(f"deepening[{index}].channel is invalid")
        reason = _string(batch, "reason", required=True) or ""
        if reason not in DEEPEN_REASONS:
            raise InputError(f"deepening[{index}].reason is invalid")
        _string(batch, "decision_gap", required=True)
        _string_array(batch, "queries", required=True)
        for field in ("candidate_ids", "claim_ids"):
            if field in batch:
                _string_array(batch, field)
        limit = _integer(batch, "limit", minimum=1)
        if limit > 5:
            raise InputError(f"deepening[{index}].limit must be <= 5")
    return plan


def _plan_scope_sha256(plan: Mapping[str, Any]) -> str:
    scope = dict(plan)
    for key in (
        "deepening", "estimates", "scope_approval_event_id",
        "budget_authorization_event_id", "account_authorization_event_id",
    ):
        scope.pop(key, None)
    return sha256_text(canonical_json(scope))


def _require_user_event(
    conn: sqlite3.Connection, run_id: str, event_id: int, label: str, *,
    expected_event_type: str,
) -> sqlite3.Row:
    event = conn.execute(
        "SELECT event_type,actor,verbatim,sha256 FROM events WHERE seq=? AND run_id=?",
        (event_id, run_id),
    ).fetchone()
    if (
        event is None or event["actor"] != "user" or not event["verbatim"]
        or event["event_type"] != expected_event_type
    ):
        raise InputError(
            f"{label} must reference a non-empty actor=user event of type "
            f"{expected_event_type!r} in this run"
        )
    return event


def set_plan(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        run = conn.execute("SELECT profile FROM runs WHERE id=?", (selected,)).fetchone()
        assert run is not None
        plan = _validate_plan_payload(data, expected_profile=run["profile"])
        approval_id = _integer(plan, "scope_approval_event_id", minimum=1)
        budget_auth_id = plan.get("budget_authorization_event_id")
        account_auth_id = plan.get("account_authorization_event_id")
        budgets = plan["budgets"]
        plan_asr_ms = _seconds_to_ms(budgets["asr_seconds"], "budgets.asr_seconds")
        plan_cost_micros = _cost_to_micros(budgets["asr_cost_cny"], "budgets.asr_cost_cny")
        account_actions = budgets.get("account_actions", False)
        payload = canonical_json(plan)
        plan_digest = sha256_text(payload)
        scope_digest = _plan_scope_sha256(plan)
        now = utc_now()
        with transaction(conn, immediate=True):
            approval = _require_user_event(
                conn, selected, approval_id, "scope_approval_event_id",
                expected_event_type="user.search-scope-approval",
            )
            budget = conn.execute("SELECT * FROM budgets WHERE run_id=?", (selected,)).fetchone()
            assert budget is not None
            if plan_asr_ms != budget["asr_limit_ms"] or plan_cost_micros != budget["cost_limit_micros"]:
                raise InputError("plan ASR limits must exactly match the canonical budget broker")
            if plan_asr_ms or plan_cost_micros:
                if budget_auth_id is None:
                    raise InputError("non-zero plan ASR limits require budget_authorization_event_id")
                _require_user_event(
                    conn, selected, budget_auth_id, "budget_authorization_event_id",
                    expected_event_type="user.asr-authorization",
                )
                config = json.loads(conn.execute(
                    "SELECT config_json FROM runs WHERE id=?", (selected,)
                ).fetchone()["config_json"])
                if config.get("budget_authorization_event_id") != budget_auth_id:
                    raise InputError("plan budget authorization does not match the budget broker")
            elif budget_auth_id is not None:
                raise InputError("zero ASR limits must not claim a budget authorization event")
            if account_actions:
                if account_auth_id is None:
                    raise InputError("account_actions=true requires account_authorization_event_id")
                _require_user_event(
                    conn, selected, account_auth_id, "account_authorization_event_id",
                    expected_event_type="user.account-authorization",
                )
            elif account_auth_id is not None:
                raise InputError("account_actions=false must not claim an account authorization event")

            reused = conn.execute(
                "SELECT scope_sha256 FROM plan_revisions WHERE run_id=? AND approval_event_id=?",
                (selected, approval_id),
            ).fetchall()
            if any(row["scope_sha256"] != scope_digest for row in reused):
                raise InputError("a scope approval event cannot authorize a materially different plan")
            existing = conn.execute("SELECT * FROM plans WHERE run_id=?", (selected,)).fetchone()
            if existing is not None and existing["plan_sha256"] == plan_digest:
                return {
                    "run_id": selected, "revision": existing["revision"],
                    "plan_sha256": plan_digest, "scope_sha256": scope_digest,
                    "scope_approval_event_id": approval_id, "idempotent_replay": True,
                }
            revision = 1 if existing is None else existing["revision"] + 1
            created = now if existing is None else existing["created_at"]
            conn.execute(
                "INSERT INTO plan_revisions(run_id,revision,plan_sha256,scope_sha256,"
                "approval_event_id,payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
                (selected, revision, plan_digest, scope_digest, approval_id, payload, now),
            )
            conn.execute(
                "INSERT INTO plans(run_id,revision,plan_sha256,scope_sha256,approval_event_id,"
                "payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision,"
                "plan_sha256=excluded.plan_sha256,scope_sha256=excluded.scope_sha256,"
                "approval_event_id=excluded.approval_event_id,payload_json=excluded.payload_json,"
                "updated_at=excluded.updated_at",
                (selected, revision, plan_digest, scope_digest, approval_id, payload, created, now),
            )
            conn.execute("UPDATE runs SET updated_at=? WHERE id=?", (now, selected))
            _insert_event(conn, selected, "plan.set", "system", canonical_json({
                "revision": revision, "plan_sha256": plan_digest,
                "scope_sha256": scope_digest, "scope_approval_event_id": approval_id,
                "user_event_sha256": approval["sha256"],
            }), now)
            return {
                "run_id": selected, "revision": revision, "plan_sha256": plan_digest,
                "scope_sha256": scope_digest, "scope_approval_event_id": approval_id,
                "idempotent_replay": False,
            }
    finally:
        conn.close()


def _require_plan_for_new_reservation(
    conn: sqlite3.Connection, run_id: str, budget: sqlite3.Row,
) -> None:
    run = conn.execute("SELECT profile,config_json FROM runs WHERE id=?", (run_id,)).fetchone()
    plan_row = conn.execute("SELECT * FROM plans WHERE run_id=?", (run_id,)).fetchone()
    if run is None or plan_row is None:
        raise InputError("a user-approved current plan is required before a new reservation")
    try:
        plan = _validate_plan_payload(
            json.loads(plan_row["payload_json"]), expected_profile=run["profile"]
        )
    except (InputError, json.JSONDecodeError, TypeError) as error:
        raise InputError("the current plan is invalid") from error
    if (
        sha256_text(canonical_json(plan)) != plan_row["plan_sha256"]
        or _plan_scope_sha256(plan) != plan_row["scope_sha256"]
    ):
        raise InputError("the current plan hash is invalid")
    revision_row = conn.execute(
        "SELECT * FROM plan_revisions WHERE run_id=? AND revision=?",
        (run_id, plan_row["revision"]),
    ).fetchone()
    if revision_row is None or any(plan_row[field] != revision_row[field] for field in (
        "plan_sha256", "scope_sha256", "approval_event_id", "payload_json",
    )):
        raise InputError("the current plan does not match its immutable revision")
    _require_user_event(
        conn, run_id, plan_row["approval_event_id"], "scope_approval_event_id",
        expected_event_type="user.search-scope-approval",
    )
    plan_budgets = plan["budgets"]
    if (
        _seconds_to_ms(plan_budgets["asr_seconds"], "budgets.asr_seconds")
        != budget["asr_limit_ms"]
        or _cost_to_micros(plan_budgets["asr_cost_cny"], "budgets.asr_cost_cny")
        != budget["cost_limit_micros"]
    ):
        raise InputError("the current plan does not match the canonical budget broker")
    budget_event_id = plan.get("budget_authorization_event_id")
    if not isinstance(budget_event_id, int) or isinstance(budget_event_id, bool):
        raise InputError("the current plan lacks its ASR budget authorization event")
    _require_user_event(
        conn, run_id, budget_event_id, "budget_authorization_event_id",
        expected_event_type="user.asr-authorization",
    )
    config = json.loads(run["config_json"])
    if config.get("budget_authorization_event_id") != budget_event_id:
        raise InputError("the current plan references a stale ASR budget authorization")


def upsert_finding(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    channel = (_string(data, "channel", required=True) or "").strip().lower()
    source_url = _string(data, "source_url", required=True) or ""
    canonical_url = canonicalize_url(source_url)
    title = _string(data, "title", required=True, allow_empty=True) or ""
    headline = _string(data, "headline", required=True) or ""
    note = _string(data, "note", required=True) or ""
    source_id = _string(data, "source_id")
    fingerprint = finding_fingerprint(data)
    finding_id = "fnd_" + fingerprint[:32]
    media = media_fingerprint(data)
    payload = canonical_json(data)
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            existing = conn.execute(
                "SELECT id,disposition,disposition_reason,disposition_at,created_at,payload_json "
                "FROM findings WHERE run_id=? AND fingerprint=?",
                (selected, fingerprint),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO findings(id,run_id,fingerprint,channel,source_url,"
                    "canonical_source_url,source_id,media_fingerprint,title,headline,note,"
                    "payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (finding_id, selected, fingerprint, channel, source_url, canonical_url,
                     source_id, media, title, headline, note, payload, now, now),
                )
                created = True
                changed = True
                revision = 1
            else:
                finding_id = existing["id"]
                changed = existing["payload_json"] != payload
                revision = conn.execute(
                    "SELECT COALESCE(MAX(revision),0) FROM finding_revisions WHERE finding_id=?",
                    (finding_id,),
                ).fetchone()[0] + (1 if changed else 0)
                conn.execute(
                    "UPDATE findings SET channel=?,source_url=?,canonical_source_url=?,"
                    "source_id=?,media_fingerprint=COALESCE(?,media_fingerprint),title=?,"
                    "headline=?,note=?,payload_json=?,"
                    "disposition=CASE WHEN ? THEN 'pending' ELSE disposition END,"
                    "disposition_reason=CASE WHEN ? THEN NULL ELSE disposition_reason END,"
                    "disposition_at=CASE WHEN ? THEN NULL ELSE disposition_at END,"
                    "updated_at=? WHERE run_id=? AND fingerprint=?",
                    (channel, source_url, canonical_url, source_id, media, title, headline,
                     note, payload, int(changed), int(changed), int(changed), now, selected, fingerprint),
                )
                if changed:
                    conn.execute("DELETE FROM finding_claims WHERE finding_id=?", (finding_id,))
                created = False
            if changed:
                conn.execute(
                    "INSERT INTO finding_revisions(finding_id,run_id,revision,payload_json,"
                    "payload_sha256,created_at) VALUES (?,?,?,?,?,?)",
                    (finding_id, selected, revision, payload, sha256_text(payload), now),
                )
            _insert_event(
                conn, selected, "finding.upserted", "system",
                canonical_json({"finding_id": finding_id, "fingerprint": fingerprint,
                                "created": created, "changed": changed,
                                "revision": revision, "payload_sha256": sha256_text(payload)}), now,
            )
        return {
            "id": finding_id, "fingerprint": fingerprint,
            "media_fingerprint": media, "created": created,
            "changed": changed, "revision": revision,
        }
    finally:
        conn.close()


def _attempt_public(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "run_id": row["run_id"], "kind": row["kind"],
        "idempotency_key": row["idempotency_key"], "finding_id": row["finding_id"],
        "request_fingerprint": row["request_fingerprint"],
        "media_fingerprint": row["media_fingerprint"],
        "provider_task_id": row["provider_task_id"], "status": row["status"],
        "requested_asr_seconds": _ms_to_seconds(row["requested_asr_ms"]),
        "requested_cost": _micros_to_cost(row["requested_cost_micros"]),
        "charged_asr_seconds": _ms_to_seconds(row["charged_asr_ms"]),
        "charged_cost": _micros_to_cost(row["charged_cost_micros"]),
        "payload": json.loads(row["payload_json"]), "created_at": row["created_at"],
        "updated_at": row["updated_at"], "settled_at": row["settled_at"],
    }


def reserve_budget(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    key = _string(data, "idempotency_key", required=True) or ""
    kind = _string(data, "kind", default="asr") or "asr"
    requested_ms = _seconds_to_ms(data.get("requested_asr_seconds", 0), "requested_asr_seconds")
    requested_cost = _cost_to_micros(data.get("requested_cost", 0), "requested_cost")
    if requested_ms == 0 and requested_cost == 0:
        raise InputError("a reservation must request ASR seconds, cost, or both")
    finding_id = _string(data, "finding_id")
    supplied_media = _string(data, "media_fingerprint")
    if supplied_media is not None and not supplied_media.startswith("med_"):
        raise InputError("media_fingerprint must be generated by upsert-finding")
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            if finding_id is not None:
                finding = conn.execute(
                    "SELECT media_fingerprint FROM findings WHERE id=? AND run_id=?",
                    (finding_id, selected),
                ).fetchone()
                if finding is None:
                    raise InputError(f"unknown finding_id: {finding_id}")
                if supplied_media is None:
                    supplied_media = finding["media_fingerprint"]
            if kind == "asr" and supplied_media is None:
                raise InputError("ASR reservations require a finding/media fingerprint")
            model = data.get("model", "default")
            options = data.get("options", {})
            if not isinstance(model, str) or not model.strip():
                raise InputError("model must be a non-empty string")
            if not isinstance(options, dict):
                raise InputError("options must be a JSON object")
            request_fingerprint = None
            if supplied_media is not None:
                request_fingerprint = sha256_text(canonical_json({
                    "kind": kind, "media_fingerprint": supplied_media,
                    "model": model, "options": options,
                }))
            existing = conn.execute(
                "SELECT * FROM attempts WHERE run_id=? AND kind=? AND idempotency_key=?",
                (selected, kind, key),
            ).fetchone()
            if existing is not None:
                if (existing["requested_asr_ms"] != requested_ms
                        or existing["requested_cost_micros"] != requested_cost
                        or existing["finding_id"] != finding_id
                        or existing["media_fingerprint"] != supplied_media
                        or existing["request_fingerprint"] != request_fingerprint):
                    raise InputError("idempotency_key was already used with different reservation data")
                result = _attempt_public(existing)
                result["idempotent_replay"] = True
                return result
            if request_fingerprint is not None:
                existing_request = conn.execute(
                    "SELECT * FROM attempts WHERE run_id=? AND kind=? AND request_fingerprint=?",
                    (selected, kind, request_fingerprint),
                ).fetchone()
                if existing_request is not None:
                    if (existing_request["requested_asr_ms"] != requested_ms
                            or existing_request["requested_cost_micros"] != requested_cost):
                        raise InputError(
                            "the same media/model/options request already exists with a different budget"
                        )
                    result = _attempt_public(existing_request)
                    result["idempotent_replay"] = True
                    result["replayed_by_request_fingerprint"] = True
                    return result
            budget = conn.execute("SELECT * FROM budgets WHERE run_id=?", (selected,)).fetchone()
            assert budget is not None
            _require_plan_for_new_reservation(conn, selected, budget)
            next_asr = budget["spent_asr_ms"] + budget["reserved_asr_ms"] + requested_ms
            next_cost = (budget["spent_cost_micros"] + budget["reserved_cost_micros"]
                         + requested_cost)
            if next_asr > budget["asr_limit_ms"]:
                raise BudgetError(
                    f"ASR reservation exceeds limit: {_ms_to_seconds(next_asr)} > "
                    f"{_ms_to_seconds(budget['asr_limit_ms'])} seconds"
                )
            if next_cost > budget["cost_limit_micros"]:
                raise BudgetError(
                    f"cost reservation exceeds limit: {_micros_to_cost(next_cost)} > "
                    f"{_micros_to_cost(budget['cost_limit_micros'])} {budget['currency']}"
                )
            attempt_id = "att_" + sha256_text(f"{selected}\0{kind}\0{key}")[:32]
            conn.execute(
                "INSERT INTO attempts(id,run_id,kind,idempotency_key,request_fingerprint,finding_id,"
                "media_fingerprint,status,requested_asr_ms,requested_cost_micros,payload_json,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,'reserved',?,?,?,?,?)",
                (attempt_id, selected, kind, key, request_fingerprint, finding_id, supplied_media, requested_ms,
                 requested_cost, canonical_json(data), now, now),
            )
            conn.execute(
                "UPDATE budgets SET reserved_asr_ms=reserved_asr_ms+?,"
                "reserved_cost_micros=reserved_cost_micros+?,updated_at=? WHERE run_id=?",
                (requested_ms, requested_cost, now, selected),
            )
            _insert_event(
                conn, selected, "budget.reserved", "system",
                canonical_json({"attempt_id": attempt_id, "kind": kind,
                                "idempotency_key": key}), now,
            )
            row = conn.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            assert row is not None
            result = _attempt_public(row)
            result["idempotent_replay"] = False
            return result
    finally:
        conn.close()


def settle_budget(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    key = _string(data, "idempotency_key", required=True) or ""
    kind = _string(data, "kind", default="asr") or "asr"
    status = _string(data, "status", required=True) or ""
    if status not in {"settled", "released", "unknown"}:
        raise InputError("status must be settled, released, or unknown")
    charged_ms = _seconds_to_ms(data.get("charged_asr_seconds", 0), "charged_asr_seconds")
    charged_cost = _cost_to_micros(data.get("charged_cost", 0), "charged_cost")
    provider_task_id = _string(data, "provider_task_id")
    if status == "released" and (charged_ms or charged_cost):
        raise InputError("released attempts cannot have charges; use settled")
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            row = conn.execute(
                "SELECT * FROM attempts WHERE run_id=? AND kind=? AND idempotency_key=?",
                (selected, kind, key),
            ).fetchone()
            if row is None:
                raise InputError("cannot settle an unreserved idempotency_key")
            if row["status"] in {"settled", "released"}:
                expected = (status, charged_ms, charged_cost, provider_task_id)
                actual = (row["status"], row["charged_asr_ms"],
                          row["charged_cost_micros"], row["provider_task_id"])
                if expected != actual:
                    raise InputError("attempt is already final with different settlement data")
                result = _attempt_public(row)
                result["idempotent_replay"] = True
                return result
            if status == "unknown":
                conn.execute(
                    "UPDATE attempts SET status='unknown',provider_task_id=COALESCE(?,provider_task_id),"
                    "payload_json=?,updated_at=? WHERE id=?",
                    (provider_task_id, canonical_json(data), now, row["id"]),
                )
            else:
                if charged_ms > row["requested_asr_ms"] or charged_cost > row["requested_cost_micros"]:
                    raise BudgetError("settlement cannot exceed the amount reserved")
                conn.execute(
                    "UPDATE budgets SET reserved_asr_ms=reserved_asr_ms-?,"
                    "reserved_cost_micros=reserved_cost_micros-?,"
                    "spent_asr_ms=spent_asr_ms+?,spent_cost_micros=spent_cost_micros+?,"
                    "updated_at=? WHERE run_id=?",
                    (row["requested_asr_ms"], row["requested_cost_micros"], charged_ms,
                     charged_cost, now, selected),
                )
                conn.execute(
                    "UPDATE attempts SET status=?,provider_task_id=?,charged_asr_ms=?,"
                    "charged_cost_micros=?,payload_json=?,updated_at=?,settled_at=? WHERE id=?",
                    (status, provider_task_id, charged_ms, charged_cost, canonical_json(data),
                     now, now, row["id"]),
                )
            _insert_event(
                conn, selected, "budget.settled", "system",
                canonical_json({"attempt_id": row["id"], "status": status}), now,
            )
            final = conn.execute("SELECT * FROM attempts WHERE id=?", (row["id"],)).fetchone()
            assert final is not None
            result = _attempt_public(final)
            result["idempotent_replay"] = False
            return result
    finally:
        conn.close()


def project_notes(
    path: str | os.PathLike[str], *, run_id: str | None = None, cursor: int = 0,
    limit: int = 50, disposition: str = "pending",
) -> dict[str, Any]:
    if cursor < 0 or limit < 1 or limit > 1000:
        raise InputError("cursor must be >= 0 and limit must be between 1 and 1000")
    if disposition not in {*DISPOSITIONS, "all"}:
        raise InputError("disposition must be pending, consumed, excluded, or all")
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        clause = "run_id=? AND rowid>?"
        params: list[Any] = [selected, cursor]
        if disposition != "all":
            clause += " AND disposition=?"
            params.append(disposition)
        rows = conn.execute(
            f"SELECT rowid,* FROM findings WHERE {clause} ORDER BY rowid LIMIT ?",
            (*params, limit),
        ).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            items.append({
                "cursor": row["rowid"], "id": row["id"], "fingerprint": row["fingerprint"],
                "channel": row["channel"], "source_url": row["source_url"],
                "media_fingerprint": row["media_fingerprint"], "title": row["title"],
                "headline": row["headline"], "note": row["note"],
                "published_at": payload.get("published_at"),
                "unknown_terms": payload.get("unknown_terms", []),
                "disposition": row["disposition"],
                "disposition_reason": row["disposition_reason"],
            })
        next_cursor = rows[-1]["rowid"] if rows else None
        count_clause = "run_id=? AND rowid>?"
        count_params: list[Any] = [selected, next_cursor if next_cursor is not None else cursor]
        if disposition != "all":
            count_clause += " AND disposition=?"
            count_params.append(disposition)
        remaining = conn.execute(
            f"SELECT COUNT(*) FROM findings WHERE {count_clause}", count_params,
        ).fetchone()[0]
        return {"items": items, "next_cursor": next_cursor, "remaining": remaining}
    finally:
        conn.close()


def acknowledge_notes(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise InputError("items must be a non-empty array")
    normalized = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        item = _expect_object(raw, f"items[{index}]")
        finding_id = _string(item, "finding_id", required=True) or ""
        if finding_id in seen:
            raise InputError(f"duplicate finding_id in items: {finding_id}")
        seen.add(finding_id)
        disposition = _string(item, "disposition", required=True) or ""
        if disposition not in {"consumed", "excluded"}:
            raise InputError("acknowledged disposition must be consumed or excluded")
        reason = _string(item, "reason", allow_empty=True)
        if disposition == "excluded" and (reason is None or not reason.strip()):
            raise InputError("excluded findings require a non-empty reason")
        claim_ids = item.get("claim_ids", [])
        if not isinstance(claim_ids, list) or any(not isinstance(x, str) for x in claim_ids):
            raise InputError("claim_ids must be an array of strings")
        relation = _string(item, "relation", default="supports") or "supports"
        normalized.append((finding_id, disposition, reason, claim_ids, relation))
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            for finding_id, disposition, reason, claim_ids, relation in normalized:
                row = conn.execute(
                    "SELECT 1 FROM findings WHERE id=? AND run_id=?", (finding_id, selected),
                ).fetchone()
                if row is None:
                    raise InputError(f"unknown finding_id: {finding_id}")
                for claim_id in claim_ids:
                    claim = conn.execute(
                        "SELECT 1 FROM claims WHERE id=? AND run_id=?", (claim_id, selected),
                    ).fetchone()
                    if claim is None:
                        raise InputError(f"unknown claim_id: {claim_id}")
                conn.execute(
                    "UPDATE findings SET disposition=?,disposition_reason=?,disposition_at=?,"
                    "updated_at=? WHERE id=?", (disposition, reason, now, now, finding_id),
                )
                for claim_id in claim_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO finding_claims VALUES (?,?,?)",
                        (finding_id, claim_id, relation),
                    )
            event = _insert_event(
                conn, selected, "notes.acknowledged", "system", canonical_json(data), now,
            )
        return {"acknowledged": len(normalized), "event_seq": event["seq"]}
    finally:
        conn.close()


def upsert_candidate(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    name = _string(data, "name", required=True) or ""
    candidate_type = (_string(data, "candidate_type", required=True) or "").strip().casefold()
    candidate_status = _string(data, "status", default="active") or "active"
    canonical_name = " ".join(name.casefold().split())
    fingerprint = sha256_text(canonical_json({
        "candidate_type": candidate_type.casefold(), "canonical_name": canonical_name,
    }))
    candidate_id = "cand_" + fingerprint[:32]
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            existing = conn.execute(
                "SELECT 1 FROM candidates WHERE run_id=? AND canonical_name=? AND candidate_type=?",
                (selected, canonical_name, candidate_type),
            ).fetchone()
            conn.execute(
                "INSERT INTO candidates(id,run_id,name,canonical_name,candidate_type,status,"
                "payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id,canonical_name,candidate_type) DO UPDATE SET "
                "name=excluded.name,status=excluded.status,payload_json=excluded.payload_json,"
                "updated_at=excluded.updated_at",
                (candidate_id, selected, name, canonical_name, candidate_type, candidate_status,
                 canonical_json(data), now, now),
            )
            _insert_event(conn, selected, "candidate.upserted", "system",
                          canonical_json({"candidate_id": candidate_id}), now)
        return {"id": candidate_id, "created": existing is None}
    finally:
        conn.close()


def upsert_artifact(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    kind = _string(data, "kind", required=True) or ""
    artifact_path = _string(data, "path", required=True) or ""
    pure_path = pathlib.PurePosixPath(artifact_path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise InputError("artifact path must be a relative path without '..'")
    digest = _string(data, "sha256", required=True) or ""
    if not HEX_64.fullmatch(digest):
        raise InputError("sha256 must contain exactly 64 hexadecimal characters")
    digest = digest.lower()
    root = pathlib.Path(path).resolve().parent
    resolved_artifact = (root / pathlib.Path(*pure_path.parts)).resolve()
    try:
        resolved_artifact.relative_to(root)
    except ValueError as error:
        raise InputError("artifact path resolves outside the run directory") from error
    if not resolved_artifact.is_file() or resolved_artifact.stat().st_size <= 0:
        raise InputError("artifact path must reference a real non-empty file")
    if sha256_file(resolved_artifact) != digest:
        raise InputError("artifact sha256 does not match the file content")
    finding_id = _string(data, "finding_id")
    media = _string(data, "media_fingerprint")
    artifact_id = "art_" + sha256_text(canonical_json({
        "kind": kind, "sha256": digest,
    }))[:32]
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            if finding_id is not None:
                finding = conn.execute(
                    "SELECT media_fingerprint FROM findings WHERE id=? AND run_id=?",
                    (finding_id, selected),
                ).fetchone()
                if finding is None:
                    raise InputError(f"unknown finding_id: {finding_id}")
                if media is None:
                    media = finding["media_fingerprint"]
            existing = conn.execute(
                "SELECT id FROM artifacts WHERE run_id=? AND kind=? AND sha256=?",
                (selected, kind, digest),
            ).fetchone()
            if existing is not None:
                return {"id": existing["id"], "created": False}
            conn.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                (artifact_id, selected, finding_id, kind, artifact_path, digest, media,
                 canonical_json(data), now),
            )
            _insert_event(conn, selected, "artifact.recorded", "system",
                          canonical_json({"artifact_id": artifact_id}), now)
        return {"id": artifact_id, "created": True}
    finally:
        conn.close()


def upsert_evidence_cluster(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    label = _string(data, "label", required=True) or ""
    sources = data.get("source_fingerprints")
    if not isinstance(sources, list) or any(not isinstance(x, str) or not x for x in sources):
        raise InputError("source_fingerprints must be an array of non-empty strings")
    sources = sorted(set(sources))
    members = data.get("members")
    if not isinstance(members, list) or not members:
        raise InputError("evidence cluster members must be a non-empty array")
    member_sources = []
    independence_keys = []
    for index, raw_member in enumerate(members):
        member = _expect_object(raw_member, f"members[{index}]")
        member_sources.append(_string(member, "source_fingerprint", required=True) or "")
        _string(member, "quote", required=True)
        _string(member, "locator", required=True)
        independence_keys.append(_string(member, "independence_key", required=True) or "")
    if len(set(member_sources)) != len(member_sources):
        raise InputError("evidence cluster members must not repeat source_fingerprint")
    if sorted(member_sources) != sources:
        raise InputError("evidence cluster members must cover source_fingerprints exactly")
    inferred_independent = len(set(independence_keys))
    independent = _integer(
        data, "independent_source_count", default=inferred_independent, minimum=0,
    )
    if independent != inferred_independent:
        raise InputError(
            "independent_source_count must equal the unique evidence member "
            "independence_key count"
        )
    fingerprint = sha256_text(canonical_json({"label": label.strip(), "sources": sources}))
    cluster_id = "evc_" + fingerprint[:32]
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            known = {
                row["fingerprint"] for row in conn.execute(
                    "SELECT fingerprint FROM findings WHERE run_id=?", (selected,)
                )
            }
            unknown = sorted(set(sources) - known)
            if unknown:
                raise InputError(
                    "evidence source_fingerprints must reference findings in this run: "
                    + ", ".join(unknown[:10])
                )
            existing = conn.execute(
                "SELECT 1 FROM evidence_clusters WHERE run_id=? AND fingerprint=?",
                (selected, fingerprint),
            ).fetchone()
            conn.execute(
                "INSERT INTO evidence_clusters(id,run_id,label,fingerprint,"
                "independent_source_count,source_fingerprints_json,payload_json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,fingerprint) DO UPDATE SET "
                "label=excluded.label,independent_source_count=excluded.independent_source_count,"
                "source_fingerprints_json=excluded.source_fingerprints_json,"
                "payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (cluster_id, selected, label, fingerprint, independent, canonical_json(sources),
                 canonical_json(data), now, now),
            )
            _insert_event(conn, selected, "evidence.upserted", "system",
                          canonical_json({"evidence_cluster_id": cluster_id}), now)
        return {"id": cluster_id, "fingerprint": fingerprint, "created": existing is None}
    finally:
        conn.close()


def upsert_claim(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    text = _string(data, "text", required=True) or ""
    critical = _boolean(data, "critical", default=False)
    sufficiency = _string(data, "sufficiency", default="insufficient") or "insufficient"
    if sufficiency not in SUFFICIENCY_STATUSES:
        raise InputError(f"sufficiency must be one of {sorted(SUFFICIENCY_STATUSES)}")
    required = _integer(data, "required_evidence_count", default=1 if critical else 0, minimum=0)
    if critical and required < 1:
        raise InputError("critical claims require at least one independent evidence source")
    evidence_ids = data.get("evidence_cluster_ids", [])
    if not isinstance(evidence_ids, list) or any(not isinstance(x, str) for x in evidence_ids):
        raise InputError("evidence_cluster_ids must be an array of strings")
    evidence_ids = list(dict.fromkeys(evidence_ids))
    normalized_text = " ".join(text.casefold().split())
    fingerprint = sha256_text(normalized_text)
    claim_id = "clm_" + fingerprint[:32]
    now = utc_now()
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            independent_total = 0
            claim_sources: set[str] = set()
            for evidence_id in evidence_ids:
                row = conn.execute(
                    "SELECT independent_source_count,source_fingerprints_json "
                    "FROM evidence_clusters WHERE id=? AND run_id=?",
                    (evidence_id, selected),
                ).fetchone()
                if row is None:
                    raise InputError(f"unknown evidence_cluster_id: {evidence_id}")
                cluster_sources = set(json.loads(row["source_fingerprints_json"]))
                overlap = claim_sources & cluster_sources
                if overlap:
                    raise InputError(
                        "claim evidence clusters overlap source fingerprints: "
                        + ", ".join(sorted(overlap)[:10])
                    )
                claim_sources.update(cluster_sources)
                independent_total += row["independent_source_count"]
            if sufficiency == "sufficient" and independent_total < required:
                raise InputError(
                    f"sufficient claim requires {required} independent evidence source(s); "
                    f"only {independent_total} recorded"
                )
            existing = conn.execute(
                "SELECT 1 FROM claims WHERE run_id=? AND fingerprint=?", (selected, fingerprint),
            ).fetchone()
            conn.execute(
                "INSERT INTO claims(id,run_id,text,fingerprint,critical,sufficiency,"
                "required_evidence_count,payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id,fingerprint) DO UPDATE SET text=excluded.text,"
                "critical=excluded.critical,sufficiency=excluded.sufficiency,"
                "required_evidence_count=excluded.required_evidence_count,"
                "payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (claim_id, selected, text, fingerprint, int(critical), sufficiency, required,
                 canonical_json(data), now, now),
            )
            conn.execute("DELETE FROM claim_evidence WHERE claim_id=?", (claim_id,))
            conn.executemany(
                "INSERT INTO claim_evidence VALUES (?,?)",
                [(claim_id, evidence_id) for evidence_id in evidence_ids],
            )
            _insert_event(conn, selected, "claim.upserted", "system",
                          canonical_json({"claim_id": claim_id}), now)
        return {"id": claim_id, "fingerprint": fingerprint, "created": existing is None}
    finally:
        conn.close()


def _validate_decision_contract(
    data: Mapping[str, Any], contract_verbatim: str, requested_status: str,
) -> tuple[dict[str, Any], int]:
    contract = _expect_object(data.get("decision_contract"), "decision_contract")
    for field in ("hard_constraints", "preferences", "success_metrics"):
        values = contract.get(field)
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise InputError(f"decision_contract.{field} must be an array of objects")
    for field in ("approved_costs", "unresolved"):
        if not isinstance(contract.get(field), list):
            raise InputError(f"decision_contract.{field} must be an array")
    for field in ("risk_tolerance", "time_horizon"):
        value = contract.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise InputError(f"decision_contract.{field} must be null or a non-empty string")
    if requested_status != "blocked" and not contract["success_metrics"]:
        raise InputError("non-blocked decisions require decision_contract.success_metrics")
    if requested_status == "production-ready" and contract["unresolved"]:
        raise InputError("production-ready decisions require an empty decision_contract.unresolved")
    try:
        displayed_contract = json.loads(contract_verbatim)
    except json.JSONDecodeError as error:
        raise InputError("contract_verbatim must be JSON for the displayed decision_contract") from error
    if displayed_contract != contract:
        raise InputError("contract_verbatim must exactly represent decision_contract")
    presentation_id = _integer(data, "contract_presentation_event_id", minimum=1)
    return contract, presentation_id


def set_decision(
    path: str | os.PathLike[str], data: Mapping[str, Any], run_id: str | None = None,
) -> dict[str, Any]:
    data = _expect_object(dict(data))
    contract = _string(data, "contract_verbatim", required=True) or ""
    confirmed = _boolean(data, "confirmed", default=False)
    status = _string(data, "requested_status", required=True) or ""
    if status not in DECISION_STATUSES:
        raise InputError(f"requested_status must be one of {sorted(DECISION_STATUSES)}")
    if status == "blocked":
        if data.get("recommendation") is not None:
            raise InputError("blocked decisions must not contain a recommendation")
        if not (data.get("blockers") or data.get("next_research")):
            raise InputError("blocked decisions require blockers or next_research")
    confirmation_id = data.get("user_confirmation_event_id")
    if confirmation_id is not None and (not isinstance(confirmation_id, int)
                                        or isinstance(confirmation_id, bool)
                                        or confirmation_id < 1):
        raise InputError("user_confirmation_event_id must be a positive integer")
    if confirmed and confirmation_id is None:
        raise InputError("confirmed decisions require user_confirmation_event_id")
    _, presentation_id = _validate_decision_contract(data, contract, status)
    now = utc_now()
    digest = sha256_text(contract)
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        with transaction(conn, immediate=True):
            plan = conn.execute("SELECT revision FROM plans WHERE run_id=?", (selected,)).fetchone()
            if plan is None:
                raise InputError("set-plan with a user-approved search scope before set-decision")
            plan_revision = plan["revision"]
            profile = conn.execute("SELECT profile FROM runs WHERE id=?", (selected,)).fetchone()["profile"]
            expected_task_types = {
                "technical": "implementation", "travel": "itinerary",
                "policy-forecast": "forecast",
            }
            task_type = data.get("task_type", expected_task_types.get(profile, "research-only"))
            if status != "blocked" and profile in expected_task_types and task_type != expected_task_types[profile]:
                raise InputError(
                    f"{profile} decisions must use task_type={expected_task_types[profile]!r}"
                )
            recommendation = data.get("recommendation")
            if isinstance(recommendation, dict):
                selected_candidate_id = _string(data, "selected_candidate_id", required=True) or ""
                candidate = conn.execute(
                    "SELECT status FROM candidates WHERE id=? AND run_id=?",
                    (selected_candidate_id, selected),
                ).fetchone()
                if candidate is None or candidate["status"] != "active":
                    raise InputError(
                        "selected_candidate_id must reference an active candidate in this run"
                    )
                recommendation_candidate_id = recommendation.get("candidate_id")
                if (
                    recommendation_candidate_id is not None
                    and recommendation_candidate_id != selected_candidate_id
                ):
                    raise InputError(
                        "recommendation.candidate_id must match selected_candidate_id"
                    )
            presentation = conn.execute(
                "SELECT event_type,actor,verbatim FROM events WHERE seq=? AND run_id=?",
                (presentation_id, selected),
            ).fetchone()
            if (
                presentation is None
                or presentation["event_type"] != "agent.decision-contract"
                or presentation["actor"] not in {"agent", "main-agent", "summary-agent"}
                or presentation["verbatim"] != contract
            ):
                raise InputError(
                    "contract_presentation_event_id must reference the exact displayed "
                    "agent.decision-contract event"
                )
            if confirmation_id is not None:
                _require_user_event(
                    conn, selected, confirmation_id, "user_confirmation_event_id",
                    expected_event_type="user.decision-confirmation",
                )
                if confirmation_id <= presentation_id:
                    raise InputError(
                        "user.decision-confirmation must follow the displayed decision contract"
                    )
                later_input = conn.execute(
                    "SELECT event_type FROM events WHERE run_id=? AND seq>? "
                    "AND event_type NOT IN ('decision.set','delivery.exported') LIMIT 1",
                    (selected, confirmation_id),
                ).fetchone()
                if later_input is not None:
                    raise InputError(
                        "user.decision-confirmation must follow all current research inputs"
                    )
            pending_findings = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE run_id=? AND disposition='pending'",
                (selected,),
            ).fetchone()[0]
            if pending_findings:
                raise InputError("all findings must be consumed or excluded before set-decision")
            budget_state = conn.execute("SELECT * FROM budgets WHERE run_id=?", (selected,)).fetchone()
            assert budget_state is not None
            if budget_state["reserved_asr_ms"] or budget_state["reserved_cost_micros"]:
                raise InputError("all budget reservations must be settled or released before set-decision")
            input_event_seq = conn.execute(
                "SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id=? "
                "AND event_type NOT IN ('decision.set','delivery.exported')",
                (selected,),
            ).fetchone()[0]
            payload = canonical_json(data)
            decision_digest = sha256_text(canonical_json({
                "plan_revision": plan_revision, "input_event_seq": input_event_seq,
                "payload": data,
            }))
            existing = conn.execute("SELECT * FROM decisions WHERE run_id=?", (selected,)).fetchone()
            if existing is not None and existing["decision_sha256"] == decision_digest:
                return {
                    "contract_sha256": digest, "decision_sha256": decision_digest,
                    "confirmed": confirmed, "requested_status": status,
                    "plan_revision": plan_revision, "decision_revision": existing["revision"],
                    "input_event_seq": input_event_seq,
                    "idempotent_replay": True,
                }
            revision = 1 if existing is None else existing["revision"] + 1
            created = existing["created_at"] if existing else now
            conn.execute(
                "INSERT INTO decision_revisions(run_id,revision,plan_revision,input_event_seq,decision_sha256,"
                "contract_verbatim,contract_sha256,confirmed,user_confirmation_event_id,"
                "requested_status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (selected, revision, plan_revision, input_event_seq, decision_digest, contract, digest,
                 int(confirmed), confirmation_id, status, payload, now),
            )
            conn.execute(
                "INSERT INTO decisions(run_id,revision,plan_revision,input_event_seq,decision_sha256,"
                "contract_verbatim,contract_sha256,confirmed,"
                "user_confirmation_event_id,requested_status,payload_json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                "revision=excluded.revision,plan_revision=excluded.plan_revision,"
                "input_event_seq=excluded.input_event_seq,decision_sha256=excluded.decision_sha256,"
                "contract_verbatim=excluded.contract_verbatim,"
                "contract_sha256=excluded.contract_sha256,"
                "confirmed=excluded.confirmed,user_confirmation_event_id=excluded.user_confirmation_event_id,"
                "requested_status=excluded.requested_status,payload_json=excluded.payload_json,"
                "updated_at=excluded.updated_at",
                (selected, revision, plan_revision, input_event_seq, decision_digest, contract, digest,
                 int(confirmed), confirmation_id, status, payload, created, now),
            )
            _insert_event(conn, selected, "decision.set", "system",
                          canonical_json({"contract_sha256": digest, "confirmed": confirmed,
                                          "requested_status": status,
                                          "plan_revision": plan_revision,
                                          "decision_revision": revision,
                                          "input_event_seq": input_event_seq,
                                          "decision_sha256": decision_digest}), now)
        return {"contract_sha256": digest, "decision_sha256": decision_digest,
                "confirmed": confirmed, "requested_status": status,
                "plan_revision": plan_revision, "decision_revision": revision,
                "input_event_seq": input_event_seq,
                "idempotent_replay": False}
    finally:
        conn.close()


def _budget_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "currency": row["currency"],
        "asr_seconds_limit": _ms_to_seconds(row["asr_limit_ms"]),
        "asr_seconds_reserved": _ms_to_seconds(row["reserved_asr_ms"]),
        "asr_seconds_spent": _ms_to_seconds(row["spent_asr_ms"]),
        "cost_limit": _micros_to_cost(row["cost_limit_micros"]),
        "cost_reserved": _micros_to_cost(row["reserved_cost_micros"]),
        "cost_spent": _micros_to_cost(row["spent_cost_micros"]),
        "updated_at": row["updated_at"],
    }


def _representative_poc(payload: Mapping[str, Any]) -> tuple[bool, set[str], set[str]]:
    recommendation = payload.get("recommendation")
    recommendation = recommendation if isinstance(recommendation, dict) else {}
    implementation = payload.get("implementation")
    implementation = implementation if isinstance(implementation, dict) else {}
    poc = payload.get("poc") or implementation.get("poc") or recommendation.get("poc")
    if not isinstance(poc, dict):
        return False, set(), set()
    sources = poc.get("sources")
    valid_sources = (
        isinstance(sources, list) and bool(sources)
        and all(isinstance(source, str) and source for source in sources)
    )
    artifact_ids = poc.get("artifact_ids")
    valid_artifacts = (
        isinstance(artifact_ids, list) and bool(artifact_ids)
        and all(isinstance(artifact_id, str) and artifact_id for artifact_id in artifact_ids)
    )
    valid = (
        poc.get("result") == "passed"
        and bool(poc.get("sample"))
        and isinstance(poc.get("baseline"), dict) and bool(poc["baseline"])
        and bool(poc.get("metrics"))
        and bool(poc.get("thresholds"))
        and isinstance(poc.get("budget"), dict) and bool(poc["budget"])
        and isinstance(poc.get("failure_tests"), list) and bool(poc["failure_tests"])
        and isinstance(poc.get("rollback"), str) and bool(poc["rollback"].strip())
        and valid_sources
        and valid_artifacts
    )
    return (
        valid,
        set(sources or []) if valid_sources else set(),
        set(artifact_ids or []) if valid_artifacts else set(),
    )


def _artifact_row_is_current(
    db_path: str | os.PathLike[str], row: sqlite3.Row | Mapping[str, Any],
) -> bool:
    try:
        root = pathlib.Path(db_path).resolve().parent
        relative = pathlib.PurePosixPath(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            return False
        candidate = (root / pathlib.Path(*relative.parts)).resolve()
        candidate.relative_to(root)
        return (
            candidate.is_file() and candidate.stat().st_size > 0
            and sha256_file(candidate) == row["sha256"]
        )
    except (KeyError, OSError, ValueError, TypeError):
        return False


def _typed_delivery_shape_ok(
    payload: Mapping[str, Any], *, profile: str, objective: str, readiness: str,
) -> tuple[bool, list[str]]:
    """Run the same typed contract used by the renderer against a decision payload."""
    from render_delivery import build_runbook, typed_runbook_problems, validate_decision

    decision = dict(payload)
    task_type = decision.get("task_type", {
        "technical": "implementation", "travel": "itinerary",
        "policy-forecast": "forecast",
    }.get(profile, "research-only"))
    if readiness == "blocked":
        task_type = "research-only"
    decision.update({
        "schema_version": SCHEMA_VERSION, "run_id": "run_gate_projection",
        "plan_revision": 1, "decision_revision": 1,
        "task_type": task_type, "readiness": readiness,
        "title": decision.get("title", objective),
        "summary": decision.get(
            "summary",
            "The confirmed decision contract and evidence gate determine the readiness shown here.",
        ),
    })
    if readiness == "blocked":
        decision["recommendation"] = None
        if not decision.get("blockers"):
            decision["blockers"] = ["The requested delivery did not pass its release gate."]
    for key in (
        "contract_verbatim", "contract_presentation_event_id", "confirmed",
        "user_confirmation_event_id", "requested_status",
    ):
        decision.pop(key, None)
    try:
        validate_decision(decision)
        problems = typed_runbook_problems(build_runbook(decision), decision)
    except (TypeError, ValueError) as error:
        problems = [str(error)]
    return not problems, problems


def _source_references_ok(value: Any, valid_ids: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"sources", "finding_ids", "evidence_ids"}:
                if (
                    not isinstance(child, list)
                    or any(not isinstance(item, str) or item not in valid_ids for item in child)
                ):
                    return False
            if not _source_references_ok(child, valid_ids):
                return False
    elif isinstance(value, list):
        return all(_source_references_ok(item, valid_ids) for item in value)
    return True


def _public_plan(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    payload.update({
        "schema_version": SCHEMA_VERSION,
        "run_id": row["run_id"],
        "revision": row["revision"],
        "plan_sha256": row["plan_sha256"],
        "scope_sha256": row["scope_sha256"],
        "scope_approval_event_id": row["approval_event_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    })
    return payload


def evaluate_gate(
    path: str | os.PathLike[str], run_id: str | None = None,
) -> dict[str, Any]:
    conn = _connect(path)
    try:
        selected = _default_run_id(conn, run_id)
        run = conn.execute("SELECT * FROM runs WHERE id=?", (selected,)).fetchone()
        assert run is not None
        plan_row = conn.execute("SELECT * FROM plans WHERE run_id=?", (selected,)).fetchone()
        plan_payload: dict[str, Any] | None = None
        plan_ok = False
        if plan_row is not None:
            try:
                plan_payload = _validate_plan_payload(
                    json.loads(plan_row["payload_json"]), expected_profile=run["profile"]
                )
                approval = conn.execute(
                    "SELECT event_type,actor,verbatim FROM events WHERE seq=? AND run_id=?",
                    (plan_row["approval_event_id"], selected),
                ).fetchone()
                plan_revision_row = conn.execute(
                    "SELECT * FROM plan_revisions WHERE run_id=? AND revision=?",
                    (selected, plan_row["revision"]),
                ).fetchone()
                plan_ok = (
                    sha256_text(canonical_json(plan_payload)) == plan_row["plan_sha256"]
                    and _plan_scope_sha256(plan_payload) == plan_row["scope_sha256"]
                    and approval is not None and approval["actor"] == "user"
                    and approval["event_type"] == "user.search-scope-approval"
                    and bool(approval["verbatim"])
                    and plan_revision_row is not None
                    and all(plan_row[field] == plan_revision_row[field] for field in (
                        "plan_sha256", "scope_sha256", "approval_event_id", "payload_json",
                    ))
                )
            except (InputError, ValueError, TypeError, json.JSONDecodeError):
                plan_ok = False
        decision = conn.execute("SELECT * FROM decisions WHERE run_id=?", (selected,)).fetchone()
        decision_ok = False
        decision_revision_ok = False
        payload_value: dict[str, Any] = {}
        if decision is not None:
            revision_row = conn.execute(
                "SELECT * FROM decision_revisions WHERE run_id=? AND revision=?",
                (selected, decision["revision"]),
            ).fetchone()
            try:
                payload_value = json.loads(decision["payload_json"])
                expected_decision_hash = sha256_text(canonical_json({
                    "plan_revision": decision["plan_revision"],
                    "input_event_seq": decision["input_event_seq"],
                    "payload": payload_value,
                }))
                decision_revision_ok = (
                    revision_row is not None
                    and decision["decision_sha256"] == expected_decision_hash
                    and all(decision[field] == revision_row[field] for field in (
                        "plan_revision", "input_event_seq", "decision_sha256", "contract_verbatim",
                        "contract_sha256", "confirmed", "user_confirmation_event_id",
                        "requested_status", "payload_json",
                    ))
                )
            except (json.JSONDecodeError, TypeError):
                decision_revision_ok = False
        decision_inputs_current = (
            decision is not None and conn.execute(
                "SELECT 1 FROM events WHERE run_id=? AND seq>? "
                "AND event_type NOT IN ('decision.set','delivery.exported') LIMIT 1",
                (selected, decision["input_event_seq"]),
            ).fetchone() is None
        )
        if decision is not None and decision["confirmed"] and decision["contract_verbatim"]:
            event = conn.execute(
                "SELECT event_type,actor,verbatim FROM events WHERE seq=? AND run_id=?",
                (decision["user_confirmation_event_id"], selected),
            ).fetchone()
            try:
                _, presentation_id = _validate_decision_contract(
                    payload_value, decision["contract_verbatim"], decision["requested_status"]
                )
                presentation = conn.execute(
                    "SELECT event_type,actor,verbatim FROM events WHERE seq=? AND run_id=?",
                    (presentation_id, selected),
                ).fetchone()
                presentation_ok = (
                    presentation is not None
                    and presentation["event_type"] == "agent.decision-contract"
                    and presentation["actor"] in {"agent", "main-agent", "summary-agent"}
                    and presentation["verbatim"] == decision["contract_verbatim"]
                    and presentation_id < decision["user_confirmation_event_id"]
                )
            except (InputError, TypeError, ValueError):
                presentation_ok = False
            decision_ok = (
                sha256_text(decision["contract_verbatim"]) == decision["contract_sha256"]
                and event is not None and event["actor"] == "user" and bool(event["verbatim"])
                and event["event_type"] == "user.decision-confirmation"
                and decision["user_confirmation_event_id"] == decision["input_event_seq"]
                and presentation_ok and decision_revision_ok
            )
        decision_plan_ok = (
            decision is not None and plan_row is not None
            and decision["plan_revision"] == plan_row["revision"]
        )
        finding_counts = {
            row["disposition"]: row["count"]
            for row in conn.execute(
                "SELECT disposition,COUNT(*) count FROM findings WHERE run_id=? GROUP BY disposition",
                (selected,),
            )
        }
        findings_ok = finding_counts.get("pending", 0) == 0
        evidence_present = finding_counts.get("consumed", 0) > 0
        critical_rows = conn.execute(
            "SELECT id,sufficiency,required_evidence_count FROM claims "
            "WHERE run_id=? AND critical=1",
            (selected,),
        ).fetchall()
        critical_ok = True
        for critical in critical_rows:
            cluster_rows = conn.execute(
                "SELECT e.* FROM evidence_clusters e JOIN claim_evidence ce "
                "ON ce.evidence_cluster_id=e.id WHERE ce.claim_id=?",
                (critical["id"],),
            ).fetchall()
            independent_total = 0
            seen_sources: set[str] = set()
            claim_evidence_ok = True
            for cluster in cluster_rows:
                try:
                    sources = json.loads(cluster["source_fingerprints_json"])
                    payload = json.loads(cluster["payload_json"])
                    members = payload.get("members") if isinstance(payload, dict) else None
                except (json.JSONDecodeError, TypeError):
                    claim_evidence_ok = False
                    continue
                if (
                    not isinstance(sources, list) or not sources
                    or len(sources) != len(set(sources))
                    or cluster["independent_source_count"] > len(sources)
                    or seen_sources.intersection(sources)
                    or not isinstance(members, list)
                ):
                    claim_evidence_ok = False
                    continue
                member_map = {
                    member.get("source_fingerprint"): member for member in members
                    if isinstance(member, dict)
                }
                if len(members) != len(sources) or set(member_map) != set(sources) or any(
                    not isinstance(member_map[source].get("quote"), str)
                    or not member_map[source]["quote"].strip()
                    or not isinstance(member_map[source].get("locator"), str)
                    or not member_map[source]["locator"].strip()
                    or not isinstance(member_map[source].get("independence_key"), str)
                    or not member_map[source]["independence_key"].strip()
                    for source in sources
                ):
                    claim_evidence_ok = False
                    continue
                if cluster["independent_source_count"] != len({
                    member_map[source]["independence_key"].strip() for source in sources
                }):
                    claim_evidence_ok = False
                    continue
                dispositions = {
                    row["fingerprint"]: row["disposition"] for row in conn.execute(
                        "SELECT fingerprint,disposition FROM findings WHERE run_id=? "
                        f"AND fingerprint IN ({','.join('?' for _ in sources)})",
                        (selected, *sources),
                    )
                }
                if set(dispositions) != set(sources) or any(
                    disposition != "consumed" for disposition in dispositions.values()
                ):
                    claim_evidence_ok = False
                    continue
                seen_sources.update(sources)
                independent_total += cluster["independent_source_count"]
            if not (
                critical["sufficiency"] == "sufficient"
                and claim_evidence_ok
                and independent_total >= critical["required_evidence_count"]
            ):
                critical_ok = False
        if run["require_critical_claims"] and not critical_rows:
            critical_ok = False
        claim_ids = {
            row["id"] for row in conn.execute("SELECT id FROM claims WHERE run_id=?", (selected,))
        }
        evidence_ids = {
            row["id"] for row in conn.execute(
                "SELECT id FROM evidence_clusters WHERE run_id=?", (selected,)
            )
        }
        finding_ids = {
            row["id"] for row in conn.execute("SELECT id FROM findings WHERE run_id=?", (selected,))
        }
        reference_payload = dict(payload_value)
        # These payload keys are ignored by the public projection; canonical tables
        # always replace them, so they cannot participate in reference validation.
        reference_payload.pop("claims", None)
        reference_payload.pop("evidence_clusters", None)
        canonical_reference_ok = _source_references_ok(
            reference_payload, finding_ids | claim_ids | evidence_ids
        )
        requested = decision["requested_status"] if decision is not None else "blocked"
        recommendation = payload_value.get("recommendation")
        recommendation_ok = requested != "production-ready" or isinstance(recommendation, dict)
        load_bearing_claim_ids = {
            row["id"] for row in critical_rows if row["sufficiency"] == "sufficient"
        }
        load_bearing_evidence_ids = {
            row["evidence_cluster_id"] for row in conn.execute(
                "SELECT ce.evidence_cluster_id FROM claim_evidence ce "
                "JOIN claims c ON c.id=ce.claim_id WHERE c.run_id=? AND c.critical=1 "
                "AND c.sufficiency='sufficient'",
                (selected,),
            )
        }
        recommendation_evidence_ok = True
        if requested == "production-ready" and isinstance(recommendation, dict):
            rationale = recommendation.get("rationale")
            recommendation_evidence_ok = (
                isinstance(rationale, list) and bool(rationale)
                and all(
                    isinstance(item, dict)
                    and isinstance(item.get("sources"), list)
                    and bool(
                        set(item["sources"])
                        & (load_bearing_claim_ids | load_bearing_evidence_ids)
                    )
                    for item in rationale
                )
            )
        candidate_ok = True
        if isinstance(recommendation, dict):
            selected_candidate_id = payload_value.get("selected_candidate_id")
            candidate = conn.execute(
                "SELECT status FROM candidates WHERE id=? AND run_id=?",
                (selected_candidate_id, selected),
            ).fetchone()
            candidate_ok = (
                candidate is not None and candidate["status"] == "active"
                and recommendation.get("candidate_id", selected_candidate_id)
                == selected_candidate_id
            )
        production_shape_ok, production_shape_problems = _typed_delivery_shape_ok(
            payload_value, profile=run["profile"], objective=run["objective"],
            readiness="production-ready",
        ) if decision is not None else (False, ["decision is absent"])
        pilot_shape_ok, pilot_shape_problems = _typed_delivery_shape_ok(
            payload_value, profile=run["profile"], objective=run["objective"],
            readiness="pilot-only",
        ) if decision is not None else (False, ["decision is absent"])
        requested_shape_ok = (
            True if requested == "blocked"
            else production_shape_ok if requested == "production-ready"
            else pilot_shape_ok
        )
        poc_ok = True
        if run["profile"] == "technical" and requested == "production-ready":
            poc_shape_ok, poc_sources, poc_artifact_ids = _representative_poc(payload_value)
            critical_ids = {row["id"] for row in critical_rows}
            artifact_rows = conn.execute(
                "SELECT id,kind,path,sha256 FROM artifacts WHERE run_id=?", (selected,)
            ).fetchall()
            artifacts_by_id = {row["id"]: row for row in artifact_rows}
            poc_ok = (
                poc_shape_ok and poc_sources <= claim_ids and bool(poc_sources & critical_ids)
                and poc_artifact_ids <= set(artifacts_by_id)
                and all(
                    artifacts_by_id[artifact_id]["kind"] == "poc-result"
                    and _artifact_row_is_current(path, artifacts_by_id[artifact_id])
                    for artifact_id in poc_artifact_ids
                )
            )
        declared_artifact_ids: set[str] = set()
        implementation_container = payload_value.get("implementation")
        implementation_container = (
            implementation_container if isinstance(implementation_container, dict) else {}
        )
        recommendation_container = recommendation if isinstance(recommendation, dict) else {}
        poc_container = (
            payload_value.get("poc")
            or implementation_container.get("poc")
            or recommendation_container.get("poc")
        )
        if isinstance(poc_container, dict) and isinstance(poc_container.get("artifact_ids"), list):
            declared_artifact_ids.update(
                value for value in poc_container["artifact_ids"] if isinstance(value, str)
            )
        artifact_rows = conn.execute(
            "SELECT id,kind,path,sha256 FROM artifacts WHERE run_id=?", (selected,)
        ).fetchall()
        artifacts_by_id = {row["id"]: row for row in artifact_rows}
        declared_artifacts_ok = (
            declared_artifact_ids <= set(artifacts_by_id)
            and all(
                artifacts_by_id[artifact_id]["kind"] == "poc-result"
                and _artifact_row_is_current(path, artifacts_by_id[artifact_id])
                for artifact_id in declared_artifact_ids
            )
        )
        budget = conn.execute("SELECT * FROM budgets WHERE run_id=?", (selected,)).fetchone()
        assert budget is not None
        if plan_ok and plan_payload is not None:
            plan_budgets = plan_payload["budgets"]
            plan_ok = (
                _seconds_to_ms(plan_budgets["asr_seconds"], "budgets.asr_seconds")
                == budget["asr_limit_ms"]
                and _cost_to_micros(plan_budgets["asr_cost_cny"], "budgets.asr_cost_cny")
                == budget["cost_limit_micros"]
            )
            try:
                if budget["asr_limit_ms"] or budget["cost_limit_micros"]:
                    budget_event_id = plan_payload.get("budget_authorization_event_id")
                    if not isinstance(budget_event_id, int) or isinstance(budget_event_id, bool):
                        raise InputError("plan lacks budget authorization")
                    _require_user_event(
                        conn, selected, budget_event_id, "budget_authorization_event_id",
                        expected_event_type="user.asr-authorization",
                    )
                    config = json.loads(run["config_json"])
                    plan_ok = plan_ok and config.get("budget_authorization_event_id") == budget_event_id
                if plan_budgets.get("account_actions") is True:
                    account_event_id = plan_payload.get("account_authorization_event_id")
                    if not isinstance(account_event_id, int) or isinstance(account_event_id, bool):
                        raise InputError("plan lacks account authorization")
                    _require_user_event(
                        conn, selected, account_event_id, "account_authorization_event_id",
                        expected_event_type="user.account-authorization",
                    )
            except InputError:
                plan_ok = False
        aggregates = conn.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN status IN ('reserved','unknown') THEN requested_asr_ms ELSE 0 END),0) r_ms,"
            "COALESCE(SUM(CASE WHEN status='settled' THEN charged_asr_ms ELSE 0 END),0) s_ms,"
            "COALESCE(SUM(CASE WHEN status IN ('reserved','unknown') THEN requested_cost_micros ELSE 0 END),0) r_cost,"
            "COALESCE(SUM(CASE WHEN status='settled' THEN charged_cost_micros ELSE 0 END),0) s_cost "
            "FROM attempts WHERE run_id=?", (selected,),
        ).fetchone()
        budget_ok = (
            budget["reserved_asr_ms"] == aggregates["r_ms"]
            and budget["spent_asr_ms"] == aggregates["s_ms"]
            and budget["reserved_cost_micros"] == aggregates["r_cost"]
            and budget["spent_cost_micros"] == aggregates["s_cost"]
            and budget["reserved_asr_ms"] == 0
            and budget["reserved_cost_micros"] == 0
            and budget["spent_asr_ms"] <= budget["asr_limit_ms"]
            and budget["spent_cost_micros"] <= budget["cost_limit_micros"]
        )
        checks = {
            "search_plan_approved": plan_ok,
            "decision_contract_confirmed": decision_ok,
            "decision_revision_intact": decision_revision_ok,
            "decision_inputs_current": decision_inputs_current,
            "decision_uses_current_plan": decision_plan_ok,
            "all_findings_disposed": findings_ok,
            "evidence_present": evidence_present,
            "critical_claims_sufficient": critical_ok,
            "production_poc_passed": poc_ok,
            "production_recommendation_present": recommendation_ok,
            "production_rationale_evidence_sufficient": recommendation_evidence_ok,
            "selected_candidate_canonical": candidate_ok,
            "canonical_references_resolve": canonical_reference_ok,
            "declared_artifacts_current": declared_artifacts_ok,
            "requested_delivery_shape_valid": requested_shape_ok,
            "budget_sane": budget_ok,
        }
        problems = []
        warnings = []
        if not plan_ok:
            problems.append("search plan is absent, invalid, stale against budget, or lacks user scope approval")
        if not decision_ok:
            problems.append("decision contract is not confirmed by a verbatim user event")
        if not decision_revision_ok:
            problems.append("current decision does not match its immutable revision")
        if not decision_inputs_current:
            problems.append("research inputs changed after the current decision revision")
        if not decision_plan_ok:
            problems.append("decision was not made from the current approved plan revision")
        if not findings_ok:
            problems.append(f"{finding_counts.get('pending', 0)} findings remain pending")
        if not budget_ok:
            problems.append("budget has active/unknown reservations, inconsistent counters, or an overrun")
        if not canonical_reference_ok:
            problems.append("decision contains a source reference outside canonical findings/claims/evidence")
        if not candidate_ok:
            problems.append("decision recommendation does not reference an active canonical candidate")
        if not declared_artifacts_ok:
            problems.append("decision references a missing, changed, or non-POC artifact")
        delivery_consistent = (
            plan_ok and decision_ok and decision_revision_ok and decision_inputs_current
            and decision_plan_ok
            and findings_ok and budget_ok and canonical_reference_ok
            and candidate_ok and declared_artifacts_ok
        )
        if not delivery_consistent:
            status = "blocked"
            ok = False
        elif not evidence_present:
            status = "blocked"
            ok = True
            warnings.append("no findings were captured; only a research-only blocked delivery is allowed")
        elif requested == "production-ready" and (
            not critical_ok or not poc_ok or not recommendation_ok
            or not recommendation_evidence_ok or not production_shape_ok
        ):
            status = "pilot-only" if pilot_shape_ok else "blocked"
            ok = True
            if not critical_ok:
                warnings.append("requested production-ready was downgraded because critical evidence is insufficient")
            if not poc_ok:
                warnings.append("requested production-ready was downgraded because a representative POC has not passed")
            if not recommendation_ok:
                warnings.append("requested production-ready was downgraded because no structured recommendation exists")
            if not recommendation_evidence_ok:
                warnings.append("requested production-ready was downgraded because its rationale lacks sufficient load-bearing evidence")
            if not production_shape_ok:
                warnings.append(
                    "requested production-ready was downgraded because its typed delivery is incomplete: "
                    + "; ".join(production_shape_problems[:3])
                )
            if not pilot_shape_ok:
                warnings.append(
                    "the decision was further downgraded to blocked because no usable pilot runbook can be rendered: "
                    + "; ".join(pilot_shape_problems[:3])
                )
        elif requested == "pilot-only" and not pilot_shape_ok:
            status = "blocked"
            ok = True
            warnings.append(
                "requested pilot-only was downgraded because no usable pilot runbook can be rendered: "
                + "; ".join(pilot_shape_problems[:3])
            )
        else:
            status = requested
            ok = True
            if not critical_ok:
                warnings.append("critical claims are missing or insufficient; production-ready is prohibited")
        return {
            "run_id": selected, "status": status, "ok": ok,
            "checks": checks, "problems": problems, "warnings": warnings,
            "finding_counts": {key: finding_counts.get(key, 0) for key in sorted(DISPOSITIONS)},
            "critical_claims": len(critical_rows), "budget": _budget_public(budget),
        }
    finally:
        conn.close()


def status(path: str | os.PathLike[str], run_id: str | None = None) -> dict[str, Any]:
    gate = evaluate_gate(path, run_id)
    conn = _connect(path)
    try:
        selected = gate["run_id"]
        run = conn.execute("SELECT * FROM runs WHERE id=?", (selected,)).fetchone()
        assert run is not None
        counts = {}
        for table in ("events", "plans", "plan_revisions", "findings", "finding_revisions", "artifacts", "candidates", "evidence_clusters",
                      "claims", "attempts", "decisions", "decision_revisions", "deliveries"):
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (selected,),
            ).fetchone()[0]
        return {
            "schema_version": SCHEMA_VERSION,
            "run": {
                "id": run["id"], "objective": run["objective"], "profile": run["profile"],
                "status": run["status"], "require_critical_claims": bool(run["require_critical_claims"]),
                "config": json.loads(run["config_json"]), "created_at": run["created_at"],
                "updated_at": run["updated_at"],
            },
            "plan": _public_plan(conn.execute(
                "SELECT * FROM plans WHERE run_id=?", (selected,)
            ).fetchone()),
            "counts": counts, "gate": gate,
        }
    finally:
        conn.close()


def doctor(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    tools_dir = pathlib.Path(os.environ.get("RESEARCH_TOOLS_DIR", "~/tools")).expanduser()
    usage_mode = os.environ.get("RESEARCH_USAGE_MODE", "commercial").strip().lower()
    media_root = tools_dir / "MediaCrawler"
    media_allowed = media_root.is_dir() and usage_mode == "personal-noncommercial"
    yt_dlp = shutil.which("yt-dlp")
    twitter_command = os.environ.get("RESEARCH_TWITTER_COMMAND")
    xhs_mcp = tools_dir / "xiaohongshu-mcp"
    media_reason = (
        None if media_allowed else
        "MediaCrawler missing or disabled; set RESEARCH_USAGE_MODE=personal-noncommercial only when that license applies"
    )
    connectors: dict[str, Any] = {
        "web": {"availability": "host-dependent", "available": None,
                "capabilities": ["search", "fetch", "browser-if-host-provides"],
                "command": None, "data_dir": None, "license": "provider-specific",
                "account_risk": "provider-specific"},
        "github": {"availability": "host-dependent", "available": None,
                   "capabilities": ["search", "repository", "file", "release", "issue"],
                   "command": None, "data_dir": None, "license": "GitHub/provider terms",
                   "account_risk": "low for public read-only access"},
        "youtube": {"availability": "available" if yt_dlp else "unavailable",
                    "available": bool(yt_dlp), "capabilities": ["search", "detail", "subtitle", "media"],
                    "command": yt_dlp, "data_dir": None, "license": "platform terms",
                    "account_risk": "higher when cookies/account are used"},
        "twitter": {"availability": "available" if twitter_command else "unavailable",
                    "available": bool(twitter_command), "capabilities": ["search", "thread", "replies", "media"],
                    "command": twitter_command, "data_dir": None, "license": "connector/platform terms",
                    "account_risk": "high", "reason": None if twitter_command else "set RESEARCH_TWITTER_COMMAND to an authorized connector"},
        "xiaohongshu-mcp": {"availability": "available" if xhs_mcp.is_dir() else "unavailable",
                            "available": xhs_mcp.is_dir(), "capabilities": ["search", "detail", "comments"],
                            "command": None, "data_dir": str(xhs_mcp) if xhs_mcp.is_dir() else None,
                            "license": "connector/platform terms", "account_risk": "high"},
    }
    for channel in ("douyin", "xiaohongshu", "zhihu"):
        connectors[channel] = {
            "availability": "available" if media_allowed else "unavailable",
            "available": media_allowed,
            "capabilities": ["search", "detail", "comments", "media-reference"],
            "command": str(media_root / "main.py") if media_root.is_dir() else None,
            "data_dir": str(media_root / "data") if media_root.is_dir() else None,
            "license": "MediaCrawler NON-COMMERCIAL LEARNING LICENSE 1.1",
            "account_risk": "high", "reason": media_reason,
        }
    connectors["bilibili"] = {
        "availability": "available" if (yt_dlp or media_allowed) else "unavailable",
        "available": bool(yt_dlp or media_allowed),
        "capabilities": (["subtitle", "detail", "media"] if yt_dlp else [])
                        + (["search", "comments"] if media_allowed else []),
        "command": yt_dlp or (str(media_root / "main.py") if media_root.is_dir() else None),
        "data_dir": str(media_root / "data") if media_allowed else None,
        "license": "platform terms; MediaCrawler non-commercial restriction when used",
        "account_risk": "higher when cookies/account are used",
        "reason": None if (yt_dlp or media_allowed) else media_reason,
    }
    checks: dict[str, Any] = {
        "python_3_11_or_newer": sys.version_info >= (3, 11),
        "sqlite_3_37_or_newer": sqlite3.sqlite_version_info >= (3, 37),
        "sqlite_threadsafe": sqlite3.threadsafety > 0,
    }
    details: dict[str, Any] = {
        "app_version": APP_VERSION,
        "python": ".".join(map(str, sys.version_info[:3])),
        "sqlite": sqlite3.sqlite_version,
        "research_tools_dir": str(tools_dir),
        "usage_mode": usage_mode,
        "connectors": connectors,
    }
    if path is not None:
        try:
            conn = _connect(path)
            try:
                schema = conn.execute("PRAGMA user_version").fetchone()[0]
                journal = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                required_tables = {
                    "runs", "events", "plans", "plan_revisions", "findings",
                    "finding_revisions", "evidence_clusters", "claims", "budgets",
                    "attempts", "decisions", "decision_revisions", "deliveries",
                }
                actual_tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                missing_tables = sorted(required_tables - actual_tables)
                checks.update({
                    "schema_v3": schema == SCHEMA_VERSION,
                    "required_tables_present": not missing_tables,
                    "journal_mode_wal": journal == "wal",
                    "integrity_ok": integrity == "ok",
                    "foreign_keys_enabled": conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
                })
                details.update({"db": os.path.abspath(os.fspath(path)), "schema_version": schema,
                                "journal_mode": journal, "integrity": integrity,
                                "missing_tables": missing_tables})
            finally:
                conn.close()
        except ResearchError as exc:
            checks["database_readable"] = False
            details["database_error"] = str(exc)
    return {"ok": all(checks.values()), "checks": checks, "details": details}


def _row_object(row: sqlite3.Row, *, json_fields: Iterable[str] = ()) -> dict[str, Any]:
    out = dict(row)
    for field in json_fields:
        if field in out:
            out[field.removesuffix("_json")] = json.loads(out.pop(field))
    for field in ("critical", "confirmed", "require_critical_claims"):
        if field in out:
            out[field] = bool(out[field])
    return out


def _atomic_write(path: pathlib.Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return "".join(canonical_json(dict(row)) + "\n" for row in rows).encode("utf-8")


def _public_decision(
    row: sqlite3.Row | None, *, run: Mapping[str, Any],
    claims: list[dict[str, Any]], evidence: list[dict[str, Any]],
    effective_status: str,
) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    public = dict(payload)
    profile_task_types = {
        "technical": "implementation",
        "travel": "itinerary",
        "policy-forecast": "forecast",
    }
    task_type = public.get("task_type", profile_task_types.get(run["profile"], "research-only"))
    if effective_status == "blocked":
        task_type = "research-only"
    title = public.get("title", run["objective"])
    summary = public.get(
        "summary",
        "The confirmed decision contract and evidence gate determine the readiness shown here.",
    )
    contract = public.get("decision_contract")
    if not isinstance(contract, dict):
        contract = {}
    contract = {
        **contract,
        "confirmed": bool(row["confirmed"]),
        "verbatim": row["contract_verbatim"],
        "sha256": row["contract_sha256"],
        "user_confirmation_event_id": row["user_confirmation_event_id"],
        "presentation_event_id": payload.get("contract_presentation_event_id"),
    }
    public_claims = []
    for claim in claims:
        projected = dict(claim)
        projected["sources"] = list(projected.get("evidence_cluster_ids") or [])
        public_claims.append(projected)
    public_evidence = evidence
    # Canonical state fields override similarly named agent payload fields.
    public.update({
        "schema_version": SCHEMA_VERSION,
        "run_id": row["run_id"],
        "plan_revision": row["plan_revision"],
        "decision_revision": row["revision"],
        "input_event_seq": row["input_event_seq"],
        "decision_sha256": row["decision_sha256"],
        "task_type": task_type,
        "readiness": effective_status,
        "title": title,
        "summary": summary,
        "decision_contract": contract,
        "claims": public_claims,
        "evidence_clusters": public_evidence,
        "metadata": {
            **(public.get("metadata") if isinstance(public.get("metadata"), dict) else {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "contract_sha256": row["contract_sha256"],
        },
    })
    if effective_status == "blocked":
        public["recommendation"] = None
        blockers = public.get("blockers") if isinstance(public.get("blockers"), list) else []
        if not blockers:
            blockers = ["No findings were captured under the approved search plan."]
        public["blockers"] = blockers
    # These are internal write-contract fields, represented canonically above.
    for key in (
        "contract_verbatim", "contract_presentation_event_id", "confirmed",
        "user_confirmation_event_id", "requested_status",
    ):
        public.pop(key, None)
    return public


def export_state(
    path: str | os.PathLike[str], out_dir: str | os.PathLike[str],
    run_id: str | None = None, *, allow_incomplete: bool = False,
) -> dict[str, Any]:
    conn = _connect(path)
    try:
        # BEGIN IMMEDIATE prevents a writer from changing state between the status
        # projection and the table projections. WAL readers remain unblocked.
        with transaction(conn, immediate=True):
            selected = _default_run_id(conn, run_id)
            snapshot = status(path, selected)
            if not snapshot["gate"]["ok"] and not allow_incomplete:
                raise ResearchError(
                    "delivery gate is not internally consistent; fix blockers or use "
                    "--allow-incomplete for a non-deliverable diagnostic projection"
                )
            events = [_row_object(row) for row in conn.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY seq", (selected,)
            )]
            plan_row = conn.execute(
                "SELECT * FROM plans WHERE run_id=?", (selected,)
            ).fetchone()
            plan = _public_plan(plan_row)
            plan_revisions = [_row_object(row, json_fields=("payload_json",)) for row in conn.execute(
                "SELECT * FROM plan_revisions WHERE run_id=? ORDER BY revision", (selected,)
            )]
            findings = [_row_object(row, json_fields=("payload_json",)) for row in conn.execute(
                "SELECT * FROM findings WHERE run_id=? ORDER BY rowid", (selected,)
            )]
            finding_revisions = [_row_object(row, json_fields=("payload_json",)) for row in conn.execute(
                "SELECT * FROM finding_revisions WHERE run_id=? ORDER BY seq", (selected,)
            )]
            artifacts = [_row_object(row, json_fields=("payload_json",)) for row in conn.execute(
                "SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at,id", (selected,)
            )]
            candidates = [_row_object(row, json_fields=("payload_json",)) for row in conn.execute(
                "SELECT * FROM candidates WHERE run_id=? ORDER BY created_at,id", (selected,)
            )]
            evidence = [_row_object(
                row, json_fields=("source_fingerprints_json", "payload_json")
            ) for row in conn.execute(
                "SELECT * FROM evidence_clusters WHERE run_id=? ORDER BY created_at,id", (selected,)
            )]
            claims = []
            for row in conn.execute("SELECT * FROM claims WHERE run_id=? ORDER BY created_at,id", (selected,)):
                item = _row_object(row, json_fields=("payload_json",))
                item["evidence_cluster_ids"] = [value[0] for value in conn.execute(
                    "SELECT evidence_cluster_id FROM claim_evidence WHERE claim_id=? "
                    "ORDER BY evidence_cluster_id", (row["id"],)
                )]
                claims.append(item)
            attempts = [_attempt_public(row) for row in conn.execute(
                "SELECT * FROM attempts WHERE run_id=? ORDER BY created_at,id", (selected,)
            )]
            decision_row = conn.execute("SELECT * FROM decisions WHERE run_id=?", (selected,)).fetchone()
            decision_revisions = [_row_object(row, json_fields=("payload_json",)) for row in conn.execute(
                "SELECT * FROM decision_revisions WHERE run_id=? ORDER BY revision", (selected,)
            )]
            decision = _public_decision(
                decision_row, run=snapshot["run"], claims=claims, evidence=evidence,
                effective_status=snapshot["gate"]["status"],
            )
    finally:
        conn.close()

    out = pathlib.Path(out_dir)
    generated = utc_now()
    manifest = {
        "schema_version": SCHEMA_VERSION, "run_id": selected, "generated_at": generated,
        "plan_revision": plan["revision"] if isinstance(plan, dict) else None,
        "decision_revision": (
            decision["decision_revision"] if isinstance(decision, dict) else None
        ),
        "run": snapshot["run"], "counts": snapshot["counts"],
        "gate": snapshot["gate"], "budget": snapshot["gate"]["budget"],
    }
    contents = {
        "manifest.v3.json": _json_bytes(manifest),
        "events.jsonl": _jsonl_bytes(events),
        "plan.json": _json_bytes(plan),
        "plan-revisions.jsonl": _jsonl_bytes(plan_revisions),
        "findings.jsonl": _jsonl_bytes(findings),
        "finding-revisions.jsonl": _jsonl_bytes(finding_revisions),
        "artifacts.jsonl": _jsonl_bytes(artifacts),
        "candidates.jsonl": _jsonl_bytes(candidates),
        "evidence-clusters.jsonl": _jsonl_bytes(evidence),
        "claims.jsonl": _jsonl_bytes(claims),
        "attempts.jsonl": _jsonl_bytes(attempts),
        "decision.json": _json_bytes(decision),
        "decision-revisions.jsonl": _jsonl_bytes(decision_revisions),
    }
    file_records = {}
    for name, content in contents.items():
        digest = _atomic_write(out / name, content)
        file_records[name] = {"sha256": digest, "bytes": len(content)}
    delivery_id = "del_" + uuid.uuid4().hex
    delivery_manifest = {
        "schema_version": SCHEMA_VERSION, "delivery_id": delivery_id,
        "run_id": selected, "gate_status": snapshot["gate"]["status"],
        "created_at": generated, "files": file_records,
    }
    delivery_content = _json_bytes(delivery_manifest)
    _atomic_write(out / "delivery-manifest.json", delivery_content)
    conn = _connect(path)
    try:
        with transaction(conn, immediate=True):
            conn.execute(
                "INSERT INTO deliveries VALUES (?,?,?,?,?)",
                (delivery_id, selected, snapshot["gate"]["status"],
                 canonical_json(file_records), generated),
            )
            _insert_event(conn, selected, "delivery.exported", "system",
                          canonical_json({"delivery_id": delivery_id, "files": file_records}), generated)
    finally:
        conn.close()
    return delivery_manifest


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=APP_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_p = sub.add_parser("doctor", help="check runtime and, optionally, database health")
    doctor_p.add_argument("--db")

    init_p = sub.add_parser("init", help="initialize a v3 database")
    init_p.add_argument("--db", required=True)
    init_p.add_argument("--input", required=True,
                        help="JSON: objective, optional profile/run_id; ASR limits must be zero")

    def db_command(name: str, help_text: str, *, takes_input: bool = False) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--db", required=True)
        command.add_argument("--run-id")
        if takes_input:
            command.add_argument("--input", required=True, help="JSON file, or - for stdin")
        return command

    db_command("status", "show run, counts, budget, and gate status")
    db_command("record-event", "append an immutable verbatim event", takes_input=True)
    db_command("authorize-budget", "set hard ASR limits from a verbatim user authorization", takes_input=True)
    db_command("set-plan", "validate and revision a user-approved search plan", takes_input=True)
    db_command("upsert-finding", "insert/update a stable-fingerprint finding", takes_input=True)
    db_command("reserve-budget", "atomically reserve ASR duration/cost", takes_input=True)
    db_command("settle-budget", "settle/release/mark unknown a reservation", takes_input=True)
    notes_p = db_command("project-notes", "page through note projections")
    notes_p.add_argument("--cursor", type=int, default=0)
    notes_p.add_argument("--limit", type=int, default=50)
    notes_p.add_argument("--disposition", default="pending",
                         choices=["pending", "consumed", "excluded", "all"])
    db_command("ack-notes", "mark projected notes consumed/excluded", takes_input=True)
    db_command("upsert-candidate", "insert/update a normalized solution candidate", takes_input=True)
    db_command("upsert-artifact", "record a content-addressed artifact", takes_input=True)
    db_command("upsert-evidence-cluster", "insert/update an evidence independence cluster",
               takes_input=True)
    db_command("upsert-claim", "insert/update a decision claim and evidence links", takes_input=True)
    db_command("set-decision", "set the confirmed decision contract", takes_input=True)
    db_command("gate", "evaluate release gates")
    export_p = db_command("export", "atomically export JSON/JSONL projections")
    export_p.add_argument("--out-dir", required=True)
    export_p.add_argument("--allow-incomplete", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.db)
            _print_json(result)
            return 0 if result["ok"] else 1
        if args.command == "init":
            result = init_database(args.db, load_json_input(args.input))
        elif args.command == "status":
            result = status(args.db, args.run_id)
        elif args.command == "record-event":
            result = record_event(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "authorize-budget":
            result = authorize_budget(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "set-plan":
            result = set_plan(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "upsert-finding":
            result = upsert_finding(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "reserve-budget":
            result = reserve_budget(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "settle-budget":
            result = settle_budget(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "project-notes":
            result = project_notes(args.db, run_id=args.run_id, cursor=args.cursor,
                                   limit=args.limit, disposition=args.disposition)
        elif args.command == "ack-notes":
            result = acknowledge_notes(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "upsert-candidate":
            result = upsert_candidate(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "upsert-artifact":
            result = upsert_artifact(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "upsert-evidence-cluster":
            result = upsert_evidence_cluster(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "upsert-claim":
            result = upsert_claim(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "set-decision":
            result = set_decision(args.db, load_json_input(args.input), args.run_id)
        elif args.command == "gate":
            result = evaluate_gate(args.db, args.run_id)
            _print_json(result)
            return 0 if result["ok"] else 2
        elif args.command == "export":
            result = export_state(args.db, args.out_dir, args.run_id,
                                  allow_incomplete=args.allow_incomplete)
        else:  # pragma: no cover - argparse makes this unreachable
            parser.error(f"unknown command: {args.command}")
            return 2
        _print_json(result)
        return 0
    except (ResearchError, sqlite3.Error) as exc:
        _print_json({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
