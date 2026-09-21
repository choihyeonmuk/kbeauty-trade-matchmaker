#!/usr/bin/env python3
"""validate_output.py - the verifier's entry point.

Validates any document this package produces against `schemas/*.json` using the
dependency-free validator in `_common.py`, and then enforces the BUILD-CONTRACT section 11
invariants that JSON Schema cannot express - the ones a schema cannot reach because they
relate two fields, two documents, or a value to the scoring config.

Korean gloss: 스키마 검증 + 계약 불변식(INV-xx) 검사.

Exit codes: 0 clean, 1 schema error or invariant failure, 2 usage error.
stdout always carries exactly one JSON report; the human-readable summary goes to stderr so
that `a.py | validate_output.py` stays safe (BUILD-CONTRACT 7.2 stream discipline).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "validate_output.py"

KINDS = (
    "buyer", "seller", "rfq", "evidence", "match-result", "discovery-result",
    # A calibration MEASUREMENT document (scripts/acceptance_report.py). It carries no
    # score of its own, so only the generic document-shape invariants apply to it.
    "acceptance-report",
    # A COMPARISON of two scored runs (scripts/diff_runs.py). Like the acceptance report
    # it carries no score of its own, so only the generic invariants apply to it.
    "run-diff",
    # An AUDIT document (scripts/stale_evidence.py): which evidence to re-read. Like the
    # acceptance report it carries no score, so only the generic invariants apply.
    "recheck-queue",
    # An EXPORT document (scripts/export_leads.py --format tradewith-json): the TradeWith
    # bulk-import request body. It carries no score, so only the generic invariants apply.
    "tradewith-bulk-buyers",
    # An INTAKE document (scripts/intake_rfq.py): the RFQ built from a buyer message and
    # the questions to ask back. It carries no score, so only the generic invariants apply.
    "rfq-intake",
)

# BUILD-CONTRACT 7.5: the closed keyword subset _common.validate implements. A keyword
# outside it, used in a shipped schema, is a build blocker rather than a silent no-op.
SUPPORTED_KEYWORDS = frozenset(
    [
        "type", "properties", "required", "additionalProperties", "items",
        "enum", "const", "oneOf", "anyOf", "allOf",
        "if", "then", "else",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern",
        "format", "$ref", "$defs", "definitions",
        # annotation-only keywords: read, never constraining
        "$schema", "$id", "title", "description", "$comment", "default", "examples",
    ]
)

# PRD 8 / BUILD-CONTRACT 9: states this package must never produce (INV-09, INV-37).
FORBIDDEN_STATES = frozenset(
    [
        "APPROVED_FOR_OUTREACH",
        "CONTACTED",
        "REPLIED",
        "RFQ_RECEIVED",
        "PROPOSAL_RECEIVED",
        "MATCHED",
    ]
)

# INV-02: strings that silently stand in for "unknown" and must never be stored.
UNKNOWN_SURROGATES = frozenset(["n/a", "na", "null", "nil", "none given", "-", "--", "?", "tbd", "n.a."])
# "NA" is also a real code: the North America token of seller.export_regions and the ISO-3166-1
# alpha-2 of Namibia. In a field whose values are codes, the exact upper-case token is data.
CODE_FIELDS = frozenset(["country", "export_countries", "export_regions", "excluded_markets",
                         "destination_country", "region_countries", "required_seller_countries",
                         "excluded_seller_countries"])
_CODE_PATH = re.compile(r"(?:^|\.)(%s)(?:\[\d+\])?$" % "|".join(sorted(CODE_FIELDS)))

TRI_STATE_FIELDS = (
    "korean_products_signal",
    "wholesale_signal",
    "partnership_signal",
    "oem_odm",
    "private_label",
    "brand_export",
    "english_site",
    "overseas_partner_signal",
    "certifications_verified",
)

# SCORING-CONTRACT 3.1: the criterion whose existing unknown_penalty record pairs with a
# hard-filter rule skipped for unknown input; anything else pairs via a zero-weight record
# keyed on the rule_id itself.
HF_PENALTY_CRITERION = {
    "HF-01": "S-PF1",
    "HF-02": "S-CM1",
    "HF-03": "S-OP1",
    "HF-04": "S-CP1",
    "HF-07": "S-OP2",
}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _dec(value):
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(0)
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def _is_num(value):
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _walk(node, path=""):
    """Yield (json_path, value) for every node of the document."""
    yield path, node
    if isinstance(node, dict):
        for key in node:
            child = "%s.%s" % (path, key) if path else str(key)
            for item in _walk(node[key], child):
                yield item
    elif isinstance(node, list):
        for index, value in enumerate(node):
            for item in _walk(value, "%s[%d]" % (path, index)):
                yield item


def _doc_id(document):
    if not isinstance(document, dict):
        return None
    for key in ("match_run_id", "rfq_id", "buyer_id", "seller_id", "evidence_id"):
        value = document.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _detect_kind(document):
    if not isinstance(document, dict):
        return None
    # report_kind is the acceptance report's own discriminator; it is checked first
    # because the report echoes score_version and as_of like every other document.
    if document.get("report_kind") == "acceptance-report":
        return "acceptance-report"
    if document.get("report_kind") == "run-diff":
        return "run-diff"
    if document.get("report_kind") == "recheck-queue":
        return "recheck-queue"
    if document.get("report_kind") == "rfq-intake":
        return "rfq-intake"
    if "match_run_id" in document or "no_match" in document:
        return "match-result"
    if "entity" in document and "records" in document:
        return "discovery-result"
    if "rfq_id" in document and "product_category" in document:
        return "rfq"
    if "seller_id" in document:
        return "seller"
    if "buyer_id" in document and "company_name" in document:
        return "buyer"
    if "evidence_id" in document and "source_url" in document:
        return "evidence"
    # Checked last: the export body has no discriminator of its own, only its "buyers"
    # array, so any other kind that matched above wins.
    if "buyers" in document:
        return "tradewith-bulk-buyers"
    return None


def _date_part(value):
    if not isinstance(value, str) or len(value) < 10:
        return None
    head = value[:10]
    return head if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", head) else None


def _preflight(schema, path="#"):
    """Return a list of unsupported-keyword messages for a shipped schema."""
    problems = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            child = "%s/%s" % (path, key)
            if key in ("properties", "$defs", "definitions"):
                if isinstance(value, dict):
                    for name, sub in value.items():
                        problems.extend(_preflight(sub, "%s/%s" % (child, name)))
                continue
            if key not in SUPPORTED_KEYWORDS:
                problems.append("unsupported schema keyword '%s' at %s" % (key, path))
                continue
            if key in ("items", "then", "else", "if", "additionalProperties", "not"):
                problems.extend(_preflight(value, child))
            elif key in ("oneOf", "anyOf", "allOf") and isinstance(value, list):
                for index, sub in enumerate(value):
                    problems.extend(_preflight(sub, "%s[%d]" % (child, index)))
    return problems


# --------------------------------------------------------------------------------------
# invariants
# --------------------------------------------------------------------------------------


class _Failures(object):
    def __init__(self):
        self.items = []
        self.warnings = []

    def add(self, invariant, index, ident, message):
        self.items.append(
            {"invariant": invariant, "index": index, "id": ident, "message": message}
        )

    def warn(self, invariant, index, ident, message):
        """A rule that fails only under --strict (the report's warnings[])."""
        self.warnings.append(
            {"invariant": invariant, "index": index, "id": ident, "path": "<root>",
             "message": "%s: %s" % (invariant, message)}
        )


def _inv_generic(document, kind, index, ident, out):
    """Document-shape invariants that apply to every kind."""
    # INV-02: no silent stand-in for "unknown" anywhere in the document.
    # An evidence item mirrors the field it proves, so its value holds the same codes.
    code_values = set(
        "%s.value" % path if path else "value"
        for path, node in _walk(document)
        if isinstance(node, dict) and node.get("claim") in CODE_FIELDS
    )
    for path, value in _walk(document):
        if isinstance(value, str) and value.strip().casefold() in UNKNOWN_SURROGATES:
            if value == "NA" and (_CODE_PATH.search(path)
                                  or re.sub(r"\[\d+\]$", "", path) in code_values):
                continue
            out.add(
                "INV-02",
                index,
                ident,
                "%s holds '%s'; unknown must be the literal lowercase word \"unknown\""
                % (path or "<root>", value),
            )
    if isinstance(document, dict):
        for field in TRI_STATE_FIELDS:
            if field in document:
                value = document[field]
                if not (isinstance(value, bool) or value == _common.UNKNOWN):
                    out.add(
                        "INV-02",
                        index,
                        ident,
                        "%s is %r; a tri-state field is true, false or \"unknown\"" % (field, value),
                    )

    # INV-09 / INV-37: nothing this package emits may sit past human review.
    for path, value in _walk(document):
        if not isinstance(value, dict):
            continue
        status = value.get("status")
        if isinstance(status, str) and status in FORBIDDEN_STATES:
            out.add(
                "INV-09",
                index,
                ident,
                "%s.status is '%s'; this package never produces a state past READY_FOR_REVIEW"
                % (path or "<root>", status),
            )
        outreach_status = value.get("outreach_status")
        if isinstance(outreach_status, str) and outreach_status != "READY_FOR_REVIEW":
            out.add(
                "INV-09",
                index,
                ident,
                "%s.outreach_status is '%s'; every outreach artifact ends at READY_FOR_REVIEW"
                % (path or "<root>", outreach_status),
            )
        if value.get("auto_send") is True:
            out.add(
                "INV-09", index, ident, "%s.auto_send is true; it must be false" % (path or "<root>",)
            )
        if "manual_approval_required" in value and value.get("manual_approval_required") is not True:
            out.add(
                "INV-09",
                index,
                ident,
                "%s.manual_approval_required must be true" % (path or "<root>",),
            )

    # INV-24: observed_at never later than as_of; source_date never later than observed_at.
    as_of = document.get("as_of") if isinstance(document, dict) else None
    for path, value in _walk(document):
        if not isinstance(value, dict) or "observed_at" not in value:
            continue
        observed = _date_part(value.get("observed_at"))
        if observed is None:
            continue
        if as_of and observed > as_of:
            out.add(
                "INV-24",
                index,
                ident,
                "%s.observed_at %s is later than as_of %s" % (path, observed, as_of),
            )
        source_date = _date_part(value.get("source_date"))
        if source_date and source_date > observed:
            out.add(
                "INV-24",
                index,
                ident,
                "%s.source_date %s is later than its own observed_at %s"
                % (path, source_date, observed),
            )

    # INV-03: every unknown_penalty label surfaces in missing[].
    penalties = document.get("unknown_penalty_applied") if isinstance(document, dict) else None
    missing = document.get("missing") if isinstance(document, dict) else None
    if isinstance(penalties, list) and isinstance(missing, list):
        for entry in penalties:
            if isinstance(entry, dict) and entry.get("label") not in missing:
                out.add(
                    "INV-03",
                    index,
                    ident,
                    "unknown_penalty label '%s' is absent from missing[]" % (entry.get("label"),),
                )

    # INV-06: every hard-filter failure is fully explained. A failed_rule is discriminated
    # from a relaxation_suggestion (which also carries an optional rule_id) by these keys.
    for path, value in _walk(document):
        if not isinstance(value, dict) or "rule_id" not in value:
            continue
        if not any(
            key in value for key in ("rule_name", "reason", "observed_value", "required_value")
        ):
            continue
        if not re.match(r"^HF-[0-9]{2}$", str(value.get("rule_id") or "")):
            out.add(
                "INV-06", index, ident, "%s.rule_id '%s' is not an HF-nn id" % (path, value.get("rule_id"))
            )
        for field in ("rule_name", "reason"):
            if not str(value.get(field) or "").strip():
                out.add("INV-06", index, ident, "%s.%s is empty" % (path, field))
        for field in ("observed_value", "required_value"):
            if field not in value:
                out.add("INV-06", index, ident, "%s has no %s" % (path, field))


def _claim_tokens(claim, value):
    """Comparable tokens for one claim value: a set for list claims, else a 1-tuple.

    Returns None when the value cannot be compared (an "unknown" or an open-ended MOQ
    such as "from 1,000", which evidence-policy 3.1 keeps as unknown on the record).
    """
    if value is None or _common.is_unknown(value):
        return None
    if claim == "moq":
        try:
            rng = _common.coerce_range(value)
        except _common.DataError:
            return None
        if rng is None:
            return None
        unit = value.get("unit") if isinstance(value, dict) else None
        return (("%s" % _dec(rng["min"]).normalize(), "%s" % _dec(rng["max"]).normalize(),
                 _common.normalize_unit(unit)),)
    if isinstance(value, list):
        tokens = set()
        for item in value:
            if isinstance(item, dict):
                # contact_channels: an evidence value names the channel type or the value.
                for key in ("type", "value"):
                    if isinstance(item.get(key), str) and item[key].strip():
                        tokens.add(item[key].strip().casefold())
                continue
            token = _claim_scalar(claim, item)
            if token is not None:
                tokens.add(token)
        return tokens
    token = _claim_scalar(claim, value)
    return None if token is None else (token,)


def _claim_scalar(claim, value):
    if isinstance(value, bool):
        return value
    if _is_num(value):
        return "%s" % _dec(value).normalize()
    if not isinstance(value, str) or not value.strip() or _common.is_unknown(value):
        return None
    if claim in TRI_STATE_FIELDS:
        state = _common.tri_state(value)
        return state if isinstance(state, bool) else None
    if claim in ("country", "export_markets"):
        return _common.normalize_country(value) or value.strip().upper()
    if claim == "product_categories":
        return _common.normalize_category(value) or value.strip().casefold()
    if claim == "certifications":
        return _common.normalize_certification(value)
    return value.strip().casefold()


# evidence-policy 8 row 9 / 4.1: a material field needs a tier 1-3 source; tier 4-5 items are
# supporting signal that may sit beside an "unknown" field.
FIELD_MAX_SOURCE_TIER = 3


def _inv_claim_values(document, kind, index, ident, claims, evidence, out):
    """INV-01 on VALUES: the evidence must carry what the record claims."""
    by_claim = {}
    for item in evidence:
        if isinstance(item, dict) and item.get("claim") in claims:
            by_claim.setdefault(item["claim"], []).append(item)
    for claim in claims:
        items = by_claim.get(claim) or []
        record_value = document.get(claim)
        record_unknown = (
            claim not in document or _common.is_unknown(record_value) or record_value in ("", None)
        )
        if record_unknown:
            # evidence-policy 6.4: an undecidable conflict holds the field as "unknown" with both
            # items kept and a conflicts[] entry whose winning_value is "unknown".
            if any(
                isinstance(conflict, dict) and conflict.get("field") == claim
                and _common.is_unknown(conflict.get("winning_value"))
                for conflict in document.get("conflicts") or []
            ):
                continue
            # evidence-policy 8 rows 9 and 11: a stale item (a page that now 404s) and a
            # supporting signal below the tier floor are kept but never force a value.
            asserted = [
                item.get("evidence_id")
                for item in items
                if _claim_tokens(claim, item.get("value"))
                and item.get("stale") is not True
                and not (isinstance(item.get("source_tier"), int)
                         and item["source_tier"] > FIELD_MAX_SOURCE_TIER)
            ]
            if asserted:
                out.add(
                    "INV-01",
                    index,
                    ident,
                    "evidence %s asserts a value for material claim '%s' but the record field "
                    "is %s; a claim the evidence carries is written onto the record"
                    % (", ".join(str(x) for x in asserted), claim,
                       "absent" if claim not in document else repr(record_value)),
                )
            continue
        if not items:
            out.add(
                "INV-01",
                index,
                ident,
                "material claim '%s' has neither evidence nor \"unknown\"" % (claim,),
            )
            continue
        wanted = _claim_tokens(claim, record_value)
        if wanted is None:
            continue
        if isinstance(record_value, list):
            if claim == "contact_channels":
                pool = set()
                for item in items:
                    pool |= set(_claim_tokens(claim, item.get("value")) or ())
                unsupported = [
                    channel for channel in record_value
                    if isinstance(channel, dict)
                    and not ({str(channel.get("type") or "").casefold(),
                              str(channel.get("value") or "").strip().casefold()} & pool)
                ]
                missing = [str(c.get("type")) for c in unsupported]
            else:
                pool = set()
                for item in items:
                    pool |= set(_claim_tokens(claim, item.get("value")) or ())
                missing = sorted(str(token) for token in wanted - pool)
            if missing:
                out.add(
                    "INV-01",
                    index,
                    ident,
                    "material claim '%s' lists %s, which no '%s' evidence value contains"
                    % (claim, ", ".join(missing), claim),
                )
            continue
        matched = any(
            set(_claim_tokens(claim, item.get("value")) or ()) & set(wanted) for item in items
        )
        if not matched:
            out.add(
                "INV-01",
                index,
                ident,
                "material claim '%s' is %r, but its evidence says %s"
                % (claim, record_value,
                   ", ".join(repr(item.get("value")) for item in items)),
            )


def _inv_evidence_ids(document, index, ident, evidence, out):
    """INV-22 on a buyer / seller record: ids are unique and every reference resolves."""
    seen = set()
    for item in evidence:
        eid = item.get("evidence_id") if isinstance(item, dict) else None
        if not isinstance(eid, str):
            continue
        if eid in seen:
            out.add("INV-22", index, ident, "evidence_id '%s' appears more than once" % (eid,))
        seen.add(eid)
    for item in evidence:
        if not isinstance(item, dict):
            continue
        for link in item.get("conflicts_with") or []:
            if link not in seen:
                out.add(
                    "INV-22",
                    index,
                    ident,
                    "evidence %s conflicts_with '%s', which is not an evidence_id on this record"
                    % (item.get("evidence_id"), link),
                )
            elif link == item.get("evidence_id"):
                out.add(
                    "INV-22", index, ident,
                    "evidence %s lists itself in conflicts_with" % (link,),
                )
    for key, value in document.items():
        if key == "evidence":
            continue
        for path, node in _walk(value, key):
            if not isinstance(node, dict):
                continue
            for field in ("evidence_ids", "winning_evidence_ids", "losing_evidence_ids"):
                for reference in node.get(field) or []:
                    if isinstance(reference, str) and reference not in seen:
                        out.add(
                            "INV-22",
                            index,
                            ident,
                            "%s.%s references evidence_id '%s', which resolves nowhere on this "
                            "record" % (path, field, reference),
                        )


# evidence-policy 4.1: which source_type a tier-1 item comes from, and who controls it.
TIER_ONE_SOURCE_TYPE = "official_site"
INFERRED_CONFIDENCE_CAP = Decimal("0.60")


def _inv_evidence_integrity(document, kind, index, ident, evidence, config, as_of, out):
    """EVI-01..EVI-06: cross-field evidence rules and recomputed derived values."""
    record_domains = set()
    for field in ("canonical_domain", "website"):
        value = document.get(field)
        if isinstance(value, str) and value and not _common.is_unknown(value):
            domain = _common.canonical_domain(value)
            if domain:
                record_domains.add(domain)
    for alias in document.get("alias_domains") or []:
        domain = _common.canonical_domain(alias) if isinstance(alias, str) else None
        if domain:
            record_domains.add(domain)
    threshold = int(config["evidence"].get("stale_threshold_days", 730))
    # evidence-policy 5.3 lists both as record-level staleness signals, so an old item on such a
    # record is already flagged and EVI-06 has nothing to add.
    record_stale = document.get("stale") is True or document.get("operational_status") in (
        "closed",
        "unreachable",
    )
    for item in evidence:
        if not isinstance(item, dict):
            continue
        eid = item.get("evidence_id")
        tier = item.get("source_tier")
        source_type = item.get("source_type")
        official = item.get("is_official") is True
        # EVI-01: tier 1 <=> the company's own site, read on a channel it controls.
        tier_one_shape = source_type == TIER_ONE_SOURCE_TYPE and official
        if (tier == 1) != tier_one_shape and isinstance(tier, int):
            out.add(
                "EVI-01",
                index,
                ident,
                "evidence %s is source_tier %s / source_type %s / is_official %s; tier 1 is "
                "exactly an official_site item with is_official true (evidence-policy 4.1)"
                % (eid, tier, source_type, item.get("is_official")),
            )
        if source_type == "third_party" and official:
            out.add(
                "EVI-01",
                index,
                ident,
                "evidence %s is third_party but is_official is true; a third party's listing is "
                "never official for the company (PRD EVID-02)" % (eid,),
            )
        # EVI-02: the domain an item names is the domain it was read on, and an official
        # site item was read on the record's own domain.
        url_domain = _common.canonical_domain(item.get("source_url") or "")
        declared = item.get("source_domain")
        if isinstance(declared, str) and declared and url_domain:
            if _common.canonical_domain(declared) != url_domain:
                out.add(
                    "EVI-02",
                    index,
                    ident,
                    "evidence %s source_domain %s is not the domain of its source_url (%s)"
                    % (eid, declared, url_domain),
                )
        if source_type == TIER_ONE_SOURCE_TYPE and official and record_domains and url_domain:
            if url_domain not in record_domains:
                out.add(
                    "EVI-02",
                    index,
                    ident,
                    "evidence %s is an official_site item on %s, which is not the record's "
                    "domain (%s) or an alias_domains entry"
                    % (eid, url_domain, ", ".join(sorted(record_domains))),
                )
        # EVI-03: an inference never outranks the ceiling evidence-policy 4.3 puts on it.
        confidence = item.get("confidence")
        if item.get("inferred") is True and _is_num(confidence):
            if _dec(confidence) > INFERRED_CONFIDENCE_CAP:
                out.add(
                    "EVI-03",
                    index,
                    ident,
                    "evidence %s is inferred with confidence %s; an inferred item is capped at "
                    "%s (evidence-policy 4.3)" % (eid, confidence, INFERRED_CONFIDENCE_CAP),
                )
        # EVI-06: an item older than stale_threshold_days that nobody flagged.
        if as_of and not record_stale and item.get("stale") is not True:
            age = _common._evidence_age_days(item, as_of)
            if age is not None and age > threshold:
                out.warn(
                    "EVI-06",
                    index,
                    ident,
                    "evidence %s source_date %s is %d days before as_of %s (stale_threshold_days "
                    "%d) but neither the item nor the record is stale; set stale true or "
                    "re-verify the source (evidence-policy 5.3)"
                    % (eid, item.get("source_date"), age, as_of, threshold),
                )

    # EVI-04 / EVI-05: derived values are recomputed, never trusted.
    if document.get("score_version") in (None, "unscored") or not as_of:
        return
    dimension_scores = document.get("dimension_scores")
    recorded_quality = (
        dimension_scores.get("evidence_quality") if isinstance(dimension_scores, dict) else None
    )
    claims = config["evidence"]["material_claims"].get(kind, [])
    try:
        quality = _common.evidence_quality(document, claims, as_of, config=config)
    except _common.KbtmError:
        return
    if _is_num(recorded_quality) and abs(_dec(recorded_quality) - _dec(quality)) > Decimal(1):
        out.add(
            "EVI-04",
            index,
            ident,
            "dimension_scores.evidence_quality is %s but the evidence recomputes to %s "
            "(_common.evidence_quality, as_of %s)" % (recorded_quality, quality, as_of),
        )
    recorded_confidence = document.get("confidence")
    if _is_num(recorded_confidence):
        expected = _common.record_confidence(document, kind, quality, config)
        if abs(_dec(recorded_confidence) - _dec(expected)) > Decimal("0.01"):
            out.add(
                "EVI-05",
                index,
                ident,
                "confidence is %s but the record recomputes to %s (_common.record_confidence)"
                % (recorded_confidence, expected),
            )


def _inv_entity(document, kind, index, ident, config, out, as_of=None):
    """Buyer / seller record invariants."""
    claims = config["evidence"]["material_claims"].get(kind, [])
    evidence = document.get("evidence")
    evidence = evidence if isinstance(evidence, list) else []
    # INV-01: a material claim is evidenced, or it is "unknown"/absent. No third
    # possibility -- and "evidenced" means the evidence carries the value, not the key.
    _inv_claim_values(document, kind, index, ident, claims, evidence, out)
    _inv_evidence_ids(document, index, ident, evidence, out)
    record_as_of = document.get("as_of") if isinstance(document.get("as_of"), str) else as_of
    _inv_evidence_integrity(document, kind, index, ident, evidence, config, record_as_of, out)

    score_version = document.get("score_version")
    dimension_scores = document.get("dimension_scores")

    # INV-37: state entry conditions hold within the document. Checked before the
    # unscored early return below, which used to let an unscored record claim QUALIFIED.
    status = document.get("status")
    if status in ("QUALIFIED", "MATCH_CANDIDATE", "READY_FOR_REVIEW"):
        if score_version in (None, "unscored") or not dimension_scores:
            out.add(
                "INV-37",
                index,
                ident,
                "status %s requires a scored record with dimension_scores" % (status,),
            )
    if status == "DISCOVERED" and not evidence:
        out.add("INV-37", index, ident, "status DISCOVERED requires at least one evidence item")
    if status == "VERIFIED":
        ok = [
            item
            for item in evidence
            if isinstance(item, dict)
            and item.get("claim") in claims
            and isinstance(item.get("source_tier"), int)
            and item["source_tier"] <= 3
            and item.get("inferred") is not True
            and item.get("stale") is not True
        ]
        if not ok:
            out.add(
                "INV-37",
                index,
                ident,
                "status VERIFIED requires a material claim evidenced at source_tier <= 3 by an "
                "item that is neither inferred nor stale",
            )

    # INV-23
    if not document.get("schema_version"):
        out.add("INV-23", index, ident, "schema_version is absent")
    if score_version == "unscored":
        if document.get("qualification_score") not in (0, None):
            out.add(
                "INV-23",
                index,
                ident,
                "score_version is \"unscored\" but qualification_score is %r"
                % (document.get("qualification_score"),),
            )
        if document.get("dimension_scores"):
            out.add(
                "INV-23", index, ident, "score_version is \"unscored\" but dimension_scores is present"
            )
        return

    _inv_discovery_lines(document, index, ident, out)

    dimension_scores = document.get("dimension_scores")
    if not isinstance(dimension_scores, dict):
        return

    # INV-04: the recorded total is the weighted sum of the recorded dimensions.
    weights = config[kind]["weights"]
    key_map = config[kind]["dimension_score_key_map"]
    total = Decimal(0)
    complete = True
    for dimension, weight in weights.items():
        out_key = key_map.get(dimension, dimension)
        value = dimension_scores.get(out_key)
        if not _is_num(value):
            complete = False
            break
        total += _dec(value) * _dec(weight)
    if complete:
        expected = _dec(_common.round_half_up(total / Decimal(100)))
        actual = document.get("qualification_score")
        if not _is_num(actual) or abs(_dec(actual) - expected) > Decimal(1):
            out.add(
                "INV-04",
                index,
                ident,
                "qualification_score %r does not match the weighted sum of dimension_scores (%s)"
                % (actual, expected),
            )



# INV-19 (BUILD-CONTRACT 10.5, PRD 15.1 criterion 2, PRD 12.2) -- every rendered candidate
# must show a score, a type, a `Why:` line with >= 2 reasons, a contact channel, an
# `Evidence:` line and a `Missing:` line; a DISCOVERY candidate additionally shows
# `Website:` and `Country:`. The renderer can only show what the document carries, so the
# machine half of INV-19 checks that each of those lines has a source. It checks PRESENCE,
# never non-emptiness: a present-and-empty list is a verified "none" (E08) and renders as
# `none evidenced`, which is a legal line.


def _inv_discovery_lines(document, index, ident, out):
    """INV-19 for a scored buyer/seller record rendered under 10.1 / 10.2."""
    for field in (
        "qualification_score",
        "company_type",
        "country",
        "evidence",
        "missing",
        "dimension_details",
    ):
        if field not in document:
            out.add(
                "INV-19",
                index,
                ident,
                "%s is absent, so the 10.1/10.2 block cannot render its required line" % (field,),
            )
    # `website` is absent exactly when no website was discoverable, and BUILD-CONTRACT 8.1
    # makes that the only case in which canonical_domain is "unknown". A known domain with
    # no website therefore leaves the `Website:` line with no source.
    if "website" not in document and document.get("canonical_domain") != _common.UNKNOWN:
        out.add(
            "INV-19",
            index,
            ident,
            "website is absent while canonical_domain is %r; canonical_domain is \"unknown\" "
            "only when no website was discoverable (BUILD-CONTRACT 8.1), so the Website: line "
            "has no source" % (document.get("canonical_domain"),),
        )
    # An absent contact_channels renders `Contact: unknown`, which is legal only when the
    # unknown was taken and recorded -- INV-03 puts that criterion's label in missing[].
    if "contact_channels" not in document and not (document.get("missing") or []):
        out.add(
            "INV-19",
            index,
            ident,
            "contact_channels is absent and missing[] names no gap, so the Contact: line "
            "would render unknown with nothing accounting for it (INV-03)",
        )
    details = document.get("dimension_details")
    if isinstance(details, list):
        evidenced = [
            entry for entry in details if isinstance(entry, dict) and entry.get("evidence_ids")
        ]
        if len(evidenced) < 2:
            out.add(
                "INV-19",
                index,
                ident,
                "only %d dimension_details entries carry evidence_ids; the Why: line needs "
                ">= 2 evidenced reasons (PRD 15.1)" % (len(evidenced),),
            )


def _inv_candidate_lines(candidate, cid, index, ident, out):
    """INV-19 for one hard-filter-passing match candidate rendered under 10.3."""
    for field in (
        "match_score",
        "company_type",
        "oem_odm",
        "evidence_ids",
        "missing",
        "rationale",
    ):
        if field not in candidate:
            out.add(
                "INV-19",
                index,
                ident,
                "%s %s is absent, so the 10.3 block cannot render its required line"
                % (cid, field),
            )
    if "website" not in candidate and candidate.get("canonical_domain") != _common.UNKNOWN:
        out.add(
            "INV-19",
            index,
            ident,
            "%s carries no website display carry-over while canonical_domain is %r "
            "(BUILD-CONTRACT 6.3)" % (cid, candidate.get("canonical_domain")),
        )
    if "contact_channels" not in candidate and not (candidate.get("missing") or []):
        out.add(
            "INV-19",
            index,
            ident,
            "%s carries no contact_channels display carry-over and missing[] names no gap, "
            "so the Contact: line would render unknown with nothing accounting for it "
            "(BUILD-CONTRACT 6.3, INV-03)" % (cid,),
        )


def _inv_match_result(document, index, ident, config, out):
    results = document.get("results") or []
    excluded = document.get("excluded") or []
    no_match = document.get("no_match") or {}
    max_delta = int(config["match"]["rerank"]["max_delta"])

    # INV-23
    if document.get("score_version") == "unscored":
        out.add("INV-23", index, ident, "a match-result may never carry score_version \"unscored\"")

    # INV-21
    if not results and no_match.get("is_no_match") is not True:
        out.add(
            "INV-21",
            index,
            ident,
            "results is empty but no_match.is_no_match is not true (PRD T10)",
        )
    if no_match.get("is_no_match") is True:
        if results:
            out.add("INV-21", index, ident, "no_match.is_no_match is true but results is not empty")
        if not str(no_match.get("reason") or "").strip():
            out.add("INV-21", index, ident, "no_match.reason is empty")

    # INV-08 / INV-32: the rerank never resurrects a hard-filter failure.
    result_ids = [item.get("seller_id") for item in results]
    excluded_ids = set(item.get("seller_id") for item in excluded)
    overlap = sorted(set(result_ids) & excluded_ids)
    if overlap:
        out.add(
            "INV-08",
            index,
            ident,
            "seller(s) %s appear in both results and excluded" % (", ".join(str(x) for x in overlap),),
        )
    for position, candidate in enumerate(excluded):
        if "match_score" in candidate:
            out.add(
                "INV-32",
                index,
                ident,
                "excluded[%d] %s carries a match_score" % (position, candidate.get("seller_id")),
            )
        if not candidate.get("failed_rules"):
            out.add(
                "INV-32",
                index,
                ident,
                "excluded[%d] %s carries no failed_rules" % (position, candidate.get("seller_id")),
            )

    evidence_index = document.get("evidence_index") or []
    known_ids = set(
        entry.get("evidence_id") for entry in evidence_index if isinstance(entry, dict)
    )

    previous = None
    for position, candidate in enumerate(results):
        cid = candidate.get("seller_id")
        rerank = candidate.get("rerank") or {}
        delta = rerank.get("delta")
        if _is_num(delta) and abs(_dec(delta)) > Decimal(max_delta):
            out.add(
                "INV-08",
                index,
                ident,
                "%s rerank delta %s exceeds match.rerank.max_delta %d" % (cid, delta, max_delta),
            )
        if rerank.get("applied") is True:
            if not str(rerank.get("rationale") or "").strip():
                out.add("INV-08", index, ident, "%s rerank applied with no rationale" % (cid,))
            if not (rerank.get("evidence_ids") or []):
                out.add("INV-08", index, ident, "%s rerank applied with no evidence_ids" % (cid,))
        elif delta not in (0, None):
            out.add("INV-08", index, ident, "%s rerank not applied but delta is %r" % (cid, delta))

        base = candidate.get("base_score")
        match_score = candidate.get("match_score")
        if _is_num(base) and _is_num(match_score) and _is_num(delta):
            expected = max(0, min(100, int(_dec(base) + _dec(delta))))
            if int(_dec(match_score)) != expected:
                out.add(
                    "INV-05",
                    index,
                    ident,
                    "%s match_score %s != clamp(base_score %s + delta %s, 0, 100)"
                    % (cid, match_score, base, delta),
                )
        contributions = candidate.get("weighted_contributions")
        if isinstance(contributions, dict) and _is_num(base):
            total = sum((_dec(v) for v in contributions.values() if _is_num(v)), Decimal(0))
            if abs(total - _dec(base)) > Decimal("0.5"):
                out.add(
                    "INV-05",
                    index,
                    ident,
                    "%s sum(weighted_contributions) %s is more than 0.5 from base_score %s"
                    % (cid, total, base),
                )

        hard_filter = candidate.get("hard_filter") or {}
        if hard_filter.get("passed") is True:
            _inv_candidate_lines(candidate, cid, index, ident, out)
            rationale = candidate.get("rationale") or []
            evidenced = [
                item
                for item in rationale
                if isinstance(item, dict) and item.get("evidence_ids")
            ]
            if len(evidenced) < 2:
                out.add(
                    "INV-20",
                    index,
                    ident,
                    "%s cleared the hard filter with %d evidenced rationale item(s); 2 are required "
                    "(PRD 15.3)" % (cid, len(evidenced)),
                )
            if hard_filter.get("failed_rules"):
                out.add(
                    "INV-06",
                    index,
                    ident,
                    "%s hard_filter.passed is true but failed_rules is not empty" % (cid,),
                )
        # INV-07: an unknown input never causes a failure, and every skipped rule is paired.
        failed_ids = set(
            rule.get("rule_id") for rule in (hard_filter.get("failed_rules") or []) if isinstance(rule, dict)
        )
        skipped_ids = set(hard_filter.get("rules_skipped_unknown") or [])
        both = sorted(failed_ids & skipped_ids)
        if both:
            out.add(
                "INV-07",
                index,
                ident,
                "%s rules %s are both failed and skipped-for-unknown" % (cid, ", ".join(both)),
            )
        penalties = candidate.get("unknown_penalty_applied") or []
        penalty_criteria = set(
            entry.get("criterion_id") for entry in penalties if isinstance(entry, dict)
        )
        for rule_id in sorted(skipped_ids):
            paired = rule_id in penalty_criteria or (
                HF_PENALTY_CRITERION.get(rule_id) in penalty_criteria
            )
            if not paired:
                out.add(
                    "INV-07",
                    index,
                    ident,
                    "%s lists %s in rules_skipped_unknown with no matching unknown_penalty record"
                    % (cid, rule_id),
                )
        # INV-03 on the candidate.
        candidate_missing = candidate.get("missing")
        if isinstance(candidate_missing, list):
            for entry in penalties:
                if isinstance(entry, dict) and entry.get("label") not in candidate_missing:
                    out.add(
                        "INV-03",
                        index,
                        ident,
                        "%s unknown_penalty label '%s' is absent from missing[]"
                        % (cid, entry.get("label")),
                    )

        # INV-22: every referenced evidence id resolves in evidence_index.
        referenced = []
        for item in candidate.get("rationale") or []:
            referenced.extend(item.get("evidence_ids") or [])
        for item in candidate.get("risks") or []:
            referenced.extend(item.get("evidence_ids") or [])
        referenced.extend((candidate.get("rerank") or {}).get("evidence_ids") or [])
        for detail in candidate.get("component_details") or []:
            referenced.extend(detail.get("evidence_ids") or [])
        if referenced and not evidence_index:
            out.add(
                "INV-22",
                index,
                ident,
                "%s references evidence ids but the document carries no evidence_index" % (cid,),
            )
        for reference in sorted(set(referenced)):
            if evidence_index and reference not in known_ids:
                out.add(
                    "INV-22",
                    index,
                    ident,
                    "%s references evidence_id '%s', which resolves nowhere in this document"
                    % (cid, reference),
                )

        # INV-30: score desc, then canonical_domain asc with "unknown" last.
        key = (
            -int(_dec(match_score)) if _is_num(match_score) else 0,
            candidate.get("canonical_domain") == _common.UNKNOWN,
            str(candidate.get("canonical_domain") or ""),
        )
        if previous is not None and key < previous:
            out.add(
                "INV-30",
                index,
                ident,
                "results[%d] %s breaks the (match_score desc, canonical_domain asc) order"
                % (position, cid),
            )
        previous = key

    summary = document.get("summary") or {}
    considered = summary.get("candidates_considered")
    passed = summary.get("passed_hard_filter")
    returned = summary.get("returned")
    if _is_num(considered) and _is_num(passed) and _is_num(returned):
        if not (considered >= passed >= returned):
            out.add(
                "INV-30",
                index,
                ident,
                "summary must satisfy candidates_considered >= passed_hard_filter >= returned "
                "(got %s, %s, %s)" % (considered, passed, returned),
            )
    # excluded_count is a RUN counter: it may exceed len(excluded) when --no-include-excluded
    # suppressed the array, so only a count below the emitted array is an inconsistency.
    # Against candidates_considered - passed_hard_filter it is an EXACT identity
    # (SCORING-CONTRACT 3.5): passed_hard_filter is taken after the HF-00 rendering gate has
    # moved its candidates into excluded[], so a gated candidate is already counted on both
    # sides and carves nothing out of the identity.
    if _is_num(summary.get("excluded_count")):
        if summary["excluded_count"] < len(excluded):
            out.add(
                "INV-32",
                index,
                ident,
                "summary.excluded_count %s is smaller than len(excluded) %d"
                % (summary["excluded_count"], len(excluded)),
            )
        if _is_num(considered) and _is_num(passed):
            if summary["excluded_count"] != considered - passed:
                out.add(
                    "INV-32",
                    index,
                    ident,
                    "summary.excluded_count %s != candidates_considered - passed_hard_filter "
                    "(%s) (SCORING-CONTRACT 3.5)"
                    % (summary["excluded_count"], considered - passed),
                )


def _run_invariants(document, kind, index, config, out, as_of=None):
    ident = _doc_id(document)
    if not isinstance(document, dict):
        return
    _inv_generic(document, kind, index, ident, out)
    if kind in ("buyer", "seller"):
        _inv_entity(document, kind, index, ident, config, out, as_of)
    elif kind == "match-result":
        _inv_match_result(document, index, ident, config, out)


# --------------------------------------------------------------------------------------
# outreach drafts (references/output-format.md 10.4, templates/*_outreach.md)
# --------------------------------------------------------------------------------------
#
# Mode 4 produces text, not JSON, so the draft is parsed into the outreach-draft.schema.json
# shape and then checked rule by rule. Rule ids are listed in references/output-format.md
# 10.4.1. validate_outreach_draft() is importable so the adapter can run the same check
# before it queues a draft (adapters/tradewith_adapter.py _normalize_draft).

OUTREACH_KIND = "outreach-draft"
COMPLIANCE_CONFIG_PATH = os.path.join(_common.SCHEMA_DIR, "compliance.config.json")
LEGAL_NOTICES_PATH = os.path.join(_common.PACKAGE_ROOT, "templates", "legal_notices.md")

DRAFT_HEADER_RE = re.compile(r"^Outreach Draft — (Buyer|Seller) — (.+)$")
DRAFT_STATUS_LINE = "Status: READY_FOR_REVIEW"
DRAFT_AUTO_SEND_LINE = "Auto-send: false"
DRAFT_APPROVAL_LINE = "Approval: required (human)"
DRAFT_CHANNEL_RE = re.compile(r"^Recipient channel: (\S+) — (.+)$")
DRAFT_NEXT_ACTION = (
    "Next action: human review, then APPROVED_FOR_OUTREACH in the application layer"
)
DRAFT_TRAILER = ("status: READY_FOR_REVIEW", "auto_send: false", "manual_approval_required: true")
DRAFT_HEADINGS = (
    "Body",
    "Personalization facts",
    "Compliance checks",
    "Reviewer checklist",
    "Required legal notices",
)
# output-format.md 10.4: the six items every draft's checklist carries (templates add more).
DRAFT_BASE_CHECKLIST = (
    "- [ ] Every personalization fact traces to a cited source",
    '- [ ] No claim that a buyer is "currently looking" unless an RFQ id is cited',
    "- [ ] Any demand claim cites an `rfq_id` whose status is qualified / matching / proposal_open",
    "- [ ] MOQ, certifications, exclusivity and export markets stated only where evidenced",
    "- [ ] Contact channel is an official company channel",
    "- [ ] Jurisdiction rules confirmed before sending",
)
DRAFT_FACT_RE = re.compile(
    r"^- (?P<fact>.+?) — \[(?P<url>https?://[^\]\s]+)\] "
    r"\(observed (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\)$"
)
DRAFT_JURISDICTION_RE = re.compile(r"^- Jurisdiction: (.+) — direct-marketing review: (\S+)$")
DRAFT_AD_LABEL_RE = re.compile(r"^- Advertising label / opt-out required: (\S+)$")
DRAFT_PERSONAL_DATA_RE = re.compile(r"^- Personal data used: (.+)$")
DRAFT_CLAIMS_RE = re.compile(r"^- Claims verified against evidence: (\S+)$")
DRAFT_PERSONAL_DATA_LITERAL = "none (company-level channel only)"
DRAFT_SUBJECT_MAX_CHARS = 60
# templates/buyer_outreach.md 1.7 and templates/seller_outreach.md 1.7.
DRAFT_BODY_MAX_WORDS = {"Buyer": 150, "Seller": 180}

# INV-34 / R10.4.3: a live-demand claim is licensed only by an open RFQ, cited with its
# status and as-of date. The phrase list is the seller_outreach.md section 8 table and the
# outreach-guidelines.md 7.1 rows, widened to third-party interest and action claims. It is a
# floor under the reviewer, never a licence for wording it fails to match.
LIVE_DEMAND_RFQ_STATES = ("qualified", "matching", "proposal_open")
# The patterns are anchored on a THIRD PARTY said to want the recipient (a buyer, distributor,
# client ...), or on urgency. A sender describing its own search ("We are currently looking for
# UAE distributors", "저희는 유통 파트너를 찾고 있습니다"), an invitation ("관심이 있는 카테고리를
# 알려 주시면") or a negative ("회신 대기 중인 문의는 없습니다") claims no demand and is not matched.
_EN_THIRD_PARTY = (r"(?:buyer|distributor|importer|retailer|wholesaler|purchaser|client|customer|"
                   r"partner|company|companies|chain)s?")
_KO_THIRD_PARTY = r"(?:바이어|유통사|유통업체|고객사|수입사|수입업체|구매사|거래처|클라이언트)(?:들)?"
LIVE_DEMAND_PATTERNS = (
    _EN_THIRD_PARTY + r"(?:\s+(?:who|that))?(?:\s+(?:is|are)|'re|'s)?\s+(?:(?:also|still|now)\s+)?"
    r"(?:currently|actively)\s+(?:looking|searching|seeking|sourcing)",
    # a modifier between the third party and the verb ("A distributor in Dubai is actively
    # looking for your sunscreen") when the object is the recipient.
    _EN_THIRD_PARTY + r"\b(?:\s+[^\s.!?]+){0,5}?\s+(?:is|are)\s+(?:(?:also|still|now|currently|actively)\s+)*"
    r"(?:looking|searching|seeking|sourcing)\s+for\s+(?:you|your)\b",
    _EN_THIRD_PARTY + r"\b(?:\s+[^\s.!?]+){0,5}?\s+(?:wants?|needs?)\s+(?:you|your)\b",
    r"\b(?:they|he|she|someone)(?:\s+(?:is|are)|'re|'s)\s+(?:(?:also|still|now)\s+)?"
    r"(?:currently|actively)\s+(?:looking|searching|seeking|sourcing)",
    r"we\s+have\s+(?:a\s+|several\s+|many\s+|\d+\s+)?(?:\w+\s+)?buyers?\b",
    r"active\s+demand",
    r"buyers?\s+(?:is|are)\s+(?:\w+\s+)?(?:searching|waiting|looking|interested)",
    r"(?:a\s+)?buyers?\s+(?:is\s+|are\s+)?waiting\b",
    r"orders?\s+(?:is|are)\s+waiting",
    r"several\s+buyers",
    r"waiting\s+for\s+(?:a\s+)?(?:supplier|manufacturer|partner)s?\s+like\s+you",
    r"looking\s+for\s+(?:a\s+)?(?:supplier|manufacturer|partner|brand)s?\s+like\s+(?:you|yours)",
    r"asked\s+(?:us\s+)?(?:specifically\s+)?about\s+your",
    _EN_THIRD_PARTY + r"\s+(?:has\s+|have\s+)?(?:\w+\s+)?(?:asked|requested|enquired|inquired)\s+"
    r"(?:us\s+)?(?:\w+\s+)?(?:(?:about|for|after)\s+)?(?:you|your)\b",
    r"(?:is|are)\s+interested\s+in\s+your",
    r"(?:has|have)\s+(?:shown|expressed)\s+(?:an\s+)?interest",
    r"competitors\s+have\s+already",
    r"limited\s+slots",
    r"only\s+\d+\s+(?:\w+\s+){0,2}slots?",
    r"closing\s+soon",
    r"last\s+chance",
    r"respond\s+within\s+\d+",
    r"exclusive\s+supply\s+opportunity",
    r"place\s+an\s+order\s+immediately",
    r"guarantee\s+you\s+orders",
    _KO_THIRD_PARTY + r"(?:이|가|은|는|께서)\s*(?:[^\s.!?]+\s+){0,4}?찾(?:고|는|으)",
    r"(?:찾고\s*있는|찾는)\s*" + _KO_THIRD_PARTY,
    _KO_THIRD_PARTY + r"(?:이|가|은|는|께서)\s*(?:[^\s.!?]+\s+){0,3}?대기",
    r"(?:주문|발주)(?:이|가)\s*대기",
    r"바이어를\s*보유",
    r"수요가\s*많",
    _KO_THIRD_PARTY + r"(?:이|가|은|는|께서)?\s*(?:[^\s.!?]+\s+){0,4}?관심(?:이|을)",
    r"관심(?:이|을)?\s*(?:있는|보이는|보인|가진)\s*" + _KO_THIRD_PARTY,
    r"관심을\s*(?:보이|보였|보여|표명|가지고\s*있)",
    r"문의(?:가|를)\s*(?:왔|받았|주셨|하셨|해\s*왔)",
    r"즉시\s*발주",
    r"독점\s*공급\s*기회",
    r"마감\s*임박",
    r"곧\s*마감",
    r"마지막\s*기회",
    r"지금이\s*적기",
)
_LIVE_DEMAND_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in LIVE_DEMAND_PATTERNS)
# templates/buyer_outreach.md section 3: the recipient's OWN public sourcing action ("Your page,
# dated 2026-07-30, says you are actively looking for Korean sun-care brands") is licensed by a
# dated `sourcing_signals` fact instead of an RFQ. See recipient_sourcing_licensed().
RECIPIENT_SOURCING_PATTERNS = (
    r"\byou(?:\s+are|'re)\s+(?:(?:also|still|now)\s+)?(?:currently|actively)\s+"
    r"(?:looking|searching|seeking|sourcing)",
    r"귀사\S*\s+(?:[^\s.!?]+\s+){0,5}?찾고\s*있",
)
_RECIPIENT_SOURCING_RE = tuple(re.compile(p, re.IGNORECASE) for p in RECIPIENT_SOURCING_PATTERNS)
SOURCING_SIGNAL_CLAIM = "sourcing_signals"
SOURCING_SIGNAL_MAX_TIER = 2
_ISO_DATE_RE = re.compile(r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})\b")
_RFQ_CITATION_RE = re.compile(r"RFQ\s*#\s*([A-Za-z0-9][A-Za-z0-9._-]*)", re.IGNORECASE)
_RFQ_STATUS_RE = re.compile(
    r"(?:status|상태)\W{0,3}(?:is\s+)?(qualified|matching|proposal_open|draft|matched|closed)\b",
    re.IGNORECASE,
)
_RFQ_DATE_RE = re.compile(r"(?:as\s+of|기준일)\W{0,3}([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)

# outreach-guidelines.md 7.4 and seller_outreach.md 5.1: "ignore this" is not an opt-out.
FAKE_OPT_OUT_PATTERNS = (
    r"ignor(?:e|ing)\s+this\s+(?:e-?mail|mail|message)",
    r"unsubscribe\s+by\s+ignoring",
    r"무시하셔도",
    r"무시해\s*주",
    r"무시하시면",
)
_FAKE_OPT_OUT_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in FAKE_OPT_OUT_PATTERNS)

_PLACEHOLDER_RE = (re.compile(r"\{\{[^{}]*\}\}"), re.compile(r"\[\[\s*ev\s*:", re.IGNORECASE))

# INV-31: company-level channels only. Free-mail domains, mobile numbers and an English
# honorific with a capitalised name are unambiguous and fail. The two SHAPE heuristics -- a
# local part of two or more name-like segments with no role word ("minji.kim@"), and a Korean
# surname syllable before a title or 님 ("김민지 과장님") -- also fire on honest role wording,
# so they are warnings that never fail, not even under --strict ("strict": false). The role
# lexicons below and the target's own company names keep common role wording from warning.
ROLE_LOCAL_PARTS = frozenset(
    """info sales export exports overseas global partner partners partnership partnerships
    sourcing contact contacts hello support trade wholesale marketing business biz cs help
    office admin enquiry enquiries inquiry inquiries pr press order orders purchasing
    procurement brand brands team desk service services oem odm import imports buyer buying
    supply supplier suppliers distribution retail account accounts customer care general mail
    noreply reply international cosmetic cosmetics beauty b2b hq main corp company dept
    department bd development new opt out optout unsubscribe kr en jp ae uk us sg
    lab labs rnd rd research qa qc quality factory plant production manufacturing mfg
    korea korean asia asean europe america americas usa mena gulf gcc japan china world
    worldwide intl kbeauty kbeaute skincare skin makeup official store stores shop online
    ecommerce ecom media design creative content social sns hr recruit recruiting jobs careers
    legal compliance privacy finance billing invoice invoices accounting payments logistics
    shipping cargo trading group inc ltd co web webmaster it tech dev data ops operations
    management manager director head exec executive assistant reception front client clients
    member members program affiliate affiliates distributor distributors dealer dealers agent
    agents agency franchise retailer channel channels b2c private label product products
    bizdev growth news newsletter event events expo fair show ask question questions feedback
    request quote quotes rfq sample samples purchase vendor vendors regulatory registration
    certification export-sales exportsales globalsales trade-desk tradedesk""".split()
)
_KO_ROLE_WORDS = frozenset(
    """기업 이메일 고객 담당 관계 회원 업체 제조 수출 해외 공급 구매 영업 유통 본사 지사 전체 각사
    귀사 당사 저희 연락 기술 개발 품질 마케 한국 서울 무역 조달 주식 유한 대표 전무 상무
    기획 홍보 서비스 연구 마케팅 수입 국내 사업 전략 운영 관리 인사 총무 재무 회계 법무 생산
    물류 디자인 브랜드 상품 제품 성분 원료 소싱 지원 전문가 전문 담당자 책임자 관계자 대표자
    이사 파트너 제휴 협력 신규 글로벌 온라인 채널 세일즈 바이어 구매자 주신 계신 하신 보신
    오신 맡은 문의 부서 본부 센터 연구소 사업부 영업부 여러분 선생 교수 박사 원장 수석 선임
    책임 주임 전임 신사업 경영 안전 허가 인증 규제 통관 선적 배송 수주 발주 견적 계약 정산
    결제 상담 상담원 매장 점장 지점 지점장 공장 공장장 소장 연구원 개발자 임원 직원 사원 팀원
    파트 파트장 사업팀 수출입 국제 통상 이사장 부사장 사장 회장 부회장 대리점 총판 도매 소매
    수입사 유통사 제조사 고객사 거래처 기업부 전략팀 해외사 구매팀 담당관 실무 실무자 총괄""".split()
)
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
MOBILE_PATTERNS = (
    r"(?<![\d+])01[016789][\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)",
    r"\+82[\s.-]?(?:\(0\)[\s.-]?)?0?1[016789][\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)",
)
_MOBILE_RE = tuple(re.compile(pattern) for pattern in MOBILE_PATTERNS)
_EN_HONORIFIC_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Mx|Miss)\.?\s+[A-Z][a-z]+")
_KO_SURNAMES = (
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지엄채원"
    "천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용"
)
_KO_TITLE_RE = re.compile(
    r"(?<![가-힣])([%s][가-힣]{1,2})\s?"
    r"(?:과장|부장|대리|차장|팀장|대표|이사|실장|매니저|주임|사원|선생|사장|상무|전무|본부장)님?"
    % _KO_SURNAMES
)
_KO_NAME_NIM_RE = re.compile(r"(?<![가-힣])([%s][가-힣]{2})\s?(?:님|씨)(?![가-힣])" % _KO_SURNAMES)
_KO_UNIT_SUFFIXES = ("팀", "부", "실", "과", "처", "소", "사")

_NOTICE_HEADING_RE = re.compile(r"^###\s+[0-9.]+\s+`([A-Z]{2}\.[a-z_]+)`\s*$")
_NOTICE_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


def load_compliance_config(path=None):
    """schemas/compliance.config.json: the jurisdiction x channel lookup (not legal advice)."""
    import json

    resolved = path or COMPLIANCE_CONFIG_PATH
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError) as exc:
        raise _common.ConfigError("cannot read compliance config %s: %s" % (resolved, exc))


def load_legal_notices(path=None):
    """{"KR.corporate_email": [block lines], ...} from templates/legal_notices.md section 3."""
    resolved = path or LEGAL_NOTICES_PATH
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            lines = handle.read().split("\n")
    except (IOError, OSError) as exc:
        raise _common.ConfigError("cannot read legal notices %s: %s" % (resolved, exc))
    blocks = {}
    key = None
    fence = capture = False
    body = []
    for value in lines:
        if value.strip().startswith("```"):
            if fence:
                if capture:
                    blocks[key] = body
                    key = None
                fence = capture = False
            else:
                fence = True
                capture = key is not None and key not in blocks
                body = []
            continue
        if fence:
            if capture:
                body.append(value.rstrip())
            continue
        heading = _NOTICE_HEADING_RE.match(value)
        if heading:
            key = heading.group(1)
    return blocks


def _trim_blank(lines):
    lines = list(lines)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def parse_outreach_draft(text):
    """Parse a rendered 10.4 draft into the outreach-draft.schema.json shape.

    Returns (draft, problems): problems is a list of (rule_id, message) for envelope structure
    the parser could not read. A draft that does not parse cannot be reviewed.
    """
    problems = []
    lines = _trim_blank(
        line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )
    draft = {"format": "markdown"}
    if not lines:
        return draft, [("DRAFT-01", "the draft is empty")]

    def line(number):
        return lines[number] if len(lines) > number else ""

    header = DRAFT_HEADER_RE.match(lines[0])
    if header:
        draft["side"], draft["company_name"] = header.group(1), header.group(2).strip()
    else:
        problems.append(
            ("DRAFT-01", "line 1 is %r; it must read 'Outreach Draft — Buyer|Seller — "
             "<company name>' (output-format 10.4)" % (lines[0][:80],))
        )
    if line(1) == DRAFT_STATUS_LINE:
        draft["status"] = "READY_FOR_REVIEW"
    else:
        problems.append(("INV-09", "line 2 is %r; it must be exactly %r (R10.4.1)"
                         % (line(1)[:80], DRAFT_STATUS_LINE)))
    if line(2) == DRAFT_AUTO_SEND_LINE:
        draft["auto_send"] = False
    else:
        problems.append(("INV-09", "line 3 is %r; it must be exactly %r (R10.4.1)"
                         % (line(2)[:80], DRAFT_AUTO_SEND_LINE)))
    if line(3) == DRAFT_APPROVAL_LINE:
        draft["approval"] = "required (human)"
    else:
        problems.append(("DRAFT-01", "line 4 is %r; it must be exactly %r"
                         % (line(3)[:80], DRAFT_APPROVAL_LINE)))
    channel = DRAFT_CHANNEL_RE.match(line(4))
    if channel:
        draft["channel_type"], draft["channel_value"] = channel.group(1), channel.group(2).strip()
    else:
        problems.append(("DRAFT-01", "line 5 is %r; it must read 'Recipient channel: <type> — "
                         "<value>'" % (line(4)[:80],)))
    if line(5).startswith("Language: ") and line(5)[len("Language: "):].strip():
        draft["language"] = line(5)[len("Language: "):].strip()
    else:
        problems.append(("DRAFT-01", "line 6 must read 'Language: <language>'"))
    if line(6).startswith("Subject:"):
        draft["subject"] = line(6)[len("Subject:"):].strip()
    else:
        problems.append(("DRAFT-01", "line 7 must read 'Subject: <subject>'"))

    positions = {}
    next_action = None
    for number, value in enumerate(lines):
        if number < 7:
            continue
        if value in DRAFT_HEADINGS:
            if value in positions:
                problems.append(("DRAFT-01", "the %r heading appears more than once" % (value,)))
            else:
                positions[value] = number
        elif value == DRAFT_NEXT_ACTION and next_action is None:
            next_action = number
    if line(7).strip() or positions.get("Body") != 8:
        problems.append(("DRAFT-01", "line 8 must be blank and line 9 the 'Body' heading"))
    for heading in DRAFT_HEADINGS[:4]:
        if heading not in positions:
            problems.append(("DRAFT-01", "the %r heading is missing" % (heading,)))
    ordered = [positions[h] for h in DRAFT_HEADINGS[:3] if h in positions]
    if ordered != sorted(ordered):
        problems.append(("DRAFT-01", "Body, Personalization facts and Compliance checks must "
                         "appear in that order"))
    for later in DRAFT_HEADINGS[3:]:
        if later in positions and positions[later] < positions.get("Compliance checks", -1):
            problems.append(("DRAFT-01", "%r must come after Compliance checks" % (later,)))
    if next_action is None:
        problems.append(("DRAFT-01", "the literal line %r is missing" % (DRAFT_NEXT_ACTION,)))
    else:
        draft["next_action"] = DRAFT_NEXT_ACTION
        for heading, number in positions.items():
            if number > next_action:
                problems.append(("DRAFT-01", "%r appears after the Next action line" % (heading,)))
        tail = [value for value in lines[next_action + 1:] if value.strip()]
        if tail and tuple(tail) != DRAFT_TRAILER:
            problems.append(("INV-09", "the lines after Next action must be exactly the "
                             "three-line trailer %s" % (" / ".join(DRAFT_TRAILER),)))
        elif tail:
            draft["manual_approval_required"] = True

    stops = sorted(list(positions.values()) + [len(lines)]
                   + ([next_action] if next_action is not None else []))

    def section(heading):
        if heading not in positions:
            return None
        start = positions[heading]
        end = min(stop for stop in stops if stop > start)
        return _trim_blank(lines[start + 1:end])

    body = section("Body")
    if body is not None:
        draft["body"] = "\n".join(body)
    facts = []
    for value in section("Personalization facts") or []:
        if not value.strip():
            continue
        match = DRAFT_FACT_RE.match(value)
        if match:
            facts.append({"fact": match.group("fact"), "source_url": match.group("url"),
                          "observed_date": match.group("date")})
        else:
            problems.append(("DRAFT-04", "Personalization facts line %r is not '- <fact> — "
                             "[<source_url>] (observed YYYY-MM-DD)'" % (value[:80],)))
    if "Personalization facts" in positions:
        draft["personalization_facts"] = facts
    compliance = {}
    for value in section("Compliance checks") or []:
        if not value.strip():
            continue
        for regex, fields in (
            (DRAFT_JURISDICTION_RE, ("jurisdiction", "direct_marketing_review")),
            (DRAFT_AD_LABEL_RE, ("ad_label_required",)),
            (DRAFT_PERSONAL_DATA_RE, ("personal_data_used",)),
            (DRAFT_CLAIMS_RE, ("claims_verified",)),
        ):
            match = regex.match(value)
            if match:
                for position, field in enumerate(fields):
                    compliance[field] = match.group(position + 1).strip()
                break
        else:
            problems.append(("DRAFT-08", "Compliance checks line %r is not one of the four "
                             "lines of output-format 10.4" % (value[:80],)))
    if "Compliance checks" in positions:
        draft["compliance"] = compliance
    checklist = section("Reviewer checklist")
    if checklist is not None:
        draft["reviewer_checklist"] = [value for value in checklist if value.strip()]
    notices = section("Required legal notices")
    if notices is not None:
        draft["legal_notices"] = notices
    return draft, problems


def _draft_from_json(document):
    """A JSON draft (the adapter's queue shape): its draft_markdown is the reviewable text."""
    markdown = document.get("draft_markdown")
    if isinstance(markdown, str) and markdown.strip():
        parsed, problems = parse_outreach_draft(markdown)
        for field in ("side", "channel_type", "channel_value", "subject"):
            if field in document and field in parsed and str(document[field]).strip() != parsed[field]:
                problems.append(("DRAFT-01", "JSON %s %r disagrees with the draft_markdown "
                                 "envelope (%r)" % (field, document[field], parsed[field])))
        by_url = {}
        for fact in document.get("personalization_facts") or []:
            if isinstance(fact, dict) and fact.get("evidence_id"):
                by_url.setdefault(_url_key(fact.get("source_url")), str(fact["evidence_id"]))
        for fact in parsed.get("personalization_facts") or []:
            if _url_key(fact["source_url"]) in by_url:
                fact["evidence_id"] = by_url[_url_key(fact["source_url"])]
        for field in ("entity_id", "rfq_id", "rfq_status", "rfq_as_of"):
            if document.get(field) is not None:
                parsed[field] = str(document[field])
        for field, value in (("status", "READY_FOR_REVIEW"), ("auto_send", False),
                             ("manual_approval_required", True)):
            if field in document and document[field] != value:
                problems.append(("INV-09", "JSON %s is %r; it must be %s"
                                 % (field, document[field], value)))
        parsed["format"] = "json"
        parsed["draft_markdown"] = markdown
        return parsed, problems, markdown
    parsed = dict(document)
    parsed["format"] = "json"
    problems = [("DRAFT-01", "the JSON draft carries no draft_markdown, so the 10.4 envelope, "
                 "compliance block and legal notices a reviewer reads cannot be checked")]
    return parsed, problems, None


def _url_key(url):
    return str(url or "").strip().rstrip("/")


def _name_variants(name):
    """The spellings a company name may take in a draft: as written, without a trailing
    parenthetical, and the parenthetical itself ("한빛 주식회사 (Hanbit Co., Ltd.)")."""
    if not isinstance(name, str) or not name.strip():
        return []
    text = name.strip()
    variants = [text]
    outer = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    if outer and outer not in variants:
        variants.append(outer)
    for inner in re.findall(r"\(([^)]*)\)", text):
        if inner.strip() and inner.strip() not in variants:
            variants.append(inner.strip())
    return variants


def _name_keys(name):
    keys = set()
    for variant in _name_variants(name):
        keys.add(variant.casefold())
        normalized = _common.normalize_company_name(variant)
        if normalized:
            keys.add(normalized)
    return keys


def _rfq_key(value):
    return re.sub(r"(?i)^rfq[-_#\s]*", "", str(value or "")).strip()


def _draft_target(records, draft):
    """Find the draft's target among the --record documents.

    Returns (found, evidence, rfq_documents): found is (target, kind, document) with kind
    buyer / seller / match_candidate / excluded, or None.
    """
    wanted_names = _name_keys(draft.get("company_name"))
    wanted_id = draft.get("entity_id")

    def is_target(item, name_field, id_field):
        if wanted_id and item.get(id_field) == wanted_id:
            return True
        return bool(wanted_names & _name_keys(item.get(name_field)))

    found = None
    evidence = []
    rfqs = []
    for doc in records:
        if not isinstance(doc, dict):
            continue
        if "rfq_id" in doc and "product_category" in doc and "match_run_id" not in doc:
            rfqs.append(doc)
            evidence.extend(item for item in doc.get("evidence") or [] if isinstance(item, dict))
            continue
        if "match_run_id" in doc or "no_match" in doc:
            for candidate in doc.get("results") or []:
                if found is None and isinstance(candidate, dict) and is_target(
                    candidate, "seller_name", "seller_id"
                ):
                    found = (candidate, "match_candidate", doc)
                    evidence.extend(
                        item for item in doc.get("evidence_index") or []
                        if isinstance(item, dict)
                        and item.get("owner_id") in (None, candidate.get("seller_id"))
                    )
            for candidate in doc.get("excluded") or []:
                if found is None and isinstance(candidate, dict) and is_target(
                    candidate, "seller_name", "seller_id"
                ):
                    found = (candidate, "excluded", doc)
            continue
        records_in = doc.get("records") if isinstance(doc.get("records"), list) else [doc]
        for record in records_in:
            if not isinstance(record, dict):
                continue
            kind = "buyer" if "buyer_id" in record else ("seller" if "seller_id" in record else None)
            if found is None and kind and is_target(record, "company_name", kind + "_id"):
                found = (record, kind, doc)
                evidence.extend(
                    item for item in record.get("evidence") or [] if isinstance(item, dict)
                )
        for record in doc.get("excluded") or []:
            if found is None and isinstance(record, dict) and is_target(record, "company_name", "id"):
                found = (record, "excluded", doc)
    return found, evidence, rfqs


def _jurisdiction_row(config, code):
    rows = config.get("jurisdictions") or {}
    if code in rows:
        return rows[code]
    for group, members in (config.get("jurisdiction_groups") or {}).items():
        if code in members and group in rows:
            return rows[group]
    return None


def expected_compliance(config, notices, code, channel_type):
    """(direct_marketing_review, ad_label_required, notice_key) for a country and channel,
    resolved as compliance.config.json resolution_note describes."""
    default = config.get("default") or {}
    row = _jurisdiction_row(config, code) if code else None
    review = (row or default).get("direct_marketing_review", _common.UNKNOWN)
    key = None
    if code and channel_type:
        if "%s.%s" % (code, channel_type) in notices:
            key = "%s.%s" % (code, channel_type)
        else:
            for family, members in (config.get("notice_block_channel_family") or {}).items():
                if channel_type in members and "%s.%s" % (code, family) in notices:
                    key = "%s.%s" % (code, family)
                    break
    if key:
        status = ((config.get("notice_blocks") or {}).get(key) or {}).get("status", _common.UNKNOWN)
        label = (config.get("notice_block_status_to_ad_label") or {}).get(status, _common.UNKNOWN)
    elif row and channel_type == "corporate_email":
        label = row.get("corporate_email_ad_label_required", _common.UNKNOWN)
    else:
        label = default.get("ad_label_required", _common.UNKNOWN)
    return review, label, key


def _match_notice_block(block, rendered):
    """Find `block` verbatim (tokens filled) as consecutive lines of `rendered`.

    Returns ({token: value}, None) on a match, or (None, reason).
    """
    block = _trim_blank(block)
    patterns = []
    for value in block:
        parts = _NOTICE_TOKEN_RE.split(value)
        regex = "".join(
            re.escape(part) if position % 2 == 0 else "(?P<%s>.+?)" % (part,)
            for position, part in enumerate(parts)
        )
        patterns.append((re.compile("^%s$" % regex), value))
    rendered = [value.rstrip() for value in rendered]
    best = None
    for start in range(0, max(0, len(rendered) - len(patterns)) + 1):
        tokens = {}
        for offset, (regex, template) in enumerate(patterns):
            if start + offset >= len(rendered):
                break
            match = regex.match(rendered[start + offset])
            if not match:
                if best is None or offset > best[0]:
                    best = (offset, template)
                break
            clash = [name for name, text in match.groupdict().items()
                     if name in tokens and tokens[name] != text]
            if clash:
                best = (offset, "token %s filled with two different values" % (clash[0],))
                break
            tokens.update(match.groupdict())
        else:
            if patterns:
                return tokens, None
    if best is None:
        return None, "the section has fewer lines than the block"
    return None, "first line that differs from the block: %r" % (best[1],)


def _personal_data_findings(text, company_names=()):
    """INV-31 findings as (severity, message). `company_names` (the envelope and target
    record's names) are never mistaken for a person or a personal local part."""
    findings = []
    ko_tokens = set()
    ascii_tokens = set()
    for name in company_names:
        if isinstance(name, str):
            ko_tokens.update(token for token in re.findall(r"[가-힣]{2,}", name))
            ascii_tokens.update(token for token in re.findall(r"[a-z0-9]{3,}", name.casefold()))
    for local, domain in _EMAIL_RE.findall(text):
        address = "%s@%s" % (local, domain)
        if domain.lower() in _common.FREE_MAIL_DOMAINS:
            findings.append(("error", "%s is on a free-mail domain, not a company domain" % (address,)))
            continue
        labels = [label for label in domain.lower().split(".") if len(label) >= 3]
        segments = [segment for segment in re.split(r"[._-]", local.lower()) if segment]
        if (
            len(segments) >= 2
            and all(segment.isalpha() for segment in segments)
            and not any(segment in ROLE_LOCAL_PARTS for segment in segments)
            and not any(len(segment) >= 3 and (segment in ascii_tokens
                                               or any(segment in label for label in labels))
                        for segment in segments)
        ):
            findings.append(("warning", "%s has a name-shaped local part with no role word; confirm "
                             "it is a role address, not a person's" % (address,)))
    for regex in _MOBILE_RE:
        for match in regex.finditer(text):
            findings.append(("error", "%s is a personal mobile number" % (match.group(0).strip(),)))
    for match in _EN_HONORIFIC_RE.finditer(text):
        findings.append(("error", "%r addresses a named individual" % (match.group(0),)))
    for regex in (_KO_TITLE_RE, _KO_NAME_NIM_RE):
        for match in regex.finditer(text):
            name = match.group(1)
            if (name in _KO_ROLE_WORDS or name[:2] in _KO_ROLE_WORDS
                    or name.endswith(_KO_UNIT_SUFFIXES)
                    or any(token.startswith(name) or name.startswith(token) for token in ko_tokens)):
                continue
            findings.append(("warning", "%r may address a named individual; greet a role instead"
                             % (match.group(0),)))
    return findings


def _sourcing_sentence_licensed(sentence, facts, evidence):
    """templates/buyer_outreach.md section 3: "you are looking for..." is licensed only by the
    recipient's own dated public sourcing action, stated with its date."""
    dates = set(_ISO_DATE_RE.findall(sentence))
    if not dates:
        return False
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if evidence is None:
            # No --record: the date must be the one a Personalization fact line itself states.
            if dates & set(_ISO_DATE_RE.findall(str(fact.get("fact") or ""))):
                return True
            continue
        for item in evidence:
            tier = item.get("source_tier") if isinstance(item, dict) else None
            if (
                isinstance(item, dict)
                and item.get("claim") == SOURCING_SIGNAL_CLAIM
                and _url_key(item.get("source_url")) == _url_key(fact.get("source_url"))
                and isinstance(tier, int) and not isinstance(tier, bool)
                and tier <= SOURCING_SIGNAL_MAX_TIER
                and item.get("stale") is not True and item.get("inferred") is not True
                and _date_part(item.get("source_date")) in dates
            ):
                return True
    return False


def live_demand_matches(text, facts=(), evidence=None):
    """INV-34: the patterns in `text` that still need an open, cited RFQ.

    Every LIVE_DEMAND_PATTERNS match counts. A RECIPIENT_SOURCING_PATTERNS match ("you are
    actively looking") does not when its sentence states a date backed by a Personalization
    fact: with `evidence` (the --record target's items), a fact whose source_url is a
    sourcing_signals item of tier <= 2, neither stale nor inferred, with that source_date;
    without it, a fact line whose own text states that date. Shared with the adapter.
    """
    matched = [regex.pattern for regex in _LIVE_DEMAND_RE if regex.search(text)]
    for sentence in re.split(r"(?<=[.!?。])\s+|\n", text):
        for regex in _RECIPIENT_SOURCING_RE:
            if (regex.pattern not in matched and regex.search(sentence)
                    and not _sourcing_sentence_licensed(sentence, facts, evidence)):
                matched.append(regex.pattern)
    return matched


def validate_outreach_draft(draft, record=None, config=None, notices=None, schema=None):
    """Check one outreach draft against output-format 10.4 and the templates.

    `draft` is the rendered Markdown text or the adapter's JSON draft. `record` is optional:
    one document or a list of documents the draft was built from (a scored buyer / seller
    record, a discovery-result, a match-result, and optionally the RFQ it cites). `config`
    is schemas/compliance.config.json, `notices` the parsed templates/legal_notices.md.

    Returns a list of {"invariant", "severity", "message", "strict"}. Any "error" means the
    draft does not go to review; a "warning" fails only under --strict, and a warning with
    "strict": false (the INV-31 name-shape heuristics) never fails.
    """
    issues = []

    def fail(rule, message):
        issues.append({"invariant": rule, "severity": "error", "message": message, "strict": True})

    def warn(rule, message, strict=True):
        issues.append({"invariant": rule, "severity": "warning", "message": message,
                       "strict": strict})

    config = config if config is not None else load_compliance_config()
    notices = notices if notices is not None else load_legal_notices()
    if isinstance(draft, str):
        parsed, problems = parse_outreach_draft(draft)
        text = draft
    elif isinstance(draft, dict):
        parsed, problems, text = _draft_from_json(draft)
    else:
        fail("SCHEMA", "an outreach draft is Markdown text or a JSON object, got %s"
             % (type(draft).__name__,))
        return issues
    has_envelope = text is not None
    for rule, message in problems:
        fail(rule, message)
    schema = schema if schema is not None else _common.load_schema(OUTREACH_KIND)
    for message in _common.validate(parsed, schema):
        fail("SCHEMA", message)

    side = parsed.get("side")
    subject = parsed.get("subject") if isinstance(parsed.get("subject"), str) else ""
    body = parsed.get("body") if isinstance(parsed.get("body"), str) else ""
    channel_type = parsed.get("channel_type")
    channel_value = str(parsed.get("channel_value") or "")
    facts = [fact for fact in parsed.get("personalization_facts") or [] if isinstance(fact, dict)]
    compliance = parsed.get("compliance") if isinstance(parsed.get("compliance"), dict) else {}
    notice_lines = [str(value) for value in parsed.get("legal_notices") or []]
    scan = "\n".join(
        [str(parsed.get("company_name") or ""), channel_value, subject, body]
        + ["%s %s" % (fact.get("fact", ""), fact.get("source_url", "")) for fact in facts]
        + ["%s" % (value,) for value in compliance.values()]
        + notice_lines
    )

    # --record: resolve the target first; several rules read it.
    found, evidence, rfq_docs = (None, [], [])
    target = kind = target_doc = None
    if record is not None:
        records = record if isinstance(record, list) else [record]
        found, evidence, rfq_docs = _draft_target(records, parsed)
        if found is None:
            fail("DRAFT-09", "no --record document carries the draft's target %r; a draft is "
                 "built only from a scored target (SKILL.md Mode 4)"
                 % (parsed.get("company_name") or parsed.get("entity_id"),))
        else:
            target, kind, target_doc = found

    # INV-09: nothing in the draft sits past review.
    if text is not None:
        for value in text.split("\n"):
            stripped = value.strip()
            status_line = re.match(r"(?i)^status\s*:\s*(.*)$", stripped)
            if status_line and status_line.group(1).strip() != "READY_FOR_REVIEW":
                fail("INV-09", "%r: every outreach artifact ends at READY_FOR_REVIEW" % (stripped[:80],))
            send_line = re.match(r"(?i)^auto[-_ ]send\s*:\s*(.*)$", stripped)
            if send_line and send_line.group(1).strip() != "false":
                fail("INV-09", "%r: auto-send is always false" % (stripped[:80],))
            approval = re.match(r"(?i)^manual_approval_required\s*:\s*(.*)$", stripped)
            if approval and approval.group(1).strip() != "true":
                fail("INV-09", "%r: manual approval is always required" % (stripped[:80],))
    for state in sorted(FORBIDDEN_STATES):
        if re.search(r"\b%s\b" % state, scan):
            fail("INV-09", "the draft names the state %s outside the Next action line" % (state,))

    if has_envelope:
        checklist = parsed.get("reviewer_checklist") or []
        for item in DRAFT_BASE_CHECKLIST:
            if item not in checklist:
                fail("DRAFT-01", "Reviewer checklist is missing %r" % (item,))
        for item in checklist:
            if re.match(r"^- \[[^ \]]\]", item):
                fail("DRAFT-01", "Reviewer checklist item %r is pre-ticked; a person completes "
                     "it (outreach-guidelines 8)" % (item[:80],))

    # DRAFT-02: the subject line.
    if subject:
        if len(subject) > DRAFT_SUBJECT_MAX_CHARS:
            fail("DRAFT-02", "Subject is %d characters; the limit is %d"
                 % (len(subject), DRAFT_SUBJECT_MAX_CHARS))
        if "!" in subject:
            fail("DRAFT-02", "Subject carries an exclamation mark")
        if re.match(r"(?i)^re\s*:", subject):
            fail("DRAFT-02", "Subject opens with 'Re:' although no reply exists")

    # DRAFT-03: the greeting names the company and its team, never a person. With --record the
    # target's normalized_name, company_name_ko and aliases are the company's names too, so a
    # Korean greeting may address an English-named company by its Korean name.
    company = parsed.get("company_name")
    company_names = [company] if company else []
    if target is not None:
        for field in ("company_name", "seller_name", "normalized_name", "company_name_ko"):
            if isinstance(target.get(field), str) and target[field].strip():
                company_names.append(target[field])
        company_names.extend(alias for alias in target.get("aliases") or [] if isinstance(alias, str))
    if company and body:
        greeting = next((value.strip() for value in body.split("\n") if value.strip()), "")
        keys = set()
        for name in company_names:
            keys |= set(key for key in _name_keys(name) if key)
        if not any(key in greeting.casefold() for key in keys):
            fail("DRAFT-03", "the greeting %r does not address %s; greet the company and a role "
                 "(templates section 4)" % (greeting[:80], company))

    # DRAFT-04: facts exist, are well formed, and cover every URL the body cites.
    if not facts:
        fail("DRAFT-04", "Personalization facts lists no fact; a draft personalizes only from "
             "evidence, each fact with its source URL (R10.4.2)")
    fact_urls = set(_url_key(fact.get("source_url")) for fact in facts)
    for url in re.findall(r"\[(https?://[^\]\s]+)\]", body):
        if _url_key(url) not in fact_urls:
            fail("DRAFT-04", "the body cites [%s], which no Personalization facts line lists" % (url,))

    # DRAFT-06: nothing unrendered.
    for regex in _PLACEHOLDER_RE:
        leftovers = sorted(set(regex.findall(scan)))
        if leftovers:
            fail("DRAFT-06", "unrendered placeholder(s) survive in the draft: %s"
                 % (", ".join(leftovers[:5]),))

    # DRAFT-07: no fake opt-out.
    for regex in _FAKE_OPT_OUT_RE:
        match = regex.search(scan)
        if match:
            fail("DRAFT-07", "%r is not an opt-out; use the notice block's own wording "
                 "(outreach-guidelines 7.4)" % (match.group(0),))

    # INV-31: company-level contact data only.
    for severity, finding in _personal_data_findings(scan, company_names):
        if severity == "error":
            fail("INV-31", finding)
        else:
            warn("INV-31", finding, strict=False)

    # INV-34: a live-demand claim needs an open RFQ, cited with its status and date.
    demand_text = "%s\n%s" % (subject, body)
    matched = live_demand_matches(demand_text, facts, evidence if record is not None else None)
    if matched:
        reasons = []
        cited = [_rfq_key(value) for value in _RFQ_CITATION_RE.findall(demand_text)]
        if parsed.get("rfq_id"):
            cited.append(_rfq_key(parsed["rfq_id"]))
        stated = [value.lower() for value in _RFQ_STATUS_RE.findall(demand_text)]
        open_stated = [value for value in stated if value in LIVE_DEMAND_RFQ_STATES]
        dates = _RFQ_DATE_RE.findall(demand_text)
        if not cited:
            reasons.append("no RFQ is cited")
        if not open_stated:
            reasons.append("no RFQ status in %s is stated" % ("/".join(LIVE_DEMAND_RFQ_STATES),))
        if not dates:
            reasons.append("no RFQ as-of date is stated")
        if parsed.get("rfq_status") and parsed["rfq_status"] not in open_stated:
            reasons.append("rfq_status %r is not stated as an open status" % (parsed["rfq_status"],))
        if parsed.get("rfq_as_of") and parsed["rfq_as_of"] not in dates:
            reasons.append("rfq_as_of %r is not stated" % (parsed["rfq_as_of"],))
        if target_doc is not None and "match_run_id" in target_doc and cited:
            if _rfq_key(target_doc.get("rfq_id")) not in cited:
                reasons.append("the cited RFQ is not this match run's RFQ %s" % (target_doc.get("rfq_id"),))
        for rfq in rfq_docs:
            if _rfq_key(rfq.get("rfq_id")) not in cited:
                continue
            if rfq.get("status") not in LIVE_DEMAND_RFQ_STATES:
                reasons.append("RFQ %s is in status %r" % (rfq.get("rfq_id"), rfq.get("status")))
            elif open_stated and rfq["status"] not in open_stated:
                reasons.append("the draft states status %s but RFQ %s is %s"
                               % ("/".join(open_stated), rfq.get("rfq_id"), rfq["status"]))
            if rfq.get("as_of") and dates and rfq["as_of"] not in dates:
                reasons.append("the draft states %s but RFQ %s is as of %s"
                               % ("/".join(dates), rfq.get("rfq_id"), rfq["as_of"]))
        if reasons:
            fail("INV-34", "the draft makes a live-demand claim (matched %s) but %s (R10.4.3, "
                 "PRD T05)" % (", ".join("/%s/" % p for p in matched[:3]), "; ".join(reasons)))

    # DRAFT-10: a company-level channel.
    if channel_type and channel_type not in (config.get("channel_types") or []):
        fail("DRAFT-10", "Recipient channel type %r is not a company-level channel" % (channel_type,))
    if channel_type == "corporate_email" and "@" not in channel_value:
        fail("DRAFT-10", "a corporate_email channel carries no address: %r" % (channel_value,))

    # DRAFT-08 / R10.4.6 / DRAFT-11: the compliance block and its notice.
    token_values = {}
    if has_envelope:
        labels = (
            ("jurisdiction", "Jurisdiction"),
            ("direct_marketing_review", "direct-marketing review"),
            ("ad_label_required", "Advertising label / opt-out required"),
            ("personal_data_used", "Personal data used"),
            ("claims_verified", "Claims verified against evidence"),
        )
        for field, label in labels:
            if field not in compliance:
                fail("DRAFT-08", "Compliance checks has no readable %r line" % (label,))
        if compliance.get("personal_data_used") not in (None, DRAFT_PERSONAL_DATA_LITERAL):
            fail("DRAFT-08", "Personal data used must be the literal %r" % (DRAFT_PERSONAL_DATA_LITERAL,))
        if compliance.get("claims_verified") == "blocked":
            fail("DRAFT-08", "Claims verified against evidence is blocked; a blocked draft does "
                 "not go to review (outreach-guidelines 2)")
        jurisdiction = compliance.get("jurisdiction")
        code = _common.normalize_country(jurisdiction) if jurisdiction else None
        code = None if code in (None, _common.UNKNOWN) else code
        review, label, key = expected_compliance(config, notices, code, channel_type)
        if compliance.get("direct_marketing_review") not in (None, review):
            fail("DRAFT-08", "direct-marketing review is %r; compliance-notes 4.1 resolves %s to %r"
                 % (compliance["direct_marketing_review"], code or "an unresolved jurisdiction", review))
        if compliance.get("ad_label_required") not in (None, label):
            fail("DRAFT-08", "Advertising label / opt-out required is %r; %s.%s resolves to %r"
                 % (compliance["ad_label_required"], code or "??", channel_type, label))
        if label in ("yes", _common.UNKNOWN):
            if parsed.get("legal_notices") is None:
                fail("R10.4.6", "Advertising label / opt-out resolves to %r but the draft has no "
                     "Required legal notices block" % (label,))
            elif key:
                token_values, reason = _match_notice_block(notices[key], notice_lines)
                if token_values is None:
                    token_values = {}
                    fail("R10.4.6", "the %s block of templates/legal_notices.md is not reproduced "
                         "verbatim under Required legal notices (%s)" % (key, reason))
            else:
                template = config.get("fallback_line") or ""
                pattern = re.escape(template).replace(re.escape("{{country_name}}"), ".+").replace(
                    re.escape("{{channel_type}}"), re.escape(str(channel_type or "")))
                if not any(re.match("^%s$" % pattern, value) for value in notice_lines):
                    fail("R10.4.6", "no notice block is on file for %s.%s, so the literal line %r "
                         "is required" % (code or "??", channel_type, template))
        url = token_values.get("recipient_channel_source_url")
        if url is not None and not re.match(r"^https?://\S+$", url):
            fail("R10.4.6", "recipient_channel_source_url in the notice block is %r, not a URL" % (url,))
        if token_values.get("subject_line") is not None and token_values["subject_line"] != subject:
            fail("R10.4.6", "the notice block's subject line differs from Subject")
        prefix = ((config.get("notice_blocks") or {}).get(key) or {}).get("subject_prefix") if key else None
        if prefix and not subject.startswith(prefix):
            fail("DRAFT-11", "the %s block requires the Subject to begin with %s" % (key, prefix))
        if target is not None and kind != "excluded" and code:
            country = target.get("country")
            if isinstance(country, str) and not _common.is_unknown(country):
                if _common.normalize_country(country) != code:
                    fail("DRAFT-08", "Jurisdiction resolves to %s but the target record's country is %s"
                         % (code, country))

    # DRAFT-12: body length (warning).
    limit = DRAFT_BODY_MAX_WORDS.get(side)
    if limit and len(body.split()) > limit:
        warn("DRAFT-12", "the body is %d words; the %s template keeps it at or under %d"
             % (len(body.split()), side, limit))

    # DRAFT-09 / DRAFT-05 / DRAFT-10 against the record.
    if target is not None:
        ident = target.get("seller_id") or target.get("buyer_id") or target.get("id")
        if kind == "excluded":
            fail("DRAFT-09", "%s is in excluded[]; an excluded company is never drafted" % (ident,))
        elif kind == "match_candidate":
            if side != "Seller":
                fail("DRAFT-09", "%s is a match candidate (a seller) but the draft side is %s" % (ident, side))
            if (target.get("hard_filter") or {}).get("passed") is not True:
                fail("DRAFT-09", "%s did not pass the hard filter" % (ident,))
            if target.get("qualified") is not True:
                fail("DRAFT-09", "%s is not qualified (qualified: %r)" % (ident, target.get("qualified")))
        else:
            if side and (side == "Buyer") != (kind == "buyer"):
                fail("DRAFT-09", "%s is a %s record but the draft side is %s" % (ident, kind, side))
            if target.get("score_version") in (None, "unscored"):
                fail("DRAFT-09", "%s is unscored; a draft is built only from a scored record" % (ident,))
            if target.get("qualified") is not True:
                fail("DRAFT-09", "%s is not qualified (qualified: %r)" % (ident, target.get("qualified")))
        by_url = {}
        for item in evidence:
            by_url.setdefault(_url_key(item.get("source_url")), []).append(item)
        for fact in facts:
            items = by_url.get(_url_key(fact.get("source_url")))
            if not items:
                fail("DRAFT-05", "fact %r cites %s, the source_url of no evidence item on the "
                     "target record" % (str(fact.get("fact"))[:60], fact.get("source_url")))
                continue
            if fact.get("evidence_id") and not any(
                item.get("evidence_id") == fact["evidence_id"] for item in items
            ):
                fail("DRAFT-05", "fact cites evidence_id %s, which does not exist with source_url %s"
                     % (fact["evidence_id"], fact.get("source_url")))
            date = fact.get("observed_date")
            if date and not any(_date_part(item.get("observed_at")) == date for item in items):
                fail("DRAFT-05", "fact on %s says observed %s; the evidence was observed %s"
                     % (fact.get("source_url"), date,
                        ", ".join(sorted(set(str(_date_part(i.get("observed_at"))) for i in items)))))
            if all(item.get("stale") is True for item in items):
                fail("DRAFT-05", "fact on %s rests only on stale evidence (outreach-guidelines 3.6)"
                     % (fact.get("source_url"),))
        channels = target.get("contact_channels") if kind != "excluded" else None
        if isinstance(channels, list) and channel_type:
            if not any(
                isinstance(channel, dict) and channel.get("type") == channel_type
                and _url_key(channel.get("value")).casefold() == _url_key(channel_value).casefold()
                for channel in channels
            ):
                fail("DRAFT-10", "Recipient channel %s — %s is not one of the target's "
                     "contact_channels" % (channel_type, channel_value))
        url = token_values.get("recipient_channel_source_url")
        if url:
            known = set(by_url) | set(
                _url_key(channel.get("value")) for channel in channels or [] if isinstance(channel, dict)
            )
            if _url_key(url) not in known:
                fail("R10.4.6", "the notice block names %s as the channel source, which is not a "
                     "source_url or channel on the target record" % (url,))
    return issues


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Validate a document against its schema and against the BUILD-CONTRACT section 11 "
            "invariants. Korean gloss: 스키마와 계약 불변식을 함께 검증한다."
        ),
    )
    parser.add_argument("-i", "--input", default=None, help="Input JSON (default: stdin)")
    parser.add_argument("-o", "--output", default=None, help="Write the report here (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent=2 report")
    parser.add_argument("--as-of", dest="as_of", default=None, help="YYYY-MM-DD run date")
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--no-validate", dest="validate", action="store_false", default=True)
    parser.add_argument("--quiet", action="store_true", help="Suppress the stderr summary")
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--schema",
        choices=KINDS + (OUTREACH_KIND, "auto"),
        default="auto",
        help="Document kind (default: auto). outreach-draft reads a rendered Markdown draft "
        "(or the adapter's JSON draft) instead of a JSON document",
    )
    parser.add_argument(
        "--record",
        dest="records",
        action="append",
        default=[],
        help="outreach-draft only: the scored record, discovery-result or match-result the "
        "draft was built from (repeatable; add the RFQ document to check an RFQ citation)",
    )
    parser.add_argument("--schema-file", dest="schema_file", default=None)
    parser.add_argument("--profile", choices=("raw", "scored", "auto"), default="auto")
    parser.add_argument("--invariants", dest="invariants", action="store_true", default=True)
    parser.add_argument("--no-invariants", dest="invariants", action="store_false")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument("--max-errors", dest="max_errors", type=int, default=100)
    parser.add_argument(
        "--json",
        dest="json_only",
        action="store_true",
        help="Emit only the machine-readable JSON report; no human-readable stderr summary",
    )
    return parser


def _documents(payload, forced_kind):
    """Resolve the input shape into [(index, kind_hint, document), ...]."""
    if isinstance(payload, list):
        return [(i, forced_kind, doc) for i, doc in enumerate(payload)], None
    if isinstance(payload, dict):
        kind = forced_kind if forced_kind != "auto" else _detect_kind(payload)
        if kind == "discovery-result":
            entity = payload.get("entity")
            child = entity if entity in ("buyer", "seller") else "auto"
            records = payload.get("records") or []
            items = [(0, "discovery-result", payload)]
            items.extend(
                [(i + 1, child, doc) for i, doc in enumerate(records) if isinstance(doc, dict)]
            )
            return items, "discovery-result"
        # BUILD-CONTRACT 7.4 and 7.10: a plain envelope {"records": [...]} is an
        # accepted input shape for every script, so an explicit --schema buyer /
        # seller / rfq / evidence must validate the records the envelope carries,
        # not the envelope object itself. Only "discovery-result" (returned
        # above) and "match-result" are envelope-level kinds; every per-record
        # schema sets additionalProperties:false and so forbids "records".
        if kind != "match-result" and isinstance(payload.get("records"), list):
            return (
                [(i, forced_kind, doc) for i, doc in enumerate(payload["records"])],
                None,
            )
        return [(0, forced_kind, payload)], kind
    raise _common.UsageError("input must be a JSON object or array")


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
        return _run(args, config)
    except _common.UsageError as exc:
        return _common.die(str(exc), 2)
    except _common.ConfigError as exc:
        return _common.die(str(exc), 2)
    except _common.DataError as exc:
        return _common.die(str(exc), 1)
    except Exception as exc:
        return _common.die("%s: %s" % (type(exc).__name__, exc), 1)


def _load_schema_for(kind, args, cache):
    if args.schema_file:
        key = args.schema_file
        if key not in cache:
            if not os.path.isfile(args.schema_file):
                raise _common.UsageError("--schema-file not found: %s" % (args.schema_file,))
            import json

            with open(args.schema_file, "r", encoding="utf-8") as handle:
                cache[key] = json.load(handle)
        return cache[key], key
    if kind is None:
        return None, None
    if kind not in cache:
        cache[kind] = _common.load_schema(kind, args.schema_dir)
    return cache[kind], kind


def _run(args, config):
    if args.as_of:
        # 7.3: an --as-of that is not a real YYYY-MM-DD date is a usage error (exit 2).
        _common.resolve_as_of([], args.as_of)
    if args.schema == OUTREACH_KIND:
        return _run_outreach(args)
    if args.records:
        raise _common.UsageError("--record is only meaningful with --schema outreach-draft")
    payload = _common.read_input(args)
    items, envelope_kind = _documents(payload, args.schema)

    errors = []
    warnings = []
    failures = _Failures()
    cache = {}
    kinds_seen = []
    checked = 0
    as_of = args.as_of
    score_version = None
    preflighted = set()

    for index, hint, document in items:
        if not isinstance(document, dict):
            errors.append(
                {"index": index, "id": None, "path": "<root>", "message": "not a JSON object"}
            )
            continue
        checked += 1
        kind = hint if hint not in (None, "auto") else _detect_kind(document)
        if kind is None and args.schema_file:
            # An explicit --schema-file names the shape even when the kind is unknown:
            # validate against it and apply only the generic invariants.
            kind = "custom"
        if kind is None:
            # A document nobody can classify was checked against nothing; reporting it as a
            # warning let an arbitrary object exit 0 through the shipping gate.
            failures.add(
                "VAL-01",
                index,
                _doc_id(document),
                "could not detect the document kind, so no schema or invariant was checked; "
                "pass --schema",
            )
            continue
        if kind not in kinds_seen:
            kinds_seen.append(kind)
        if as_of is None and isinstance(document.get("as_of"), str):
            as_of = document["as_of"]
        if score_version is None and isinstance(document.get("score_version"), str):
            score_version = document["score_version"]
        elif (
            isinstance(document.get("score_version"), str)
            and document["score_version"] != score_version
        ):
            failures.add(
                "INV-23",
                index,
                _doc_id(document),
                "score_version '%s' differs from '%s' earlier in the same output"
                % (document["score_version"], score_version),
            )

        if args.validate:
            schema, schema_key = _load_schema_for(kind, args, cache)
            if schema is None:
                continue
            if schema_key not in preflighted:
                preflighted.add(schema_key)
                problems = _preflight(schema)
                if problems:
                    _common.die(problems[0], 1)
                    for line in problems[1:10]:
                        _common.eprint("    " + line, quiet=args.quiet)
                    return 1
            for message in _common.validate(document, schema):
                path, _, text = message.partition(": ")
                errors.append(
                    {
                        "index": index,
                        "id": _doc_id(document),
                        "path": path,
                        "message": text or message,
                    }
                )

        if args.invariants:
            try:
                _run_invariants(document, kind, index, config, failures, as_of)
            except (AttributeError, TypeError):
                # The invariants read the document's own shape; a document that merely
                # looks like `kind` (a hand-edited file, a test digest) is a finding, not
                # a crash, and the schema errors above say what is wrong with it.
                errors.append(
                    {
                        "index": index,
                        "id": _doc_id(document),
                        "path": "<root>",
                        "message": "the %s invariants could not be evaluated: the document "
                        "does not have the %s shape" % (kind, kind),
                    }
                )

    warnings.extend(failures.warnings)
    profile = args.profile
    if profile == "auto":
        if "match-result" in kinds_seen:
            profile = "scored"
        elif score_version in (None, "unscored"):
            profile = "raw"
        else:
            profile = "scored"

    limit = max(1, int(args.max_errors))
    report = {
        "valid": not errors and not failures.items and not (args.strict and warnings),
        "checked": checked,
        "schema": envelope_kind or (kinds_seen[0] if kinds_seen else (None if args.schema == "auto" else args.schema)),
        "profile": profile,
        "as_of": as_of,
        "score_version": score_version,
        "errors": errors[:limit],
        "warnings": warnings[:limit],
        "invariant_failures": failures.items[:limit],
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "invariant_failures": len(failures.items),
        },
    }

    _emit_report(args, report)
    if errors or failures.items:
        return 1
    if args.strict and warnings:
        return 1
    return 0


def _emit_report(args, report):
    stream = None
    handle = None
    if args.output:
        handle = open(args.output, "w", encoding="utf-8")
        stream = handle
    try:
        _common.write_output(report, args.pretty, stream)
    finally:
        if handle is not None:
            handle.close()
    if not args.json_only and not args.quiet:
        _human_report(report)


def _run_outreach(args):
    """--schema outreach-draft: a Markdown draft (or the adapter's JSON draft) plus --record."""
    import json

    path = args.input
    try:
        if path is None or path == "-":
            raw = sys.stdin.read()
        else:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
    except (IOError, OSError) as exc:
        raise _common.UsageError("cannot read input %s: %s" % (path or "<stdin>", exc))
    except UnicodeDecodeError as exc:
        raise _common.UsageError("input %s is not valid UTF-8: %s" % (path or "<stdin>", exc))
    if not raw.strip():
        raise _common.UsageError("input %s is empty" % (path or "<stdin>",))
    draft = raw
    if raw.lstrip().startswith("{"):
        try:
            draft = json.loads(raw)
        except ValueError as exc:
            raise _common.UsageError("input %s looks like JSON but does not parse: %s" % (path, exc))
    records = []
    for record_path in args.records:
        try:
            with open(record_path, "r", encoding="utf-8") as handle:
                records.append(json.load(handle))
        except (IOError, OSError, UnicodeDecodeError) as exc:
            raise _common.UsageError("cannot read --record %s: %s" % (record_path, exc))
        except ValueError as exc:
            raise _common.UsageError("--record %s is not valid JSON: %s" % (record_path, exc))

    issues = validate_outreach_draft(draft, records or None)
    errors, warnings, failures = [], [], []
    for issue in issues:
        if issue["invariant"] == "SCHEMA":
            where, _, text = issue["message"].partition(": ")
            errors.append({"index": 0, "id": None, "path": where, "message": text or issue["message"]})
        elif issue["severity"] == "warning":
            warnings.append({"index": 0, "id": None, "path": "<root>", "invariant": issue["invariant"],
                             "strict": issue.get("strict", True),
                             "message": "%s: %s" % (issue["invariant"], issue["message"])})
        else:
            failures.append({"invariant": issue["invariant"], "index": 0, "id": None,
                             "message": issue["message"]})
    limit = max(1, int(args.max_errors))
    strict_warnings = [warning for warning in warnings if warning["strict"]]
    report = {
        "valid": not errors and not failures and not (args.strict and strict_warnings),
        "checked": 1,
        "schema": OUTREACH_KIND,
        "profile": "draft",
        "as_of": args.as_of,
        "score_version": None,
        "records": len(records),
        "errors": errors[:limit],
        "warnings": warnings[:limit],
        "invariant_failures": failures[:limit],
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "invariant_failures": len(failures),
        },
    }
    _emit_report(args, report)
    if errors or failures or (args.strict and strict_warnings):
        return 1
    return 0


def _human_report(report):
    lines = []
    verdict = "PASS" if report["valid"] else "FAIL"
    lines.append(
        "%s  schema=%s  profile=%s  checked=%d  as_of=%s  score_version=%s"
        % (
            verdict,
            report["schema"],
            report["profile"],
            report["checked"],
            report["as_of"],
            report["score_version"],
        )
    )
    lines.append(
        "  errors=%d  warnings=%d  invariant_failures=%d"
        % (
            report["summary"]["errors"],
            report["summary"]["warnings"],
            report["summary"]["invariant_failures"],
        )
    )
    for item in report["errors"]:
        lines.append(
            "  schema  [%s] %s: %s" % (item.get("id") or item.get("index"), item["path"], item["message"])
        )
    for item in report["invariant_failures"]:
        lines.append(
            "  %s [%s] %s"
            % (item["invariant"], item.get("id") or item.get("index"), item["message"])
        )
    for item in report["warnings"]:
        lines.append(
            "  warn    [%s] %s: %s"
            % (item.get("id") or item.get("index"), item["path"], item["message"])
        )
    for line in lines:
        _common.eprint(line)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
