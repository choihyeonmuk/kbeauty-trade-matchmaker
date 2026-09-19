#!/usr/bin/env python3
"""export_leads.py - write the leads of one scored run as a file a human imports.

It reads one scored `discovery-result` document (the output of score_buyer.py or
score_seller.py) and writes it in one of three formats:

  csv            a generic spreadsheet / CRM file, buyers or sellers (the default)
  tradewith-json the request body of TradeWith's admin bulk-import endpoint
                 (`{"buyers": [...]}`, BulkBuyerRowDto rows), buyers only
  tradewith-csv  the CSV that TradeWith's admin buyer-import page reads, buyers only

Korean gloss: 점수화된 발굴 결과를 스프레드시트·CRM용 CSV 또는 TradeWith 관리자
일괄 등록 파일로 내보낸다. 파일만 쓰며, 어디에도 전송하지 않는다.

It is a FILE FORMAT, not an adapter capability. It writes a file for a person, it
opens no connection, and nothing in it can post, upload or deliver anything; an
admin decides whether and when the file is imported. No scorer reads its output
and it changes no score, so score_version and schema_version never move because of
it.

Rows exported for TradeWith carry NO quality tier label, so they land as tier C
(unreviewed market aggregate). An admin reviews them in TradeWith, promotes a row
to tier A or B and adds its tags; until then the row is not offered to sellers.
They also carry NO contact field at all - no contactName, contactPhone or
contactEmail, not even a company role mailbox: TradeWith marks any row with an
email as a high-confidence contact, and a re-import would overwrite an address an
admin corrected by hand. The generic CSV, which the operator keeps, still lists the
company-level channels.

Build rules honoured here (BUILD-CONTRACT.md 1.5, 7.11): stdlib only, Python 3.9
syntax, no network, no wall-clock read (--as-of is shape-checked only; nothing in
the output depends on it), nothing at import time, the document to stdout and
diagnostics to stderr, byte-stable output for the same input. All code comments are
English.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "export_leads.py"

FORMATS = ("csv", "tradewith-json", "tradewith-csv")
TRADEWITH_FORMATS = ("tradewith-json", "tradewith-csv")

# ---- generic CSV --------------------------------------------------------------------
# Module constants, not scoring.config.json: no scorer reads any of them, and none is
# a scoring number (INV-29 covers weights, thresholds and penalties only).
CSV_COLUMNS_HEAD = (
    "record_id",
    "entity_type",
    "company_name",
    "canonical_domain",
    "website",
    "country",
    "company_type",
    "product_categories",
    "qualification_score",
    "qualified",
    "confidence",
    "status",
    "operational_status",
    "stale",
)
BUYER_DIMENSIONS = (
    "kbeauty_fit",
    "b2b_role",
    "sourcing_intent",
    "market_relevance",
    "reachability",
    "evidence_quality",
)
SELLER_DIMENSIONS = (
    "product_fit",
    "commercial_model",
    "operational_fit",
    "compliance_readiness",
    "export_readiness",
    "evidence_quality",
)
#: A buyer dimension the scorer DROPS when the query makes it inapplicable. Its
#: absence is "not applicable", which is a different statement from "unknown".
DROPPABLE_DIMENSIONS = frozenset(["market_relevance"])
CSV_COLUMNS_TAIL = ("contact_channels", "missing", "score_version", "as_of")

UNKNOWN_CELL = "unknown"
NONE_CELL = "none"
NOT_APPLICABLE_CELL = "not_applicable"
#: Every stored channel was withheld by the role-address rule below.
WITHHELD_CELL = "withheld"
LIST_SEPARATOR = "; "
CHANNEL_SEPARATOR = " | "

# ---- filters ----------------------------------------------------------------------
#: A company that is closed or whose site is unreachable is not a lead, in any format.
SKIP_OPERATIONAL_STATUS = frozenset(["closed", "unreachable"])
SKIP_REASONS = ("operational_status", "not_qualified", "below_min_score", "country_unknown")

# ---- TradeWith --------------------------------------------------------------------
#: The IndustryCode that TradeWith's buyer matching filters on.
TRADEWITH_INDUSTRY = "BEAUTY"
TRADEWITH_ORIGINAL_SOURCE = "kbeauty-trade-matchmaker"
TRADEWITH_SOCIAL_CHANNEL = "linkedin"
#: sourceId = "kbtm:" + the www-stripped canonical_domain (the PRD 13.2 merge key, which
#: survives dedupe survivor-id churn), or "kbtm:id:" + the record id when the domain is
#: unknown. The prefix keeps these ids apart from every other CSV_IMPORT source.
TRADEWITH_SOURCE_ID_PREFIX = "kbtm:"
TRADEWITH_SOURCE_ID_RECORD_INFIX = "id:"
#: Express / Nest default JSON body limit. Exceeding it only prints a WARNING.
TRADEWITH_MAX_BODY_BYTES = 102400
#: The admin import page maps these headers by name; any other header is ignored there.
TRADEWITH_CSV_COLUMNS = ("sourceId", "companyName", "country", "website", "industry")
#: tradewith-json keys the admin page's six-column CSV cannot carry; tradewith-csv
#: drops them and says so on stderr.
TRADEWITH_CSV_DROPPED = ("category", "productsSummary", "originalSource", "sourceUrl",
                         "social")
#: The admin page splits lines on newlines and toggles on every double quote, so a
#: value carrying one of these cannot survive its parser intact.
TRADEWITH_CSV_UNSAFE = ('"', "\n", "\r")

# ---- role addresses ---------------------------------------------------------------
#: A company ROLE mailbox, matched on the exact local part. Anything else - a name, an
#: initial, a digit, a plus tag - is treated as possibly personal and is withheld. When
#: in doubt the address stays out: an unlisted role mailbox costs a lookup, a personal
#: address shipped by mistake is an INV-31 breach.
ROLE_LOCAL_PARTS = frozenset(
    [
        "b2b",
        "brands",
        "business",
        "buying",
        "contact",
        "distribution",
        "einkauf",
        "enquiries",
        "enquiry",
        "export",
        "exports",
        "hello",
        "import",
        "imports",
        "info",
        "inquiries",
        "inquiry",
        "kaigai",
        "odm",
        "oem",
        "office",
        "orders",
        "partner",
        "partners",
        "partnership",
        "partnerships",
        "procurement",
        "purchasing",
        "sales",
        "sellers",
        "sourcing",
        "trade",
        "vertrieb",
        "wholesale",
    ]
)
_EMAIL_RE = re.compile(
    r"^([a-z0-9]+(?:[._-][a-z0-9]+)*)@([a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)$"
)
_HTTP_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_ALPHA2_RE = re.compile(r"^[A-Z]{2}$")
#: A LinkedIn ORGANISATION page. A member profile (/in/, /pub/) names a person, so it is
#: withheld in every format and counted on stderr. The first path segment decides, so
#: the URL is parsed rather than prefix-matched: "/company/../in/jane" resolves to a
#: member profile in every browser. The host is not pinned: the section path is what
#: names an organisation, and fixtures use the reserved linkedin.example host.
COMPANY_SOCIAL_SECTIONS = frozenset(["company", "showcase", "school"])
#: Channel types whose value may legitimately be a published company number
#: (BUILD-CONTRACT 3.6): a switchboard, or an official WhatsApp / KakaoTalk business
#: line. The phone shape is not a refusal for these - but only when the WHOLE value is a
#: number (or a wa.me / api.whatsapp.com link to one); anything else, such as a number
#: followed by "(Mr. X, mobile)", gets the full scan. An email shape always refuses.
PHONE_SHAPED_CHANNELS = frozenset(["phone", "messenger"])
_BARE_NUMBER_RE = re.compile(r"^\+?[0-9][0-9 ()./\-]{5,23}[0-9]$")
_WHATSAPP_LINK_RE = re.compile(
    r"^https://(?:wa\.me|api\.whatsapp\.com/send/?\?phone=)/?\+?[0-9]{7,15}/?$",
    re.IGNORECASE,
)
PHONE_HIT = "a phone-number-like string"


# --------------------------------------------------------------------------
# input shape
# --------------------------------------------------------------------------


def _check_document(document):
    """The entity of a scored discovery-result; a UsageError for anything else."""
    # A document that parses but is the wrong kind breaks the input contract: exit 1
    # (BUILD-CONTRACT 7.3), as in diff_runs.py and stale_evidence.py.
    if not isinstance(document, dict):
        raise _common.DataError("input must be a scored discovery-result document object")
    if "match_run_id" in document or "no_match" in document:
        raise _common.DataError(
            "export takes a scored discovery-result; a match-result carries no company fields"
        )
    if "entity" not in document or not isinstance(document.get("records"), list):
        raise _common.DataError(
            "input is not a discovery-result document (entity + records[])"
        )
    entity = document.get("entity")
    if entity not in ("buyer", "seller"):
        raise _common.DataError(
            "discovery-result entity must be 'buyer' or 'seller', got %r" % (entity,)
        )
    return entity


def _check_score_versions(document, records):
    """INV-23 / BC 12.3 rule 4: one scored rubric per export, never a mix."""
    envelope = document.get("score_version")
    if not isinstance(envelope, str) or not envelope or envelope == "unscored":
        raise _common.DataError(
            "the input is not scored (envelope score_version %r); run score_buyer.py or "
            "score_seller.py first" % (envelope,)
        )
    for record in records:
        version = record.get("score_version")
        if version != envelope:
            raise _common.DataError(
                "record %s carries score_version %r but the run carries %r; scores from two "
                "rubrics are not on one scale, so the export is refused"
                % (_record_label(record), version, envelope)
            )


def _record_label(record):
    for key in ("buyer_id", "seller_id"):
        value = record.get(key) if isinstance(record, dict) else None
        if isinstance(value, str) and value:
            return value
    return "<no id>"


def _record_id(record, entity):
    key = "%s_id" % entity
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or _common.is_unknown(value):
        raise _common.DataError("a %s record carries no usable %s" % (entity, key))
    return value


# --------------------------------------------------------------------------
# typed field access (guards that hold even under --no-validate)
# --------------------------------------------------------------------------


def _text(record, field, rid):
    """A string field, or None when absent / "unknown" / empty; DataError on a bad type."""
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _common.DataError(
            "record %s field %s must be a string, got %s" % (rid, field, type(value).__name__)
        )
    if _common.is_unknown(value) or not value.strip():
        return None
    return value


def _string_list(record, field, rid):
    """None when absent or "unknown", else a list of strings (possibly empty)."""
    value = record.get(field)
    if value is None or (isinstance(value, str) and _common.is_unknown(value)):
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _common.DataError("record %s field %s must be a list of strings" % (rid, field))
    return value


def _channels(record, rid):
    """contact_channels as a list of {type, value} dicts, or None when unknown."""
    value = record.get("contact_channels")
    if value is None or (isinstance(value, str) and _common.is_unknown(value)):
        return None
    if not isinstance(value, list):
        raise _common.DataError("record %s field contact_channels must be a list" % rid)
    for channel in value:
        if (
            not isinstance(channel, dict)
            or not isinstance(channel.get("type"), str)
            or not isinstance(channel.get("value"), str)
        ):
            raise _common.DataError(
                "record %s has a contact channel without a string type and value" % rid
            )
    return value


def _company_domains(record, rid):
    """The www-stripped company domains a role address may sit on."""
    domains = []
    primary = _text(record, "canonical_domain", rid)
    if primary:
        domains.append(_strip_www(primary))
    for alias in _string_list(record, "alias_domains", rid) or []:
        if alias.strip() and not _common.is_unknown(alias):
            domains.append(_strip_www(alias))
    return set(domains)


def _strip_www(domain):
    host = domain.strip().lower()
    while host.startswith("www."):
        host = host[4:]
    return host


def _role_address(channel, domains):
    """The address when a corporate_email channel is a company ROLE mailbox, else None.

    All four must hold: the channel type is corporate_email; the address parses as a
    plain lower-case mailbox; its domain is the company's own (canonical or alias,
    www stripped) and not a free-mail provider; and its local part is one of
    ROLE_LOCAL_PARTS exactly. If any is in doubt, the address is withheld.
    """
    if channel.get("type") != "corporate_email":
        return None
    address = channel.get("value", "").strip().lower()
    match = _EMAIL_RE.match(address)
    if not match:
        return None
    local, domain = match.group(1), _strip_www(match.group(2))
    if domain in _common.FREE_MAIL_DOMAINS or domain not in domains:
        return None
    if local not in ROLE_LOCAL_PARTS:
        return None
    return address


def _is_number(value):
    """A finite JSON number; NaN and the infinities (which json.loads accepts) are not."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _is_non_finite(value):
    return isinstance(value, float) and not math.isfinite(value)


def _company_social(channel):
    """True when a linkedin channel points at an organisation page, not a member.

    Parsed, not prefix-matched: an http(s) URL with a host and no userinfo whose path is
    /company|showcase|school/<name>[/...] with no empty, "." or ".." segment - percent-
    encoded dots included - so no browser can resolve it to a member profile.
    """
    value = channel["value"].strip()
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https") or not host:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    path = parts.path
    if path.endswith("/"):
        path = path[:-1]
    segments = path.split("/")[1:]
    if len(segments) < 2 or segments[0].lower() not in COMPANY_SOCIAL_SECTIONS:
        return False
    for segment in segments:
        decoded = unquote(segment).strip()
        if decoded in ("", ".", "..") or "/" in decoded or "\\" in decoded:
            return False
    return True


def _phone_value_ok(ctype, value):
    """True when a phone / messenger channel value is a bare published number."""
    if ctype not in PHONE_SHAPED_CHANNELS:
        return False
    text = value.strip()
    if _BARE_NUMBER_RE.match(text):
        return True
    return ctype == "messenger" and bool(_WHATSAPP_LINK_RE.match(text))


# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------


def _select(records, entity, args):
    """(kept [(rid, record)], skipped-counter dict, total) in document order."""
    skipped = dict((reason, 0) for reason in SKIP_REASONS)
    kept = []
    seen = {}
    for record in records:
        if not isinstance(record, dict):
            raise _common.DataError("every record must be a JSON object")
        rid = _record_id(record, entity)
        if rid in seen:
            raise _common.DataError(
                "record id %s appears twice; a TradeWith sourceId and a CRM key must be "
                "unique, so the export is refused" % rid
            )
        seen[rid] = True
        status = record.get("operational_status")
        if isinstance(status, str) and status in SKIP_OPERATIONAL_STATUS:
            skipped["operational_status"] += 1
            continue
        if not args.include_unqualified and record.get("qualified") is not True:
            skipped["not_qualified"] += 1
            continue
        if args.min_score is not None:
            score = record.get("qualification_score")
            if _is_non_finite(score):
                raise _common.DataError(
                    "record %s qualification_score is %r, not a finite number" % (rid, score)
                )
            if not _is_number(score) or _common._to_decimal(score) < args.min_score:
                skipped["below_min_score"] += 1
                continue
        if args.format in TRADEWITH_FORMATS:
            country = _text(record, "country", rid)
            if country is None:
                skipped["country_unknown"] += 1
                continue
        kept.append((rid, record))
    return kept, skipped


def _duplicate_warnings(kept, entity):
    """Likely duplicates that dedupe_companies.py has not merged: one line per group."""
    by_key = {}
    for rid, record in kept:
        name = _common.normalize_company_name(record.get("company_name"))
        if name:
            by_key.setdefault(("name", name), []).append(rid)
        domain = record.get("canonical_domain")
        if isinstance(domain, str) and domain.strip() and not _common.is_unknown(domain):
            by_key.setdefault(("domain", _strip_www(domain)), []).append(rid)
    lines = []
    for (kind, key), ids in by_key.items():
        if len(ids) > 1:
            lines.append(
                "WARNING: possible duplicate %s records %s share the %s %r; run "
                "dedupe_companies.py before exporting, or the import creates two rows for "
                "one company" % (entity, ", ".join(ids), "normalized name" if kind == "name"
                                 else "domain", key)
            )
    return lines


# --------------------------------------------------------------------------
# generic CSV
# --------------------------------------------------------------------------


def _cell(value, field, rid):
    """One generic-CSV cell per the literals of references/data-contract.md section 10."""
    if value is None:
        return UNKNOWN_CELL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _common.DataError(
                "record %s field %s is %r, not a finite number" % (rid, field, value)
            )
        return json.dumps(value)
    if isinstance(value, str):
        return UNKNOWN_CELL if _common.is_unknown(value) or not value.strip() else value
    if isinstance(value, list):
        if not value:
            return NONE_CELL
        if any(_is_non_finite(item) for item in value):
            raise _common.DataError(
                "record %s field %s holds a non-finite number" % (rid, field)
            )
        if not all(isinstance(item, str) or _is_number(item) for item in value):
            raise _common.DataError("record %s field %s holds a non-scalar item" % (rid, field))
        return LIST_SEPARATOR.join(
            item if isinstance(item, str) else json.dumps(item) for item in value
        )
    raise _common.DataError(
        "record %s field %s has an unexportable %s value" % (rid, field, type(value).__name__)
    )


def _csv_channels(record, rid, withheld, withheld_social):
    """(cell, [(scan_label, text, phone_ok)]): stored channels as type=value, labels dropped."""
    channels = _channels(record, rid)
    if channels is None:
        return UNKNOWN_CELL, []
    if not channels:
        return NONE_CELL, []
    domains = _company_domains(record, rid)
    parts = []
    scan = []
    for channel in channels:
        ctype, value = channel["type"], channel["value"]
        if ctype == "corporate_email":
            address = _role_address(channel, domains)
            if address is None:
                withheld.append(rid)
                continue
            parts.append("%s=%s" % (ctype, address))
            continue
        if ctype == TRADEWITH_SOCIAL_CHANNEL and not _company_social(channel):
            withheld_social.append(rid)
            continue
        parts.append("%s=%s" % (ctype, value))
        # A published switchboard or official messenger line is company-level by
        # BUILD-CONTRACT 3.6, and the detector flags every "+"-prefixed number, so only
        # the phone shape is waived for those two types; an address still refuses.
        scan.append(("contact_channels (%s)" % ctype, value, _phone_value_ok(ctype, value)))
    if not parts:
        return WITHHELD_CELL, []
    return CHANNEL_SEPARATOR.join(parts), scan


def _csv_row(record, rid, entity, envelope_as_of, withheld, withheld_social):
    dimensions = BUYER_DIMENSIONS if entity == "buyer" else SELLER_DIMENSIONS
    row = {}
    for column in CSV_COLUMNS_HEAD:
        if column == "record_id":
            row[column] = rid
        elif column == "entity_type":
            row[column] = entity
        else:
            row[column] = _cell(record.get(column), column, rid)
    scores = record.get("dimension_scores")
    if scores is not None and not isinstance(scores, dict):
        raise _common.DataError("record %s field dimension_scores must be an object" % rid)
    for name in dimensions:
        column = "dim_%s" % name
        if scores is None:
            row[column] = UNKNOWN_CELL
        elif name not in scores and name in DROPPABLE_DIMENSIONS:
            row[column] = NOT_APPLICABLE_CELL
        else:
            row[column] = _cell(scores.get(name), column, rid)
    channel_cell, channel_scan = _csv_channels(record, rid, withheld, withheld_social)
    row["contact_channels"] = channel_cell
    row["missing"] = _cell(record.get("missing"), "missing", rid)
    row["score_version"] = _cell(record.get("score_version"), "score_version", rid)
    as_of = record.get("as_of")
    row["as_of"] = _cell(as_of if as_of is not None else envelope_as_of, "as_of", rid)

    for column, value in row.items():
        if column != "contact_channels":
            _refuse_personal(rid, column, value)
    for label, value, phone_ok in channel_scan:
        _refuse_personal(rid, label, value, phone_ok=phone_ok)
    return row


def _csv_columns(entity):
    dimensions = BUYER_DIMENSIONS if entity == "buyer" else SELLER_DIMENSIONS
    return (
        list(CSV_COLUMNS_HEAD)
        + ["dim_%s" % name for name in dimensions]
        + list(CSV_COLUMNS_TAIL)
    )


def _write_csv(columns, rows, guard):
    buffer = io.StringIO()
    # LF pinned: BUILD-CONTRACT 1.5, and the csv default of CRLF would make the golden
    # fixture platform-dependent.
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            dict((key, _common.csv_safe_cell(value) if guard else value)
                 for key, value in row.items())
        )
    return buffer.getvalue()


# --------------------------------------------------------------------------
# TradeWith
# --------------------------------------------------------------------------


def _source_id(record, rid):
    """kbtm:<www-stripped canonical_domain>, or kbtm:id:<record id> when it is unknown."""
    domain = _text(record, "canonical_domain", rid)
    if domain is not None:
        return TRADEWITH_SOURCE_ID_PREFIX + _strip_www(domain)
    return TRADEWITH_SOURCE_ID_PREFIX + TRADEWITH_SOURCE_ID_RECORD_INFIX + rid


def _stale_flag(record):
    value = record.get("stale")
    if value is True:
        return "true"
    if value is False:
        return "false"
    return UNKNOWN_CELL


def _tradewith_row(record, rid, document, withheld_social):
    """One BulkBuyerRowDto: only keys with a real value, in a fixed order.

    An omitted key leaves the stored TradeWith value alone on re-import, while an
    explicit value overwrites an admin's edit, so nothing is sent as null, "", "unknown"
    or an empty list. Never sent: contactName, contactEmail, contactPhone, notes,
    extraNotes, qualityTierLabel, hsCodes and every field this package has no source for.
    """
    row = {"sourceId": _source_id(record, rid)}
    name = _text(record, "company_name", rid)
    if name is None:
        raise _common.DataError("record %s carries no company_name" % rid)
    row["companyName"] = name
    country = _text(record, "country", rid)
    if country is None or not _ALPHA2_RE.match(country):
        raise _common.DataError(
            "record %s country %r is not an ISO alpha-2 code" % (rid, record.get("country"))
        )
    row["country"] = country
    website = _text(record, "website", rid)
    if website and _HTTP_URL_RE.match(website):
        row["website"] = website

    row["industry"] = TRADEWITH_INDUSTRY
    company_type = _text(record, "company_type", rid)
    if company_type:
        row["category"] = company_type
    summary = []
    categories = _string_list(record, "product_categories", rid)
    if categories:
        summary.append(", ".join(categories))
    brands = _string_list(record, "korean_brands_carried", rid)
    if brands:
        summary.append("Korean brands: " + ", ".join(brands))
    if summary:
        row["productsSummary"] = "; ".join(summary)

    as_of = record.get("as_of") if record.get("as_of") is not None else document.get("as_of")
    row["originalSource"] = "%s | score_version=%s | as_of=%s | record_id=%s | stale=%s" % (
        TRADEWITH_ORIGINAL_SOURCE,
        document.get("score_version"),
        as_of if isinstance(as_of, str) else "unknown",
        rid,
        _stale_flag(record),
    )
    source_url = _evidence_url(record, rid) or row.get("website")
    if source_url:
        row["sourceUrl"] = source_url
    for channel in _channels(record, rid) or []:
        if channel["type"] != TRADEWITH_SOCIAL_CHANNEL:
            continue
        # TradeWith masks only the three contact fields, and matching shows `social` to
        # sellers, so a member profile would reach them unmasked: organisation pages only.
        if not _company_social(channel):
            withheld_social.append(rid)
            continue
        if _HTTP_URL_RE.match(channel["value"]):
            row["social"] = channel["value"]
            break

    for key, value in row.items():
        _refuse_personal(rid, key, value)
    return row


def _evidence_url(record, rid):
    """The first official tier-1 evidence URL, in stored order, or None."""
    evidence = record.get("evidence")
    if evidence is None:
        return None
    if not isinstance(evidence, list):
        raise _common.DataError("record %s field evidence must be a list" % rid)
    for item in evidence:
        if not isinstance(item, dict):
            raise _common.DataError("record %s holds a non-object evidence item" % rid)
        url = item.get("source_url")
        if (
            item.get("is_official") is True
            and item.get("source_tier") == 1
            and isinstance(url, str)
            and _HTTP_URL_RE.match(url)
        ):
            return url
    return None


def _check_unique_source_ids(rows, kept):
    """Two kept records on one domain would collide on TradeWith's {source, sourceId}."""
    first = {}
    for row, (rid, _record) in zip(rows, kept):
        source_id = row["sourceId"]
        if source_id in first:
            raise _common.DataError(
                "records %s and %s share the TradeWith sourceId %s; run "
                "dedupe_companies.py (or narrow the filters) so one company is one row"
                % (first[source_id], rid, source_id)
            )
        first[source_id] = rid


def _check_tradewith_csv_cells(rows):
    """Refuse a value the admin import page would mangle; never repair it.

    The page is not a spreadsheet, so an apostrophe guard would be stored verbatim as
    part of the company name - but the operator may well open the file in one first.
    A value that needs the formula guard is therefore refused rather than rewritten,
    and so is a double quote or a line break, which the page's parser cannot read.
    """
    for row in rows:
        for key, value in row.items():
            if _common.csv_needs_formula_guard(value):
                raise _common.DataError(
                    "record %s field %s starts with a spreadsheet formula character; it "
                    "cannot be escaped without corrupting the imported value, so the "
                    "tradewith-csv export is refused" % (row["sourceId"], key)
                )
            if any(mark in value for mark in TRADEWITH_CSV_UNSAFE):
                raise _common.DataError(
                    "record %s field %s holds a double quote or a line break, which the "
                    "TradeWith admin import page cannot parse; use --format tradewith-json"
                    % (row["sourceId"], key)
                )


def _refuse_personal(rid, field, value, phone_ok=False):
    if not isinstance(value, str):
        return
    hits = [hit for hit in _common.personal_data_hits(value)
            if not (phone_ok and hit == PHONE_HIT)]
    if hits:
        raise _common.DataError(
            "record %s field %s looks like it carries %s; exports carry company-level data "
            "only (INV-31), so nothing was written" % (rid, field, " and ".join(hits))
        )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Export the leads of one scored discovery-result as a generic CSV (buyers or "
            "sellers) or as a TradeWith admin bulk-import file (buyers only). Writes a file "
            "and nothing else: it never posts, uploads or sends. Rows for TradeWith carry "
            "no quality tier label, so they land as tier C (unreviewed); an admin reviews "
            "them, promotes them to A/B and adds tags in TradeWith. "
            "Korean gloss: 점수화 결과를 CSV 또는 TradeWith 일괄 등록 파일로 내보낸다. "
            "등급 라벨 없이 C 등급으로 들어가며, 관리자가 검토 후 A/B 승격과 태그를 붙인다."
        ),
    )
    parser.add_argument("-i", "--input", default=None,
                        help="Scored discovery-result (default: stdin)")
    parser.add_argument("-o", "--output", default=None, help="Output path (default: stdout)")
    parser.add_argument(
        "--format", dest="format", choices=FORMATS, default="csv",
        help=(
            "csv (default; buyers or sellers), tradewith-json (the body an admin posts to "
            "POST /buyers/bulk-import; buyers only) or tradewith-csv (the file TradeWith's "
            "admin buyer-import page reads; buyers only). TradeWith rows land as tier C "
            "until an admin sets tier A/B and tags"
        ),
    )
    parser.add_argument(
        "--include-unqualified", dest="include_unqualified", action="store_true",
        help="Also export scored records with qualified != true (default: qualified only)",
    )
    parser.add_argument(
        "--min-score", dest="min_score", default=None,
        help="Also require qualification_score >= N (an integer 0-100)",
    )
    parser.add_argument("--pretty", action="store_true",
                        help="Indent the JSON (tradewith-json only)")
    parser.add_argument(
        "--no-validate", dest="validate", action="store_false", default=True,
        help="Skip the input schema check (the TradeWith output is always validated)",
    )
    parser.add_argument("--as-of", dest="as_of", default=None,
                        help="YYYY-MM-DD; shape-checked only, nothing depends on it")
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--quiet", action="store_true", help="Suppress the stderr summary")
    parser.add_argument("--version", action="store_true")
    return parser


def main(argv):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        config = _common.load_config(args.config)
    except Exception as exc:
        return _common.die(str(exc), 2)

    if args.version:
        sys.stdout.write(
            "%s skill_version=%s schema_version=%s score_version=%s\n"
            % (SCRIPT_NAME, _common.SKILL_VERSION, _common.SCHEMA_VERSION, config["score_version"])
        )
        return 0

    try:
        return _run(args)
    except _common.UsageError as exc:
        return _common.die(str(exc), 2)
    except _common.ConfigError as exc:
        return _common.die(str(exc), 2)
    except _common.DataError as exc:
        return _common.die(str(exc), 1)
    except Exception as exc:
        return _common.die("%s: %s" % (type(exc).__name__, exc), 1)


def _parse_min_score(raw):
    if raw is None:
        return None
    text = raw.strip()
    if not re.match(r"^[0-9]{1,3}$", text) or int(text) > 100:
        raise _common.UsageError("--min-score must be an integer from 0 to 100, got %r" % raw)
    return _common._to_decimal(int(text))


def _run(args):
    args.min_score = _parse_min_score(args.min_score)
    if args.pretty and args.format != "tradewith-json":
        raise _common.UsageError("--pretty applies only to --format tradewith-json")
    if args.as_of is not None:
        if not args.as_of.strip():
            raise _common.UsageError("--as-of is empty; give a YYYY-MM-DD date or omit the flag")
        _common.resolve_as_of([], args.as_of)

    document = _common.read_input(args)
    entity = _check_document(document)
    if entity == "seller" and args.format in TRADEWITH_FORMATS:
        raise _common.UsageError(
            "TradeWith has no seller bulk-import endpoint (sellers self-register); use "
            "--format csv"
        )
    if args.validate:
        errors = _common.validate(
            document, _common.load_schema("discovery-result", args.schema_dir)
        )
        if not errors:
            # The envelope schema leaves records[] to the entity schema, because
            # _common.validate cannot follow a cross-file $ref (validate_output.py does
            # the same two-step check).
            record_schema = _common.load_schema(entity, args.schema_dir)
            for record in document["records"]:
                found = _common.validate(record, record_schema)
                if found:
                    errors = ["%s: %s" % (_record_label(record), err) for err in found]
                    break
        if errors:
            raise _common.DataError(
                "input fails its schema (discovery-result / %s):\n" % entity
                + "\n".join("  %s" % err for err in errors[:5])
            )
    records = document["records"]
    for record in records:
        if not isinstance(record, dict):
            raise _common.DataError("every record must be a JSON object")
    _check_score_versions(document, records)

    kept, skipped = _select(records, entity, args)
    withheld = []
    withheld_social = []
    dropped_by_csv = 0
    if args.format == "csv":
        rows = [_csv_row(record, rid, entity, document.get("as_of"), withheld, withheld_social)
                for rid, record in kept]
        text = _write_csv(_csv_columns(entity), rows, guard=True)
        body = None
    else:
        rows = [_tradewith_row(record, rid, document, withheld_social) for rid, record in kept]
        _check_unique_source_ids(rows, kept)
        if args.format == "tradewith-csv":
            dropped_by_csv = sum(
                1 for row in rows if any(key in row for key in TRADEWITH_CSV_DROPPED)
            )
            rows = [
                dict((key, row[key]) for key in TRADEWITH_CSV_COLUMNS if key in row)
                for row in rows
            ]
            _check_tradewith_csv_cells(rows)
        body = {"buyers": rows}
        # Always, even under --no-validate: this file is imported into a production
        # database, and the schema is what forbids the contact fields structurally.
        errors = _common.validate(
            body, _common.load_schema("tradewith-bulk-buyers", args.schema_dir)
        )
        if errors:
            raise _common.DataError(
                "the export fails tradewith-bulk-buyers.schema.json, so nothing was written:\n"
                + "\n".join("  %s" % err for err in errors[:5])
            )
        if args.format == "tradewith-json":
            buffer = io.StringIO()
            _common.write_output(body, args.pretty, buffer)
            text = buffer.getvalue()
        else:
            full = [dict((key, "") for key in TRADEWITH_CSV_COLUMNS) for _row in rows]
            for target, row in zip(full, rows):
                target.update(row)
            text = _write_csv(list(TRADEWITH_CSV_COLUMNS), full, guard=False)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        except OSError as exc:
            return _common.die("cannot write --output: %s" % (exc,), 2)
    else:
        sys.stdout.write(text)

    counts = ", ".join("%s=%d" % (reason, skipped[reason]) for reason in SKIP_REASONS
                       if skipped[reason])
    _common.eprint(
        "exported %d of %d %s record(s) as %s; skipped: %s"
        % (len(kept), len(records), entity, args.format, counts or "none"),
        quiet=args.quiet,
    )
    if document.get("partial") is True:
        _common.eprint(
            "WARNING: the input run is partial (envelope partial=true); some candidates "
            "were not fully evaluated", quiet=args.quiet,
        )
    if not kept:
        _common.eprint("WARNING: no record passed the filters", quiet=args.quiet)
    if withheld_social:
        _common.eprint(
            "withheld %d linkedin value(s) that are not an organisation page "
            "(/company/, /showcase/, /school/); a member profile names a person "
            "(records: %s)" % (len(withheld_social), ", ".join(sorted(set(withheld_social)))),
            quiet=args.quiet,
        )
    if dropped_by_csv:
        _common.eprint(
            "note: tradewith-csv carries only the columns the admin import page maps (%s); "
            "%d row(s) lost %s, so they arrive without provenance and their matching "
            "embedding sees only name, industry and country - use --format tradewith-json "
            "to keep them" % (", ".join(TRADEWITH_CSV_COLUMNS), dropped_by_csv,
                              "/".join(TRADEWITH_CSV_DROPPED)),
            quiet=args.quiet,
        )
    if withheld:
        _common.eprint(
            "withheld %d corporate_email value(s) that are not a role mailbox on the "
            "company's own domain (records: %s)"
            % (len(withheld), ", ".join(sorted(set(withheld)))),
            quiet=args.quiet,
        )
    for line in _duplicate_warnings(kept, entity):
        _common.eprint(line, quiet=args.quiet)
    if args.format == "tradewith-json":
        # JSON carries the value verbatim, which is right for the API, but TradeWith's own
        # spreadsheet exports would later hand it to a spreadsheet: say so, never rewrite.
        formula = sorted(set(row["sourceId"] for row in body["buyers"]
                             for value in row.values()
                             if _common.csv_needs_formula_guard(value)))
        if formula:
            _common.eprint(
                "WARNING: %d row(s) hold a value that starts with a spreadsheet formula "
                "character (%s); it is exported verbatim - check it before importing, "
                "because a later spreadsheet export from TradeWith would evaluate it"
                % (len(formula), ", ".join(formula)),
                quiet=args.quiet,
            )
    if body is not None:
        size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size > TRADEWITH_MAX_BODY_BYTES:
            _common.eprint(
                "WARNING: the import body is %d bytes; TradeWith's default JSON body limit "
                "is %d bytes - split the export (e.g. raise --min-score) before importing"
                % (size, TRADEWITH_MAX_BODY_BYTES),
                quiet=args.quiet,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
