#!/usr/bin/env python3
"""Seller qualification scoring.

Implements docs/SCORING-CONTRACT.md sections 2 (seller rubric), 4 (evidence
quality, via _common), 5 (determinism), the discovery subset of the hard filters
(BUILD-CONTRACT R6.2.6) and BUILD-CONTRACT 7.8 (CLI, discovery envelope).

Every tunable number is read from schemas/scoring.config.json at run time;
nothing numeric is hard-coded here (INV-29). The only time source is --as-of
(INV-14): no wall-clock read appears anywhere in this file.

Signal firing, in three flavours (the criterion tables of SCORING-CONTRACT 2.x):

  field signal      decided by a record field (category_exact_match,
                    oem_odm_true, moq_at_or_below_max, english_site_true, ...)
  claim signal      decided by a canonical evidence claim plus the structural
                    predicate the contract states, e.g. published_oem_odm_page =
                    claim "oem_odm" with is_official true and source_tier <= 2
  agent signal      decided by what a source SAYS, which no script can judge.
                    It fires when the record carries an evidence item whose
                    `claim` equals the signal key itself, e.g.
                    {"claim": "named_product_page", ...}. That keeps the
                    deterministic half of the pipeline free of prose judgement
                    (PRD 13.2): the narrative pass records the signal as a
                    sourced claim, this script only counts it.

A criterion is `unknown` only when its contract-stated unknown condition holds
AND no signal fired; a fired signal always means at least one deciding input was
known (SCORING-CONTRACT 0.2).
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

ENTITY = "seller"

# SCORING-CONTRACT 8 reconciliation ledger item 7: S-PF1 is inapplicable in a
# discovery run that names no category, rather than firing category_no_match.
# Applied only when the config criterion carries no inapplicable_when of its own.
INTERIM_INAPPLICABLE_WHEN = {"S-PF1": ["query.product_categories_absent"]}

# Certification groups behind S-CP4 (vocabulary facts, BUILD-CONTRACT 8.6;
# the points they earn live in the config).
BASELINE_QUALITY_TOKENS = frozenset(("ISO22716", "CGMP"))
OTHER_QUALITY_TOKENS = frozenset(("ISO9001", "ISO14001", "GMP_KOREA", "COSMOS", "ECOCERT"))

# Hard-filter rules applied during discovery (BUILD-CONTRACT R6.2.6). HF-06 and
# HF-07 are deliberately excluded there.
DISCOVERY_RULES = ("HF-01", "HF-02", "HF-03", "HF-04", "HF-05", "HF-08")

# SCORING-CONTRACT 3.1: which criterion's unknown_penalty pairs with a rule that
# was skipped because its deciding input was unknown.
RULE_PENALTY_CRITERION = {
    "HF-01": "S-PF1",
    "HF-02": "S-CM1",
    "HF-03": "S-OP1",
    "HF-04": "S-CP1",
    "HF-07": "S-OP2",
}

RULE_UNKNOWN_INPUTS = {
    "HF-01": ["seller.product_categories"],
    "HF-02": ["seller.brand_export", "seller.oem_odm", "seller.private_label"],
    "HF-03": ["seller.moq"],
    "HF-04": ["seller.certifications"],
    "HF-07": ["seller.lead_time_days"],
    "HF-08": ["seller.country"],
}

# Wording of the pairing note a skipped rule leaves on its criterion's penalty
# record (SCORING-CONTRACT 3.1). Prose only; every number still comes from the config.
RULE_SKIP_NOTE = {
    "HF-01": "Product categories not published; hard filter HF-01 skipped.",
    "HF-02": "Requested commercial model not stated; hard filter HF-02 skipped.",
    "HF-03": "MOQ not published; hard filter HF-03 skipped.",
    "HF-04": "Certification list not published; hard filter HF-04 skipped.",
    "HF-07": "Lead time not published; hard filter HF-07 skipped.",
    "HF-08": "Seller country not established; hard filter HF-08 skipped.",
}

MODEL_FLAGS = {"branded": "brand_export", "private_label": "private_label", "oem_odm": "oem_odm"}

UNKNOWN_MOQ = "unknown"
UNIT_MISMATCH = "unit_mismatch"


# --------------------------------------------------------------------------
# small shared helpers
# --------------------------------------------------------------------------


def _dec(value):
    """Exact Decimal for any config number or plain scalar (SCORING-CONTRACT 0.1)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _json_num(value):
    """Convert a Decimal to the int/float JSON writes, matching _common.write_output."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def _clamp_int(value, low, high):
    return max(low, min(high, value))


def _as_list(value):
    return value if isinstance(value, list) else None


def _fmt_number(value):
    dec = _dec(value)
    if dec == dec.to_integral_value():
        return "{:,}".format(int(dec))
    return "{:,}".format(float(dec))


def _fmt_value(value):
    """Render a value for a failure reason line (BUILD-CONTRACT 10.5, R6.2.3)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return _fmt_number(value)
    if isinstance(value, (list, tuple)):
        items = [_fmt_value(v) for v in value]
        return ", ".join(items) if items else "none evidenced"
    if value is None:
        return _common.UNKNOWN
    return str(value)


def _normalise_unit(unit):
    """NFKC + casefold + strip whitespace and periods (SCORING-CONTRACT 2.3)."""
    if unit is None or _common.is_unknown(unit):
        return "units"
    text = unicodedata.normalize("NFKC", str(unit))
    text = re.sub(r"[\s.]+", "", text)
    return text.casefold() or "units"


def _cmp_max(value):
    """Worst-case bound of a quantity_value; None when unknown (SCORING-CONTRACT 0.8)."""
    rng = _common.coerce_range(value)
    if rng is None:
        return None
    return _dec(rng["max"])


# --------------------------------------------------------------------------
# evidence helpers (SCORING-CONTRACT 4.2/4.3 tie-breaks, used by adjustments)
# --------------------------------------------------------------------------


def _evidence_items(record):
    items = record.get("evidence")
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def _index_evidence(record):
    index = {}
    for item in _evidence_items(record):
        claim = item.get("claim")
        if isinstance(claim, str):
            index.setdefault(claim, []).append(item)
    return index


def _recency_multiplier(item, as_of, config):
    evidence_cfg = config["evidence"]
    source_date = item.get("source_date")
    if _common.is_unknown(source_date):
        return _dec(evidence_cfg["unknown_source_date_multiplier"])
    age_days = max(0, _common.days_between(source_date, as_of))
    buckets = evidence_cfg["recency_buckets"]
    for bucket in buckets:
        limit = bucket.get("max_age_days")
        if limit is None or age_days <= int(limit):
            return _dec(bucket["multiplier"])
    return _dec(buckets[-1]["multiplier"])


def _item_strength(item, as_of, config):
    points = config["evidence"]["source_tier_points"].get(str(item.get("source_tier")))
    if points is None:
        return Decimal(0)
    return _dec(_common.round_half_up(_dec(points) * _recency_multiplier(item, as_of, config)))


def _best_evidence(items, as_of, config):
    """argmax item_strength; ties: lower tier, newer source_date, smaller id."""
    if not items:
        return None

    def date_key(item):
        source_date = item.get("source_date")
        return "" if _common.is_unknown(source_date) else str(source_date)

    def tier_of(item):
        tier = item.get("source_tier")
        return tier if isinstance(tier, int) else 99

    ordered = sorted(items, key=lambda it: str(it.get("evidence_id") or ""))
    ordered.sort(key=date_key, reverse=True)
    ordered.sort(key=lambda it: (_item_strength(it, as_of, config), -tier_of(it)), reverse=True)
    return ordered[0]


# --------------------------------------------------------------------------
# scoring context
# --------------------------------------------------------------------------


class _Ctx(object):
    """Everything one criterion evaluator may read. Never mutated by scoring."""

    __slots__ = (
        "record", "config", "as_of", "query", "cond", "evidence_index",
        "categories", "certifications", "moq_state", "moq_value", "notes",
    )

    def __init__(self, record, config, as_of, query, cond):
        self.record = record
        self.config = config
        self.as_of = as_of
        self.query = query
        self.cond = cond
        self.evidence_index = _index_evidence(record)
        self.notes = []
        self.categories = _normalised_categories(record.get("product_categories"), self.notes)
        certifications = _as_list(record.get("certifications"))
        if certifications is None:
            self.certifications = None
        else:
            self.certifications = sorted(
                set(_common.normalize_certification(c) for c in certifications if isinstance(c, str))
                - set(("",))
            )
        self.moq_state, self.moq_value = _moq_cmp(record, query, self.notes)

    def field(self, name):
        return self.record.get(name)

    def tri(self, name):
        return _common.tri_state(self.record.get(name))


def _normalised_categories(raw, notes):
    """None = unknown; [] = verified empty; list = known (BUILD-CONTRACT 8.5)."""
    if not isinstance(raw, list):
        return None
    normalised = []
    for slug in raw:
        token = _common.normalize_category(slug) if isinstance(slug, str) else None
        if token is None:
            notes.append(
                "category token %r is a vertical marker, not a category; dropped before "
                "scoring (BUILD-CONTRACT 8.5)" % (slug,)
            )
            continue
        if token not in normalised:
            normalised.append(token)
    if raw and not normalised:
        return None
    return normalised


def _moq_cmp(record, query, notes):
    """(state, value) per SCORING-CONTRACT 2.3. State: known / unknown / unit_mismatch."""
    try:
        rng = _common.coerce_range(record.get("moq"))
    except _common.DataError as exc:
        notes.append("seller.moq could not be read (%s); treated as unknown" % (exc,))
        return UNKNOWN_MOQ, None
    if rng is None:
        return UNKNOWN_MOQ, None
    seller_unit = record.get("moq_unit")
    if _common.is_unknown(seller_unit):
        seller_unit = rng.get("unit")
    elif rng.get("unit") is not None and _normalise_unit(rng.get("unit")) != _normalise_unit(seller_unit):
        # BUILD-CONTRACT R3.3.1: moq_unit wins, and both values are noted.
        notes.append(
            "seller.moq_unit %r wins over the unit %r carried inside seller.moq "
            "(BUILD-CONTRACT 3.3 R3.3.1)" % (record.get("moq_unit"), rng.get("unit"))
        )
    if _normalise_unit(seller_unit) != _normalise_unit(query.get("moq_unit")):
        notes.append(
            "seller MOQ is denominated in %r while the request uses %r; the MOQ comparison "
            "could not be made (SCORING-CONTRACT 2.3)"
            % (_normalise_unit(seller_unit), _normalise_unit(query.get("moq_unit")))
        )
        return UNIT_MISMATCH, None
    return "known", _dec(rng["min"])


def _evidence_with(ctx, claim, official=None, max_tier=None):
    out = []
    for item in ctx.evidence_index.get(claim, ()):
        if official is not None and bool(item.get("is_official")) is not official:
            continue
        if max_tier is not None:
            tier = item.get("source_tier")
            if not isinstance(tier, int) or tier > max_tier:
                continue
        out.append(item)
    return out


def _criterion_signals(config, dimension_key, criterion_id):
    """The signal table of one criterion, read from the config (INV-29: no hard-coded
    signal key list lives in this file)."""
    for criterion in config["seller"]["dimensions"][dimension_key]["criteria"]:
        if criterion["criterion_id"] == criterion_id:
            return criterion.get("signals", {})
    return {}


def _attach_claim_evidence(ctx, evidence_ids, *claims):
    for claim in claims:
        for item in _evidence_with(ctx, claim):
            ident = item.get("evidence_id")
            if isinstance(ident, str) and ident:
                evidence_ids.add(ident)


def _fire_from_evidence(ctx, fired, evidence_ids, signal_key, claims, official=None, max_tier=None):
    items = []
    for claim in claims:
        items.extend(_evidence_with(ctx, claim, official=official, max_tier=max_tier))
    if not items:
        return False
    if signal_key not in fired:
        fired.append(signal_key)
    for item in items:
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident:
            evidence_ids.add(ident)
    return True


def _query_categories(query):
    out = []
    for slug in _as_list(query.get("product_categories")) or []:
        token = _common.normalize_category(slug) if isinstance(slug, str) else None
        if token is not None and token not in out:
            out.append(token)
    return out


def _cert_tokens(values):
    out = set()
    for value in values or []:
        if isinstance(value, str):
            token = _common.normalize_certification(value)
            if token:
                out.add(token)
    return out


# --------------------------------------------------------------------------
# criterion evaluators - SCORING-CONTRACT 2.1 .. 2.6
# --------------------------------------------------------------------------


def _c_s_pf1(ctx):
    fired, evidence_ids = [], set()
    if ctx.categories is None:
        return fired, evidence_ids, True
    relation = _common.category_relation(ctx.categories, _query_categories(ctx.query))
    fired.append({
        "exact": "category_exact_match",
        "parent": "category_parent_match",
        "adjacent": "category_adjacent_match",
        "none": "category_no_match",
    }[relation])
    _attach_claim_evidence(ctx, evidence_ids, "product_categories")
    return fired, evidence_ids, False


def _c_s_pf2(ctx):
    fired, evidence_ids = [], set()
    forms = _as_list(ctx.field("product_forms"))
    if forms is None:
        return fired, evidence_ids, True
    requested = set(_as_list(ctx.query.get("product_forms")) or [])
    held = set(forms)
    if requested and requested <= held:
        fired.append("form_exact_match")
    elif requested & held:
        fired.append("form_partial_match")
    _attach_claim_evidence(ctx, evidence_ids, "product_forms")
    return fired, evidence_ids, False


def _c_s_pf3(ctx):
    fired, evidence_ids = [], set()
    for key in ("named_product_page", "catalog_or_pdf_published"):
        _fire_from_evidence(ctx, fired, evidence_ids, key, (key,))
    if ctx.categories is not None and len(ctx.categories) >= 3:
        fired.append("multi_category_coverage")
        _attach_claim_evidence(ctx, evidence_ids, "product_categories")
    unknown = not fired and not _evidence_with(ctx, "product_categories")
    return fired, evidence_ids, unknown


def _model_flags(ctx):
    return dict((name, ctx.tri(name)) for name in ("oem_odm", "private_label", "brand_export"))


def _requested_model(query):
    model = query.get("commercial_model")
    if _common.is_unknown(model):
        # No commercial model requested imposes no constraint: the criterion
        # scores on the best available flag, exactly as "either" does.
        return "either"
    return str(model)


def _c_s_cm1(ctx):
    fired, evidence_ids = [], set()
    flags = _model_flags(ctx)
    model = _requested_model(ctx.query)
    _attach_claim_evidence(ctx, evidence_ids, "oem_odm", "private_label", "brand_export")
    if model == "either":
        if any(value is True for value in flags.values()):
            fired.append("either_model_any_supported")
        unknown = all(value == _common.UNKNOWN for value in flags.values())
        return fired, evidence_ids, unknown
    field = MODEL_FLAGS.get(model)
    if field is None:
        return fired, evidence_ids, False
    value = flags.get(field, _common.UNKNOWN)
    others = [flags[name] for name in flags if name != field]
    if value is True:
        fired.append("required_model_supported")
    elif value == _common.UNKNOWN and any(other is True for other in others):
        fired.append("related_model_supported")
    unknown = value == _common.UNKNOWN and all(other == _common.UNKNOWN for other in others)
    return fired, evidence_ids, unknown


def _c_s_cm2(ctx):
    fired, evidence_ids = [], set()
    flags = _model_flags(ctx)
    for name, key in (("oem_odm", "oem_odm_true"), ("private_label", "private_label_true"),
                      ("brand_export", "brand_export_true")):
        if flags[name] is True:
            fired.append(key)
    _attach_claim_evidence(ctx, evidence_ids, "oem_odm", "private_label", "brand_export")
    unknown = all(value == _common.UNKNOWN for value in flags.values())
    return fired, evidence_ids, unknown


def _c_s_cm3(ctx):
    fired, evidence_ids = [], set()
    _fire_from_evidence(
        ctx, fired, evidence_ids, "published_oem_odm_page",
        ("oem_odm", "published_oem_odm_page"), official=True, max_tier=2,
    )
    _fire_from_evidence(
        ctx, fired, evidence_ids, "published_moq_or_pricing_terms",
        ("moq", "published_moq_or_pricing_terms"), official=True,
    )
    unknown = not fired and not (_evidence_with(ctx, "oem_odm") or _evidence_with(ctx, "moq"))
    return fired, evidence_ids, unknown


def _c_s_cm4(ctx):
    """Manufacturer role. references/seller-discovery.md section 6 spends a whole
    section separating a factory from a broker or a brand-only marketer; before this
    criterion existed no seller dimension and no hard filter read company_type, so
    that discrimination had no effect on the ranking at all. Nothing is rejected
    here - a trading company is priced, not excluded."""
    fired, evidence_ids = [], set()
    company_type = ctx.field("company_type")
    if _common.is_unknown(company_type):
        return fired, evidence_ids, True
    fired.append("company_type_%s" % (company_type,))
    _attach_claim_evidence(ctx, evidence_ids, "company_type")
    return fired, evidence_ids, False


def _c_s_op1(ctx):
    fired, evidence_ids = [], set()
    if ctx.moq_state != "known":
        return fired, evidence_ids, True
    _attach_claim_evidence(ctx, evidence_ids, "moq")
    value = ctx.moq_value
    ceiling = ctx.query.get("max_moq")
    if _common.is_unknown(ceiling) or isinstance(ceiling, bool):
        fired.append("moq_no_constraint_known")
        return fired, evidence_ids, False
    ceiling = _dec(ceiling)
    ratio = _dec(ctx.config["hard_filter_tolerances"]["moq_overshoot_ratio"])
    # Graded band on one input: exactly one tier fires (SCORING-CONTRACT 2.3).
    # The tolerance tier is the band ABOVE the ceiling, which is what makes it
    # "structurally unreachable" at ratio 0.0 exactly as the contract states.
    if value <= ceiling / Decimal(2):
        fired.append("moq_at_or_below_half_of_max")
    elif value <= ceiling:
        fired.append("moq_at_or_below_max")
    elif value <= ceiling * (Decimal(1) + ratio):
        fired.append("moq_within_tolerance_of_max")
    else:
        fired.append("moq_above_max")
    return fired, evidence_ids, False


def _c_s_op2(ctx):
    fired, evidence_ids = [], set()
    try:
        value = _cmp_max(ctx.field("lead_time_days"))
    except _common.DataError as exc:
        ctx.notes.append("seller.lead_time_days could not be read (%s); treated as unknown" % (exc,))
        value = None
    if value is None:
        return fired, evidence_ids, True
    _attach_claim_evidence(ctx, evidence_ids, "lead_time_days")
    ceiling = ctx.query.get("max_lead_time_days")
    if _common.is_unknown(ceiling) or isinstance(ceiling, bool):
        fired.append("lead_time_no_constraint_known")
        return fired, evidence_ids, False
    ceiling = _dec(ceiling)
    ratio = _dec(ctx.config["hard_filter_tolerances"]["lead_time_overshoot_ratio"])
    if value <= ceiling:
        fired.append("lead_time_within_requirement")
    elif value <= ceiling * (Decimal(1) + ratio):
        fired.append("lead_time_within_tolerance")
    else:
        fired.append("lead_time_exceeds_requirement")
    return fired, evidence_ids, False


def _c_s_op3(ctx):
    fired, evidence_ids = [], set()
    try:
        capacity = _cmp_max(ctx.field("monthly_capacity_units"))
    except _common.DataError as exc:
        ctx.notes.append("seller.monthly_capacity_units could not be read (%s); treated as unknown" % (exc,))
        capacity = None
    if capacity is None:
        return fired, evidence_ids, True
    _attach_claim_evidence(ctx, evidence_ids, "monthly_capacity_units")
    try:
        quantity = _cmp_max(ctx.query.get("quantity"))
    except _common.DataError:
        quantity = None
    if quantity is None:
        fired.append("capacity_no_constraint_known")
        return fired, evidence_ids, False
    if capacity >= quantity * Decimal(2):
        fired.append("capacity_covers_quantity_2x")
    elif capacity >= quantity:
        fired.append("capacity_covers_quantity")
    else:
        fired.append("capacity_below_quantity")
    return fired, evidence_ids, False


def _c_s_cp1(ctx):
    fired, evidence_ids = [], set()
    if ctx.certifications is None:
        return fired, evidence_ids, True
    held = set(ctx.certifications)
    required = _cert_tokens(ctx.query.get("required_certifications"))
    if not required:
        return fired, evidence_ids, False
    hits = len(held & required)
    if required <= held:
        # certifications_verified true means an official source presented the list as
        # the company's COMPLETE set. Before the split it changed nothing: the +5
        # adjustment was clamped away at the ceiling, so a dedicated certificate page
        # with certificate images scored exactly what a marketing bullet list scored.
        verified = ctx.field("certifications_verified") is True
        key = ("all_required_certifications_held_verified" if verified
               else "all_required_certifications_held_unverified")
        signals = _criterion_signals(ctx.config, "compliance_readiness", "S-CP1")
        fired.append(key if key in signals else "all_required_certifications_held")
        if verified:
            _attach_claim_evidence(ctx, evidence_ids, "certifications_verified")
    elif hits >= 1 and _dec(hits) / _dec(len(required)) >= _dec("0.5"):
        fired.append("most_required_certifications_held")
    elif hits >= 1:
        fired.append("some_required_certifications_held")
    else:
        fired.append("no_required_certifications_held")
    _attach_claim_evidence(ctx, evidence_ids, "certifications")
    return fired, evidence_ids, False


def _c_s_cp2(ctx):
    fired, evidence_ids = [], set()
    if ctx.certifications is None:
        return fired, evidence_ids, True
    held = set(ctx.certifications)
    preferred = _cert_tokens(ctx.query.get("preferred_certifications"))
    if not preferred:
        return fired, evidence_ids, False
    if preferred <= held:
        fired.append("all_preferred_certifications_held")
    elif held & preferred:
        fired.append("some_preferred_certifications_held")
    _attach_claim_evidence(ctx, evidence_ids, "certifications")
    return fired, evidence_ids, False


def _c_s_cp3(ctx):
    fired, evidence_ids = [], set()
    registrations = _as_list(ctx.field("regulatory_registrations"))
    if registrations is None:
        return fired, evidence_ids, True
    destination = ctx.query.get("destination_country")
    if _common.is_unknown(destination):
        return fired, evidence_ids, True
    destination = str(destination).upper()
    statuses = []
    for entry in registrations:
        if not isinstance(entry, dict):
            continue
        market = entry.get("market")
        if isinstance(market, str) and market.upper() == destination:
            statuses.append(entry.get("registration_status"))
            for ident in entry.get("evidence_ids") or []:
                if isinstance(ident, str) and ident:
                    evidence_ids.add(ident)
    if "registered" in statuses:
        fired.append("registered_in_destination_market")
    elif "in_progress" in statuses:
        fired.append("registration_in_progress")
    elif statuses and all(_common.is_unknown(s) for s in statuses):
        return fired, evidence_ids, True
    else:
        fired.append("not_registered_in_destination_market")
    _attach_claim_evidence(ctx, evidence_ids, "regulatory_registrations")
    return fired, evidence_ids, False


def _c_s_cp4(ctx):
    fired, evidence_ids = [], set()
    if ctx.certifications is None:
        return fired, evidence_ids, True
    held = set(ctx.certifications)
    if held & BASELINE_QUALITY_TOKENS:
        fired.append("iso22716_or_cgmp_held")
    elif held & OTHER_QUALITY_TOKENS:
        fired.append("other_quality_certification_held")
    _attach_claim_evidence(ctx, evidence_ids, "certifications")
    return fired, evidence_ids, False


def _c_s_ex1(ctx):
    fired, evidence_ids = [], set()
    markets = _as_list(ctx.field("export_markets"))
    regions = _as_list(ctx.field("export_regions"))
    if markets is None and regions is None:
        return fired, evidence_ids, True
    markets = [str(m).upper() for m in (markets or []) if isinstance(m, str)]
    regions = [str(r).upper() for r in (regions or []) if isinstance(r, str)]
    destination = ctx.query.get("destination_country")
    region = [str(c).upper() for c in (_as_list(ctx.query.get("region_countries")) or [])]
    home = ctx.field("country")
    home = str(home).upper() if isinstance(home, str) else None
    signals = _criterion_signals(ctx.config, "export_readiness", "S-EX1")
    # A stated region token is real export evidence that export_markets (alpha-2 only)
    # cannot hold; it is weaker than a named country, so it sits one tier below.
    # The token's membership table lives in _common, never in the query surface: a
    # script must never expand a region into countries (BUILD-CONTRACT 8.7).
    destination_in_region = (
        not _common.is_unknown(destination)
        and _common.region_covers_country(regions, str(destination).upper())
    )
    # Graded band on one input: exactly one tier fires (SCORING-CONTRACT 2.6).
    if not _common.is_unknown(destination) and str(destination).upper() in markets:
        fired.append("exports_to_destination_market")
    elif set(markets) & set(region):
        fired.append("exports_to_same_region")
    elif destination_in_region and "exports_to_destination_region" in signals:
        fired.append("exports_to_destination_region")
    elif markets and any(market != home for market in markets):
        fired.append("exports_to_any_overseas_market")
    elif regions and "exports_to_stated_region" in signals:
        fired.append("exports_to_stated_region")
    else:
        fired.append("no_export_evidence")
    _attach_claim_evidence(ctx, evidence_ids, "export_markets", "export_regions")
    return fired, evidence_ids, False


def _c_s_ex2(ctx):
    fired, evidence_ids = [], set()
    signal = ctx.tri("english_site")
    partial = bool(_evidence_with(ctx, "partial_english_materials"))
    if signal is True:
        fired.append("english_site_true")
    elif partial:
        _fire_from_evidence(
            ctx, fired, evidence_ids, "partial_english_materials", ("partial_english_materials",)
        )
    elif signal is False:
        fired.append("english_site_false")
    _attach_claim_evidence(ctx, evidence_ids, "english_site")
    unknown = signal == _common.UNKNOWN and not partial
    return fired, evidence_ids, unknown


def _c_s_ex3(ctx):
    fired, evidence_ids = [], set()
    signal = ctx.tri("overseas_partner_signal")
    if signal is True:
        fired.append("overseas_partner_signal_true")
        _attach_claim_evidence(ctx, evidence_ids, "overseas_partner_signal")
    evidence_driven = False
    for key in ("trade_show_exhibitor", "export_inquiry_channel"):
        evidence_driven = _fire_from_evidence(ctx, fired, evidence_ids, key, (key,)) or evidence_driven
    # SCORING-CONTRACT 8 ledger item 8: the last two signals are evidence-derived,
    # so the criterion is unknown only when the flag is unknown AND neither fired.
    unknown = signal == _common.UNKNOWN and not evidence_driven
    return fired, evidence_ids, unknown


EVALUATORS = {
    "S-PF1": _c_s_pf1,
    "S-PF2": _c_s_pf2,
    "S-PF3": _c_s_pf3,
    "S-CM1": _c_s_cm1,
    "S-CM2": _c_s_cm2,
    "S-CM3": _c_s_cm3,
    "S-CM4": _c_s_cm4,
    "S-OP1": _c_s_op1,
    "S-OP2": _c_s_op2,
    "S-OP3": _c_s_op3,
    "S-CP1": _c_s_cp1,
    "S-CP2": _c_s_cp2,
    "S-CP3": _c_s_cp3,
    "S-CP4": _c_s_cp4,
    "S-EX1": _c_s_ex1,
    "S-EX2": _c_s_ex2,
    "S-EX3": _c_s_ex3,
}


# --------------------------------------------------------------------------
# adjustments - SCORING-CONTRACT 2.1 .. 2.6
# --------------------------------------------------------------------------


def _certification_claim_unverified(ctx):
    """A held token whose BEST supporting evidence sits on tier 4 or 5."""
    if not ctx.certifications:
        return False
    items = _evidence_with(ctx, "certifications")
    if not items:
        return False
    for token in ctx.certifications:
        supporting = []
        for item in items:
            value = item.get("value")
            tokens = _cert_tokens(value if isinstance(value, list) else [value] if isinstance(value, str) else [])
            if not tokens or token in tokens:
                supporting.append(item)
        best = _best_evidence(supporting, ctx.as_of, ctx.config)
        if best is not None and best.get("source_tier") in (4, 5):
            return True
    return False


def _adjustments(ctx, dimension_key, fired_by_criterion, state_by_criterion):
    out = []
    record = ctx.record
    query = ctx.query

    if dimension_key == "product_fit":
        if ctx.categories and state_by_criterion.get("S-PF1") == "scored":
            if "category_no_match" in (fired_by_criterion.get("S-PF1") or []):
                out.append("category_no_match_verified")

    elif dimension_key == "commercial_model":
        model = _requested_model(query)
        field = MODEL_FLAGS.get(model)
        if field is not None and ctx.tri(field) is False:
            out.append("required_model_false")

    elif dimension_key == "operational_fit":
        if _evidence_with(ctx, "moq_negotiable_statement"):
            out.append("moq_negotiable_statement")
        if _evidence_with(ctx, "sample_or_trial_order_accepted"):
            out.append("sample_or_trial_order_accepted")
        if ctx.moq_state == UNIT_MISMATCH:
            out.append("moq_unit_mismatch")

    elif dimension_key == "compliance_readiness":
        if ctx.tri("certifications_verified") is True:
            out.append("certifications_verified_official")
        if _certification_claim_unverified(ctx):
            out.append("certification_claim_unverified")

    elif dimension_key == "export_readiness":
        destination = query.get("destination_country")
        excluded = _as_list(record.get("excluded_markets")) or []
        if not _common.is_unknown(destination):
            if str(destination).upper() in [str(m).upper() for m in excluded if isinstance(m, str)]:
                out.append("excluded_market_includes_destination")

    ordered = list(ctx.config["seller"]["dimensions"][dimension_key].get("adjustments", {}).keys())
    return [key for key in ordered if key in out]


# --------------------------------------------------------------------------
# dimension / record scoring
# --------------------------------------------------------------------------


def _unknown_points(config, max_points):
    unknown = config["unknown"]
    factor = (_dec(unknown["neutral_base"]) / Decimal(100)) * _dec(unknown["penalty_factor"])
    return _dec(_common.round_half_up(_dec(max_points) * factor))


def _record_inputs(criterion):
    prefix = ENTITY + "."
    out = []
    for path in criterion.get("inputs", []) or []:
        if not isinstance(path, str) or path.startswith("query."):
            continue
        text = path if path.startswith(prefix) else prefix + path
        if ":" in text:
            continue
        out.append(text)
    return out


def _unknown_input_paths(criterion, ctx):
    prefix = ENTITY + "."
    paths = []
    for path in criterion.get("inputs", []) or []:
        if not isinstance(path, str) or path.startswith("query."):
            continue
        text = path if path.startswith(prefix) else prefix + path
        if ":" in text:
            paths.append(text)
            continue
        if _common.is_unknown(ctx.record.get(text[len(prefix):])):
            paths.append(text)
    if not paths:
        paths = [
            (path if path.startswith(prefix) else prefix + path)
            for path in criterion.get("inputs", []) or []
            if isinstance(path, str) and not path.startswith("query.")
        ]
    return sorted(set(paths))


def _score_dimension(ctx, dimension_key):
    config = ctx.config
    dimension = config["seller"]["dimensions"][dimension_key]
    details, penalties = [], []
    earned_total, max_total = Decimal(0), Decimal(0)
    fired_by_criterion, state_by_criterion = {}, {}
    dimension_signals = set()

    for criterion in dimension.get("criteria", []):
        criterion_id = criterion["criterion_id"]
        label = criterion["label"]
        max_points = _dec(criterion["max_points"])
        conditions = criterion.get("inapplicable_when") or INTERIM_INAPPLICABLE_WHEN.get(criterion_id, [])
        detail = {
            "dimension": dimension_key,
            "criterion_id": criterion_id,
            "label": label,
            "max_points": criterion["max_points"],
            "earned_points": 0.0,
        }
        if any(ctx.cond.get(key, False) for key in conditions):
            detail["state"] = "inapplicable"
            detail["note"] = "the query imposes no such constraint (%s)" % (", ".join(sorted(conditions)),)
            details.append(detail)
            state_by_criterion[criterion_id] = "inapplicable"
            fired_by_criterion[criterion_id] = []
            continue

        evaluator = EVALUATORS.get(criterion_id)
        if evaluator is None:
            # A criterion priced in the config that no evaluator implements would
            # otherwise leave the dimension silently short of a scoring input (and
            # raised a bare KeyError). Name it instead: the package is internally
            # inconsistent and that is not a data problem the run can degrade around.
            raise _common.UsageError(
                "scoring.config.json prices criterion %s but %s implements no evaluator "
                "for it; the two must be changed together"
                % (criterion_id, os.path.basename(__file__))
            )
        fired, evidence_ids, unknown_when = evaluator(ctx)
        signals = criterion.get("signals", {})
        fired = [key for key in fired if key in signals]

        if not fired and unknown_when:
            earned = _unknown_points(config, max_points)
            detail["state"] = "unknown"
            unknown_inputs = _unknown_input_paths(criterion, ctx)
            penalties.append({
                "dimension": dimension_key,
                "criterion_id": criterion_id,
                "label": label,
                "unknown_inputs": unknown_inputs,
                "criterion_max_points": criterion["max_points"],
                "neutral_base": _json_num(_dec(config["unknown"]["neutral_base"])),
                "penalty_factor": _json_num(_dec(config["unknown"]["penalty_factor"])),
                "applied_points": int(earned),
                "note": "%s not published." % (", ".join(unknown_inputs),),
            })
        else:
            detail["state"] = "scored"
            if criterion.get("aggregation") == "sum_capped":
                total = sum((_dec(signals[key]) for key in fired), Decimal(0))
                earned = min(total, max_points)
            else:
                earned = max((_dec(signals[key]) for key in fired), default=Decimal(0))
            for name in _record_inputs(criterion):
                if _common.is_unknown(ctx.record.get(name[len(ENTITY) + 1:])):
                    ctx.notes.append(
                        "partial unknown: %s is not disclosed; %s was scored on its remaining "
                        "known inputs and no unknown penalty was applied "
                        "(SCORING-CONTRACT 0.4)" % (name, criterion_id)
                    )

        detail["earned_points"] = _common.round_half_up(earned, 2)
        if fired:
            detail["signals_fired"] = sorted(fired)
            dimension_signals.update(fired)
        if evidence_ids:
            detail["evidence_ids"] = sorted(evidence_ids)[:50]
        details.append(detail)
        fired_by_criterion[criterion_id] = fired
        state_by_criterion[criterion_id] = detail["state"]
        earned_total += earned
        max_total += max_points

    if max_total <= 0:
        return None, details, penalties, sorted(dimension_signals)

    raw = Decimal(100) * earned_total / max_total
    base = _common.round_half_up(raw)
    fired_adjustments = _adjustments(ctx, dimension_key, fired_by_criterion, state_by_criterion)
    adjustment_values = dimension.get("adjustments", {})
    delta = sum(int(adjustment_values[key]) for key in fired_adjustments)
    score = _clamp_int(int(base) + delta, 0, 100)
    breakdown = {
        "raw_score": _common.round_half_up(raw, 2),
        "adjustments": [{"key": key, "delta": int(adjustment_values[key])} for key in fired_adjustments],
    }
    return (score, breakdown), details, penalties, sorted(dimension_signals)


def _material_claim_fields(config):
    return list(config["evidence"]["material_claims"][ENTITY])


def _unresolved_conflicts(ctx):
    resolved_fields = set()
    for conflict in ctx.record.get("conflicts") or []:
        if isinstance(conflict, dict) and isinstance(conflict.get("field"), str):
            resolved_fields.add(conflict["field"])
    count = 0
    for item in _evidence_items(ctx.record):
        links = item.get("conflicts_with")
        if isinstance(links, list) and links and item.get("claim") not in resolved_fields:
            count += 1
    return count


def _confidence(ctx, evidence_quality_score):
    config = ctx.config
    conf = config["confidence"]
    coverage = conf["coverage_factor"]
    unknown_claims = sum(
        1 for claim in _material_claim_fields(config) if _common.is_unknown(ctx.record.get(claim))
    )
    factor = _dec(coverage["base"]) + _dec(coverage["per_unknown_material_claim"]) * Decimal(unknown_claims)
    factor = max(_dec(coverage["floor"]), factor)
    stale = _dec(conf["stale_multiplier"]) if ctx.record.get("stale") is True else Decimal(1)
    conflicts = _dec(conf["unresolved_conflict_multiplier"]) if _unresolved_conflicts(ctx) else Decimal(1)
    value = _dec(evidence_quality_score) / Decimal(100) * factor * stale * conflicts
    rounded = _dec(_common.round_half_up(value, 2))
    rounded = max(_dec(conf["min"]), min(Decimal(1), rounded))
    return float(rounded)


def _pair_skipped_rules(ctx, penalties, skipped_rules):
    """R6.2.2 / INV-07: every skipped-for-unknown rule is visible in the audit trail."""
    rules_by_id = dict((rule["rule_id"], rule) for rule in ctx.config["hard_filters"])
    for rule_id in sorted(skipped_rules):
        rule = rules_by_id.get(rule_id, {})
        criterion_id = RULE_PENALTY_CRITERION.get(rule_id)
        existing = None
        for penalty in penalties:
            if penalty["criterion_id"] == criterion_id:
                existing = penalty
                break
        if existing is not None:
            existing["note"] = RULE_SKIP_NOTE.get(
                rule_id, "%s skipped for an unknown input." % (rule_id,)
            )
            continue
        penalties.append({
            "dimension": "hard_filter",
            "criterion_id": rule_id,
            "label": rule.get("name", rule_id),
            "unknown_inputs": sorted(RULE_UNKNOWN_INPUTS.get(rule_id, [])),
            "criterion_max_points": 0,
            "neutral_base": _json_num(_dec(ctx.config["unknown"]["neutral_base"])),
            "penalty_factor": _json_num(_dec(ctx.config["unknown"]["penalty_factor"])),
            "applied_points": 0,
            "note": "%s No criterion consumes this input, so no points were withheld."
                    % (RULE_SKIP_NOTE.get(rule_id, "%s skipped for an unknown input." % (rule_id,)),),
        })


def _score_record(record, config, as_of, query, cond, schema, skipped_rules):
    ctx = _Ctx(record, config, as_of, query, cond)
    weights = config["seller"]["weights"]

    dimension_scores, details, penalties = {}, [], []
    breakdown = {}
    weighted_total = Decimal(0)
    applicable_weight = Decimal(0)
    dropped_dimensions = []

    for dimension_key in config["seller"]["dimensions"]:
        if config["seller"]["dimensions"][dimension_key].get("computed_by") == "evidence_quality_function":
            score = _clamp_int(int(_common.evidence_quality(record, _material_claim_fields(config), as_of)), 0, 100)
            info = {"raw_score": float(score), "adjustments": []}
        else:
            result, dimension_details, dimension_penalties, signals = _score_dimension(ctx, dimension_key)
            details.extend(dimension_details)
            penalties.extend(dimension_penalties)
            if result is None:
                # Every criterion of this dimension is inapplicable to this query, so
                # the dimension leaves BOTH the numerator and the denominator - the
                # rule scoring.config.json config_notes states for a single criterion,
                # applied to a dimension all of whose criteria are inapplicable.
                # Leaving it out of the numerator alone would silently dock every
                # candidate the dimension's whole weight, which is the INAPPLICABLE
                # state being scored as if it were a zero.
                dropped_dimensions.append(dimension_key)
                continue
            score, info = result
            info["signals_fired"] = signals
        dimension_scores[dimension_key] = score
        weight = _dec(weights[dimension_key])
        applicable_weight += weight
        weighted_total += _dec(score) * weight
        info["score"] = score
        info["weight"] = int(weight)
        info["weighted_contribution"] = _common.round_half_up(_dec(score) * weight / Decimal(100), 2)
        breakdown[dimension_key] = info

    _pair_skipped_rules(ctx, penalties, skipped_rules)

    if applicable_weight <= 0:
        qualification_score = 0
    else:
        qualification_score = _clamp_int(
            int(_common.round_half_up(weighted_total / applicable_weight)), 0, 100
        )
    if dropped_dimensions:
        ctx.notes.append(
            "the query imposes no constraint that %s can score, so the dimension was dropped "
            "from both the numerator and the denominator and the remaining weights were "
            "renormalised (scoring.config.json config_notes, INAPPLICABLE vs UNKNOWN); scores "
            "from this run are not comparable to a run that supplied a query surface"
            % (", ".join(dropped_dimensions),)
        )

    missing = []
    for penalty in penalties:
        if penalty["label"] not in missing:
            missing.append(penalty["label"])

    flag_limit = _common.unknown_flag_limit(config, "seller")
    if len(penalties) > flag_limit:
        # The note MUST NAME the unpublished claims, never assert a generic gap
        # (scoring.config.json unknown.flag_behaviour): in this vertical a handful of
        # unpublished commercial terms is the baseline, so "this record has gaps"
        # tells a reviewer nothing while naming the three terms tells them what to ask
        # for. The labels are the unknown_penalty_applied entries' own labels, in
        # first-appearance order, so the note and missing[] can never disagree.
        ctx.notes.append(
            "확인 필요 / needs verification: %s (evidence_gap; SCORING-CONTRACT 2.3)"
            % ("; ".join(missing),)
        )

    confidence = _confidence(ctx, dimension_scores.get("evidence_quality", 0))

    notes = [note for note in (record.get("notes") or []) if isinstance(note, str)]
    for note in ctx.notes:
        if note not in notes:
            notes.append(note)
    notes = _cap_notes(notes, schema)

    extensions = dict(record.get("extensions") or {})
    extensions["score_breakdown"] = {
        "weights": dict((key, int(_dec(value))) for key, value in weights.items()),
        "dimensions": breakdown,
    }

    return {
        "schema_version": record.get("schema_version") or _common.SCHEMA_VERSION,
        "score_version": config["score_version"],
        "qualification_score": qualification_score,
        "dimension_scores": dimension_scores,
        "dimension_details": details,
        "unknown_penalty_applied": penalties,
        "confidence": confidence,
        "missing": missing,
        "as_of": as_of,
        "notes": notes,
        "extensions": extensions,
    }


def _cap_notes(notes, schema):
    limit = None
    if isinstance(schema, dict):
        limit = schema.get("properties", {}).get("notes", {}).get("maxItems")
    if not isinstance(limit, int) or len(notes) <= limit:
        return notes
    kept = notes[: limit - 1]
    kept.append("%d further notes omitted to stay inside the schema limit" % (len(notes) - limit + 1,))
    return kept


def _assemble_record(record, scored, schema):
    order = []
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        order = list(schema["properties"].keys())
    out = {}
    for key in order:
        if key in scored:
            out[key] = scored[key]
        elif key in record:
            out[key] = record[key]
    for key in record:
        if key not in out:
            out[key] = scored[key] if key in scored else record[key]
    for key in scored:
        if key not in out:
            out[key] = scored[key]
    return out


# --------------------------------------------------------------------------
# discovery hard filters (BUILD-CONTRACT R6.2.6, predicates SCORING-CONTRACT 3.1)
# --------------------------------------------------------------------------


def _render_reason(template, observed, required):
    return (
        str(template)
        .replace("{observed_value}", _fmt_value(observed))
        .replace("{required_value}", _fmt_value(required))
    )


def _failed_rule(rule, observed, required):
    return {
        "rule_id": rule["rule_id"],
        "rule_name": rule["name"],
        "reason": _render_reason(rule.get("failure_message_template", ""), observed, required),
        "observed_value": observed,
        "required_value": required,
    }


def _hard_filter(record, query, config, notes):
    """Evaluate the discovery subset. Returns (failed_rules, skipped_rule_ids)."""
    failed, skipped = [], set()
    rules = dict((rule["rule_id"], rule) for rule in config["hard_filters"])
    tolerances = config["hard_filter_tolerances"]

    # HF-01 product category
    rule = rules.get("HF-01")
    required = _query_categories(query)
    if rule is not None and required:
        categories = _normalised_categories(record.get("product_categories"), [])
        if categories is None:
            skipped.add("HF-01")
        elif _common.category_relation(categories, required) not in ("exact", "parent"):
            failed.append(_failed_rule(rule, categories, required))

    # HF-02 commercial model
    rule = rules.get("HF-02")
    model = _requested_model(query)
    if rule is not None and model != "either":
        field = MODEL_FLAGS.get(model)
        value = _common.tri_state(record.get(field)) if field else _common.UNKNOWN
        if value == _common.UNKNOWN:
            skipped.add("HF-02")
        elif value is False:
            failed.append(_failed_rule(rule, value, model))

    # HF-03 maximum MOQ
    rule = rules.get("HF-03")
    ceiling = query.get("max_moq")
    if rule is not None and not _common.is_unknown(ceiling) and not isinstance(ceiling, bool):
        state, value = _moq_cmp(record, query, notes)
        if state != "known":
            skipped.add("HF-03")
        else:
            limit = _dec(ceiling) * (Decimal(1) + _dec(tolerances["moq_overshoot_ratio"]))
            if value > limit:
                failed.append(_failed_rule(rule, int(value) if value == value.to_integral_value() else float(value),
                                           ceiling))

    # HF-04 required certifications (only against a verified exhaustive list)
    rule = rules.get("HF-04")
    required_certs = _cert_tokens(query.get("required_certifications"))
    if rule is not None and required_certs and _common.tri_state(record.get("certifications_verified")) is True:
        certifications = _as_list(record.get("certifications"))
        if certifications is None:
            skipped.add("HF-04")
        else:
            held = _cert_tokens(certifications)
            if not required_certs <= held:
                failed.append(_failed_rule(rule, sorted(held), sorted(required_certs)))

    # HF-05 excluded destination market
    rule = rules.get("HF-05")
    destination = query.get("destination_country")
    excluded_markets = _as_list(record.get("excluded_markets")) or []
    if rule is not None and not _common.is_unknown(destination) and excluded_markets:
        markets = [str(m).upper() for m in excluded_markets if isinstance(m, str)]
        if str(destination).upper() in markets:
            failed.append(_failed_rule(rule, markets, str(destination).upper()))

    # HF-08 seller country
    rule = rules.get("HF-08")
    required_countries = [str(c).upper() for c in (_as_list(query.get("required_seller_countries")) or [])]
    excluded_countries = [str(c).upper() for c in (_as_list(query.get("excluded_seller_countries")) or [])]
    if rule is not None and (required_countries or excluded_countries):
        country = record.get("country")
        if _common.is_unknown(country):
            skipped.add("HF-08")
        else:
            country = str(country).upper()
            ok = (not required_countries or country in required_countries) and country not in excluded_countries
            if not ok:
                failed.append(_failed_rule(rule, country, required_countries or excluded_countries))

    order = [rule_id for rule_id in DISCOVERY_RULES]
    failed.sort(key=lambda entry: order.index(entry["rule_id"]) if entry["rule_id"] in order else 99)
    return failed, skipped


# --------------------------------------------------------------------------
# query surface, exclusions, threshold
# --------------------------------------------------------------------------


CONDITION_SOURCES = {
    "query.country_absent": "country",
    "query.product_categories_absent": "product_categories",
    "query.product_forms_absent": "product_forms",
    "query.channels_absent": "channels",
    "query.required_certifications_absent": "required_certifications",
    "query.preferred_certifications_absent": "preferred_certifications",
    "query.destination_country_absent": "destination_country",
    "query.company_types_absent": "company_types",
}


def _condition_flags(query, config):
    flags = {}
    for key in config.get("condition_keys", {}):
        field = CONDITION_SOURCES.get(key)
        if field is None:
            continue
        value = query.get(field)
        if isinstance(value, (list, tuple)):
            flags[key] = len(value) == 0
        else:
            flags[key] = _common.is_unknown(value)
    return flags


def _load_query(argument):
    if argument is None:
        return {}
    text = argument.strip()
    if text.startswith("{"):
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise _common.UsageError("--query is not valid JSON: %s" % (exc,))
    else:
        if not os.path.isfile(text):
            raise _common.UsageError("--query file not found: %s" % (text,))
        with open(text, encoding="utf-8") as handle:
            try:
                value = json.load(handle)
            except ValueError as exc:
                raise _common.UsageError("--query file is not valid JSON: %s" % (exc,))
    if not isinstance(value, dict):
        raise _common.UsageError("--query must be a JSON object (the BUILD-CONTRACT 3.9 query surface)")
    return value


def _resolve_threshold(config, scores, mode_override, value_override):
    thresholds = config["thresholds"]
    mode = mode_override or str(thresholds["mode"])
    if value_override is not None:
        return int(value_override), mode
    if mode == "percentile":
        population = sorted(scores)
        minimum = int(thresholds["percentile_min_population"])
        if len(population) >= minimum and population:
            percentile = _dec(thresholds["percentile"])
            rank = int(_common.round_half_up(percentile * Decimal(len(population)) / Decimal(100)))
            rank = _clamp_int(rank, 1, len(population))
            return max(int(population[rank - 1]), int(thresholds["percentile_floor"])), "percentile"
        return int(thresholds["fixed"]), "fixed"
    return int(thresholds["fixed"]), "fixed"


def _record_id(record):
    for key in ("seller_id", "buyer_id", "id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _excluded_entry(record, reason, failed_rules=None, score=None):
    entry = {
        "id": _record_id(record),
        "company_name": record.get("company_name") or "unknown",
        "canonical_domain": record.get("canonical_domain") or _common.UNKNOWN,
        "reason_summary": reason,
    }
    website = record.get("website")
    if isinstance(website, str) and website.startswith("http"):
        entry["website"] = website
    if failed_rules:
        entry["failed_rules"] = failed_rules
    if score is not None:
        entry["qualification_score"] = score
    return entry


def _has_material_evidence(record, config):
    claims = set(config["evidence"]["material_claims"][ENTITY])
    for item in _evidence_items(record):
        if item.get("claim") in claims:
            return True
    return False


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="score_seller.py",
        description="Score seller records against a query surface (PRD 6.3, SCORING-CONTRACT 2).",
    )
    parser.add_argument("-i", "--input", default=None)
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--as-of", dest="as_of", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--no-validate", dest="validate", action="store_false", default=True)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--query", default=None)
    parser.add_argument("--threshold", type=int, default=None)
    parser.add_argument("--threshold-mode", dest="threshold_mode", choices=("fixed", "percentile"), default=None)
    parser.add_argument("--min-score", dest="min_score", type=int, default=None)
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--hard-filter", dest="hard_filter", action="store_true", default=True)
    parser.add_argument("--no-hard-filter", dest="hard_filter", action="store_false")
    parser.add_argument("--records-only", dest="records_only", action="store_true")
    return parser


def _split_input(document):
    if isinstance(document, dict) and isinstance(document.get("records"), list):
        return document["records"], document.get("query"), document.get("as_of")
    if isinstance(document, dict) and isinstance(document.get("sellers"), list):
        return document["sellers"], document.get("query"), document.get("as_of")
    if isinstance(document, list):
        return document, None, None
    if isinstance(document, dict):
        return [document], document.get("query"), document.get("as_of")
    raise _common.UsageError("input must be a record object, an array of records, or an envelope")


def main(argv):
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = _common.load_config(args.config)
    except Exception as exc:
        return _common.die(str(exc), 2)

    if args.version:
        sys.stdout.write(
            "score_seller.py skill_version=%s schema_version=%s score_version=%s\n"
            % (_common.SKILL_VERSION, _common.SCHEMA_VERSION, config["score_version"])
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
    document = _common.read_input(args)
    records, envelope_query, envelope_as_of = _split_input(document)
    query = _load_query(args.query)
    if not query and isinstance(envelope_query, dict):
        query = envelope_query
    as_of = args.as_of or envelope_as_of
    as_of = _common.resolve_as_of(records, as_of)

    schema = None
    try:
        schema = _common.load_schema(ENTITY, args.schema_dir)
    except Exception as exc:
        if args.validate:
            return _common.die("cannot load %s schema: %s" % (ENTITY, exc), 2)

    cond = _condition_flags(query, config)

    notes, excluded, scored_records = [], [], []
    partial = False
    candidates_found = len(records)

    for record in records:
        if not isinstance(record, dict):
            partial = True
            notes.append("unknown: input entry is not a JSON object; skipped (INV-35)")
            continue
        record_notes = []
        skipped_rules = set()
        if args.hard_filter:
            try:
                failed_rules, skipped_rules = _hard_filter(record, query, config, record_notes)
            except _common.DataError as exc:
                partial = True
                notes.append("%s: %s" % (_record_id(record), exc))
                failed_rules, skipped_rules = [], set()
            if failed_rules:
                excluded.append(
                    _excluded_entry(
                        record,
                        "%s %s" % (failed_rules[0]["rule_id"], failed_rules[0]["reason"]),
                        failed_rules=failed_rules,
                    )
                )
                continue
        if not _has_material_evidence(record, config):
            excluded.append(_excluded_entry(record, _common.disc06_exclusion_reason(record)))
            continue
        try:
            scored = _score_record(record, config, as_of, query, cond, schema, skipped_rules)
        except _common.DataError as exc:
            partial = True
            notes.append("%s: %s" % (_record_id(record), exc))
            continue
        scored_records.append((record, scored))

    if not args.hard_filter:
        notes.append("hard filters were disabled for this run (--no-hard-filter, BUILD-CONTRACT R6.2.6)")
    if not query:
        # Without a query surface every hard filter is skipped and most criteria are
        # inapplicable, so the run is schema-valid and almost meaningless: "0 qualified"
        # then reads as "no Korean maker meets the brief" when the brief was never
        # applied. Degrade (INV-35), but never silently.
        partial = True
        notes.append(
            "no query surface supplied (--query): hard filters %s were skipped and the "
            "criteria that read the query (S-PF1/S-PF2/S-CP1/S-CP2/S-CP3) are inapplicable, "
            "so these scores are NOT comparable to a filtered run and a low qualified_count "
            "does not mean the market has no match" % (", ".join(DISCOVERY_RULES),)
        )

    threshold_value, threshold_mode = _resolve_threshold(
        config,
        [scored["qualification_score"] for _r, scored in scored_records],
        args.threshold_mode,
        args.threshold,
    )

    emitted = []
    for record, scored in scored_records:
        score = scored["qualification_score"]
        if args.min_score is not None and score < args.min_score:
            excluded.append(
                _excluded_entry(
                    record,
                    "qualification score %d below the requested minimum %d" % (score, args.min_score),
                    score=score,
                )
            )
            continue
        scored["qualified"] = score >= threshold_value
        emitted.append(_assemble_record(record, scored, schema))

    emitted = _common.stable_sort(
        emitted, [("qualification_score", "desc"), ("canonical_domain", "asc")]
    )

    returned = emitted
    if args.top is not None and args.top >= 0:
        returned = emitted[: args.top]
        if len(returned) < args.top:
            notes.append(
                "returned %d of %d requested: evidence quality prioritised over count (DISC-06)"
                % (len(returned), args.top)
            )

    def _excluded_key(entry):
        rules = entry.get("failed_rules") or []
        rule_id = rules[0].get("rule_id", "") if rules else ""
        domain = entry.get("canonical_domain") or _common.UNKNOWN
        # A record copied straight from the input may carry a non-string
        # canonical_domain / rule_id; a mixed-type sort key raises TypeError and
        # would abort the whole run instead of degrading (INV-35, R7.3.2).
        if not isinstance(domain, str):
            domain = _common.UNKNOWN
        if not isinstance(rule_id, str):
            rule_id = ""
        return (rule_id or "\uffff", 1 if domain == _common.UNKNOWN else 0, domain)

    excluded = sorted(excluded, key=_excluded_key)

    flag_limit = _common.unknown_flag_limit(config, "seller")
    summary = {
        "candidates_found": candidates_found,
        "scored_count": len(scored_records),
        "returned": len(returned),
        "qualified_count": sum(1 for r in returned if r.get("qualified")),
        "excluded_count": len(excluded),
        "threshold_used": threshold_value,
        "threshold_mode": threshold_mode,
    }
    if args.top is not None and args.top >= 1:
        summary["top_n_requested"] = args.top
    summary["unknown_flagged_count"] = sum(
        1 for r in returned if len(r.get("unknown_penalty_applied") or []) > flag_limit
    )
    if returned:
        scores = [r["qualification_score"] for r in returned]
        summary["score_range"] = {"min": min(scores), "max": max(scores)}

    envelope = {
        "schema_version": _common.SCHEMA_VERSION,
        "score_version": config["score_version"],
        "skill_version": _common.SKILL_VERSION,
        "as_of": as_of,
        "entity": ENTITY,
        "query": query,
        "summary": summary,
        "records": returned,
        "excluded": excluded,
        "partial": partial,
        "notes": notes,
    }

    errors = []
    if args.validate and schema is not None:
        for index, record in enumerate(returned):
            for message in _common.validate(record, schema):
                errors.append("records[%d] (%s): %s" % (index, _record_id(record), message))
        try:
            envelope_schema = _common.load_schema("discovery-result", args.schema_dir)
        except Exception as exc:
            errors.append("cannot load discovery-result schema: %s" % (exc,))
        else:
            errors.extend(_common.validate(envelope, envelope_schema))

    payload = returned if args.records_only else envelope
    handle = None
    if args.output:
        try:
            handle = open(args.output, "w", encoding="utf-8")
        except OSError as exc:
            return _common.die("cannot write --output: %s" % (exc,), 2)
    try:
        _common.write_output(payload, args.pretty, handle)
    finally:
        if handle is not None:
            handle.close()

    if errors:
        # Error detail is printed even under --quiet (BUILD-CONTRACT 7.2, R7.3.3).
        _common.die("output failed validation (%d problems)" % (len(errors),), 1)
        for message in errors[:100]:
            _common.eprint("  " + message)
        return 1

    _common.eprint(
        "scored %d seller record(s) as of %s; %d qualified at >= %d"
        % (len(returned), as_of, summary["qualified_count"], threshold_value),
        quiet=args.quiet,
    )
    if partial:
        # INV-35 degrades rather than aborting, but the operator reads THIS line, not
        # the envelope: a confident "N qualified at >= T" with nothing beside it is how
        # a run that never applied the brief (no --query: hard filters skipped, the
        # query-reading criteria inapplicable) gets mistaken for a finished answer.
        # The "ERROR:" prefix stays reserved for the R7.3.3 failure line, so this one
        # says WARNING and the exit code stays 0.
        _common.eprint(
            "WARNING: partial run (envelope partial=true); these results are incomplete:",
            quiet=args.quiet,
        )
        for note in notes[:5]:
            _common.eprint("  - " + note, quiet=args.quiet)
        if len(notes) > 5:
            _common.eprint(
                "  - (+%d more in the envelope notes[])" % (len(notes) - 5,), quiet=args.quiet
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
