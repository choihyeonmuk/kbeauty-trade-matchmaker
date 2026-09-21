#!/usr/bin/env python3
"""TradeWith adapter for the kbeauty-trade-matchmaker skill.

This module is the *only* boundary between the skill and TradeWith's internal
data. It implements the six PRD 13.1 capabilities (BUILD-CONTRACT.md 13.3)
behind one interface with two interchangeable backends:

  FileAdapter  local JSON tree under --data-dir; works today, with no backend,
               no credentials and no network. This is the default.
  HttpAdapter  urllib client for TradeWith's internal API once it exists.
               Credentials are read from the environment at call time only.

Design rules this file obeys (BUILD-CONTRACT.md 1.5, 13.3, 14; INV-09, INV-10,
INV-25, INV-27):

  * Importing this module performs no network call, no filesystem write and no
    credential read. Everything happens inside a function call.
  * No credential, token or host is hard-coded. The HTTP backend fails with a
    clear, actionable message when its environment is not configured.
  * The token is never written to stdout, stderr, an exception message or a
    stored file: every outgoing string passes through _redact().
  * Every write path validates its payload against schemas/ (and, when
    scripts/validate_output.py is present, against the invariant checks too)
    and refuses to persist anything invalid.
  * The three POST capabilities are review-queue writes. Nothing in this file
    moves a message toward a recipient; there is no transport client of any
    kind here or anywhere else in the package.
  * patch_lead_status refuses APPROVED_FOR_OUTREACH and every later
    application-layer state (BUILD-CONTRACT.md 9.2, INV-09, INV-37).

CLI
    python3 adapters/tradewith_adapter.py get-rfq --id 134
    python3 adapters/tradewith_adapter.py list-sellers --country KR --limit 10
    python3 adapters/tradewith_adapter.py save-leads --input leads.json
    python3 adapters/tradewith_adapter.py save-matches --input match-134.json
    python3 adapters/tradewith_adapter.py save-outreach-drafts --input d.json
    python3 adapters/tradewith_adapter.py update-lead-status --id BUY-x-com \\
        --status QUALIFIED

Stream discipline matches the scripts (BUILD-CONTRACT.md 7.2): stdout carries
only the JSON result; every diagnostic goes to stderr. Exit codes: 0 success,
1 validation/data error, 2 usage error.

Korean gloss: 이 파일은 TradeWith 내부 데이터에 접근하는 유일한 경계이며,
API가 아직 없어도 로컬 파일 백엔드로 동작한다. 저장은 전부 검토 큐 쓰기이고,
메시지 발송 기능은 이 패키지에 존재하지 않는다.
"""

from __future__ import annotations

import abc
import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# Package-relative paths. Resolved from THIS file, never from cwd
# (BUILD-CONTRACT.md R7.1.1).
# --------------------------------------------------------------------------

ADAPTER_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(ADAPTER_DIR)
SCHEMA_DIR = os.path.join(PACKAGE_ROOT, "schemas")
SCRIPTS_DIR = os.path.join(PACKAGE_ROOT, "scripts")

SKILL_VERSION = "0.5.0"
SCHEMA_VERSION = "0.1.0"

# --------------------------------------------------------------------------
# Environment variable names. Read at CALL time only (INV-25, INV-27).
# The first spelling in each tuple is the BUILD-CONTRACT.md 13.3 name and wins;
# the second is accepted as an alias so either house style works.
# --------------------------------------------------------------------------

ENV_BASE_URL = ("TRADEWITH_BASE_URL", "TRADEWITH_API_BASE")
ENV_TOKEN = ("TRADEWITH_TOKEN", "TRADEWITH_API_TOKEN")
ENV_BACKEND = "TRADEWITH_BACKEND"
ENV_DATA_DIR = "TRADEWITH_DATA_DIR"
ENV_TIMEOUT = "TRADEWITH_TIMEOUT_SECONDS"
ENV_RETRIES = "TRADEWITH_MAX_RETRIES"
ENV_APP_URL = "TRADEWITH_APP_URL"

DEFAULT_DATA_DIR = "./tradewith-data"
DEFAULT_APP_URL = "https://app.tradewith.example"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.0
RETRY_BACKOFF_CAP_SECONDS = 8.0
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

USER_AGENT = "kbeauty-trade-matchmaker/%s (tradewith-adapter)" % SKILL_VERSION

# --------------------------------------------------------------------------
# State machine (BUILD-CONTRACT.md 9, PRD 8).
# --------------------------------------------------------------------------

ENTITY_STATES = (
    "DISCOVERED",
    "VERIFIED",
    "QUALIFIED",
    "MATCH_CANDIDATE",
    "READY_FOR_REVIEW",
    "APPROVED_FOR_OUTREACH",
    "CONTACTED",
    "REPLIED",
    "RFQ_RECEIVED",
    "PROPOSAL_RECEIVED",
    "MATCHED",
    "CLOSED",
)

# States this package is allowed to write (BUILD-CONTRACT.md 9.2 + INV-37,
# which excludes CLOSED from the application-layer-only set).
SKILL_WRITABLE_STATES = frozenset(
    {
        "DISCOVERED",
        "VERIFIED",
        "QUALIFIED",
        "MATCH_CANDIDATE",
        "READY_FOR_REVIEW",
        "CLOSED",
    }
)

# States only the TradeWith / CRM application layer may set (INV-09, INV-37).
APPLICATION_ONLY_STATES = frozenset(
    {
        "APPROVED_FOR_OUTREACH",
        "CONTACTED",
        "REPLIED",
        "RFQ_RECEIVED",
        "PROPOSAL_RECEIVED",
        "MATCHED",
    }
)

# INV-37 / SKILL.md Mode 4: these states are entered only by a scored record with
# qualified true. A raw ("unscored") or not-qualified record stops at VERIFIED.
SCORED_ONLY_STATES = frozenset({"QUALIFIED", "MATCH_CANDIDATE", "READY_FOR_REVIEW"})

# Skill-performed edges of the BUILD-CONTRACT.md 9.1 transition table. Edges
# owned by the application layer are deliberately absent, which is what makes
# them refusals here. CLOSED is terminal for the skill: only the application
# layer reopens a closed record.
SKILL_TRANSITIONS = {
    None: frozenset({"DISCOVERED"}),
    "DISCOVERED": frozenset({"VERIFIED", "CLOSED"}),
    "VERIFIED": frozenset({"QUALIFIED", "DISCOVERED", "CLOSED"}),
    "QUALIFIED": frozenset({"MATCH_CANDIDATE", "READY_FOR_REVIEW", "VERIFIED", "CLOSED"}),
    "MATCH_CANDIDATE": frozenset({"READY_FOR_REVIEW", "QUALIFIED", "CLOSED"}),
    "READY_FOR_REVIEW": frozenset({"QUALIFIED", "MATCH_CANDIDATE", "CLOSED"}),
    "CLOSED": frozenset(),
}

# --------------------------------------------------------------------------
# Outreach draft contract.
#
# No schema file owns an outreach draft: templates/*.md own its rendered
# Markdown envelope (BUILD-CONTRACT.md 10.4) and this adapter owns the minimal
# structural contract needed to queue one for review. Keep the two in step.
# --------------------------------------------------------------------------

DRAFT_REQUIRED_FIELDS = (
    "entity_id",
    "side",
    "channel_type",
    "channel_value",
    "language",
    "subject",
    "body",
)

DRAFT_SIDES = frozenset({"Buyer", "Seller"})

# BUILD-CONTRACT.md 3.6 company-level channel vocabulary.
CHANNEL_TYPES = frozenset(
    {
        "partnership_form",
        "wholesale_form",
        "form",
        "contact_page",
        "corporate_email",
        "phone",
        "linkedin",
        "messenger",
        "other",
    }
)

# Keys that would turn a review-queue record into a dispatch instruction or
# would smuggle a credential into stored data. Their presence is a refusal
# (INV-10, INV-25). The scan is RECURSIVE (_forbidden_key_paths): nesting a
# dispatch block one level down is the same instruction, so it is the same
# refusal. Tokens are compared after _norm_token, so "Reply-To", "reply to"
# and "reply_to" are one entry.
DRAFT_FORBIDDEN_KEYS = frozenset(
    {
        "access_key",
        "api_key",
        "api_token",
        "auth",
        "authorization",
        "bcc",
        "bearer",
        "cc",
        "cookie",
        "credentials",
        "deliver_at",
        "dispatch",
        "envelope_from",
        "mail_from",
        "mail_server",
        "mail_transport",
        "mailbox",
        "password",
        "queue_for_send",
        "recipients",
        "reply_to",
        "schedule_at",
        "secret",
        "send",
        "send_at",
        "sender_credentials",
        "sendgrid_key",
        "smtp",  # a forbidden key name only; no transport ships here (INV-10)
        "smtp_host",
        "smtp_password",
        "smtp_port",
        "smtp_user",
        "to",
        "token",
        "transport",
        "webhook",
        "webhook_url",
    }
)

# INV-34 / R10.4.3 first clause: a live-demand claim is forbidden unless a cited
# rfq_id resolves to an open RFQ whose status and as_of date the draft states. The
# phrase list, the status/date readers and the open states are NOT kept here: they
# are imported from scripts/validate_output.py (_import_validator), so the adapter
# and the validator can never drift into two different floors.
#
# An RFQ status is a point-in-time fact. A draft whose rfq_as_of is older than
# scoring.config.json output.max_rfq_age_days (default below) before the run's
# as_of is refused as stale demand.
DEFAULT_MAX_RFQ_AGE_DAYS = 30

DRAFT_FIXED_FIELDS = (
    ("status", "READY_FOR_REVIEW"),
    ("auto_send", False),
    ("manual_approval_required", True),
)

# --------------------------------------------------------------------------
# Document kinds.
# --------------------------------------------------------------------------

SCHEMA_KINDS = (
    "buyer",
    "seller",
    "rfq",
    "evidence",
    "match-result",
    "discovery-result",
)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


# --------------------------------------------------------------------------
# Errors. main() maps these onto the exit codes of BUILD-CONTRACT.md 7.3.
# --------------------------------------------------------------------------


class AdapterError(Exception):
    """Base class for every error this module raises deliberately."""


class UsageError(AdapterError):
    """Bad invocation or missing configuration. Exit code 2."""


class DataError(AdapterError):
    """Input parses but violates a schema, an invariant or this contract. Exit 1."""


class ValidationError(DataError):
    """A payload failed schema or invariant validation and was not persisted."""

    def __init__(self, message, errors=None):
        DataError.__init__(self, message)
        self.errors = list(errors or [])


class TransportError(DataError):
    """The HTTP backend could not complete a request. Exit 1."""


# --------------------------------------------------------------------------
# Small utilities.
# --------------------------------------------------------------------------


def _eprint(message, quiet=False):
    """Write one diagnostic line to stderr unless quiet."""
    if not quiet:
        sys.stderr.write("%s\n" % message)


def _redact(text, token=None):
    """Remove a bearer token from any string before it is printed or stored."""
    out = "" if text is None else str(text)
    candidates = []
    if token:
        candidates.append(str(token))
    for name in ENV_TOKEN:
        value = os.environ.get(name)
        if value:
            candidates.append(value)
    for value in candidates:
        if value and len(value) >= 4 and value in out:
            out = out.replace(value, "***redacted***")
    # Belt and braces: never let a full Authorization header escape. The scheme word
    # (Bearer/Basic/Token) is kept; the credential after it is what gets removed.
    out = re.sub(
        r"(?i)(authorization\s*[:=]\s*)((?:bearer|basic|token)\s+)?(\S+)",
        r"\1\2***redacted***",
        out,
    )
    return out


def _safe_id(value, label):
    """Validate an identifier that will become part of a filesystem path."""
    if value is None:
        raise UsageError("%s is required" % label)
    text = str(value).strip()
    if not text:
        raise UsageError("%s is required" % label)
    if not _ID_PATTERN.match(text) or ".." in text:
        raise UsageError(
            "%s %r is not a valid identifier; expected [A-Za-z0-9._-] and no path separators"
            % (label, text)
        )
    return text


def _as_list(value, label):
    """Accept a bare object, a list, or an envelope; always return a list."""
    if value is None:
        raise UsageError("%s is required" % label)
    if isinstance(value, dict):
        for key in ("records", "drafts", "leads", "items", "data"):
            inner = value.get(key)
            if isinstance(inner, list):
                return list(inner)
        return [value]
    if isinstance(value, list):
        return list(value)
    raise UsageError("%s must be a JSON object, array or envelope" % label)


def _coerce_range(value):
    """Normal form for a quantity (BUILD-CONTRACT.md 3.3).

    Returns {"min": m, "max": M, "unit": u?} or None when the value is unknown,
    absent, or states only an open lower bound ("MOQ from 1,000"), which does
    not establish a comparable value.
    """
    if value is None or value == "unknown":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            return None
        return {"min": value, "max": value}
    if isinstance(value, dict):
        low = value.get("min")
        high = value.get("max")
        unit = value.get("unit")
        if isinstance(low, bool) or isinstance(high, bool):
            return None
        if high is None:
            return None  # open upper bound -> unknown, never +inf
        if low is None:
            low = 0
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            return None
        if low > high:
            return None
        out = {"min": low, "max": high}
        if isinstance(unit, str) and unit:
            out["unit"] = unit
        return out
    return None


def _is_unknown(value):
    """True when a field carries the 'not yet determined' state (3.2)."""
    return value is None or value == "unknown"


def _norm_token(value):
    """Lowercase/underscore a comparison token. Retrieval filters only."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _norm_cert(value):
    """Uppercase/underscore a certification token. Retrieval filters only."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")


def _resolve_env(names, explicit=None):
    """First non-empty of an explicit value then each environment name."""
    if explicit:
        return str(explicit)
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _document_kind(record):
    """Detect which schema a record belongs to from its id fields."""
    if not isinstance(record, dict):
        raise DataError("record must be a JSON object, got %s" % type(record).__name__)
    if "buyer_id" in record and "rfq_id" not in record:
        return "buyer"
    if "seller_id" in record:
        return "seller"
    if "rfq_id" in record and "match_run_id" not in record:
        return "rfq"
    if "match_run_id" in record:
        return "match-result"
    if "evidence_id" in record and "claim" in record:
        return "evidence"
    if "entity" in record and "records" in record:
        return "discovery-result"
    raise DataError(
        "cannot determine the document kind: no buyer_id, seller_id, rfq_id, "
        "match_run_id or evidence_id field is present"
    )


def _record_id(record, kind):
    """The identifier a record is stored under."""
    key = {
        "buyer": "buyer_id",
        "seller": "seller_id",
        "rfq": "rfq_id",
        "match-result": "match_run_id",
        "evidence": "evidence_id",
    }.get(kind)
    if key is None:
        raise DataError("document kind %r has no stable identifier" % kind)
    value = record.get(key)
    if not isinstance(value, (str, int)) or str(value).strip() == "":
        raise DataError("document is missing a usable %s" % key)
    return _safe_id(value, key)


# --------------------------------------------------------------------------
# Config and validation. Both are lazy: nothing here runs at import time.
# --------------------------------------------------------------------------

_COMMON_CACHE = {}
_VALIDATOR_CACHE = {}
_CONFIG_CACHE = {}


def _load_by_path(name, path):
    """Execute one sibling script as a module, from its file, never from sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _import_common():
    """Import scripts/_common.py lazily, or return None when it is absent.

    An installed copy may share a process with another package that also ships a
    `_common` module, so a `_common` already in sys.modules is reused only when it
    is this package's own file.
    """
    if "module" in _COMMON_CACHE:
        return _COMMON_CACHE["module"]
    module = None
    path = os.path.join(SCRIPTS_DIR, "_common.py")
    if os.path.isfile(path):
        try:
            loaded = sys.modules.get("_common")
            loaded_path = getattr(loaded, "__file__", None) if loaded is not None else None
            if loaded_path and os.path.realpath(loaded_path) == os.path.realpath(path):
                module = loaded
            else:
                module = _load_by_path("kbtm_common", path)
        except Exception:  # a broken sibling must not take the adapter down
            module = None
    _COMMON_CACHE["module"] = module
    return module


def _import_validator():
    """Import scripts/validate_output.py by path, or return None when it cannot be used.

    The draft guards (INV-34 phrase list, validate_outreach_draft) come from this one
    module so the adapter never keeps a second, drifting copy. validate_output.py does
    `import _common`; for the duration of its import that name is bound to this
    package's _common, then sys.modules["_common"] is put back exactly as it was
    (restored, or removed when nothing held it) and the scripts directory that
    validate_output.py inserts into sys.path is taken out again.
    """
    if "module" in _VALIDATOR_CACHE:
        return _VALIDATOR_CACHE["module"]
    module = None
    path = os.path.join(SCRIPTS_DIR, "validate_output.py")
    common = _import_common()
    if common is not None and os.path.isfile(path):
        previous = sys.modules.get("_common")
        saved_path = list(sys.path)
        sys.modules["_common"] = common
        try:
            candidate = _load_by_path("kbtm_validate_output", path)
            if hasattr(candidate, "validate_outreach_draft"):
                module = candidate
        except Exception:
            module = None
        finally:
            if previous is not None:
                sys.modules["_common"] = previous
            else:
                sys.modules.pop("_common", None)
            sys.path[:] = saved_path
    _VALIDATOR_CACHE["module"] = module
    return module


def _max_rfq_age_days(config):
    """scoring.config.json output.max_rfq_age_days, else DEFAULT_MAX_RFQ_AGE_DAYS."""
    value = (config.get("output") or {}).get("max_rfq_age_days", DEFAULT_MAX_RFQ_AGE_DAYS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UsageError("output.max_rfq_age_days must be a non-negative integer, got %r" % (value,))
    return value


def _parse_date(value):
    """A YYYY-MM-DD string as a date, or None."""
    text = str(value or "").strip()
    if not _DATE_PATTERN.match(text):
        return None
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def load_config(path=None):
    """Load schemas/scoring.config.json (or an override) and cache it."""
    resolved = os.path.abspath(path) if path else os.path.join(SCHEMA_DIR, "scoring.config.json")
    if resolved in _CONFIG_CACHE:
        return _CONFIG_CACHE[resolved]
    if not os.path.isfile(resolved):
        raise UsageError("scoring config not found: %s" % resolved)
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except ValueError as exc:
        raise UsageError("scoring config is not valid JSON: %s" % exc)
    _CONFIG_CACHE[resolved] = config
    return config


def _load_schema(kind, schema_dir=None):
    """Load one bundled schema by bare kind name."""
    if kind not in SCHEMA_KINDS:
        raise UsageError(
            "unknown schema %r; expected one of %s" % (kind, ", ".join(SCHEMA_KINDS))
        )
    directory = os.path.abspath(schema_dir) if schema_dir else SCHEMA_DIR
    path = os.path.join(directory, "%s.schema.json" % kind)
    if not os.path.isfile(path):
        raise UsageError("schema not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        raise UsageError("schema %s is not valid JSON: %s" % (path, exc))


def _validate_with_script(kind, document, schema_dir=None):
    """Validate through scripts/validate_output.py (schema + invariant path).

    Returns a list of error strings, or None when the script is unavailable or
    could not be used, so the caller can fall back.
    """
    script = os.path.join(SCRIPTS_DIR, "validate_output.py")
    if not os.path.isfile(script):
        return None
    command = [sys.executable, script, "--schema", kind, "--input", "-"]
    if schema_dir:
        command.extend(["--schema-dir", os.path.abspath(schema_dir)])
    payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, _err = process.communicate(payload, timeout=120)
    except Exception:
        return None
    if process.returncode == 2:
        return None  # usage problem in the validator itself: fall back
    try:
        report = json.loads(out.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(report, dict):
        return None
    errors = []
    for item in report.get("errors") or []:
        errors.append(_format_report_item(item, "schema"))
    for item in report.get("invariant_failures") or []:
        errors.append(_format_report_item(item, "invariant"))
    if not errors and report.get("valid") is False:
        errors.append("validate_output.py reported the document as invalid")
    return errors


def _format_report_item(item, kind):
    """Render one validate_output.py report row as a single error line."""
    if not isinstance(item, dict):
        return "%s: %s" % (kind, item)
    parts = []
    if item.get("invariant"):
        parts.append(str(item["invariant"]))
    if item.get("id"):
        parts.append(str(item["id"]))
    if item.get("path"):
        parts.append(str(item["path"]))
    prefix = " ".join(parts) if parts else kind
    return "%s: %s" % (prefix, item.get("message", "invalid"))


def validate_document(kind, document, schema_dir=None, quiet=False):
    """Validate one document. Returns a list of human-readable error strings.

    Validation order (BUILD-CONTRACT.md 13.3 rule a):
      1. scripts/validate_output.py -- schema AND invariant checks;
      2. scripts/_common.validate   -- schema only, when the script is absent;
      3. refuse                     -- when neither validator ships, no write
                                       may proceed, because an unvalidated
                                       write is exactly what rule (a) forbids.
    """
    errors = _validate_with_script(kind, document, schema_dir=schema_dir)
    if errors is not None:
        return errors
    common = _import_common()
    if common is not None and hasattr(common, "validate"):
        _eprint(
            "note: scripts/validate_output.py unavailable; validated %s against "
            "the schema only (invariant checks skipped)" % kind,
            quiet,
        )
        return list(common.validate(document, _load_schema(kind, schema_dir)))
    raise DataError(
        "no validator is available (neither scripts/validate_output.py nor "
        "scripts/_common.py could be used); refusing to read or write "
        "unvalidated data"
    )


def _require_valid(kind, document, schema_dir=None, quiet=False, label=None):
    """Validate and raise ValidationError when anything is wrong."""
    errors = validate_document(kind, document, schema_dir=schema_dir, quiet=quiet)
    if errors:
        raise ValidationError(
            "%s failed %s validation and was not written (%d problem%s)"
            % (label or kind, kind, len(errors), "" if len(errors) == 1 else "s"),
            errors,
        )


def _warn_invalid(kind, document, schema_dir=None, quiet=False, strict=False, label=None):
    """Validate a document we READ. Warn by default, raise under --strict."""
    errors = validate_document(kind, document, schema_dir=schema_dir, quiet=quiet)
    if not errors:
        return
    if strict:
        raise ValidationError(
            "%s failed %s validation (%d problem%s)"
            % (label or kind, kind, len(errors), "" if len(errors) == 1 else "s"),
            errors,
        )
    _eprint(
        "warning: %s failed %s validation (%d problem%s); returning it anyway"
        % (label or kind, kind, len(errors), "" if len(errors) == 1 else "s"),
        quiet,
    )
    for message in errors[:5]:
        _eprint("  %s" % message, quiet)


# --------------------------------------------------------------------------
# Evidence emission (BUILD-CONTRACT.md 4.7, 13.3 rule e).
# --------------------------------------------------------------------------


def internal_evidence(
    claim,
    value,
    resource_path,
    quote_or_summary,
    as_of,
    evidence_id="EV-001",
    app_url=None,
    confidence=0.9,
    is_official=False,
):
    """Build one evidence item for a fact read out of TradeWith's own database.

    Fixed by BUILD-CONTRACT.md 4.7: source_type "internal_record", source_tier
    2, retrieval_method "internal_api", a resolvable internal source_url (never
    a bare id, never a query string), and an observed_at derived from the run's
    --as-of rather than the wall clock (INV-14, INV-24).
    """
    if not _DATE_PATTERN.match(str(as_of or "")):
        raise UsageError("as_of must be YYYY-MM-DD, got %r" % (as_of,))
    base = (app_url or _resolve_env((ENV_APP_URL,) + ENV_BASE_URL) or DEFAULT_APP_URL).rstrip("/")
    path = str(resource_path or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    item = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "claim": claim,
        "value": value,
        "source_url": base + path,
        "source_type": "internal_record",
        "source_tier": 2,
        "is_official": bool(is_official),
        "observed_at": "%sT00:00:00Z" % as_of,
        "source_date": as_of,
        "confidence": round(float(confidence), 2),
        "quote_or_summary": quote_or_summary,
        "retrieval_method": "internal_api",
    }
    return item


def _next_evidence_id(document):
    """Next free EV-NNN id inside a document, first-seen order preserved."""
    used = set()
    for item in document.get("evidence") or []:
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str):
            used.add(item["evidence_id"])
    counter = 1
    while "EV-%03d" % counter in used:
        counter += 1
    return "EV-%03d" % counter


def attach_internal_evidence(document, kind, resource_path, as_of, app_url=None):
    """Append a 4.7 internal-record evidence item to a document we just read.

    Idempotent: a second call with the same source_url and claim changes
    nothing, so re-reads stay byte-stable.
    """
    if not isinstance(document, dict):
        return document
    claim = {"rfq": "product_category", "seller": "company_name", "buyer": "company_name"}.get(
        kind, "company_name"
    )
    identifier = document.get("rfq_id") or document.get("seller_id") or document.get("buyer_id")
    summary = "TradeWith internal record %s read via the internal API on %s." % (
        identifier,
        as_of,
    )
    candidate = internal_evidence(
        claim=claim,
        value=document.get("product_category")
        if kind == "rfq"
        else document.get("company_name", "unknown"),
        resource_path=resource_path,
        quote_or_summary=summary,
        as_of=as_of,
        evidence_id=_next_evidence_id(document),
        app_url=app_url,
    )
    existing = document.setdefault("evidence", [])
    for item in existing:
        if (
            isinstance(item, dict)
            and item.get("source_url") == candidate["source_url"]
            and item.get("claim") == candidate["claim"]
        ):
            return document
    existing.append(candidate)
    return document


# --------------------------------------------------------------------------
# Outreach draft normalization.
# --------------------------------------------------------------------------


def _forbidden_key_paths(node, prefix=""):
    """Every dispatch/credential key anywhere in `node`, as JSON pointers.

    The guard used to read only the top level, so a `delivery: {transport, recipients}`
    block travelled through untouched and landed on disk as a ready-made dispatch
    instruction. Nesting changes nothing about what the key means, so the walk
    descends into every dict and list (INV-10, INV-25).
    """
    hits = []
    if isinstance(node, dict):
        for key in sorted(node, key=lambda k: str(k)):
            path = "%s/%s" % (prefix, key)
            if _norm_token(key) in DRAFT_FORBIDDEN_KEYS:
                hits.append(path.lstrip("/"))
                continue
            hits.extend(_forbidden_key_paths(node[key], path))
    elif isinstance(node, list):
        for position, item in enumerate(node):
            hits.extend(_forbidden_key_paths(item, "%s/%d" % (prefix, position)))
    return hits


def _draft_demand_text(out, validator):
    """Subject and body as a reviewer reads them: the JSON fields AND the rendered draft.

    Only subject and body are scanned, as validate_output.py does: the reviewer
    checklist of every draft quotes "currently looking" in order to forbid it.
    """
    parts = [out[field] for field in ("subject", "body") if isinstance(out.get(field), str)]
    markdown = out.get("draft_markdown")
    if isinstance(markdown, str) and markdown.strip():
        parsed, _problems = validator.parse_outreach_draft(markdown)
        parts.extend(parsed[field] for field in ("subject", "body") if isinstance(parsed.get(field), str))
    return "\n".join(parts)


def _check_live_demand_claim(out, label, validator, adapter=None, as_of=None, max_age_days=None,
                             evidence=None):
    """INV-34 / R10.4.3 first clause, on the text a reviewer will actually read.

    A draft may say a buyer is looking, or cite an RFQ at all, only when the RFQ is
    really open: it carries rfq_id, an open rfq_status and an rfq_as_of, states that
    status and date in the text, the date is no older than max_age_days before the
    run's as_of, and -- when the adapter can read them -- the RFQ resolves through
    get_rfq with that status and date, and the target passed the hard filter of every
    stored match run for that RFQ. Caller-supplied rfq_status / rfq_as_of are claims,
    not facts. Anything less is fabricated demand (PRD 11.1, PRD test T05).

    `as_of` must be the run date the caller chose: a draft that carries an rfq_* field or
    cites an RFQ is refused when it is None, because freshness measured against the
    config's frozen as_of_default would pass a stale RFQ. `evidence` (the stored lead's
    items, or None) licenses a dated "you are looking for" sentence exactly as
    validate_output.live_demand_matches does.

    Returns (rfq_documents, match_documents) read along the way, for the validator.
    """
    text = _draft_demand_text(out, validator)
    facts = [fact for fact in out.get("personalization_facts") or [] if isinstance(fact, dict)]
    markdown = out.get("draft_markdown")
    if isinstance(markdown, str) and markdown.strip():
        facts.extend(validator.parse_outreach_draft(markdown)[0].get("personalization_facts") or [])
    matched = validator.live_demand_matches(text, facts, evidence)
    cited = sorted(set(validator._rfq_key(value) for value in validator._RFQ_CITATION_RE.findall(text)))
    carries = any(str(out.get(field) or "").strip() for field in ("rfq_id", "rfq_status", "rfq_as_of"))
    if not (matched or cited or carries):
        return [], []
    if (cited or carries) and not _parse_date(as_of):
        raise DataError(
            "%s cites an RFQ (rfq_id / rfq_status / rfq_as_of or 'RFQ #…') but no explicit as_of "
            "was given; pass --as-of YYYY-MM-DD (as_of= in Python) so RFQ freshness is measured "
            "against the run date, never the config's as_of_default (INV-34, R10.4.3)" % (label,)
        )

    open_states = validator.LIVE_DEMAND_RFQ_STATES
    rfq_id = str(out.get("rfq_id") or "").strip()
    rfq_key = validator._rfq_key(rfq_id)
    rfq_status = str(out.get("rfq_status") or "").strip()
    rfq_as_of = str(out.get("rfq_as_of") or "").strip()
    problems = []
    if not rfq_id:
        problems.append("no rfq_id is cited")
    elif [value for value in cited if value != rfq_key]:
        problems.append("the text cites RFQ #%s but rfq_id is %r" % ("/#".join(cited), rfq_id))
    if rfq_status not in open_states:
        problems.append("rfq_status %r is not one of %s" % (rfq_status or None, ", ".join(open_states)))
    stated = [value.lower() for value in validator._RFQ_STATUS_RE.findall(text)]
    if rfq_status and rfq_status not in stated:
        problems.append("the draft never states the RFQ status %r" % (rfq_status,))
    not_open = sorted(set(value for value in stated if value not in open_states))
    if not_open:
        problems.append("the draft states the RFQ status %s, which is not open" % "/".join(not_open))
    rfq_date = _parse_date(rfq_as_of)
    run_date = _parse_date(as_of)
    if rfq_date is None:
        problems.append("rfq_as_of %r is not a YYYY-MM-DD date" % (rfq_as_of or None,))
    else:
        if rfq_as_of not in validator._RFQ_DATE_RE.findall(text):
            problems.append("the draft never states the RFQ date %r" % (rfq_as_of,))
        if run_date is not None and rfq_date > run_date:
            problems.append("rfq_as_of %s is after the run's as_of %s" % (rfq_as_of, as_of))
        elif run_date is not None and max_age_days is not None and (run_date - rfq_date).days > max_age_days:
            problems.append(
                "rfq_as_of %s is %d days before the run's as_of %s; an RFQ status older than %d "
                "days is stale demand (scoring.config.json output.max_rfq_age_days)"
                % (rfq_as_of, (run_date - rfq_date).days, as_of, max_age_days)
            )

    rfq_documents, match_documents = [], []
    if adapter is not None and rfq_id:
        document = adapter._lookup_rfq(rfq_key or rfq_id, rfq_id)
        if document is None:
            problems.append("RFQ %s could not be read through the %s backend" % (rfq_id, adapter.name))
        else:
            rfq_documents.append(document)
            actual = document.get("status")
            if actual not in open_states:
                problems.append("RFQ %s is %r in TradeWith, not an open status" % (rfq_id, actual))
            elif rfq_status and actual != rfq_status:
                problems.append("the draft says status %r but RFQ %s is %r" % (rfq_status, rfq_id, actual))
            stored_as_of = document.get("as_of")
            if _parse_date(stored_as_of) is not None and stored_as_of != rfq_as_of:
                problems.append(
                    "the draft says as of %r but RFQ %s is as of %s" % (rfq_as_of or None, rfq_id, stored_as_of)
                )
        entity_id = out.get("entity_id")
        for match in adapter._lookup_matches():
            if validator._rfq_key(match.get("rfq_id")) != rfq_key:
                continue
            run = match.get("match_run_id")
            for row in match.get("excluded") or []:
                if isinstance(row, dict) and row.get("seller_id") == entity_id:
                    problems.append("%s is excluded from match run %s" % (entity_id, run))
            for row in match.get("results") or []:
                if isinstance(row, dict) and row.get("seller_id") == entity_id:
                    match_documents.append(match)
                    if (row.get("hard_filter") or {}).get("passed") is not True:
                        problems.append("%s did not pass the hard filter of match run %s" % (entity_id, run))

    if problems:
        raise DataError(
            "%s %s but %s; a demand claim or RFQ citation requires an rfq_id that resolves to an "
            "open (%s), current RFQ with that status and its as_of date stated in the draft "
            "(INV-34, R10.4.3, PRD 11.1)"
            % (
                label,
                "makes a live-demand claim (matched %s)" % ", ".join("/%s/" % p for p in matched[:3])
                if matched
                else "cites an RFQ",
                "; ".join(problems),
                "/".join(open_states),
            )
        )
    return rfq_documents, match_documents


def _normalize_draft(draft, index, adapter=None, as_of=None):
    """Enforce the review-queue shape of one outreach draft.

    Rule (c) of BUILD-CONTRACT.md 13.3: drafts are written ONLY with
    status READY_FOR_REVIEW, auto_send false and manual_approval_required true.
    Absent flags are filled in with those values; a flag present with any other
    value is a refusal, never a silent correction.

    READY_FOR_REVIEW is also a claim that the draft is reviewable, so the draft then
    goes through scripts/validate_output.py validate_outreach_draft -- against the
    stored lead, match run and RFQ when `adapter` can read them -- and any
    error-severity issue is a refusal (ValidationError.errors lists them all).
    Warnings go to stderr and do not block.
    """
    label = "draft[%d]" % index
    if not isinstance(draft, dict):
        raise DataError("%s must be a JSON object, got %s" % (label, type(draft).__name__))
    out = dict(draft)

    present_forbidden = _forbidden_key_paths(out)
    if present_forbidden:
        raise DataError(
            "%s carries dispatch or credential fields (%s); this adapter writes "
            "review-queue records only and the package has no message-transport "
            "capability (PRD 11.1, 11.2)" % (label, ", ".join(present_forbidden))
        )

    missing = [field for field in DRAFT_REQUIRED_FIELDS if not str(out.get(field, "")).strip()]
    if missing:
        raise DataError("%s is missing required field(s): %s" % (label, ", ".join(missing)))

    out["entity_id"] = _safe_id(out["entity_id"], "%s.entity_id" % label)
    if out.get("rfq_id") is not None:
        out["rfq_id"] = _safe_id(out["rfq_id"], "%s.rfq_id" % label)

    if out["side"] not in DRAFT_SIDES:
        raise DataError(
            "%s.side must be one of %s, got %r"
            % (label, ", ".join(sorted(DRAFT_SIDES)), out["side"])
        )
    if out["channel_type"] not in CHANNEL_TYPES:
        raise DataError(
            "%s.channel_type %r is not one of the company-level channel types of "
            "BUILD-CONTRACT.md 3.6 (%s)"
            % (label, out["channel_type"], ", ".join(sorted(CHANNEL_TYPES)))
        )

    for field, required_value in DRAFT_FIXED_FIELDS:
        if field not in out or out[field] is None:
            out[field] = required_value
        elif out[field] != required_value:
            raise DataError(
                "%s.%s must be %s (PRD 11.2 human-in-the-loop; INV-09), got %r"
                % (label, field, json.dumps(required_value), out[field])
            )

    facts = out.get("personalization_facts")
    if facts is None:
        facts = []
        out["personalization_facts"] = facts
    if not isinstance(facts, list):
        raise DataError("%s.personalization_facts must be an array" % label)
    for position, fact in enumerate(facts):
        if not isinstance(fact, dict):
            raise DataError("%s.personalization_facts[%d] must be an object" % (label, position))
        if not str(fact.get("fact", "")).strip():
            raise DataError(
                "%s.personalization_facts[%d].fact is required" % (label, position)
            )
        source_url = str(fact.get("source_url", "")).strip()
        if not source_url.startswith(("http://", "https://")):
            raise DataError(
                "%s.personalization_facts[%d].source_url must be an absolute http(s) "
                "URL a reviewer can click (PRD OUT-02)" % (label, position)
            )

    markdown = out.get("draft_markdown")
    if isinstance(markdown, str) and markdown.strip():
        lines = [line.rstrip() for line in markdown.strip().splitlines()]
        if len(lines) < 3 or lines[1] != "Status: READY_FOR_REVIEW" or lines[2] != "Auto-send: false":
            raise DataError(
                "%s.draft_markdown must follow the outreach envelope of "
                "BUILD-CONTRACT.md 10.4: line 2 exactly 'Status: READY_FOR_REVIEW' "
                "and line 3 exactly 'Auto-send: false' (R10.4.1, INV-09)" % label
            )

    validator = _import_validator()
    if validator is None:
        raise DataError(
            "%s: scripts/validate_output.py could not be imported; refusing to queue a draft "
            "that was not validated (BUILD-CONTRACT.md 13.3 rule a)" % label
        )
    # RFQ freshness is measured against an as_of somebody chose (--as-of / as_of=), never the
    # config's frozen as_of_default.
    if adapter is not None:
        run_as_of = as_of or (adapter.as_of if adapter.as_of_explicit else None)
        max_age_days, quiet = adapter.max_rfq_age_days, adapter.quiet
    else:
        run_as_of, max_age_days, quiet = as_of, _max_rfq_age_days(load_config()), False
    # The stored target, when there is one: a match candidate first (its hard filter
    # and qualified flag gate DRAFT-09), then the lead record. The RFQ rides along so
    # the validator can check the citation. With no target the draft is checked alone.
    lead = adapter._lookup_lead(out["entity_id"]) if adapter is not None else None
    rfq_documents, records = _check_live_demand_claim(
        out, label, validator, adapter=adapter, as_of=run_as_of, max_age_days=max_age_days,
        evidence=[item for item in lead.get("evidence") or [] if isinstance(item, dict)]
        if lead is not None else None,
    )

    if not out.get("draft_id"):
        out["draft_id"] = "OD-%s-%s" % (out["entity_id"], out["channel_type"])
    out["draft_id"] = _safe_id(out["draft_id"], "%s.draft_id" % label)

    if lead is not None:
        records.append(lead)
    issues = validator.validate_outreach_draft(out, record=records + rfq_documents if records else None)
    errors = [issue for issue in issues if issue.get("severity") != "warning"]
    if errors:
        exc = ValidationError(
            "%s (%s) failed outreach-draft validation and was not queued for review (%d problem%s)"
            % (label, out["draft_id"], len(errors), "" if len(errors) == 1 else "s"),
            ["%s: %s" % (issue.get("invariant"), issue.get("message")) for issue in errors],
        )
        exc.issues = issues
        raise exc
    for issue in issues:
        _eprint("warning: %s %s: %s" % (label, issue.get("invariant"), issue.get("message")), quiet)
    return out


def _check_status_target(status):
    """Refuse any status this package is not allowed to write (13.3 rule b)."""
    if not isinstance(status, str) or not status.strip():
        raise UsageError("status is required")
    value = status.strip()
    if value not in ENTITY_STATES:
        raise UsageError(
            "status %r is not an entity state; expected one of %s"
            % (value, ", ".join(ENTITY_STATES))
        )
    if value in APPLICATION_ONLY_STATES:
        raise DataError(
            "refusing to write status %s: approval and every later state belong to "
            "the TradeWith/CRM application layer and require a human decision "
            "(BUILD-CONTRACT.md 9.2, INV-09)" % value
        )
    if value not in SKILL_WRITABLE_STATES:
        raise DataError("refusing to write status %s: outside the skill's range" % value)
    return value


def _check_scored_target(document, label, target):
    """INV-37 / SKILL.md Mode 4: QUALIFIED and later need a scored record with qualified true."""
    if target not in SCORED_ONLY_STATES:
        return
    problems = []
    if document.get("score_version") in (None, "unscored"):
        problems.append("it is unscored (score_version %r)" % (document.get("score_version"),))
    if document.get("qualified") is not True:
        problems.append("qualified is %r" % (document.get("qualified"),))
    if problems:
        raise DataError(
            "refusing status %s for %s: %s; %s are entered only by a scored record with "
            "qualified true (INV-37, SKILL.md Mode 4)"
            % (target, label, " and ".join(problems), "/".join(sorted(SCORED_ONLY_STATES)))
        )


def _check_transition(current, target):
    """Enforce the skill-performed edges of BUILD-CONTRACT.md 9.1."""
    if current is None or current == target:
        return
    if current in APPLICATION_ONLY_STATES:
        raise DataError(
            "refusing to move a record out of %s: that record is owned by the "
            "application layer (BUILD-CONTRACT.md 9.2)" % current
        )
    allowed = SKILL_TRANSITIONS.get(current)
    if allowed is None:
        raise DataError("unknown current status %r" % current)
    if target not in allowed:
        raise DataError(
            "transition %s -> %s is not a skill-performed edge of the PRD 8 state "
            "machine; allowed from %s: %s"
            % (current, target, current, ", ".join(sorted(allowed)) or "(none)")
        )


# --------------------------------------------------------------------------
# The adapter interface.
# --------------------------------------------------------------------------


class Adapter(abc.ABC):
    """One swappable boundary over the six PRD 13.1 capabilities.

    Implementations must keep the capability semantics identical, so a caller
    can move between FileAdapter and HttpAdapter without changing a line.
    """

    name = "adapter"

    def __init__(self, schema_dir=None, as_of=None, config_path=None, quiet=False, strict=False):
        self.schema_dir = os.path.abspath(schema_dir) if schema_dir else SCHEMA_DIR
        self.config_path = config_path
        self.quiet = bool(quiet)
        self.strict = bool(strict)
        config = load_config(config_path)
        # as_of_default keeps reads and observed_at deterministic; an RFQ-citing draft still
        # needs an explicit as_of (_normalize_draft), so as_of_explicit remembers the difference.
        self.as_of_explicit = bool(as_of)
        self.as_of = as_of or config.get("as_of_default")
        if not _DATE_PATTERN.match(str(self.as_of or "")):
            raise UsageError("as_of must be YYYY-MM-DD, got %r" % (self.as_of,))
        self.max_drafts_per_run = int(
            (config.get("output") or {}).get("max_outreach_drafts_per_run", 20)
        )
        self.max_rfq_age_days = _max_rfq_age_days(config)

    # -- the six capabilities -------------------------------------------

    @abc.abstractmethod
    def get_rfq(self, rfq_id):
        """GET /rfqs/:id -> one rfq document (rfq.schema.json)."""

    @abc.abstractmethod
    def list_sellers(self, filters=None):
        """GET /sellers?filters= -> a list of seller documents."""

    @abc.abstractmethod
    def post_research_leads(self, records, overwrite=False):
        """POST /research/leads -> [{"id": ..., "status": ..., "result": ...}] per record."""

    @abc.abstractmethod
    def post_matches(self, match_result):
        """POST /matches -> {"id": match_run_id} for one match-result document."""

    @abc.abstractmethod
    def post_outreach_drafts(self, drafts):
        """POST /outreach-drafts -> [{"id": ..., "status": ...}] per draft."""

    @abc.abstractmethod
    def patch_lead_status(self, lead_id, status):
        """PATCH /leads/:id/status -> {"id": ..., "status": ...}."""

    # -- shared helpers --------------------------------------------------

    def _validate_write(self, kind, document, label):
        _require_valid(
            kind, document, schema_dir=self.schema_dir, quiet=self.quiet, label=label
        )

    def _validate_read(self, kind, document, label):
        _warn_invalid(
            kind,
            document,
            schema_dir=self.schema_dir,
            quiet=self.quiet,
            strict=self.strict,
            label=label,
        )

    def _prepare_leads(self, records):
        """Validate every lead before any of them is persisted (all or nothing)."""
        items = _as_list(records, "records")
        if not items:
            raise UsageError("no lead records were supplied")
        prepared = []
        for index, record in enumerate(items):
            kind = _document_kind(record)
            if kind not in ("buyer", "seller"):
                raise DataError(
                    "records[%d] is a %s document; POST /research/leads accepts buyer "
                    "and seller records only" % (index, kind)
                )
            identifier = _record_id(record, kind)
            status = record.get("status")
            if isinstance(status, str) and status in APPLICATION_ONLY_STATES:
                raise DataError(
                    "records[%d] (%s) carries status %s; no artifact produced by this "
                    "package may set approval or any later state (INV-09, INV-37)"
                    % (index, identifier, status)
                )
            _check_scored_target(record, "records[%d] (%s)" % (index, identifier), status)
            self._validate_write(kind, record, "records[%d] (%s)" % (index, identifier))
            prepared.append((kind, identifier, record))
        return prepared

    def _prepare_drafts(self, drafts):
        """Normalize, cap and validate outreach drafts before any is persisted."""
        items = _as_list(drafts, "drafts")
        if not items:
            raise UsageError("no outreach drafts were supplied")
        if len(items) > self.max_drafts_per_run:
            raise DataError(
                "refusing %d outreach drafts in one call: the bulk guardrail caps a "
                "run at %d (scoring.config.json output.max_outreach_drafts_per_run, "
                "PRD 11.1)" % (len(items), self.max_drafts_per_run)
            )
        return [_normalize_draft(draft, index, adapter=self) for index, draft in enumerate(items)]

    # -- lookups the draft guards use; a backend that cannot read returns nothing --

    def _lookup_rfq(self, *candidates):
        """The RFQ a draft cites, read through get_rfq, or None when no spelling resolves."""
        for candidate in dict.fromkeys(value for value in candidates if value):
            try:
                return self.get_rfq(candidate)
            except AdapterError:
                continue
        return None

    def _lookup_lead(self, entity_id):
        """The stored lead a draft targets, or None when this backend cannot read leads."""
        return None

    def _lookup_matches(self):
        """Stored match-result documents, or [] when this backend cannot read them."""
        return []

    def _prepare_match(self, match_result):
        """Validate one or many match-result documents before persisting."""
        many = isinstance(match_result, list)
        items = match_result if many else [match_result]
        if not items:
            raise UsageError("no match-result document was supplied")
        prepared = []
        for index, document in enumerate(items):
            if not isinstance(document, dict):
                raise DataError("match_result[%d] must be a JSON object" % index)
            identifier = _record_id(document, "match-result")
            self._validate_write(
                "match-result", document, "match_result[%d] (%s)" % (index, identifier)
            )
            prepared.append((identifier, document))
        return prepared, many


# --------------------------------------------------------------------------
# File backend: works today, with no TradeWith API and no credentials.
# --------------------------------------------------------------------------


class FileAdapter(Adapter):
    """Read and write the six capabilities as JSON files under a data dir.

    Layout (created on first write; see adapters/tradewith_adapter.md):

        <data-dir>/
          rfqs/<rfq_id>.json                  read by get_rfq
          sellers/<seller_id>.json            read by list_sellers
          leads/<entity_id>.json              written by post_research_leads
          matches/<match_run_id>.json         written by post_matches
          outreach-drafts/<draft_id>.json     written by post_outreach_drafts

    Writes are last-write-wins on the record id, which keeps a re-run of the
    same inputs byte-stable (INV-13) -- except that save-leads refuses to
    downgrade a stored lead (scored -> unscored, or a status regression) unless
    overwrite is set (_lead_write_outcome).
    """

    name = "file"

    RFQ_DIR = "rfqs"
    SELLER_DIR = "sellers"
    LEAD_DIR = "leads"
    MATCH_DIR = "matches"
    DRAFT_DIR = "outreach-drafts"

    def __init__(self, data_dir=None, app_url=None, **kwargs):
        Adapter.__init__(self, **kwargs)
        resolved = data_dir or os.environ.get(ENV_DATA_DIR) or DEFAULT_DATA_DIR
        self.data_dir = os.path.abspath(os.path.expanduser(resolved))
        self.app_url = app_url or _resolve_env((ENV_APP_URL,)) or DEFAULT_APP_URL

    # -- path helpers ----------------------------------------------------

    def _path(self, section, identifier):
        return os.path.join(self.data_dir, section, "%s.json" % identifier)

    def _read_json(self, path, label):
        if not os.path.isfile(path):
            raise DataError("%s not found: %s" % (label, path))
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except ValueError as exc:
            raise DataError("%s is not valid JSON (%s): %s" % (label, path, exc))
        except OSError as exc:
            raise DataError("%s could not be read (%s): %s" % (label, path, exc))

    def _write_json(self, section, identifier, document):
        directory = os.path.join(self.data_dir, section)
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, "%s.json" % identifier)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2, separators=(",", ": "))
                handle.write("\n")
        except OSError as exc:
            raise DataError("could not write %s/%s.json: %s" % (section, identifier, exc))
        return path

    # -- capabilities ----------------------------------------------------

    def get_rfq(self, rfq_id):
        identifier = _safe_id(rfq_id, "rfq_id")
        document = self._read_json(self._path(self.RFQ_DIR, identifier), "RFQ %s" % identifier)
        if not isinstance(document, dict):
            raise DataError("RFQ %s must be a JSON object" % identifier)
        self._validate_read("rfq", document, "RFQ %s" % identifier)
        return document

    def list_sellers(self, filters=None):
        filters = dict(filters or {})
        directory = os.path.join(self.data_dir, self.SELLER_DIR)
        if not os.path.isdir(directory):
            _eprint(
                "note: no seller directory at %s; returning an empty list" % directory,
                self.quiet,
            )
            return []
        out = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            document = self._read_json(os.path.join(directory, name), "seller file %s" % name)
            if not isinstance(document, dict):
                raise DataError("seller file %s must contain a JSON object" % name)
            self._validate_read("seller", document, "seller %s" % name)
            if _seller_matches(document, filters):
                out.append(document)
        out.sort(key=lambda record: str(record.get("seller_id", "")))
        limit = filters.get("limit")
        if isinstance(limit, int) and limit >= 0:
            out = out[:limit]
        return out

    def post_research_leads(self, records, overwrite=False):
        prepared = self._prepare_leads(records)
        results = []
        for kind, identifier, record in prepared:
            path = self._path(self.LEAD_DIR, identifier)
            existing = self._read_json(path, "lead %s" % identifier) if os.path.isfile(path) else None
            outcome, reason = _lead_write_outcome(existing, record, overwrite)
            row = {"id": identifier, "status": record.get("status", "DISCOVERED"), "result": outcome}
            if outcome == "refused":
                row["status"] = existing.get("status")
                row["reason"] = reason
                _eprint("refused lead %s: %s" % (identifier, reason), self.quiet)
            elif outcome == "unchanged":
                _eprint("lead %s unchanged" % identifier, self.quiet)
            else:
                self._write_json(self.LEAD_DIR, identifier, record)
                _eprint("wrote lead %s (%s, %s)" % (identifier, kind, outcome), self.quiet)
            results.append(row)
        return results

    def _lookup_lead(self, entity_id):
        path = self._path(self.LEAD_DIR, _safe_id(entity_id, "entity_id"))
        if not os.path.isfile(path):
            return None
        document = self._read_json(path, "lead %s" % entity_id)
        return document if isinstance(document, dict) else None

    def _lookup_matches(self):
        directory = os.path.join(self.data_dir, self.MATCH_DIR)
        if not os.path.isdir(directory):
            return []
        out = []
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json"):
                document = self._read_json(os.path.join(directory, name), "match file %s" % name)
                if isinstance(document, dict):
                    out.append(document)
        return out

    def post_matches(self, match_result):
        prepared, many = self._prepare_match(match_result)
        results = []
        for identifier, document in prepared:
            self._write_json(self.MATCH_DIR, identifier, document)
            results.append({"id": identifier})
            _eprint("wrote match run %s" % identifier, self.quiet)
        return results if many else results[0]

    def post_outreach_drafts(self, drafts):
        prepared = self._prepare_drafts(drafts)
        results = []
        for draft in prepared:
            self._write_json(self.DRAFT_DIR, draft["draft_id"], draft)
            results.append({"id": draft["draft_id"], "status": draft["status"]})
            _eprint(
                "queued outreach draft %s for human review" % draft["draft_id"], self.quiet
            )
        return results

    def patch_lead_status(self, lead_id, status):
        identifier = _safe_id(lead_id, "lead_id")
        target = _check_status_target(status)
        path = self._path(self.LEAD_DIR, identifier)
        document = self._read_json(path, "lead %s" % identifier)
        if not isinstance(document, dict):
            raise DataError("lead %s must be a JSON object" % identifier)
        kind = _document_kind(document)
        if kind not in ("buyer", "seller"):
            raise DataError("lead %s is a %s document, not a buyer or seller" % (identifier, kind))
        current = document.get("status")
        _check_transition(current if isinstance(current, str) else None, target)
        _check_scored_target(document, "lead %s" % identifier, target)
        document["status"] = target
        self._validate_write(kind, document, "lead %s" % identifier)
        self._write_json(self.LEAD_DIR, identifier, document)
        _eprint("lead %s: %s -> %s" % (identifier, current, target), self.quiet)
        return {"id": identifier, "status": target}


def _lead_write_outcome(existing, record, overwrite):
    """created / unchanged / updated / refused for one save-leads record over a stored lead.

    A re-run of raw discovery must not silently replace a scored lead (qualification
    96 -> "unscored" 0) or walk its status backwards. Both are refused unless the
    caller passes overwrite. A lead in an application-layer state is never replaced.
    """
    if existing is None:
        return "created", None
    if existing == record:
        return "unchanged", None
    if not isinstance(existing, dict):
        return "updated", None
    old_status = existing.get("status")
    if old_status in APPLICATION_ONLY_STATES:
        return "refused", (
            "the stored lead is %s, which the application layer owns (BUILD-CONTRACT.md 9.2); "
            "--overwrite does not apply" % old_status
        )
    problems = []
    if existing.get("score_version") not in (None, "unscored") and record.get("score_version") in (None, "unscored"):
        problems.append(
            "it would replace a scored lead (%s, qualification_score %r) with an unscored one"
            % (existing.get("score_version"), existing.get("qualification_score"))
        )
    rank = dict((state, position) for position, state in enumerate(ENTITY_STATES))
    new_status = record.get("status") or "DISCOVERED"
    if old_status in rank and new_status in rank and rank[new_status] < rank[old_status]:
        problems.append("it would move status back from %s to %s" % (old_status, new_status))
    if problems and not overwrite:
        return "refused", "%s; pass --overwrite to replace it" % "; ".join(problems)
    return "updated", None


def _seller_matches(seller, filters):
    """Retrieval filter for the file backend.

    These are RETRIEVAL hints, not hard filters. An unknown or absent field
    never excludes a seller: hard filtering with its unknown handling belongs
    to scripts/score_match.py (BUILD-CONTRACT.md 6.2, INV-07). Filtering here
    on unknowns would silently delete exactly the candidates the scoring stage
    is required to surface and penalise.
    """
    ids = filters.get("seller_ids")
    if isinstance(ids, (list, tuple)) and ids:
        if str(seller.get("seller_id", "")) not in {str(value) for value in ids}:
            return False

    country = filters.get("country") or filters.get("countries")
    if country:
        wanted = {country} if isinstance(country, str) else set(country)
        value = seller.get("country")
        if not _is_unknown(value) and value not in wanted:
            return False

    company_type = filters.get("company_type") or filters.get("company_types")
    if company_type:
        wanted = (
            {_norm_token(company_type)}
            if isinstance(company_type, str)
            else {_norm_token(item) for item in company_type}
        )
        value = seller.get("company_type")
        if not _is_unknown(value) and _norm_token(value) not in wanted:
            return False

    for key, field in (
        ("product_categories", "product_categories"),
        ("product_forms", "product_forms"),
    ):
        wanted_raw = filters.get(key)
        if not wanted_raw:
            continue
        wanted = (
            {_norm_token(wanted_raw)}
            if isinstance(wanted_raw, str)
            else {_norm_token(item) for item in wanted_raw}
        )
        have = seller.get(field)
        if _is_unknown(have) or have is None:
            continue  # unknown never excludes
        if not isinstance(have, list) or not have:
            return False  # verified-empty is a real negative
        if not ({_norm_token(item) for item in have} & wanted):
            return False

    for key in ("oem_odm", "private_label", "brand_export", "english_site"):
        if key not in filters or filters[key] is None:
            continue
        value = seller.get(key)
        if _is_unknown(value):
            continue
        if bool(value) is not bool(filters[key]):
            return False

    wanted_certs = filters.get("certifications")
    if wanted_certs:
        wanted = (
            {_norm_cert(wanted_certs)}
            if isinstance(wanted_certs, str)
            else {_norm_cert(item) for item in wanted_certs}
        )
        have = seller.get("certifications")
        if not _is_unknown(have) and have is not None:
            if not isinstance(have, list):
                return False
            if not wanted.issubset({_norm_cert(item) for item in have}):
                return False

    max_moq = filters.get("max_moq")
    if isinstance(max_moq, (int, float)) and not isinstance(max_moq, bool):
        moq = _coerce_range(seller.get("moq"))
        unit = filters.get("moq_unit") or "units"
        seller_unit = seller.get("moq_unit") or (moq or {}).get("unit") or "units"
        if moq is not None and _norm_token(seller_unit) == _norm_token(unit):
            if moq["min"] > max_moq:  # compare on min (BUILD-CONTRACT.md 3.3)
                return False

    max_lead = filters.get("max_lead_time_days")
    if isinstance(max_lead, (int, float)) and not isinstance(max_lead, bool):
        lead = _coerce_range(seller.get("lead_time_days"))
        if lead is not None and lead["max"] > max_lead:  # compare on max
            return False

    statuses = filters.get("operational_status")
    if statuses:
        wanted = {statuses} if isinstance(statuses, str) else set(statuses)
        value = seller.get("operational_status")
        if not _is_unknown(value) and value not in wanted:
            return False

    return True


# --------------------------------------------------------------------------
# HTTP backend: for when TradeWith's internal API lands.
# --------------------------------------------------------------------------


class HttpAdapter(Adapter):
    """urllib client for the PRD 13.1 endpoints.

    Configuration is read from the environment at call time only:
    TRADEWITH_BASE_URL (or TRADEWITH_API_BASE) and TRADEWITH_TOKEN (or
    TRADEWITH_API_TOKEN). Nothing is cached to disk and the token never
    reaches stdout, stderr, an exception or a stored document.
    """

    name = "http"

    ENDPOINTS = {
        "get_rfq": ("GET", "/rfqs/{rfq_id}"),
        "list_sellers": ("GET", "/sellers"),
        "post_research_leads": ("POST", "/research/leads"),
        "post_matches": ("POST", "/matches"),
        "post_outreach_drafts": ("POST", "/outreach-drafts"),
        "patch_lead_status": ("PATCH", "/leads/{lead_id}/status"),
    }

    def __init__(self, base_url=None, token=None, timeout=None, max_retries=None, **kwargs):
        Adapter.__init__(self, **kwargs)
        self.base_url = self._resolve_base_url(base_url)
        self._token = self._resolve_token(token)
        self.timeout = float(
            timeout
            if timeout is not None
            else _resolve_env((ENV_TIMEOUT,)) or DEFAULT_TIMEOUT_SECONDS
        )
        if self.timeout <= 0:
            raise UsageError("timeout must be greater than 0 seconds")
        retries = (
            max_retries
            if max_retries is not None
            else _resolve_env((ENV_RETRIES,)) or DEFAULT_MAX_RETRIES
        )
        self.max_retries = int(retries)
        if self.max_retries < 0 or self.max_retries > 5:
            raise UsageError("max_retries must be between 0 and 5")

    @staticmethod
    def _resolve_base_url(explicit=None):
        value = _resolve_env(ENV_BASE_URL, explicit)
        if not value:
            raise UsageError(
                "the http backend needs a base URL: set %s (or %s) in the "
                "environment, or pass --base-url. Nothing is stored in this "
                "package; TradeWith's API host is deployment configuration."
                % (ENV_BASE_URL[0], ENV_BASE_URL[1])
            )
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise UsageError("base URL %r must be an absolute http(s) URL" % value)
        host = parsed.hostname or ""
        if parsed.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
            raise UsageError(
                "refusing to send a bearer token over plain http to %s; use https "
                "(http is allowed for localhost development only)" % host
            )
        return value.rstrip("/")

    @staticmethod
    def _resolve_token(explicit=None):
        value = _resolve_env(ENV_TOKEN, explicit)
        if not value:
            raise UsageError(
                "the http backend needs a bearer token: set %s (or %s) in the "
                "environment. Never hard-code it and never pass it on the command "
                "line, where it would land in the shell history."
                % (ENV_TOKEN[0], ENV_TOKEN[1])
            )
        return value

    # -- transport -------------------------------------------------------

    def _request(self, method, path, query=None, payload=None):
        """One bounded-retry request. Returns the parsed JSON body, or None."""
        url = self.base_url + path
        if query:
            url = "%s?%s" % (url, urllib.parse.urlencode(query))
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % self._token,
            "User-Agent": USER_AGENT,
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        attempt = 0
        last_error = "no attempt was made"
        while attempt <= self.max_retries:
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except ValueError as exc:
                        raise TransportError(
                            "%s %s returned a body that is not JSON: %s"
                            % (method, path, _redact(exc, self._token))
                        )
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = _redact(exc.read().decode("utf-8", "replace")[:400], self._token)
                except Exception:
                    detail = ""
                if exc.code in RETRYABLE_STATUS and attempt < self.max_retries:
                    last_error = "HTTP %s" % exc.code
                    self._sleep_backoff(attempt, last_error, method, path)
                    attempt += 1
                    continue
                raise TransportError(
                    "%s %s failed with HTTP %s%s"
                    % (method, path, exc.code, (": %s" % detail) if detail else "")
                )
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    last_error = _redact(getattr(exc, "reason", exc), self._token)
                    self._sleep_backoff(attempt, last_error, method, path)
                    attempt += 1
                    continue
                raise TransportError(
                    "%s %s could not reach %s: %s"
                    % (method, path, self.base_url, _redact(getattr(exc, "reason", exc), self._token))
                )
            except OSError as exc:
                if attempt < self.max_retries:
                    last_error = _redact(exc, self._token)
                    self._sleep_backoff(attempt, last_error, method, path)
                    attempt += 1
                    continue
                raise TransportError(
                    "%s %s failed: %s" % (method, path, _redact(exc, self._token))
                )
        raise TransportError(
            "%s %s failed after %d attempt(s): %s"
            % (method, path, self.max_retries + 1, _redact(last_error, self._token))
        )

    def _sleep_backoff(self, attempt, reason, method, path):
        delay = min(RETRY_BACKOFF_SECONDS * (2 ** attempt), RETRY_BACKOFF_CAP_SECONDS)
        _eprint(
            "warning: %s %s -> %s; retrying in %.1fs"
            % (method, path, _redact(reason, self._token), delay),
            self.quiet,
        )
        time.sleep(delay)

    # -- response coercion ----------------------------------------------

    @staticmethod
    def _unwrap(body, keys):
        """Accept either a bare payload or a single-key envelope around it."""
        if isinstance(body, dict):
            for key in keys:
                if key in body and isinstance(body[key], (dict, list)):
                    return body[key]
        return body

    @staticmethod
    def _id_status_list(body, count, label):
        """Normalize a write response onto [{"id": ..., "status": ...}]."""
        rows = HttpAdapter._unwrap(body, ("results", "records", "data", "items"))
        if rows is None:
            raise TransportError("%s returned an empty body; expected id/status rows" % label)
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise TransportError("%s returned %s, expected a list" % (label, type(rows).__name__))
        out = []
        for row in rows:
            if not isinstance(row, dict) or "id" not in row:
                raise TransportError("%s returned a row without an id: %r" % (label, row))
            out.append({"id": str(row["id"]), "status": row.get("status")})
        if count is not None and len(out) != count:
            _eprint(
                "warning: %s returned %d row(s) for %d submitted record(s)"
                % (label, len(out), count),
                False,
            )
        return out

    # -- capabilities ----------------------------------------------------

    def get_rfq(self, rfq_id):
        identifier = _safe_id(rfq_id, "rfq_id")
        method, template = self.ENDPOINTS["get_rfq"]
        path = template.format(rfq_id=urllib.parse.quote(identifier, safe=""))
        body = self._unwrap(self._request(method, path), ("rfq", "data", "record"))
        if not isinstance(body, dict):
            raise TransportError("GET /rfqs/%s did not return an RFQ object" % identifier)
        self._validate_read("rfq", body, "RFQ %s" % identifier)
        return body

    def list_sellers(self, filters=None):
        filters = dict(filters or {})
        method, path = self.ENDPOINTS["list_sellers"]
        # PRD 13.1 spells this endpoint "GET /sellers?filters=...", so the whole
        # filter object travels as one URL-encoded JSON parameter.
        query = {"filters": json.dumps(filters, ensure_ascii=False, sort_keys=True)}
        body = self._unwrap(self._request(method, path, query=query), ("sellers", "records", "data", "items"))
        if body is None:
            return []
        if isinstance(body, dict):
            body = [body]
        if not isinstance(body, list):
            raise TransportError("GET /sellers returned %s, expected a list" % type(body).__name__)
        for index, seller in enumerate(body):
            if not isinstance(seller, dict):
                raise TransportError("GET /sellers returned a non-object at index %d" % index)
            self._validate_read("seller", seller, "seller[%d]" % index)
        return body

    def post_research_leads(self, records, overwrite=False):
        # This backend cannot read a stored lead, so the downgrade rule of
        # _lead_write_outcome is the server's to enforce; `overwrite` travels with
        # the request (adapters/tradewith_adapter.md 5.3).
        prepared = self._prepare_leads(records)
        method, path = self.ENDPOINTS["post_research_leads"]
        payload = {
            "as_of": self.as_of,
            "schema_version": SCHEMA_VERSION,
            "overwrite": bool(overwrite),
            "records": [record for _kind, _id, record in prepared],
        }
        body = self._request(method, path, payload=payload)
        return self._id_status_list(body, len(prepared), "POST /research/leads")

    def post_matches(self, match_result):
        prepared, many = self._prepare_match(match_result)
        method, path = self.ENDPOINTS["post_matches"]
        results = []
        for identifier, document in prepared:
            body = self._request(method, path, payload={"match_result": document})
            row = self._unwrap(body, ("match", "data", "record"))
            if isinstance(row, dict) and "id" in row:
                results.append({"id": str(row["id"])})
            else:
                results.append({"id": identifier})
                _eprint(
                    "warning: POST /matches returned no id; echoing %s" % identifier,
                    self.quiet,
                )
        return results if many else results[0]

    def post_outreach_drafts(self, drafts):
        prepared = self._prepare_drafts(drafts)
        method, path = self.ENDPOINTS["post_outreach_drafts"]
        payload = {
            "as_of": self.as_of,
            "auto_send": False,
            "manual_approval_required": True,
            "drafts": prepared,
        }
        body = self._request(method, path, payload=payload)
        rows = self._id_status_list(body, len(prepared), "POST /outreach-drafts")
        for row in rows:
            if row.get("status") not in (None, "READY_FOR_REVIEW"):
                raise DataError(
                    "POST /outreach-drafts stored draft %s with status %r; this "
                    "package only ever queues drafts at READY_FOR_REVIEW (INV-09)"
                    % (row.get("id"), row.get("status"))
                )
            row["status"] = "READY_FOR_REVIEW"
        return rows

    def patch_lead_status(self, lead_id, status):
        identifier = _safe_id(lead_id, "lead_id")
        target = _check_status_target(status)
        method, template = self.ENDPOINTS["patch_lead_status"]
        path = template.format(lead_id=urllib.parse.quote(identifier, safe=""))
        body = self._request(method, path, payload={"status": target})
        row = self._unwrap(body, ("lead", "data", "record"))
        returned = row.get("status") if isinstance(row, dict) else None
        return {"id": identifier, "status": returned or target}


# --------------------------------------------------------------------------
# Backend selection and the module-level capability functions.
# --------------------------------------------------------------------------


def get_adapter(
    backend=None,
    data_dir=None,
    fixtures_dir=None,
    base_url=None,
    token=None,
    timeout=None,
    max_retries=None,
    schema_dir=None,
    as_of=None,
    config_path=None,
    quiet=False,
    strict=False,
    app_url=None,
):
    """Build the adapter for the requested backend.

    Resolution order: explicit `backend` -> TRADEWITH_BACKEND -> "file".
    The default is deliberately the offline file backend: no call ever reaches
    the network unless someone asked for the http backend by name.

    `fixtures_dir` is the BUILD-CONTRACT.md 13.3 rule (d) spelling of the same
    idea and is treated as a data dir, so tests and the harness can run every
    capability offline.
    """
    chosen = (backend or os.environ.get(ENV_BACKEND) or "file").strip().lower()
    shared = {
        "schema_dir": schema_dir,
        "as_of": as_of,
        "config_path": config_path,
        "quiet": quiet,
        "strict": strict,
    }
    if chosen == "file":
        return FileAdapter(data_dir=data_dir or fixtures_dir, app_url=app_url, **shared)
    if chosen == "http":
        return HttpAdapter(
            base_url=base_url,
            token=token,
            timeout=timeout,
            max_retries=max_retries,
            **shared
        )
    raise UsageError("unknown backend %r; expected 'file' or 'http'" % chosen)


def get_rfq(rfq_id, adapter=None, **kwargs):
    """PRD 13.1 `GET /rfqs/:id` -> one rfq document."""
    return (adapter or get_adapter(**kwargs)).get_rfq(rfq_id)


def list_sellers(filters=None, adapter=None, **kwargs):
    """PRD 13.1 `GET /sellers?filters=` -> a list of seller documents."""
    return (adapter or get_adapter(**kwargs)).list_sellers(filters)


def post_research_leads(records, adapter=None, overwrite=False, **kwargs):
    """PRD 13.1 `POST /research/leads` -> [{"id", "status", "result"}]."""
    return (adapter or get_adapter(**kwargs)).post_research_leads(records, overwrite=overwrite)


def post_matches(match_result, adapter=None, **kwargs):
    """PRD 13.1 `POST /matches` -> {"id"} (or a list, for a list input)."""
    return (adapter or get_adapter(**kwargs)).post_matches(match_result)


def post_outreach_drafts(drafts, adapter=None, **kwargs):
    """PRD 13.1 `POST /outreach-drafts` -> [{"id", "status"}], review queue only."""
    return (adapter or get_adapter(**kwargs)).post_outreach_drafts(drafts)


def patch_lead_status(lead_id, status, adapter=None, **kwargs):
    """PRD 13.1 `PATCH /leads/:id/status` -> {"id", "status"}."""
    return (adapter or get_adapter(**kwargs)).patch_lead_status(lead_id, status)


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------


def _read_json_argument(path, label):
    """Read a JSON payload from a path, or from stdin for '-' / omitted."""
    if path in (None, "-"):
        data = sys.stdin.read()
        source = "stdin"
    else:
        expanded = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(expanded):
            raise UsageError("%s not found: %s" % (label, expanded))
        try:
            with open(expanded, "r", encoding="utf-8") as handle:
                data = handle.read()
        except OSError as exc:
            raise UsageError("%s could not be read: %s" % (label, exc))
        source = expanded
    if not data.strip():
        raise UsageError("%s is empty (%s)" % (label, source))
    try:
        return json.loads(data)
    except ValueError as exc:
        raise UsageError("%s is not valid JSON (%s): %s" % (label, source, exc))


# Shared flags accepted BEFORE or AFTER the subcommand. Every one defaults to
# SUPPRESS so a value given on the left of the subcommand is not clobbered by
# the subparser's own unset default; _apply_defaults fills in the gaps.
_COMMON_DEFAULTS = {
    "backend": None,
    "data_dir": None,
    "fixtures_dir": None,
    "base_url": None,
    "timeout": None,
    "retries": None,
    "schema_dir": None,
    "config": None,
    "as_of": None,
    "strict": False,
    "pretty": False,
    "quiet": False,
    "with_evidence": False,
    "input": None,
}


def _common_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--backend",
        choices=("file", "http"),
        default=argparse.SUPPRESS,
        help="backend to use (default: TRADEWITH_BACKEND, else file)",
    )
    common.add_argument(
        "--data-dir",
        default=argparse.SUPPRESS,
        help="file backend root (default: TRADEWITH_DATA_DIR, else %s)" % DEFAULT_DATA_DIR,
    )
    common.add_argument(
        "--fixtures-dir",
        default=argparse.SUPPRESS,
        help="alias for --data-dir, for offline fixture runs",
    )
    common.add_argument(
        "--base-url",
        default=argparse.SUPPRESS,
        help="http backend base URL (default: %s)" % ENV_BASE_URL[0],
    )
    common.add_argument(
        "--timeout", type=float, default=argparse.SUPPRESS, help="http request timeout, seconds"
    )
    common.add_argument(
        "--retries", type=int, default=argparse.SUPPRESS, help="http retry budget (0-5, default 2)"
    )
    common.add_argument(
        "--schema-dir", default=argparse.SUPPRESS, help="alternative schema directory"
    )
    common.add_argument(
        "--config", default=argparse.SUPPRESS, help="alternative scoring.config.json"
    )
    common.add_argument(
        "--as-of",
        default=argparse.SUPPRESS,
        help="YYYY-MM-DD used for every date-bearing value",
    )
    common.add_argument(
        "--strict",
        action="store_true",
        default=argparse.SUPPRESS,
        help="treat a validation warning on a READ as an error",
    )
    common.add_argument(
        "--pretty",
        action="store_true",
        default=argparse.SUPPRESS,
        help="indent the JSON written to stdout",
    )
    common.add_argument(
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="suppress informational stderr output",
    )
    return common


def _apply_defaults(args):
    """Fill in every shared flag argparse suppressed."""
    for name, value in _COMMON_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)
    return args


def _build_parser():
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="tradewith_adapter.py",
        parents=[common],
        description=(
            "TradeWith adapter for the kbeauty-trade-matchmaker skill: the six "
            "PRD 13.1 capabilities over a local file backend (default) or the "
            "TradeWith HTTP API. Writes are review-queue writes; this package "
            "has no message-transport capability."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        default=False,
        help="print version information and exit",
    )

    subparsers = parser.add_subparsers(dest="command")

    get_rfq_parser = subparsers.add_parser(
        "get-rfq", parents=[common], help="GET /rfqs/:id — load a buying request"
    )
    get_rfq_parser.add_argument("--id", required=True, help="RFQ id, e.g. 134")
    get_rfq_parser.add_argument(
        "--with-evidence",
        action="store_true",
        help="append the BUILD-CONTRACT.md 4.7 internal_record evidence item for this read",
    )

    sellers_parser = subparsers.add_parser(
        "list-sellers", parents=[common], help="GET /sellers?filters= — internal seller candidates"
    )
    sellers_parser.add_argument("--filters", default=None, help="filter object as a JSON string")
    sellers_parser.add_argument("--filters-file", default=None, help="filter object as a JSON file")
    sellers_parser.add_argument("--country", default=None, help="ISO-3166-1 alpha-2, e.g. KR")
    sellers_parser.add_argument(
        "--product-category", action="append", default=None, help="repeatable category filter"
    )
    sellers_parser.add_argument("--company-type", default=None, help="seller company_type filter")
    sellers_parser.add_argument("--max-moq", type=float, default=None, help="MOQ ceiling")
    sellers_parser.add_argument("--limit", type=int, default=None, help="maximum rows to return")

    leads_parser = subparsers.add_parser(
        "save-leads",
        parents=[common],
        aliases=["post-research-leads"],
        help="POST /research/leads — store discovery results",
    )
    leads_parser.add_argument("-i", "--input", default=None, help="buyer/seller records (default: stdin)")
    leads_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace a stored lead even when that downgrades it (scored -> unscored, status regression)",
    )

    matches_parser = subparsers.add_parser(
        "save-matches",
        parents=[common],
        aliases=["post-matches"],
        help="POST /matches — store one match-result document",
    )
    matches_parser.add_argument("-i", "--input", default=None, help="match-result document (default: stdin)")

    drafts_parser = subparsers.add_parser(
        "save-outreach-drafts",
        parents=[common],
        aliases=["post-outreach-drafts"],
        help="POST /outreach-drafts — queue drafts for human review",
    )
    drafts_parser.add_argument("-i", "--input", default=None, help="draft records (default: stdin)")

    status_parser = subparsers.add_parser(
        "update-lead-status",
        parents=[common],
        aliases=["patch-lead-status"],
        help="PATCH /leads/:id/status — move a lead inside the skill's range",
    )
    status_parser.add_argument("--id", required=True, help="lead id, e.g. BUY-acme-com")
    status_parser.add_argument(
        "--status",
        required=True,
        help="target state: %s" % ", ".join(sorted(SKILL_WRITABLE_STATES)),
    )

    return parser


def _adapter_from_args(args):
    return get_adapter(
        backend=args.backend,
        data_dir=args.data_dir,
        fixtures_dir=args.fixtures_dir,
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=args.retries,
        schema_dir=args.schema_dir,
        as_of=args.as_of,
        config_path=args.config,
        quiet=args.quiet,
        strict=args.strict,
    )


def _seller_filters_from_args(args):
    filters = {}
    if args.filters_file:
        loaded = _read_json_argument(args.filters_file, "--filters-file")
        if not isinstance(loaded, dict):
            raise UsageError("--filters-file must contain a JSON object")
        filters.update(loaded)
    if args.filters:
        try:
            loaded = json.loads(args.filters)
        except ValueError as exc:
            raise UsageError("--filters is not valid JSON: %s" % exc)
        if not isinstance(loaded, dict):
            raise UsageError("--filters must be a JSON object")
        filters.update(loaded)
    if args.country:
        filters["country"] = args.country
    if args.product_category:
        filters["product_categories"] = list(args.product_category)
    if args.company_type:
        filters["company_type"] = args.company_type
    if args.max_moq is not None:
        filters["max_moq"] = args.max_moq
    if args.limit is not None:
        filters["limit"] = args.limit
    return filters


def _dispatch(args):
    command = args.command
    if command == "get-rfq":
        adapter = _adapter_from_args(args)
        document = adapter.get_rfq(args.id)
        if args.with_evidence:
            document = attach_internal_evidence(
                document,
                "rfq",
                "/rfqs/%s" % _safe_id(args.id, "rfq_id"),
                adapter.as_of,
                app_url=getattr(adapter, "app_url", None),
            )
        return document
    if command == "list-sellers":
        adapter = _adapter_from_args(args)
        return adapter.list_sellers(_seller_filters_from_args(args))
    if command in ("save-leads", "post-research-leads"):
        adapter = _adapter_from_args(args)
        return adapter.post_research_leads(
            _read_json_argument(args.input, "--input"), overwrite=args.overwrite
        )
    if command in ("save-matches", "post-matches"):
        adapter = _adapter_from_args(args)
        return adapter.post_matches(_read_json_argument(args.input, "--input"))
    if command in ("save-outreach-drafts", "post-outreach-drafts"):
        adapter = _adapter_from_args(args)
        return adapter.post_outreach_drafts(_read_json_argument(args.input, "--input"))
    if command in ("update-lead-status", "patch-lead-status"):
        adapter = _adapter_from_args(args)
        return adapter.patch_lead_status(args.id, args.status)
    raise UsageError("unknown command %r" % command)


def main(argv=None):
    """Entry point. Returns the process exit code; never calls sys.exit itself."""
    parser = _build_parser()
    args = _apply_defaults(parser.parse_args(list(sys.argv[1:] if argv is None else argv)))

    if getattr(args, "version", False):
        try:
            score_version = load_config(getattr(args, "config", None)).get("score_version", "unknown")
        except AdapterError:
            score_version = "unknown"
        sys.stdout.write(
            "tradewith_adapter.py skill_version=%s schema_version=%s score_version=%s\n"
            % (SKILL_VERSION, SCHEMA_VERSION, score_version)
        )
        return 0

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2

    try:
        result = _dispatch(args)
    except UsageError as exc:
        sys.stderr.write("ERROR: %s\n" % _redact(exc))
        return 2
    except ValidationError as exc:
        sys.stderr.write("ERROR: %s\n" % _redact(exc))
        for message in exc.errors[: getattr(args, "max_errors", 20) or 20]:
            sys.stderr.write("  %s\n" % _redact(message))
        return 1
    except DataError as exc:
        sys.stderr.write("ERROR: %s\n" % _redact(exc))
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("ERROR: interrupted\n")
        return 1

    if args.pretty:
        text = json.dumps(result, ensure_ascii=False, indent=2, separators=(",", ": "))
    else:
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(text + "\n")
    refused = [
        row for row in (result if isinstance(result, list) else [])
        if isinstance(row, dict) and row.get("result") == "refused"
    ]
    if refused:
        sys.stderr.write(
            "ERROR: %d record(s) refused and left as stored: %s\n"
            % (len(refused), ", ".join(str(row.get("id")) for row in refused))
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
