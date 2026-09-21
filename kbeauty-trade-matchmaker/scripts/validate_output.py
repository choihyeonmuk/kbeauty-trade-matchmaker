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

    def add(self, invariant, index, ident, message):
        self.items.append(
            {"invariant": invariant, "index": index, "id": ident, "message": message}
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


def _inv_entity(document, kind, index, ident, config, out):
    """Buyer / seller record invariants."""
    claims = config["evidence"]["material_claims"].get(kind, [])
    evidence = document.get("evidence")
    evidence = evidence if isinstance(evidence, list) else []
    covered = set(
        item.get("claim") for item in evidence if isinstance(item, dict) and item.get("claim")
    )
    # INV-01: a material claim is evidenced, or it is "unknown"/absent. No third possibility.
    for claim in claims:
        if claim not in document:
            continue
        value = document.get(claim)
        if _common.is_unknown(value):
            continue
        if claim not in covered:
            out.add(
                "INV-01",
                index,
                ident,
                "material claim '%s' has neither evidence nor \"unknown\"" % (claim,),
            )

    score_version = document.get("score_version")
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

    # INV-37: state entry conditions hold within the document.
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
        ]
        if not ok:
            out.add(
                "INV-37",
                index,
                ident,
                "status VERIFIED requires a material claim evidenced at source_tier <= 3",
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


def _run_invariants(document, kind, index, config, out):
    ident = _doc_id(document)
    if not isinstance(document, dict):
        return
    _inv_generic(document, kind, index, ident, out)
    if kind in ("buyer", "seller"):
        _inv_entity(document, kind, index, ident, config, out)
    elif kind == "match-result":
        _inv_match_result(document, index, ident, config, out)


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
        "--schema", choices=KINDS + ("auto",), default="auto", help="Document kind (default: auto)"
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
            warnings.append(
                {
                    "index": index,
                    "id": _doc_id(document),
                    "path": "<root>",
                    "message": "could not detect the document kind; pass --schema",
                }
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
                _run_invariants(document, kind, index, config, failures)
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

    if errors or failures.items:
        return 1
    if args.strict and warnings:
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
