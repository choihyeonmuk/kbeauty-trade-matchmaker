#!/usr/bin/env python3
"""score_match.py - run the RFQ -> seller matching pipeline and emit one match-result document.

Implements PRD 6.4 exactly as specified by docs/SCORING-CONTRACT.md section 3 and
docs/BUILD-CONTRACT.md sections 6 and 7.9:

  stage 1  hard_filter      HF-01..HF-08, no short-circuit, unknown never rejects (INV-07)
  stage 2  weighted_score   the six seller dimensions re-scored against the projected RFQ
  stage 3  semantic_rerank  NOT computed here: the agent reasons, this script emits the rerank
                            slot and applies bounded adjustments handed back via
                            --rerank-adjustments / --rerank-input
  stage 4  unknown_handling reporting contract for the penalties applied inside stages 1 and 2

Every weight, point value, penalty, tolerance and threshold is read from
schemas/scoring.config.json at run time (INV-29). No wall clock is read anywhere: --as-of is
the only time source (INV-14). Korean gloss: RFQ -> 셀러 매칭 엔진.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "score_match.py"

# Sentinels for a comparison that cannot be made. Distinct objects so that a legitimate
# numeric 0 is never confused with "we do not know".
_UNKNOWN = "\x00unknown"
_UNIT_MISMATCH = "\x00unit_mismatch"

# PRD 12.2 render labels, fixed by BUILD-CONTRACT 6.1. Labels, never numbers.
COMPONENT_LABELS = {
    "product_fit": "Product Fit",
    "model_fit": "Model Fit",
    "operation_fit": "MOQ",
    "compliance_fit": "Compliance",
    "market_fit": "Market Fit",
    "evidence_quality": "Evidence Quality",
}

# SCORING-CONTRACT 3.1: which criterion's existing unknown_penalty record pairs with a
# hard-filter rule skipped for unknown input. A rule absent from this map (HF-08) is paired
# with a zero-weight record instead, and so is any rule here whose criterion did not itself
# take the unknown path.
_HF_PENALTY_CRITERION = {
    "HF-01": "S-PF1",
    "HF-02": "S-CM1",
    "HF-03": "S-OP1",
    "HF-04": "S-CP1",
    "HF-07": "S-OP2",
}

# The constraint each hard-filter rule relaxes, for no_match.relaxation_suggestions.
_HF_CONSTRAINT = {
    "HF-01": "rfq.product_category",
    "HF-02": "rfq.commercial_model",
    "HF-03": "rfq.max_moq",
    "HF-04": "rfq.required_certifications",
    "HF-05": "rfq.destination_country",
    "HF-06": "seller.operational_status",
    "HF-07": "rfq.max_lead_time_days",
    "HF-08": "rfq.required_seller_countries",
    "HF-00": "rendering.evidence_sufficiency",
}

# BUILD-CONTRACT 8.6 group membership (a vocabulary fact, not a number) consumed by S-CP4.
_CERT_BASELINE_QUALITY = ("CGMP", "ISO22716")
_CERT_OTHER_QUALITY = ("COSMOS", "ECOCERT", "GMP_KOREA", "ISO14001", "ISO9001")

# Deterministic surface-text markers for the evidence-derived signals and adjustments that
# no structured field carries. Matched only against official, company-published evidence.
_RE_NEGOTIABLE = re.compile(r"negotiab|flexible\s+moq|moq[^.]{0,24}flexible|협의|조정\s*가능", re.I)
_RE_SAMPLE = re.compile(
    r"sample\s+(order|purchase|quantit)|trial\s+order|small\s+trial|샘플\s*주문|시험\s*주문", re.I
)
_RE_CATALOG = re.compile(r"catalog|catalogue|brochure|line\s*sheet|\.pdf", re.I)
_RE_EXHIBITOR = re.compile(r"exhibit|booth|부스|출품|참가\s*업체", re.I)
_RE_EXPORT_CHANNEL = re.compile(r"export|overseas|global|해외|수출", re.I)
_RE_PARTIAL_ENGLISH = re.compile(
    r"english\s+(catalog|catalogue|brochure|pdf|page|landing|material)"
    r"|partial(ly)?\s+english|영문\s*(카탈로그|브로슈어|페이지|자료)",
    re.I,
)


# --------------------------------------------------------------------------------------
# small numeric / value helpers
# --------------------------------------------------------------------------------------


def _dec(value):
    """Exact Decimal for any config or record number (SCORING-CONTRACT 0.1)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise _common.DataError("a boolean is not a numeric value")
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def _json_num(value):
    """Convert a Decimal to the int/float JSON writes, matching _common.write_output."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def _trim(text, limit):
    """Keep a rendered string inside its schema maxLength.

    Record-derived text (a seller's own unit string, a category list) reaches these fields,
    so the cap is a real guard rather than decoration: an over-long reason would otherwise
    fail the document at the self-validation step and take the whole run down.
    """
    if not isinstance(text, str):
        return text
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def _is_number(value):
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _tri(record, field):
    """Tri-state of a record field: True, False or "unknown" (absent is unknown)."""
    if not isinstance(record, dict) or field not in record:
        return _common.UNKNOWN
    return _common.tri_state(record.get(field))


def _known_list(record, field):
    """The list stored at `field`, or None when the field is absent/"unknown".

    A present-and-empty list is a KNOWN value and comes back as [] (BUILD-CONTRACT 3.2).
    """
    if not isinstance(record, dict) or field not in record:
        return None
    value = record.get(field)
    if value is None or _common.is_unknown(value):
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _normalise_unit(value):
    if value is None:
        return "units"
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[\s.]+", "", text)
    return text or "units"


def _url_path(url):
    """Path component of a URL, without importing urllib (scripts purity rule R7.11.1)."""
    if not isinstance(url, str):
        return ""
    rest = url.split("://", 1)[-1]
    cut = rest.find("/")
    if cut < 0:
        return ""
    path = rest[cut:]
    for marker in ("?", "#"):
        pos = path.find(marker)
        if pos >= 0:
            path = path[:pos]
    return path


def _fmt_value(value):
    """Human rendering for a failure message (BUILD-CONTRACT 10.5 shared line rules)."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return "{:,}".format(int(value))
        return "{:,}".format(float(value))
    if isinstance(value, int):
        return "{:,}".format(value)
    if isinstance(value, float):
        if float(value).is_integer():
            return "{:,}".format(int(value))
        return "{:,}".format(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        parts = [_fmt_value(item) for item in value]
        return ", ".join(parts) if parts else "none"
    return str(value)


def _render_message(template, observed, required):
    text = template.replace("{observed_value}", _fmt_value(observed))
    return text.replace("{required_value}", _fmt_value(required))


# --------------------------------------------------------------------------------------
# evidence helpers
# --------------------------------------------------------------------------------------


def _evidence(record):
    items = record.get("evidence") if isinstance(record, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _evidence_by_claim(record):
    index = {}
    for item in _evidence(record):
        index.setdefault(item.get("claim"), []).append(item)
    return index


def _evidence_text(item):
    parts = []
    value = item.get("value")
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, (list, tuple)):
        parts.extend([str(entry) for entry in value])
    for key in ("quote_or_summary", "notes"):
        text = item.get(key)
        if isinstance(text, str):
            parts.append(text)
    parts.append(str(item.get("source_url") or ""))
    return " ".join(parts)


def _is_official(item):
    return item.get("is_official") is True


def _tier(item):
    tier = item.get("source_tier")
    return tier if isinstance(tier, int) and not isinstance(tier, bool) else 5


def _ids(items):
    out = []
    for item in items:
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident and ident not in out:
            out.append(ident)
    out.sort()
    return out


def _own_domain(item, seller):
    domain = _common.canonical_domain(item.get("source_domain") or item.get("source_url") or "")
    if not domain:
        return False
    own = seller.get("canonical_domain")
    if isinstance(own, str) and own != _common.UNKNOWN and domain == own:
        return True
    aliases = seller.get("alias_domains")
    if isinstance(aliases, list):
        for alias in aliases:
            if _common.canonical_domain(alias) == domain:
                return True
    return False


# --------------------------------------------------------------------------------------
# range comparison helpers (SCORING-CONTRACT 0.8, 2.3)
# --------------------------------------------------------------------------------------


def _cmp_min(value):
    rng = _common.coerce_range(value)
    if rng is None:
        return _UNKNOWN
    return _dec(rng["min"])


def _cmp_max(value):
    rng = _common.coerce_range(value)
    if rng is None:
        return _UNKNOWN
    return _dec(rng["max"])


def _moq_cmp(seller, query, notes):
    """MOQ comparison value, or _UNKNOWN / _UNIT_MISMATCH (SCORING-CONTRACT 2.3).

    Never indexes seller["moq"] directly: coerce_range is the single place every legal
    spelling (scalar, {min,max}, {max}, open lower bound, "unknown") is normalised, and it
    is the same function HF-03 calls, so the filter and the score can never disagree.
    """
    rng = _common.coerce_range(seller.get("moq"))
    if rng is None:
        return _UNKNOWN
    range_unit = rng.get("unit")
    field_unit = seller.get("moq_unit")
    if field_unit is not None and range_unit is not None:
        if _normalise_unit(field_unit) != _normalise_unit(range_unit):
            notes.append(
                "seller.moq_unit '%s' takes precedence over moq.unit '%s' (BUILD-CONTRACT R3.3.1)"
                % (field_unit, range_unit)
            )
    seller_unit = field_unit if field_unit is not None else range_unit
    if _normalise_unit(seller_unit) != _normalise_unit(query.get("moq_unit")):
        return _UNIT_MISMATCH
    return _dec(rng["min"])


# --------------------------------------------------------------------------------------
# scoring context
# --------------------------------------------------------------------------------------


class _Ctx(object):
    """Everything the criterion handlers read for one seller, resolved once."""

    def __init__(self, seller, query, config, as_of, flags):
        self.seller = seller
        self.query = query
        self.config = config
        self.as_of = as_of
        self.flags = flags
        self.notes = []
        self.ev = _evidence_by_claim(seller)
        self.categories = self._categories()
        self.query_categories = [
            slug for slug in (query.get("product_categories") or []) if slug
        ]
        self.certifications = self._certifications()
        self.required_certs = self._cert_tokens(query.get("required_certifications"))
        self.preferred_certs = self._cert_tokens(query.get("preferred_certifications"))
        self.moq_value = _moq_cmp(seller, query, self.notes)
        self.lead_time = _cmp_max(seller.get("lead_time_days"))
        self.capacity = _cmp_max(seller.get("monthly_capacity_units"))
        self.quantity = _cmp_max(query.get("quantity"))

    def _categories(self):
        raw = _known_list(self.seller, "product_categories")
        if raw is None:
            return None
        out = []
        for slug in raw:
            token = _common.normalize_category(slug)
            if token is None:
                self.notes.append(
                    "category token '%s' is a vertical marker, not a category: dropped from "
                    "the scored list (BUILD-CONTRACT 8.5)" % (slug,)
                )
                continue
            if token not in out:
                out.append(token)
        return out

    def _certifications(self):
        raw = _known_list(self.seller, "certifications")
        if raw is None:
            return None
        return self._cert_tokens(raw)

    @staticmethod
    def _cert_tokens(values):
        tokens = []
        for value in values or []:
            token = _common.normalize_certification(value)
            if token and token not in tokens:
                tokens.append(token)
        return tokens


class _Outcome(object):
    """Result of evaluating one criterion."""

    __slots__ = ("state", "fired", "unknown_inputs", "evidence_ids", "note")

    def __init__(self, state, fired=None, unknown_inputs=None, evidence_ids=None, note=None):
        self.state = state
        self.fired = list(fired or [])
        self.unknown_inputs = list(unknown_inputs or [])
        self.evidence_ids = list(evidence_ids or [])
        self.note = note


def _scored(fired, evidence_ids=None, note=None):
    return _Outcome("scored", fired, None, evidence_ids, note)


def _unknown(inputs, note=None):
    return _Outcome("unknown", None, inputs, None, note)


_INAPPLICABLE = "inapplicable"


def _criterion_signal_table(ctx, dimension_key, criterion_id):
    """The signal table of one seller criterion, read from the config (INV-29)."""
    dimensions = ctx.config["seller"]["dimensions"]
    for criterion in dimensions.get(dimension_key, {}).get("criteria", []):
        if criterion.get("criterion_id") == criterion_id:
            return criterion.get("signals", {})
    return {}


# --------------------------------------------------------------------------------------
# criterion handlers - SCORING-CONTRACT 2.1 .. 2.6
# --------------------------------------------------------------------------------------


def _crit_s_pf1(ctx):
    if ctx.categories is None:
        return _unknown(["seller.product_categories"])
    relation = _common.category_relation(ctx.categories, ctx.query_categories)
    fired = {
        "exact": ["category_exact_match"],
        "parent": ["category_parent_match"],
        "adjacent": ["category_adjacent_match"],
    }.get(relation, ["category_no_match"])
    return _scored(fired, _ids(ctx.ev.get("product_categories", [])))


def _crit_s_pf2(ctx):
    if ctx.flags.get("query.product_forms_absent"):
        return _Outcome(_INAPPLICABLE)
    seller_forms = _known_list(ctx.seller, "product_forms")
    if seller_forms is None:
        return _unknown(["seller.product_forms"])
    have = set(_normalise_unit(form) for form in seller_forms)
    want = set(_normalise_unit(form) for form in (ctx.query.get("product_forms") or []))
    fired = []
    if want and want.issubset(have):
        fired.append("form_exact_match")
    elif want & have:
        fired.append("form_partial_match")
    return _scored(fired, _ids(ctx.ev.get("product_forms", [])))


def _crit_s_pf3(ctx):
    items = ctx.ev.get("product_categories", [])
    if not items:
        return _unknown(["seller.evidence:product_categories"])
    fired = []
    used = []
    for item in items:
        if not _is_official(item) or _tier(item) != 1 or not _own_domain(item, ctx.seller):
            continue
        path = _url_path(item.get("source_url") or "")
        if path.strip("/"):
            if "named_product_page" not in fired:
                fired.append("named_product_page")
            used.append(item)
    for item in items:
        if _is_official(item) and _RE_CATALOG.search(_evidence_text(item)):
            if "catalog_or_pdf_published" not in fired:
                fired.append("catalog_or_pdf_published")
            used.append(item)
    if ctx.categories is not None and len(ctx.categories) >= 3:
        fired.append("multi_category_coverage")
    return _scored(fired, _ids(used or items))


def _model_flag(commercial_model):
    return {
        "branded": "brand_export",
        "private_label": "private_label",
        "oem_odm": "oem_odm",
    }.get(commercial_model)


def _crit_s_cm1(ctx):
    model = ctx.query.get("commercial_model") or "either"
    flags = dict(
        (name, _tri(ctx.seller, name)) for name in ("oem_odm", "private_label", "brand_export")
    )
    evidence_ids = _ids(
        ctx.ev.get("oem_odm", []) + ctx.ev.get("private_label", []) + ctx.ev.get("brand_export", [])
    )
    if model == "either":
        if all(state == _common.UNKNOWN for state in flags.values()):
            return _unknown(["seller.brand_export", "seller.oem_odm", "seller.private_label"])
        fired = ["either_model_any_supported"] if any(v is True for v in flags.values()) else []
        return _scored(fired, evidence_ids)
    flag = _model_flag(model)
    if flag is None:
        return _unknown(["query.commercial_model"])
    others = [name for name in flags if name != flag]
    if flags[flag] == _common.UNKNOWN and all(flags[o] == _common.UNKNOWN for o in others):
        return _unknown(sorted("seller." + name for name in flags))
    fired = []
    if flags[flag] is True:
        fired.append("required_model_supported")
    elif flags[flag] == _common.UNKNOWN and any(flags[o] is True for o in others):
        fired.append("related_model_supported")
    return _scored(fired, evidence_ids)


def _crit_s_cm2(ctx):
    flags = dict(
        (name, _tri(ctx.seller, name)) for name in ("oem_odm", "private_label", "brand_export")
    )
    if all(state == _common.UNKNOWN for state in flags.values()):
        return _unknown(["seller.brand_export", "seller.oem_odm", "seller.private_label"])
    fired = []
    for name in ("brand_export", "oem_odm", "private_label"):
        if flags[name] is True:
            fired.append(name + "_true")
        elif flags[name] == _common.UNKNOWN:
            # Partial unknown: the signal simply does not fire, the field is noted, and NO
            # unknown_penalty record is emitted (SCORING-CONTRACT 0.4).
            ctx.notes.append(
                "seller.%s not stated on any official channel (partial unknown on S-CM2)" % (name,)
            )
    return _scored(
        fired,
        _ids(
            ctx.ev.get("oem_odm", [])
            + ctx.ev.get("private_label", [])
            + ctx.ev.get("brand_export", [])
        ),
    )


def _crit_s_cm3(ctx):
    oem_items = ctx.ev.get("oem_odm", [])
    moq_items = ctx.ev.get("moq", [])
    if not oem_items and not moq_items:
        return _unknown(["seller.evidence:moq", "seller.evidence:oem_odm"])
    fired = []
    used = []
    for item in oem_items:
        if _is_official(item) and _tier(item) <= 2:
            fired.append("published_oem_odm_page")
            used.append(item)
            break
    for item in moq_items:
        if _is_official(item):
            fired.append("published_moq_or_pricing_terms")
            used.append(item)
            break
    return _scored(fired, _ids(used))


def _crit_s_cm4(ctx):
    """Manufacturer role - the seller-rubric criterion that finally reads company_type."""
    company_type = ctx.seller.get("company_type")
    if _common.is_unknown(company_type):
        return _unknown(["seller.company_type"])
    return _scored(["company_type_%s" % (company_type,)], _ids(ctx.ev.get("company_type", [])))


def _crit_s_op1(ctx):
    value = ctx.moq_value
    if value is _UNKNOWN or value is _UNIT_MISMATCH:
        note = None
        if value is _UNIT_MISMATCH:
            note = "seller.moq_unit does not match the RFQ unit, so the comparison was not made."
        return _unknown(["seller.moq"], note)
    ceiling = ctx.query.get("max_moq")
    ratio = _dec(ctx.config["hard_filter_tolerances"]["moq_overshoot_ratio"])
    fired = []
    if _is_number(ceiling):
        limit = _dec(ceiling)
        if value <= limit / Decimal(2):
            fired.append("moq_at_or_below_half_of_max")
        if value <= limit:
            fired.append("moq_at_or_below_max")
        if value <= limit * (Decimal(1) + ratio):
            fired.append("moq_within_tolerance_of_max")
        else:
            fired.append("moq_above_max")
    else:
        fired.append("moq_no_constraint_known")
    return _scored(sorted(fired), _ids(ctx.ev.get("moq", [])))


def _crit_s_op2(ctx):
    value = ctx.lead_time
    if value is _UNKNOWN:
        return _unknown(["seller.lead_time_days"])
    ceiling = ctx.query.get("max_lead_time_days")
    ratio = _dec(ctx.config["hard_filter_tolerances"]["lead_time_overshoot_ratio"])
    fired = []
    if _is_number(ceiling):
        limit = _dec(ceiling)
        if value <= limit:
            fired.append("lead_time_within_requirement")
        if value <= limit * (Decimal(1) + ratio):
            fired.append("lead_time_within_tolerance")
        else:
            fired.append("lead_time_exceeds_requirement")
    else:
        fired.append("lead_time_no_constraint_known")
    return _scored(sorted(fired), _ids(ctx.ev.get("lead_time_days", [])))


def _crit_s_op3(ctx):
    capacity = ctx.capacity
    if capacity is _UNKNOWN:
        return _unknown(["seller.monthly_capacity_units"])
    quantity = ctx.quantity
    fired = []
    if quantity is _UNKNOWN:
        fired.append("capacity_no_constraint_known")
    else:
        if capacity >= quantity * Decimal(2):
            fired.append("capacity_covers_quantity_2x")
        if capacity >= quantity:
            fired.append("capacity_covers_quantity")
        if capacity < quantity:
            fired.append("capacity_below_quantity")
    return _scored(sorted(fired), _ids(ctx.ev.get("monthly_capacity_units", [])))


def _crit_s_cp1(ctx):
    if ctx.flags.get("query.required_certifications_absent"):
        return _Outcome(_INAPPLICABLE)
    if ctx.certifications is None:
        return _unknown(["seller.certifications"])
    required = ctx.required_certs
    held = set(ctx.certifications)
    hit = len([token for token in required if token in held])
    fired = []
    if required and hit == len(required):
        verified = ctx.seller.get("certifications_verified") is True
        key = ("all_required_certifications_held_verified" if verified
               else "all_required_certifications_held_unverified")
        signals = _criterion_signal_table(ctx, "compliance_readiness", "S-CP1")
        fired.append(key if key in signals else "all_required_certifications_held")
    elif hit >= 1 and _dec(hit) / _dec(len(required)) >= Decimal("0.5"):
        fired.append("most_required_certifications_held")
    elif hit >= 1:
        fired.append("some_required_certifications_held")
    else:
        fired.append("no_required_certifications_held")
    return _scored(fired, _ids(ctx.ev.get("certifications", [])))


def _crit_s_cp2(ctx):
    if ctx.flags.get("query.preferred_certifications_absent"):
        return _Outcome(_INAPPLICABLE)
    if ctx.certifications is None:
        return _unknown(["seller.certifications"])
    preferred = ctx.preferred_certs
    held = set(ctx.certifications)
    hit = [token for token in preferred if token in held]
    fired = []
    if preferred and len(hit) == len(preferred):
        fired.append("all_preferred_certifications_held")
    elif hit:
        fired.append("some_preferred_certifications_held")
    return _scored(fired, _ids(ctx.ev.get("certifications", [])))


def _crit_s_cp3(ctx):
    if ctx.flags.get("query.destination_country_absent"):
        return _Outcome(_INAPPLICABLE)
    registrations = _known_list(ctx.seller, "regulatory_registrations")
    if registrations is None:
        return _unknown(["seller.regulatory_registrations"])
    destination = ctx.query.get("destination_country")
    matches = [
        entry
        for entry in registrations
        if isinstance(entry, dict) and entry.get("market") == destination
    ]
    statuses = [entry.get("registration_status") for entry in matches]
    if statuses and all(status == _common.UNKNOWN for status in statuses):
        return _unknown(["seller.regulatory_registrations"])
    evidence_ids = []
    for entry in matches:
        for ident in entry.get("evidence_ids") or []:
            if ident not in evidence_ids:
                evidence_ids.append(ident)
    if "registered" in statuses:
        fired = ["registered_in_destination_market"]
    elif "in_progress" in statuses:
        fired = ["registration_in_progress"]
    else:
        fired = ["not_registered_in_destination_market"]
    return _scored(fired, sorted(evidence_ids) or _ids(ctx.ev.get("regulatory_registrations", [])))


def _crit_s_cp4(ctx):
    if ctx.certifications is None:
        return _unknown(["seller.certifications"])
    held = set(ctx.certifications)
    fired = []
    if held & set(_CERT_BASELINE_QUALITY):
        fired.append("iso22716_or_cgmp_held")
    if held & set(_CERT_OTHER_QUALITY):
        fired.append("other_quality_certification_held")
    return _scored(fired, _ids(ctx.ev.get("certifications", [])))


def _crit_s_ex1(ctx):
    markets = _known_list(ctx.seller, "export_markets")
    regions = _known_list(ctx.seller, "export_regions")
    if markets is None and regions is None:
        return _unknown(["seller.export_markets", "seller.export_regions"])
    markets = markets or []
    regions = regions or []
    destination = ctx.query.get("destination_country")
    region = set(ctx.query.get("region_countries") or [])
    own_country = ctx.seller.get("country")
    overseas = [code for code in markets if code != own_country]
    signals = _criterion_signal_table(ctx, "export_readiness", "S-EX1")
    fired = []
    if destination and destination != _common.UNKNOWN and destination in markets:
        fired.append("exports_to_destination_market")
    if region and set(markets) & region:
        fired.append("exports_to_same_region")
    # A stated region token is real export evidence that export_markets (alpha-2 only)
    # cannot hold, priced one tier below its country-level equivalent. The membership
    # test never returns a country list, so no region is ever expanded onto a record.
    if (
        "exports_to_destination_region" in signals
        and destination
        and destination != _common.UNKNOWN
        and _common.region_covers_country(regions, destination)
    ):
        fired.append("exports_to_destination_region")
    if overseas:
        fired.append("exports_to_any_overseas_market")
    if regions and "exports_to_stated_region" in signals:
        fired.append("exports_to_stated_region")
    if not fired:
        fired.append("no_export_evidence")
    used = _ids(ctx.ev.get("export_markets", [])) + _ids(ctx.ev.get("export_regions", []))
    return _scored(sorted(fired), sorted(set(used)))


def _crit_s_ex2(ctx):
    state = _tri(ctx.seller, "english_site")
    items = ctx.ev.get("english_site", [])
    partial = [
        item for item in items if _is_official(item) and _RE_PARTIAL_ENGLISH.search(_evidence_text(item))
    ]
    if state == _common.UNKNOWN and not partial:
        return _unknown(["seller.english_site"])
    fired = []
    if state is True:
        fired.append("english_site_true")
    if partial:
        fired.append("partial_english_materials")
    if state is False:
        fired.append("english_site_false")
    return _scored(sorted(fired), _ids(partial or items))


def _crit_s_ex3(ctx):
    state = _tri(ctx.seller, "overseas_partner_signal")
    exhibitor = [
        item
        for item in _evidence(ctx.seller)
        if item.get("source_type") == "trade_show" and _RE_EXHIBITOR.search(_evidence_text(item))
    ]
    channels = _known_list(ctx.seller, "contact_channels") or []
    channel_hit = [
        channel
        for channel in channels
        if isinstance(channel, dict)
        and _RE_EXPORT_CHANNEL.search(
            " ".join([str(channel.get("value") or ""), str(channel.get("label") or "")])
        )
    ]
    if state == _common.UNKNOWN and not exhibitor and not channel_hit:
        return _unknown(["seller.overseas_partner_signal"])
    fired = []
    if state is True:
        fired.append("overseas_partner_signal_true")
    if exhibitor:
        fired.append("trade_show_exhibitor")
    if channel_hit:
        fired.append("export_inquiry_channel")
    evidence_ids = _ids(exhibitor + ctx.ev.get("overseas_partner_signal", []))
    for channel in channel_hit:
        for ident in channel.get("evidence_ids") or []:
            if ident not in evidence_ids:
                evidence_ids.append(ident)
    return _scored(sorted(fired), sorted(evidence_ids))


CRITERION_HANDLERS = {
    "S-PF1": _crit_s_pf1,
    "S-PF2": _crit_s_pf2,
    "S-PF3": _crit_s_pf3,
    "S-CM1": _crit_s_cm1,
    "S-CM2": _crit_s_cm2,
    "S-CM3": _crit_s_cm3,
    "S-CM4": _crit_s_cm4,
    "S-OP1": _crit_s_op1,
    "S-OP2": _crit_s_op2,
    "S-OP3": _crit_s_op3,
    "S-CP1": _crit_s_cp1,
    "S-CP2": _crit_s_cp2,
    "S-CP3": _crit_s_cp3,
    "S-CP4": _crit_s_cp4,
    "S-EX1": _crit_s_ex1,
    "S-EX2": _crit_s_ex2,
    "S-EX3": _crit_s_ex3,
}


# --------------------------------------------------------------------------------------
# dimension adjustments - SCORING-CONTRACT 2.1 .. 2.6
# --------------------------------------------------------------------------------------


def _adj_category_no_match_verified(ctx):
    if not ctx.categories:
        return False
    return _common.category_relation(ctx.categories, ctx.query_categories) == "none"


def _adj_required_model_false(ctx):
    model = ctx.query.get("commercial_model") or "either"
    if model == "either":
        return False
    flag = _model_flag(model)
    return flag is not None and _tri(ctx.seller, flag) is False


def _adj_moq_negotiable(ctx):
    for item in ctx.ev.get("moq", []):
        if _is_official(item) and _RE_NEGOTIABLE.search(_evidence_text(item)):
            return True
    return False


def _adj_sample_or_trial(ctx):
    for item in _evidence(ctx.seller):
        if _is_official(item) and _RE_SAMPLE.search(_evidence_text(item)):
            return True
    return False


def _adj_moq_unit_mismatch(ctx):
    return ctx.moq_value is _UNIT_MISMATCH


def _adj_certifications_verified_official(ctx):
    return _tri(ctx.seller, "certifications_verified") is True


def _best_cert_item(ctx, token):
    """Best evidence item backing one certification token (SCORING-CONTRACT 4.3 ordering)."""
    candidates = []
    for item in ctx.ev.get("certifications", []):
        value = item.get("value")
        values = value if isinstance(value, (list, tuple)) else [value]
        tokens = set()
        for entry in values:
            if isinstance(entry, str):
                normalised = _common.normalize_certification(entry)
                if normalised:
                    tokens.add(normalised)
        text = _evidence_text(item)
        if token in tokens or re.search(re.escape(token.replace("_", "")), re.sub(r"[^A-Za-z0-9]+", "", text), re.I):
            candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (_tier(item), str(item.get("evidence_id") or "")))
    return candidates[0]


def _adj_certification_claim_unverified(ctx):
    for token in ctx.certifications or []:
        best = _best_cert_item(ctx, token)
        if best is not None and _tier(best) in (4, 5):
            return True
    return False


def _adj_excluded_market_includes_destination(ctx):
    destination = ctx.query.get("destination_country")
    markets = _known_list(ctx.seller, "excluded_markets") or []
    return bool(destination) and destination != _common.UNKNOWN and destination in markets


ADJUSTMENT_PREDICATES = {
    "category_no_match_verified": _adj_category_no_match_verified,
    "required_model_false": _adj_required_model_false,
    "moq_negotiable_statement": _adj_moq_negotiable,
    "sample_or_trial_order_accepted": _adj_sample_or_trial,
    "moq_unit_mismatch": _adj_moq_unit_mismatch,
    "certifications_verified_official": _adj_certifications_verified_official,
    "certification_claim_unverified": _adj_certification_claim_unverified,
    "excluded_market_includes_destination": _adj_excluded_market_includes_destination,
}


# --------------------------------------------------------------------------------------
# dimension scoring
# --------------------------------------------------------------------------------------


def _unknown_points(config, max_points):
    neutral = _dec(config["unknown"]["neutral_base"])
    factor = _dec(config["unknown"]["penalty_factor"])
    return _dec(_common.round_half_up(_dec(max_points) * (neutral / Decimal(100)) * factor))


def _score_dimension(component, dimension_cfg, ctx, skipped_rules):
    """Score one seller dimension against the projected query.

    Returns (score, details, penalties). SCORING-CONTRACT 0.3, 0.5.
    """
    config = ctx.config
    details = []
    penalties = []
    earned_total = Decimal(0)
    max_total = Decimal(0)

    for criterion in dimension_cfg.get("criteria", []):
        criterion_id = criterion["criterion_id"]
        handler = CRITERION_HANDLERS.get(criterion_id)
        if handler is None:
            raise _common.DataError(
                "no predicate implemented for criterion %s" % (criterion_id,)
            )
        max_points = _dec(criterion["max_points"])
        inapplicable_when = criterion.get("inapplicable_when") or []
        if any(ctx.flags.get(key) for key in inapplicable_when):
            outcome = _Outcome(_INAPPLICABLE)
        else:
            outcome = handler(ctx)

        detail = {
            "dimension": component,
            "criterion_id": criterion_id,
            "label": criterion["label"],
            "max_points": _json_num(max_points),
            "earned_points": 0.0,
        }

        if outcome.state == _INAPPLICABLE:
            detail["state"] = _INAPPLICABLE
            if outcome.note:
                detail["note"] = _trim(outcome.note, 500)
            details.append(detail)
            continue

        if outcome.state == "unknown":
            earned = _unknown_points(config, max_points)
            rule_id = None
            for candidate_rule, candidate_criterion in sorted(_HF_PENALTY_CRITERION.items()):
                if candidate_criterion == criterion_id and candidate_rule in skipped_rules:
                    rule_id = candidate_rule
                    break
            inputs = sorted(outcome.unknown_inputs)
            note = outcome.note or ("%s not published." % (", ".join(inputs),))
            if rule_id is not None:
                note = "%s hard filter %s skipped." % (note.rstrip(". ") + ";", rule_id)
            penalties.append(
                {
                    "dimension": component,
                    "criterion_id": criterion_id,
                    "label": criterion["label"],
                    "unknown_inputs": inputs,
                    "criterion_max_points": _json_num(max_points),
                    "neutral_base": _json_num(_dec(config["unknown"]["neutral_base"])),
                    "penalty_factor": _json_num(_dec(config["unknown"]["penalty_factor"])),
                    "applied_points": _json_num(earned),
                    "note": _trim(note, 500),
                }
            )
            detail["state"] = "unknown"
            detail["note"] = _trim(note, 500)
        else:
            signals = criterion.get("signals") or {}
            fired = sorted(set(outcome.fired))
            for key in fired:
                if key not in signals:
                    raise _common.DataError(
                        "signal '%s' fired on %s but is absent from the config"
                        % (key, criterion_id)
                    )
            points = [_dec(signals[key]) for key in fired]
            if criterion.get("aggregation") == "sum_capped":
                earned = min(sum(points, Decimal(0)), max_points)
            else:
                earned = max(points) if points else Decimal(0)
            earned = _clamp(earned, Decimal(0), max_points)
            detail["state"] = "scored"
            if fired:
                detail["signals_fired"] = fired
            if outcome.note:
                detail["note"] = _trim(outcome.note, 500)

        detail["earned_points"] = _common.round_half_up(earned, 2)
        if outcome.evidence_ids:
            detail["evidence_ids"] = sorted(set(outcome.evidence_ids))[:50]
        details.append(detail)
        earned_total += earned
        max_total += max_points

    if max_total == 0:
        return None, details, penalties

    raw = Decimal(100) * earned_total / max_total
    score = _dec(_common.round_half_up(raw))
    fired_adjustments = []
    for name in sorted(dimension_cfg.get("adjustments") or {}):
        predicate = ADJUSTMENT_PREDICATES.get(name)
        if predicate is None:
            raise _common.DataError("no predicate implemented for adjustment '%s'" % (name,))
        if predicate(ctx):
            delta = _dec(dimension_cfg["adjustments"][name])
            score += delta
            fired_adjustments.append(name)
    if fired_adjustments:
        ctx.notes.append(
            "%s adjustments applied: %s" % (component, ", ".join(fired_adjustments))
        )
    return int(_clamp(score, Decimal(0), Decimal(100))), details, penalties


def _confidence(ctx, evidence_quality):
    """SCORING-CONTRACT 0.7. Never reads any score of the company itself."""
    claims = ctx.config["evidence"]["material_claims"]["seller"]
    unknown_count = 0
    for claim in claims:
        if claim not in ctx.seller or _common.is_unknown(ctx.seller.get(claim)):
            unknown_count += 1
    coverage_factor = max(Decimal("0.5"), Decimal(1) - Decimal("0.05") * Decimal(unknown_count))
    stale_multiplier = Decimal("0.6") if ctx.seller.get("stale") is True else Decimal(1)
    resolved_fields = set()
    for conflict in ctx.seller.get("conflicts") or []:
        if isinstance(conflict, dict) and conflict.get("field"):
            resolved_fields.add(conflict["field"])
    dangling = set()
    for item in _evidence(ctx.seller):
        links = item.get("conflicts_with")
        if links and item.get("claim") not in resolved_fields:
            dangling.add(item.get("claim"))
    conflict_multiplier = Decimal("0.85") if dangling else Decimal(1)
    value = (
        _dec(evidence_quality) / Decimal(100) * coverage_factor * stale_multiplier * conflict_multiplier
    )
    rounded = _dec(_common.round_half_up(value, 2))
    return float(_clamp(rounded, Decimal("0.05"), Decimal("1.0")))


# --------------------------------------------------------------------------------------
# stage 1 - hard filters
# --------------------------------------------------------------------------------------


class _RuleResult(object):
    __slots__ = ("rule_id", "applicable", "skipped", "passed", "failed_rule")

    def __init__(self, rule_id, applicable=False, skipped=False, passed=True, failed_rule=None):
        self.rule_id = rule_id
        self.applicable = applicable
        self.skipped = skipped
        self.passed = passed
        self.failed_rule = failed_rule


def _fail(rule, observed, required, evidence_ids=None):
    failed = {
        "rule_id": rule["rule_id"],
        "rule_name": rule["name"],
        "reason": _trim(_render_message(rule["failure_message_template"], observed, required), 500),
        "observed_value": observed,
        "required_value": required,
    }
    if evidence_ids:
        failed["evidence_ids"] = sorted(set(evidence_ids))[:50]
    return _RuleResult(rule["rule_id"], True, False, False, failed)


def _hard_filters(ctx, rules):
    """Evaluate every applicable rule. No short-circuit (config hard_filters_note)."""
    results = []
    seller = ctx.seller
    query = ctx.query
    for rule in rules:
        rule_id = rule["rule_id"]

        if rule_id == "HF-01":
            required = sorted(set(ctx.query_categories))
            if not required:
                results.append(_RuleResult(rule_id))
                continue
            if ctx.categories is None:
                results.append(_RuleResult(rule_id, skipped=True))
                continue
            relation = _common.category_relation(ctx.categories, required)
            if relation in ("exact", "parent"):
                results.append(_RuleResult(rule_id, True))
            else:
                results.append(
                    _fail(rule, list(ctx.categories), required, _ids(ctx.ev.get("product_categories", [])))
                )

        elif rule_id == "HF-02":
            model = query.get("commercial_model") or "either"
            if model == "either":
                results.append(_RuleResult(rule_id))
                continue
            flag = _model_flag(model)
            state = _tri(seller, flag) if flag else _common.UNKNOWN
            if state == _common.UNKNOWN:
                results.append(_RuleResult(rule_id, skipped=True))
            elif state is False:
                results.append(_fail(rule, False, model, _ids(ctx.ev.get(flag, []))))
            else:
                results.append(_RuleResult(rule_id, True))

        elif rule_id == "HF-03":
            ceiling = query.get("max_moq")
            if not _is_number(ceiling):
                results.append(_RuleResult(rule_id))
                continue
            value = ctx.moq_value
            if value is _UNKNOWN or value is _UNIT_MISMATCH:
                results.append(_RuleResult(rule_id, skipped=True))
                continue
            ratio = _dec(ctx.config["hard_filter_tolerances"]["moq_overshoot_ratio"])
            if value <= _dec(ceiling) * (Decimal(1) + ratio):
                results.append(_RuleResult(rule_id, True))
            else:
                results.append(
                    _fail(rule, _json_num(value), _json_num(_dec(ceiling)), _ids(ctx.ev.get("moq", [])))
                )

        elif rule_id == "HF-04":
            required = ctx.required_certs
            verified = _tri(seller, "certifications_verified")
            if not required or verified is not True:
                # applies_when is literally false: neither evaluated nor skipped (3.1).
                results.append(_RuleResult(rule_id))
                continue
            if ctx.certifications is None:
                results.append(_RuleResult(rule_id, skipped=True))
                continue
            held = set(ctx.certifications)
            if set(required).issubset(held):
                results.append(_RuleResult(rule_id, True))
            else:
                results.append(
                    _fail(
                        rule,
                        sorted(held),
                        sorted(set(required)),
                        _ids(ctx.ev.get("certifications", [])),
                    )
                )

        elif rule_id == "HF-05":
            destination = query.get("destination_country")
            markets = _known_list(seller, "excluded_markets")
            if not destination or destination == _common.UNKNOWN or not markets:
                results.append(_RuleResult(rule_id))
                continue
            if destination in markets:
                results.append(
                    _fail(rule, list(markets), destination, _ids(ctx.ev.get("excluded_markets", [])))
                )
            else:
                results.append(_RuleResult(rule_id, True))

        elif rule_id == "HF-06":
            status = seller.get("operational_status", _common.UNKNOWN)
            if status in ("closed", "unreachable"):
                results.append(
                    _fail(rule, status, "active or unknown", _ids(ctx.ev.get("operational_status", [])))
                )
            else:
                results.append(_RuleResult(rule_id, True))

        elif rule_id == "HF-07":
            ceiling = query.get("max_lead_time_days")
            if not _is_number(ceiling):
                results.append(_RuleResult(rule_id))
                continue
            value = ctx.lead_time
            if value is _UNKNOWN:
                results.append(_RuleResult(rule_id, skipped=True))
                continue
            ratio = _dec(ctx.config["hard_filter_tolerances"]["lead_time_overshoot_ratio"])
            if value <= _dec(ceiling) * (Decimal(1) + ratio):
                results.append(_RuleResult(rule_id, True))
            else:
                results.append(
                    _fail(
                        rule,
                        _json_num(value),
                        _json_num(_dec(ceiling)),
                        _ids(ctx.ev.get("lead_time_days", [])),
                    )
                )

        elif rule_id == "HF-08":
            required = [code for code in (query.get("required_seller_countries") or []) if code]
            excluded = [code for code in (query.get("excluded_seller_countries") or []) if code]
            if not required and not excluded:
                results.append(_RuleResult(rule_id))
                continue
            country = seller.get("country")
            if country is None or _common.is_unknown(country):
                results.append(_RuleResult(rule_id, skipped=True))
                continue
            ok = (not required or country in required) and country not in excluded
            if ok:
                results.append(_RuleResult(rule_id, True))
            else:
                results.append(
                    _fail(rule, country, sorted(required) or sorted(excluded), _ids(ctx.ev.get("country", [])))
                )

        else:
            raise _common.DataError("unknown hard-filter rule '%s' in the config" % (rule_id,))

    return results


# --------------------------------------------------------------------------------------
# RFQ projection and readiness
# --------------------------------------------------------------------------------------

_READINESS_FIELDS = (
    "destination_country",
    "product_category",
    "product_description",
    "quantity",
    "max_moq",
    "commercial_model",
    "required_certifications",
    "timeline",
    "target_price",
    "buyer_id",
)


def _readiness_filled(rfq, field):
    if field not in rfq:
        return False
    value = rfq.get(field)
    if value is None:
        return False
    if isinstance(value, str) and (value == _common.UNKNOWN or value.strip() == ""):
        return False
    if field == "required_certifications":
        # Present at all counts as filled, including a deliberate empty list.
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return False
    return True


def _rfq_readiness(rfq):
    filled = 0
    missing = []
    for field in _READINESS_FIELDS:
        if _readiness_filled(rfq, field):
            filled += 1
        else:
            missing.append(field)
    score = _common.round_half_up(Decimal(100) * Decimal(filled) / Decimal(len(_READINESS_FIELDS)))
    return int(score), {"filled": filled, "total": len(_READINESS_FIELDS), "missing_fields": sorted(missing)}


def _project_rfq(rfq, base_query, as_of, notes):
    """BUILD-CONTRACT 6.1: project the RFQ onto the section 3.9 query surface."""
    query = dict(base_query or {})

    categories = []
    primary = rfq.get("product_category")
    if isinstance(primary, str) and primary and not _common.is_unknown(primary):
        categories.append(primary)
    for slug in rfq.get("product_categories_extra") or []:
        if slug not in categories:
            categories.append(slug)
    normalised = []
    for slug in categories:
        token = _common.normalize_category(slug)
        if token is None:
            notes.append(
                "RFQ category token '%s' is a vertical marker, not a category: dropped "
                "(BUILD-CONTRACT 8.5)" % (slug,)
            )
            continue
        if token not in normalised:
            normalised.append(token)
    query["product_categories"] = normalised

    forms = rfq.get("product_forms")
    query["product_forms"] = list(forms) if isinstance(forms, list) else []
    query["commercial_model"] = rfq.get("commercial_model") or "either"
    if "quantity" in rfq:
        query["quantity"] = rfq.get("quantity")
    query["max_moq"] = rfq.get("max_moq")

    unit = rfq.get("moq_unit")
    if unit is None:
        quantity = rfq.get("quantity")
        if isinstance(quantity, dict) and quantity.get("unit"):
            unit = quantity.get("unit")
    query["moq_unit"] = unit if unit is not None else "units"

    for key in ("required_certifications", "preferred_certifications"):
        value = rfq.get(key)
        query[key] = list(value) if isinstance(value, list) else []
    for key in ("required_seller_countries", "excluded_seller_countries"):
        value = rfq.get(key)
        query[key] = list(value) if isinstance(value, list) else []

    destination = rfq.get("destination_country")
    query["destination_country"] = destination
    query["country"] = destination

    explicit = rfq.get("max_lead_time_days")
    if _is_number(explicit):
        query["max_lead_time_days"] = explicit
    else:
        deadline = None
        timeline = rfq.get("timeline")
        if isinstance(timeline, str) and not _common.is_unknown(timeline):
            deadline = timeline
        elif isinstance(timeline, dict) and timeline.get("end"):
            deadline = timeline.get("end")
        if deadline:
            days = _common.days_between(as_of, deadline)
            if days < 0:
                notes.append(
                    "rfq.timeline deadline %s is before as_of %s: max_lead_time_days clamped to 0"
                    % (deadline, as_of)
                )
                days = 0
            query["max_lead_time_days"] = days
        else:
            query["max_lead_time_days"] = None
    return query


def _condition_flags(query):
    def absent(key):
        value = query.get(key)
        if value is None:
            return True
        if isinstance(value, str):
            return value == "" or _common.is_unknown(value)
        if isinstance(value, (list, tuple, dict)):
            return len(value) == 0
        return False

    return {
        "query.country_absent": absent("country"),
        "query.product_categories_absent": absent("product_categories"),
        "query.product_forms_absent": absent("product_forms"),
        "query.channels_absent": absent("channels"),
        "query.required_certifications_absent": absent("required_certifications"),
        "query.preferred_certifications_absent": absent("preferred_certifications"),
        "query.destination_country_absent": absent("destination_country"),
        "query.company_types_absent": absent("company_types"),
    }


# --------------------------------------------------------------------------------------
# rationale / risks (BUILD-CONTRACT 6.6)
# --------------------------------------------------------------------------------------


def _synthesise_rationale(components, details, contributions, seller):
    claim_of = {}
    for item in _evidence(seller):
        ident = item.get("evidence_id")
        if isinstance(ident, str):
            claim_of[ident] = item.get("claim") or "evidence"
    ordered = sorted(
        components, key=lambda name: (-_dec(contributions.get(name, 0)), name)
    )
    out = []
    for component in ordered:
        if len(out) >= 2:
            break
        best = None
        for detail in details:
            if detail["dimension"] != component or not detail.get("signals_fired"):
                continue
            if not detail.get("evidence_ids"):
                continue
            points = _dec(detail.get("earned_points") or 0)
            if best is None or points > best[0]:
                best = (points, detail)
        if best is None:
            continue
        detail = best[1]
        signal = sorted(detail["signals_fired"])[0]
        evidence_ids = list(detail["evidence_ids"])
        claim = claim_of.get(evidence_ids[0], "evidence")
        out.append(
            {
                "statement": _trim(
                    "%s: %s (%s)" % (COMPONENT_LABELS[component], signal, claim), 1000
                ),
                "evidence_ids": evidence_ids[:50],
                "component": component,
            }
        )
    return out


def _synthesise_risks(penalties, config, extra):
    risks = []
    for entry in penalties:
        risks.append(
            {
                "statement": _trim(
                    entry.get("note") or ("%s is unverified" % (entry["label"],)), 1000
                ),
                "severity": "medium",
                "evidence_ids": [],
                "category": "evidence_gap",
            }
        )
    limit = _common.unknown_flag_limit(config, "match")
    if len(penalties) > int(limit):
        # NAME the claims, never assert a bare count (scoring.config.json
        # unknown.flag_behaviour). A handful of unpublished commercial terms is this
        # vertical's baseline, so "5 material facts unverified" tells a reviewer
        # nothing while naming them says what to ask the seller for.
        labels = []
        for entry in penalties:
            label = entry.get("label")
            if isinstance(label, str) and label and label not in labels:
                labels.append(label)
        risks.append(
            {
                "statement": _trim(
                    "확인 필요 / needs verification: %s" % ("; ".join(labels),), 1000
                ),
                "severity": "high",
                "evidence_ids": [],
                "category": "evidence_gap",
            }
        )
    risks.extend(extra)
    return risks[:20]


# --------------------------------------------------------------------------------------
# rerank
# --------------------------------------------------------------------------------------


def _rerank_slot(config, rfq, works):
    rerank_cfg = config["match"]["rerank"]
    top_n = int(rerank_cfg["applies_to_top_n"])
    order = _common.stable_sort(works, [("base_score", "desc"), ("canonical_domain", "asc")])
    candidates = []
    for position, work in enumerate(order, start=1):
        seller = work["seller"]
        reasoning_fields = []
        for item in _evidence(seller):
            quote = item.get("quote_or_summary")
            if not isinstance(quote, str) or not quote:
                continue
            reasoning_fields.append(
                {
                    "evidence_id": item.get("evidence_id"),
                    "claim": item.get("claim"),
                    "source_url": item.get("source_url"),
                    "quote_or_summary": quote,
                }
            )
        candidates.append(
            {
                "seller_id": work["seller_id"],
                "seller_name": work["seller_name"],
                "canonical_domain": work["canonical_domain"],
                "base_score_rank": position,
                "base_score": work["base_score"],
                "component_scores": dict(work["component_scores"]),
                "eligible": position <= top_n,
                "reason_over": reasoning_fields[:50],
            }
        )
    return {
        "stage": "semantic_rerank",
        "owner": "agent",
        "instruction": (
            "Stage 3 is the agent's judgement, not this script's. Propose at most one bounded "
            "adjustment per eligible candidate and hand them back with "
            "--rerank-adjustments FILE as {seller_id: {delta, rationale, evidence_ids, "
            "model_note?}}. Every adjustment requires a rationale and at least one "
            "evidence_id, |delta| must not exceed max_delta, and an adjustment may never "
            "resurrect a hard-filter failure (INV-08)."
        ),
        "input_flag": "--rerank-adjustments",
        "max_delta": _json_num(_dec(rerank_cfg["max_delta"])),
        "applies_to_top_n": top_n,
        "require_rationale": bool(rerank_cfg["require_rationale"]),
        "require_evidence_ids": bool(rerank_cfg["require_evidence_ids"]),
        "allowed_inputs": list(rerank_cfg["allowed_inputs"]),
        "forbidden_inputs": list(rerank_cfg["forbidden_inputs"]),
        "rfq_product_description": rfq.get("product_description") or "",
        "candidates": candidates,
    }


def _load_rerank(path, works, excluded_ids, config, notes):
    """Validate the agent's bounded adjustments, then apply them under the ordering guard."""
    rerank_cfg = config["match"]["rerank"]
    max_delta = int(rerank_cfg["max_delta"])
    top_n = int(rerank_cfg["applies_to_top_n"])
    by_id = dict((work["seller_id"], work) for work in works)

    proposals = {}
    if path is not None:
        raw = _read_json_file(path, "--rerank-adjustments")
        if not isinstance(raw, dict):
            raise _common.DataError(
                "rerank adjustments must be a JSON object mapping seller_id to an adjustment"
            )
        order = _common.stable_sort(works, [("base_score", "desc"), ("canonical_domain", "asc")])
        eligible = set(work["seller_id"] for work in order[:top_n])
        for seller_id in sorted(raw):
            entry = raw[seller_id]
            if not isinstance(entry, dict):
                raise _common.DataError(
                    "rerank adjustment for %s is not an object" % (seller_id,)
                )
            if seller_id in excluded_ids:
                raise _common.DataError(
                    "rerank adjustment for %s references a hard-filter-excluded seller; the "
                    "rerank can never resurrect a hard-filter failure (INV-08)" % (seller_id,)
                )
            if seller_id not in by_id:
                raise _common.DataError(
                    "rerank adjustment references unknown seller_id '%s'" % (seller_id,)
                )
            delta = entry.get("delta")
            if isinstance(delta, bool) or not isinstance(delta, int):
                raise _common.DataError(
                    "rerank delta for %s must be an integer (match_score is an integer)"
                    % (seller_id,)
                )
            if abs(delta) > max_delta:
                raise _common.DataError(
                    "rerank delta %d for %s exceeds match.rerank.max_delta %d"
                    % (delta, seller_id, max_delta)
                )
            rationale = entry.get("rationale")
            if rerank_cfg["require_rationale"] and (
                not isinstance(rationale, str) or not rationale.strip()
            ):
                raise _common.DataError(
                    "rerank adjustment for %s has no rationale (match.rerank.require_rationale)"
                    % (seller_id,)
                )
            evidence_ids = entry.get("evidence_ids")
            if rerank_cfg["require_evidence_ids"] and (
                not isinstance(evidence_ids, list) or not evidence_ids
            ):
                raise _common.DataError(
                    "rerank adjustment for %s carries no evidence_ids "
                    "(match.rerank.require_evidence_ids)" % (seller_id,)
                )
            known_ids = set(
                item.get("evidence_id") for item in _evidence(by_id[seller_id]["seller"])
            )
            unresolved = [ident for ident in evidence_ids or [] if ident not in known_ids]
            if unresolved:
                raise _common.DataError(
                    "rerank evidence_ids %s for %s do not resolve in that seller's evidence "
                    "(INV-22)" % (sorted(unresolved), seller_id)
                )
            if seller_id not in eligible:
                notes.append(
                    "rerank adjustment for %s ignored: outside the top %d by base_score "
                    "(match.rerank.applies_to_top_n)" % (seller_id, top_n)
                )
                continue
            proposals[seller_id] = {
                "delta": delta,
                "rationale": rationale.strip(),
                "evidence_ids": list(evidence_ids or []),
                "model_note": entry.get("model_note"),
            }

    # Ordering guard, SCORING-CONTRACT 3.3 rule 4: walk in descending base order so every
    # blocker is already final.
    order = _common.stable_sort(works, [("base_score", "desc"), ("canonical_domain", "asc")])
    finalised = []
    for work in order:
        base = work["base_score"]
        proposal = proposals.get(work["seller_id"])
        proposed = _clamp(base + (proposal["delta"] if proposal else 0), 0, 100)
        blockers = [done for done in finalised if done["base_score"] - base > max_delta]
        ceiling = min([done["match_score"] for done in blockers]) if blockers else 100
        final = min(proposed, ceiling)
        work["match_score"] = final
        applied_delta = final - base
        if proposal is None:
            work["rerank"] = {"applied": False, "delta": 0, "rationale": "", "evidence_ids": []}
        else:
            record = {
                "applied": True,
                "delta": applied_delta,
                "rationale": _trim(proposal["rationale"], 2000),
                "evidence_ids": proposal["evidence_ids"][:50],
            }
            model_note = proposal.get("model_note") or ""
            if final != proposed:
                model_note = (
                    model_note + " (proposed %d, clipped by the ordering guard)" % (proposal["delta"],)
                ).strip()
            if model_note:
                record["model_note"] = _trim(model_note, 500)
            work["rerank"] = record
        finalised.append(work)
    return len([w for w in works if w["rerank"]["applied"]])


# --------------------------------------------------------------------------------------
# input plumbing
# --------------------------------------------------------------------------------------


def _read_json_file(path, label):
    if not os.path.isfile(path):
        raise _common.UsageError("%s: file not found: %s" % (label, path))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        raise _common.UsageError("%s: %s is not valid JSON (%s)" % (label, path, exc))
    except OSError as exc:
        raise _common.UsageError("%s: cannot read %s (%s)" % (label, path, exc))


def _as_records(payload, label):
    if payload is None:
        return []
    if isinstance(payload, dict):
        for key in ("records", "sellers"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise _common.UsageError("%s: expected an object, an array or an envelope" % (label,))


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Run the RFQ -> seller matching pipeline and emit one match-result document. "
            "Korean gloss: RFQ 기준으로 셀러를 필터링/점수화하여 매칭 결과 문서를 만든다."
        ),
    )
    parser.add_argument("-i", "--input", default=None, help="Input envelope JSON (default: stdin)")
    parser.add_argument("-o", "--output", default=None, help="Write the result here (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent=2 output")
    parser.add_argument("--as-of", dest="as_of", default=None, help="YYYY-MM-DD run date")
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None, help="Alternative schema dir")
    parser.add_argument("--no-validate", dest="validate", action="store_false", default=True)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--version", action="store_true", help="Print versions and exit")
    parser.add_argument("--rfq", default=None, help="RFQ document (required unless the envelope carries one)")
    parser.add_argument(
        "--sellers", default=None, help="Seller records (required unless the envelope carries them)"
    )
    parser.add_argument("--top", type=int, default=None, help="Truncate results to N candidates")
    parser.add_argument("--threshold", type=int, default=None, help="Override the resolved threshold")
    parser.add_argument(
        "--threshold-mode", dest="threshold_mode", choices=("fixed", "percentile"), default=None
    )
    parser.add_argument("--match-run-id", dest="match_run_id", default=None)
    parser.add_argument("--skill-version", dest="skill_version", default=None)
    parser.add_argument(
        "--include-excluded", dest="include_excluded", action="store_true", default=True
    )
    parser.add_argument("--no-include-excluded", dest="include_excluded", action="store_false")
    parser.add_argument(
        "--rerank-input",
        "--rerank-adjustments",
        dest="rerank_input",
        default=None,
        help=(
            "JSON map seller_id -> {delta, rationale, evidence_ids, model_note?} produced by the "
            "agent's stage-3 pass. Absent means every rerank.applied is false."
        ),
    )
    parser.add_argument(
        "--rationale-input",
        dest="rationale_input",
        default=None,
        help="JSON map seller_id -> {rationale: [...], risks: [...]} from the narrative pass",
    )
    return parser


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------


def _threshold(config, works, args):
    thresholds = config["thresholds"]
    mode = args.threshold_mode or thresholds["mode"]
    if args.threshold is not None:
        return {
            "mode": args.threshold_mode or "fixed",
            "value": int(_clamp(args.threshold, 0, 100)),
        }
    if mode == "percentile":
        population = sorted(work["base_score"] for work in works)
        block = {"mode": "percentile", "percentile": _json_num(_dec(thresholds["percentile"]))}
        block["population_size"] = len(population)
        minimum = int(thresholds["percentile_min_population"])
        if len(population) < minimum:
            block["mode"] = "fixed"
            block["value"] = int(thresholds["fixed"])
            block["fallback_reason"] = (
                "population %d is below thresholds.percentile_min_population %d"
                % (len(population), minimum)
            )
            return block
        percentile = _dec(thresholds["percentile"])
        exact = percentile * Decimal(len(population)) / Decimal(100)
        rank = int(exact)
        if Decimal(rank) < exact:  # nearest rank is a ceiling, 1-based
            rank += 1
        rank = max(1, min(rank, len(population)))
        value = population[rank - 1]
        block["value"] = int(max(value, int(thresholds["percentile_floor"])))
        return block
    return {"mode": "fixed", "value": int(thresholds["fixed"])}


def _no_match(config, works, excluded, threshold, considered, passed, notes):
    limit = int(config["match"]["no_match"]["max_relaxation_suggestions"])
    qualified = [work for work in works if work["match_score"] >= threshold["value"]]
    if qualified:
        return {"is_no_match": False, "reason": "", "relaxation_suggestions": []}, []

    if considered == 0:
        return (
            {
                "is_no_match": True,
                "reason": "No seller candidates were supplied to this run.",
                "relaxation_suggestions": [],
                "binding_rule_ids": [],
            },
            [],
        )

    if passed > 0 and works:
        top = max(work["match_score"] for work in works)
        suggested = top
        counted = len([w for w in works if w["match_score"] >= suggested])
        suggestions = [
            {
                "constraint": "threshold.value",
                "statement": _trim(
                    "lower the qualifying threshold from %d to %d to admit %d candidate(s)"
                    % (threshold["value"], suggested, counted),
                    500,
                ),
                "current_value": threshold["value"],
                "suggested_value": suggested,
                "expected_additional_candidates": counted,
            }
        ]
        nearest = [
            work["seller_id"]
            for work in _common.stable_sort(
                works, [("match_score", "desc"), ("canonical_domain", "asc")]
            )
        ][:5]
        return (
            {
                "is_no_match": True,
                "reason": _trim(
                    "%d of %d sellers passed every hard filter; the highest match score was %d "
                    "against a threshold of %d." % (passed, considered, top, threshold["value"]),
                    1000,
                ),
                "relaxation_suggestions": suggestions[:limit],
                "binding_rule_ids": [],
                "nearest_misses": nearest,
            },
            nearest,
        )

    # Every candidate was hard-filtered out: name the rules that rejected the most sellers.
    counts = {}
    only_blocker = {}
    for candidate in excluded:
        rule_ids = [rule["rule_id"] for rule in candidate["failed_rules"]]
        for rule_id in rule_ids:
            counts[rule_id] = counts.get(rule_id, 0) + 1
        if len(set(rule_ids)) == 1:
            only_blocker[rule_ids[0]] = only_blocker.get(rule_ids[0], 0) + 1
    binding = sorted(counts, key=lambda rule_id: (-counts[rule_id], rule_id))
    suggestions = []
    for rule_id in binding[:limit]:
        sample = None
        for candidate in excluded:
            for rule in candidate["failed_rules"]:
                if rule["rule_id"] == rule_id:
                    sample = rule
                    break
            if sample:
                break
        suggestions.append(
            {
                "constraint": _HF_CONSTRAINT.get(rule_id, rule_id),
                "statement": _trim(
                    "relax %s: %d seller(s) were rejected by %s (%s)"
                    % (
                        _HF_CONSTRAINT.get(rule_id, rule_id),
                        counts[rule_id],
                        rule_id,
                        sample["rule_name"] if sample else rule_id,
                    ),
                    500,
                ),
                "rule_id": rule_id,
                "current_value": sample.get("required_value") if sample else None,
                "expected_additional_candidates": only_blocker.get(rule_id, 0),
            }
        )
    suggestions.sort(
        key=lambda item: (-item["expected_additional_candidates"], item["constraint"])
    )
    reason = _trim(
        "No seller cleared every hard filter; %s rejected the most candidates."
        % (", ".join(binding[:3]) if binding else "the hard filters",),
        1000,
    )
    notes.append("no qualified match: %d of %d candidates were excluded" % (len(excluded), considered))
    return (
        {
            "is_no_match": True,
            "reason": reason,
            "relaxation_suggestions": suggestions,
            "binding_rule_ids": binding,
        },
        [],
    )


def main(argv):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        config = _common.load_config(args.config)
    except Exception as exc:  # ConfigError and anything the loader raises
        return _common.die(str(exc), 2)

    if args.version:
        sys.stdout.write(
            "%s skill_version=%s schema_version=%s score_version=%s\n"
            % (
                SCRIPT_NAME,
                _common.SKILL_VERSION,
                _common.SCHEMA_VERSION,
                config["score_version"],
            )
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
    except Exception as exc:  # never let a raw traceback escape main (BUILD-CONTRACT 7.5)
        return _common.die("%s: %s" % (type(exc).__name__, exc), 1)


def _run(args, config):
    notes = []
    envelope = {}
    rfq = None
    sellers = None

    if args.rfq is not None:
        rfq = _read_json_file(args.rfq, "--rfq")
    if args.sellers is not None:
        sellers = _as_records(_read_json_file(args.sellers, "--sellers"), "--sellers")

    if rfq is None or sellers is None:
        payload = _common.read_input(args)
        if isinstance(payload, dict):
            envelope = payload
        elif isinstance(payload, list):
            envelope = {"records": payload}
        else:
            raise _common.UsageError("input must be a JSON object or array")
        if rfq is None:
            rfq = envelope.get("rfq")
        if sellers is None:
            sellers = _as_records(
                envelope.get("records")
                if isinstance(envelope.get("records"), list)
                else envelope.get("sellers"),
                "envelope records",
            )

    if not isinstance(rfq, dict):
        raise _common.UsageError("no RFQ document: pass --rfq PATH or an envelope carrying 'rfq'")
    if sellers is None:
        sellers = []

    explicit = args.as_of
    if explicit is None and isinstance(envelope.get("as_of"), str):
        explicit = envelope["as_of"]
    as_of = _common.resolve_as_of(list(sellers) + [rfq], explicit)

    base_query = envelope.get("query") if isinstance(envelope.get("query"), dict) else {}
    query = _project_rfq(rfq, base_query, as_of, notes)
    flags = _condition_flags(query)

    rules = config["hard_filters"]
    components = list(config["match"]["weights"].keys())
    component_source = {}
    for component, source in config["match"]["component_sources"].items():
        component_source[component] = str(source).rsplit(".", 1)[-1]

    evidence_quality_fn = getattr(_common, "evidence_quality", None)
    if evidence_quality_fn is None:
        raise _common.DataError(
            "_common.evidence_quality is unavailable; the shared evidence-quality function is "
            "required by SCORING-CONTRACT section 4"
        )
    claim_set = config["evidence"]["material_claims"]["seller"]

    works = []
    excluded = []
    partial = False
    considered = 0

    rationale_map = {}
    if args.rationale_input is not None:
        raw = _read_json_file(args.rationale_input, "--rationale-input")
        if not isinstance(raw, dict):
            raise _common.DataError("--rationale-input must be a JSON object keyed by seller_id")
        rationale_map = raw

    for seller in sellers:
        considered += 1
        seller_id = seller.get("seller_id") or "SEL-unknown-%d" % (considered,)
        seller_name = seller.get("company_name") or seller_id
        domain = seller.get("canonical_domain") or _common.UNKNOWN
        try:
            ctx = _Ctx(seller, query, config, as_of, flags)
            rule_results = _hard_filters(ctx, rules)
        except _common.DataError as exc:
            partial = True
            notes.append("%s: %s" % (seller_id, exc))
            continue

        evaluated = sorted(r.rule_id for r in rule_results if r.applicable)
        skipped = sorted(r.rule_id for r in rule_results if r.skipped)
        failed = [r.failed_rule for r in rule_results if r.failed_rule is not None]

        if failed:
            excluded.append(
                {
                    "seller_id": seller_id,
                    "seller_name": seller_name,
                    "canonical_domain": domain,
                    "website": seller.get("website"),
                    "reason_summary": _trim(failed[0]["reason"], 500),
                    "failed_rules": failed,
                    "unknown_penalty_applied": [],
                    "notes": [_trim(note, 1000) for note in ctx.notes][:20],
                }
            )
            continue

        component_scores = {}
        contributions = {}
        details = []
        penalties = []
        extra_risks = []
        skipped_set = set(skipped)

        for component in components:
            dimension_key = component_source[component]
            dimension_cfg = config["seller"]["dimensions"][dimension_key]
            if dimension_cfg.get("computed_by") == "evidence_quality_function":
                score = int(evidence_quality_fn(seller, claim_set, as_of))
                component_scores[component] = int(_clamp(score, 0, 100))
                continue
            score, dim_details, dim_penalties = _score_dimension(
                component, dimension_cfg, ctx, skipped_set
            )
            if score is None:
                partial = True
                notes.append("%s: every criterion of %s was inapplicable" % (seller_id, component))
                score = 0
            component_scores[component] = score
            details.extend(dim_details)
            penalties.extend(dim_penalties)

        # Zero-weight pairing records for skipped rules no criterion accounted for (3.1).
        paired = set()
        for entry in penalties:
            for rule_id, criterion_id in _HF_PENALTY_CRITERION.items():
                if entry["criterion_id"] == criterion_id and rule_id in skipped_set:
                    paired.add(rule_id)
        for rule in rules:
            rule_id = rule["rule_id"]
            if rule_id not in skipped_set or rule_id in paired:
                continue
            unknown_inputs = sorted(
                name for name in rule.get("inputs", []) if name.startswith("seller.")
            )
            penalties.append(
                {
                    "dimension": "hard_filter",
                    "criterion_id": rule_id,
                    "label": rule["name"],
                    "unknown_inputs": unknown_inputs or ["seller.%s" % (rule_id,)],
                    "criterion_max_points": 0,
                    "neutral_base": _json_num(_dec(config["unknown"]["neutral_base"])),
                    "penalty_factor": _json_num(_dec(config["unknown"]["penalty_factor"])),
                    "applied_points": 0,
                    "note": _trim(
                        "%s not established; %s skipped. No criterion consumes this input, so "
                        "no points were withheld." % (", ".join(unknown_inputs) or rule_id, rule_id),
                        500,
                    ),
                }
            )
            if rule_id == "HF-08":
                extra_risks.append(
                    {
                        "statement": "seller country not established",
                        "severity": "medium",
                        "evidence_ids": [],
                        "category": "evidence_gap",
                    }
                )

        if ctx.required_certs and _tri(seller, "certifications_verified") is not True:
            extra_risks.append(
                {
                    "statement": "certification list not verified as exhaustive",
                    "severity": "medium",
                    "evidence_ids": [],
                    "category": "compliance",
                }
            )

        raw_total = Decimal(0)
        for component in components:
            weight = _dec(config["match"]["weights"][component])
            raw_total += _dec(component_scores[component]) * weight
            contributions[component] = _common.round_half_up(
                _dec(component_scores[component]) * weight, 2
            )
        base_score = int(_clamp(_dec(_common.round_half_up(raw_total)), Decimal(0), Decimal(100)))

        missing = []
        for entry in penalties:
            if entry["label"] not in missing:
                missing.append(entry["label"])

        works.append(
            {
                "seller": seller,
                "seller_id": seller_id,
                "seller_name": seller_name,
                "canonical_domain": domain,
                "base_score": base_score,
                "match_score": base_score,
                "component_scores": component_scores,
                "weighted_contributions": contributions,
                "component_details": details,
                "unknown_penalty_applied": penalties,
                "missing": [_trim(label, 200) for label in missing][:40],
                "extra_risks": extra_risks,
                "rules_evaluated": evaluated,
                "rules_skipped_unknown": skipped,
                "confidence": _confidence(ctx, component_scores["evidence_quality"]),
                "notes": [_trim(note, 1000) for note in ctx.notes][:20],
                "rerank": {"applied": False, "delta": 0, "rationale": "", "evidence_ids": []},
            }
        )

    excluded_ids = set(candidate["seller_id"] for candidate in excluded)
    reranked_count = _load_rerank(args.rerank_input, works, excluded_ids, config, notes)

    # Stage 3 aftermath: rationale, risks and the HF-00 evidence-sufficiency rendering gate.
    survivors = []
    for work in works:
        provided = rationale_map.get(work["seller_id"]) if isinstance(rationale_map, dict) else None
        if isinstance(provided, dict) and isinstance(provided.get("rationale"), list):
            rationale = []
            for item in provided["rationale"]:
                if not isinstance(item, dict) or not item.get("statement"):
                    raise _common.DataError(
                        "--rationale-input: a rationale item for %s has no statement"
                        % (work["seller_id"],)
                    )
                ids = item.get("evidence_ids")
                if not isinstance(ids, list) or not ids:
                    raise _common.DataError(
                        "--rationale-input: rationale '%s' for %s carries no evidence_ids "
                        "(PRD 15.3, INV-20)" % (item.get("statement"), work["seller_id"])
                    )
                entry = {
                    "statement": _trim(item["statement"], 1000),
                    "evidence_ids": list(ids)[:50],
                }
                if item.get("component") in COMPONENT_LABELS:
                    entry["component"] = item["component"]
                rationale.append(entry)
        else:
            rationale = _synthesise_rationale(
                list(config["match"]["weights"].keys()),
                work["component_details"],
                work["weighted_contributions"],
                work["seller"],
            )
        if isinstance(provided, dict) and isinstance(provided.get("risks"), list):
            risks = []
            for item in provided["risks"]:
                if not isinstance(item, dict) or not item.get("statement"):
                    continue
                risks.append(
                    {
                        "statement": _trim(item["statement"], 1000),
                        "severity": item.get("severity") or "medium",
                        "evidence_ids": list(item.get("evidence_ids") or [])[:50],
                        "category": item.get("category") or "other",
                    }
                )
        else:
            risks = _synthesise_risks(work["unknown_penalty_applied"], config, work["extra_risks"])

        evidenced = [item for item in rationale if item.get("evidence_ids")]
        if len(evidenced) < 2:
            # HF-00: a rendering gate evaluated after stage 3 (BUILD-CONTRACT 6.6.4).
            excluded.append(
                {
                    "seller_id": work["seller_id"],
                    "seller_name": work["seller_name"],
                    "canonical_domain": work["canonical_domain"],
                    "website": work["seller"].get("website"),
                    "reason_summary": "fewer than two evidenced fit reasons (PRD 15.3)",
                    "failed_rules": [
                        {
                            "rule_id": "HF-00",
                            "rule_name": "Evidence sufficiency",
                            "reason": "fewer than two evidenced fit reasons (PRD 15.3)",
                            "observed_value": len(evidenced),
                            "required_value": 2,
                        }
                    ],
                    "unknown_penalty_applied": list(work["unknown_penalty_applied"])[:40],
                    "notes": [_trim(note, 1000) for note in work["notes"]][:20],
                }
            )
            continue
        work["rationale"] = rationale
        work["risks"] = risks
        survivors.append(work)

    works = survivors
    # SCORING-CONTRACT 3.5: excluded_count == candidates_considered - passed_hard_filter, and
    # excluded[] carries the HF-00 rejects too (BUILD-CONTRACT 6.6.4), so the counter is taken
    # after the rendering gate. This also keeps threshold.population_size == passed_hard_filter,
    # which 3.5 requires and which _threshold() computes over these same survivors.
    passed_hard_filter = len(works)
    reranked_count = len([work for work in works if work["rerank"]["applied"]])
    threshold = _threshold(config, works, args)
    qualifying = [work for work in works if work["match_score"] >= threshold["value"]]
    ordered = _common.stable_sort(
        qualifying, [("match_score", "desc"), ("canonical_domain", "asc")]
    )
    top_n = args.top if args.top is not None else int(config["output"]["match_default_top_n"])
    top_n = max(1, min(int(top_n), int(config["output"]["max_top_n"])))
    returned = ordered[:top_n]

    no_match, _nearest = _no_match(
        config, works, excluded, threshold, considered, passed_hard_filter, notes
    )
    if no_match["is_no_match"]:
        returned = []

    results = []
    for rank, work in enumerate(returned, start=1):
        seller = work["seller"]
        evidence_ids = []
        for source in (
            [ident for item in work["rationale"] for ident in item["evidence_ids"]],
            [ident for item in work["risks"] for ident in item["evidence_ids"]],
            list(work["rerank"]["evidence_ids"]),
            [
                ident
                for detail in work["component_details"]
                for ident in detail.get("evidence_ids", [])
            ],
        ):
            for ident in source:
                if ident not in evidence_ids:
                    evidence_ids.append(ident)
        candidate = {
            "seller_id": work["seller_id"],
            "seller_name": work["seller_name"],
            "canonical_domain": work["canonical_domain"],
            "rank": rank,
            "match_score": work["match_score"],
            "base_score": work["base_score"],
            "component_scores": work["component_scores"],
            "weighted_contributions": work["weighted_contributions"],
            "component_details": work["component_details"][:40],
            "hard_filter": {
                "passed": True,
                "failed_rules": [],
                "rules_evaluated": work["rules_evaluated"],
                "rules_skipped_unknown": work["rules_skipped_unknown"],
            },
            "unknown_penalty_applied": work["unknown_penalty_applied"][:40],
            "rerank": work["rerank"],
            "rationale": work["rationale"][:20],
            "risks": work["risks"][:20],
            "confidence": work["confidence"],
            "evidence_ids": evidence_ids[:50],
            "qualified": True,
            "missing": work["missing"],
            "notes": work["notes"],
        }
        if isinstance(seller.get("website"), str):
            candidate["website"] = seller["website"]
        if isinstance(seller.get("company_type"), str):
            candidate["company_type"] = seller["company_type"]
        if "oem_odm" in seller:
            candidate["oem_odm"] = _tri(seller, "oem_odm")
        channels = _known_list(seller, "contact_channels")
        if channels is not None:
            candidate["contact_channels"] = [
                dict(
                    (key, channel[key])
                    for key in ("type", "value", "label", "evidence_ids")
                    if key in channel
                )
                for channel in channels
                if isinstance(channel, dict)
            ][:20]
        score = seller.get("qualification_score")
        if isinstance(score, int) and not isinstance(score, bool) and seller.get(
            "score_version"
        ) not in (None, "unscored"):
            candidate["seller_qualification_score"] = int(_clamp(score, 0, 100))
        results.append(candidate)

    # Deterministic excluded order: first failed rule_id asc, canonical_domain asc.
    for candidate in excluded:
        candidate["_first_rule"] = candidate["failed_rules"][0]["rule_id"]
    excluded = _common.stable_sort(
        excluded, [("_first_rule", "asc"), ("canonical_domain", "asc")]
    )
    for candidate in excluded:
        del candidate["_first_rule"]
        if candidate.get("website") is None:
            candidate.pop("website", None)
        if not candidate.get("notes"):
            candidate.pop("notes", None)
        if not candidate.get("unknown_penalty_applied"):
            candidate.pop("unknown_penalty_applied", None)

    emitted_excluded = excluded if args.include_excluded else []
    if not args.include_excluded and excluded:
        notes.append(
            "excluded candidates suppressed by --no-include-excluded (%d hidden)" % (len(excluded),)
        )

    evidence_index = _build_evidence_index(results, emitted_excluded, sellers, rfq, notes)

    unknown_limit = _common.unknown_flag_limit(config, "match")
    unknown_flagged = len(
        [work for work in works if len(work["unknown_penalty_applied"]) > unknown_limit]
    )
    summary = {
        "candidates_considered": considered,
        "passed_hard_filter": passed_hard_filter,
        "returned": len(results),
        "excluded_count": len(excluded),
        "qualified_count": len(qualifying),
        "threshold_used": threshold["value"],
        "threshold_mode": threshold["mode"],
        "top_n_requested": top_n,
        "reranked_count": reranked_count,
        "unknown_flagged_count": unknown_flagged,
    }
    if results:
        summary["score_range"] = {
            "min": min(item["match_score"] for item in results),
            "max": max(item["match_score"] for item in results),
        }

    readiness_score, readiness_detail = _rfq_readiness(rfq)

    run_id = args.match_run_id or "MR-%s-%s-01" % (rfq.get("rfq_id") or "000", as_of)
    document = {
        "schema_version": _common.SCHEMA_VERSION,
        "score_version": config["score_version"],
        "match_run_id": run_id,
        "rfq_id": str(rfq.get("rfq_id") or ""),
        "as_of": as_of,
        "weights_used": dict(
            (name, _json_num(_dec(config["match"]["weights"][name]))) for name in components
        ),
        "threshold": threshold,
        "summary": summary,
        "results": results,
        "excluded": emitted_excluded,
        "no_match": no_match,
        "evidence_index": evidence_index,
        "notes": [_trim(note, 1000) for note in notes][:50],
        "skill_version": args.skill_version or _common.SKILL_VERSION,
        "partial": partial,
        "extensions": {
            "rerank_slot": _rerank_slot(config, rfq, works),
            "rfq_readiness": {
                "qualification_score": readiness_score,
                "readiness_detail": readiness_detail,
            },
        },
    }
    if rfq.get("buyer_id"):
        document["buyer_id"] = str(rfq["buyer_id"])
    document["rfq_constraints"] = _rfq_constraints(rfq, query)

    exit_code = 0
    if args.validate:
        schema = _common.load_schema("match-result", args.schema_dir)
        errors = _common.validate(document, schema)
        if errors:
            _common.die("output failed match-result.schema.json validation", 1)
            for line in errors[:20]:
                _common.eprint("    " + line, quiet=args.quiet)
            exit_code = 1

    stream = None
    handle = None
    if args.output:
        handle = open(args.output, "w", encoding="utf-8")
        stream = handle
    try:
        _common.write_output(document, args.pretty, stream)
    finally:
        if handle is not None:
            handle.close()
    return exit_code


def _rfq_constraints(rfq, query):
    block = {}
    if rfq.get("destination_country"):
        block["destination_country"] = rfq["destination_country"]
    if rfq.get("product_category"):
        block["product_category"] = rfq["product_category"]
    if isinstance(rfq.get("product_categories_extra"), list):
        block["product_categories_extra"] = list(rfq["product_categories_extra"])
    if isinstance(rfq.get("product_forms"), list):
        block["product_forms"] = list(rfq["product_forms"])
    if rfq.get("commercial_model"):
        block["commercial_model"] = rfq["commercial_model"]
    if _is_number(rfq.get("max_moq")):
        block["max_moq"] = _json_num(rfq["max_moq"])
    if _is_number(query.get("max_lead_time_days")):
        block["max_lead_time_days"] = _json_num(query["max_lead_time_days"])
    for key in ("required_certifications", "preferred_certifications", "required_seller_countries"):
        if isinstance(rfq.get(key), list):
            block[key] = list(rfq[key])
    return block


def _build_evidence_index(results, excluded, sellers, rfq, notes):
    referenced = []
    for candidate in results:
        for ident in candidate.get("evidence_ids", []):
            if ident not in referenced:
                referenced.append(ident)
        for detail in candidate.get("component_details", []):
            for ident in detail.get("evidence_ids", []):
                if ident not in referenced:
                    referenced.append(ident)
        for channel in candidate.get("contact_channels", []) or []:
            for ident in channel.get("evidence_ids", []) or []:
                if ident not in referenced:
                    referenced.append(ident)
    for candidate in excluded:
        for rule in candidate.get("failed_rules", []):
            for ident in rule.get("evidence_ids", []) or []:
                if ident not in referenced:
                    referenced.append(ident)
        for ident in candidate.get("evidence_ids", []) or []:
            if ident not in referenced:
                referenced.append(ident)

    wanted = set(referenced)
    index = []
    seen = set()
    owners = [(record.get("seller_id"), record) for record in sellers]
    owners.append((rfq.get("rfq_id"), rfq))
    for owner_id, record in owners:
        for item in _evidence(record):
            ident = item.get("evidence_id")
            if ident not in wanted:
                continue
            key = (owner_id, ident)
            if key in seen:
                continue
            seen.add(key)
            entry = {
                "evidence_id": ident,
                "claim": item.get("claim") or "",
                "source_url": item.get("source_url") or "",
                "source_type": item.get("source_type") or "third_party",
                "observed_at": item.get("observed_at") or "",
            }
            if isinstance(item.get("source_tier"), int) and not isinstance(
                item.get("source_tier"), bool
            ):
                entry["source_tier"] = item["source_tier"]
            if isinstance(item.get("is_official"), bool):
                entry["is_official"] = item["is_official"]
            if owner_id:
                entry["owner_id"] = str(owner_id)
            if isinstance(item.get("stale"), bool):
                entry["stale"] = item["stale"]
            index.append(entry)
    resolved = set(entry["evidence_id"] for entry in index)
    dangling = sorted(wanted - resolved)
    if dangling:
        notes.append(
            "evidence ids %s could not be resolved to a source (INV-22)" % (", ".join(dangling),)
        )
    index.sort(key=lambda entry: (entry["evidence_id"], entry.get("owner_id", "")))
    return index


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
