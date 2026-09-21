#!/usr/bin/env python3
"""Buyer qualification scoring.

Implements docs/SCORING-CONTRACT.md sections 1 (buyer rubric), 4 (evidence
quality, via _common), 5 (determinism) and docs/BUILD-CONTRACT.md 7.8
(CLI, discovery envelope).

Every tunable number is read from schemas/scoring.config.json at run time;
nothing numeric is hard-coded here (INV-29). The only time source is --as-of
(INV-14): no wall-clock read appears anywhere in this file.

Signal firing, in three flavours (the criterion tables of SCORING-CONTRACT 1.x):

  field signal      decided by a record field (korean_products_signal_true,
                    company_type_distributor, channel_match, ...)
  claim signal      decided by a canonical evidence claim plus a structural
                    predicate the contract states (is_official, source_tier)
  agent signal      decided by what a source SAYS, which no script can judge.
                    It fires when the record carries an evidence item whose
                    `claim` equals the signal key itself, e.g.
                    {"claim": "korean_category_page", ...}. That keeps the
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
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

ENTITY = "buyer"

# Criteria whose `inapplicable_when` list is not yet carried by the config.
# SCORING-CONTRACT 8 reconciliation ledger; empty for the buyer rubric.
INTERIM_INAPPLICABLE_WHEN = {}

# Buyer contact-channel types that carry a URL (SCORING-CONTRACT 1.5, B-RE2).
URL_CHANNEL_TYPES = frozenset(
    ("partnership_form", "wholesale_form", "form", "contact_page", "linkedin", "messenger")
)


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


def _is_absent(value):
    """True when the field carries no information at all (absent or null)."""
    return value is None


def _as_list(value):
    return value if isinstance(value, list) else None


def _url_path(value):
    """Path component of a URL, without importing urllib (R7.11.1 grep list)."""
    text = str(value).strip()
    marker = text.find("://")
    rest = text[marker + 3:] if marker >= 0 else text
    for sep in ("?", "#"):
        cut = rest.find(sep)
        if cut >= 0:
            rest = rest[:cut]
    slash = rest.find("/")
    return rest[slash:] if slash >= 0 else ""


def _email_domain(value):
    text = str(value).strip()
    if text.lower().startswith("mailto:"):
        text = text[7:]
    return _common.canonical_domain(text.split("@")[-1])


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
    tier_points = config["evidence"]["source_tier_points"]
    points = tier_points.get(str(item.get("source_tier")))
    if points is None:
        return Decimal(0)
    return _dec(_common.round_half_up(_dec(points) * _recency_multiplier(item, as_of, config)))


def _best_evidence(items, as_of, config):
    """argmax item_strength; ties: lower tier, newer source_date, smaller id.

    Three stable passes, weakest tie-break first (SCORING-CONTRACT 4.3).
    """
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
        "categories", "categories_raw", "had_vertical_marker", "notes",
    )

    def __init__(self, record, config, as_of, query, cond):
        self.record = record
        self.config = config
        self.as_of = as_of
        self.query = query
        self.cond = cond
        self.evidence_index = _index_evidence(record)
        self.notes = []
        raw = record.get("product_categories")
        self.categories_raw = raw
        self.had_vertical_marker = False
        if not isinstance(raw, list):
            self.categories = None
        else:
            normalised = []
            for slug in raw:
                token = _common.normalize_category(slug) if isinstance(slug, str) else None
                if token is None:
                    self.had_vertical_marker = True
                    self.notes.append(
                        "category token %r is a vertical marker, not a category; dropped before "
                        "scoring (BUILD-CONTRACT 8.5)" % (slug,)
                    )
                    continue
                if token not in normalised:
                    normalised.append(token)
            if raw and not normalised:
                # A list that consisted only of vertical markers is UNKNOWN,
                # never verified-empty and never non-beauty (BUILD-CONTRACT 8.5).
                self.categories = None
            else:
                self.categories = normalised

    def field(self, name):
        return self.record.get(name)

    def tri(self, name):
        return _common.tri_state(self.record.get(name))


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


def _attach_claim_evidence(ctx, evidence_ids, *claims):
    """Attach the record's own evidence for a field-driven signal's claim key."""
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
    fired.append(signal_key)
    for item in items:
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident:
            evidence_ids.add(ident)
    return True


# --------------------------------------------------------------------------
# category vocabulary classes (BUILD-CONTRACT 8.5, derived - never duplicated)
# --------------------------------------------------------------------------


def _vocabulary():
    parents = getattr(_common, "CATEGORY_PARENTS", {}) or {}
    vocab = set(parents.keys()) | set(parents.values())
    adjacent_roots = ("personal_care", "inner_beauty")
    adjacent = set(adjacent_roots) & vocab
    for child, parent in parents.items():
        if parent in adjacent_roots:
            adjacent.add(child)
    return vocab, adjacent, vocab - adjacent


VOCABULARY, BEAUTY_ADJACENT, BEAUTY_CORE = _vocabulary()

# buyer.schema.json channels values that mean a multi-store buying format, i.e. a
# retailer that runs a central buying function rather than a single storefront
# (scoring.config.json b2b_commercial_role.adjustments.retail_chain_procurement).
CHAIN_PROCUREMENT_CHANNELS = frozenset(("pharmacy", "department_store", "marketplace"))


# --------------------------------------------------------------------------
# criterion evaluators - SCORING-CONTRACT 1.1 .. 1.5
# --------------------------------------------------------------------------


def _c_b_kf1(ctx):
    fired, evidence_ids = [], set()
    signal = ctx.tri("korean_products_signal")
    if signal is True:
        fired.append("korean_products_signal_true")
    elif signal is False:
        fired.append("korean_products_signal_false")
    brands = _as_list(ctx.field("korean_brands_carried"))
    if brands is not None:
        if len(brands) >= 3:
            fired.append("korean_brands_named_3_plus")
        elif len(brands) >= 1:
            fired.append("korean_brands_named_1_2")
    statement = _fire_from_evidence(
        ctx, fired, evidence_ids, "korea_sourcing_statement", ("korea_sourcing_statement",), official=True
    )
    _attach_claim_evidence(ctx, evidence_ids, "korean_products_signal", "korean_brands_carried")
    unknown = (signal == _common.UNKNOWN) and (brands is None) and not statement
    return fired, evidence_ids, unknown


def _c_b_kf2(ctx):
    fired, evidence_ids = [], set()
    for key in ("kbeauty_specialist_positioning", "korean_category_page", "korean_beauty_marketing_mention"):
        _fire_from_evidence(ctx, fired, evidence_ids, key, (key,))
    positioning = bool(fired) or bool(_evidence_with(ctx, "kbeauty_positioning"))
    # Field-derived fallback: evidenced Korean carriage on a beauty catalogue IS
    # K-Beauty positioning, and it is the only route to this criterion that does not
    # need an agent-authored claim key. Without it the criterion fired for none of a
    # live 20-company cohort and scored 0 whenever product_categories was known,
    # while an all-unknown record took the neutral - i.e. knowing less scored higher.
    korean = ctx.tri("korean_products_signal")
    if korean is True and ctx.categories and any(slug in BEAUTY_CORE for slug in ctx.categories):
        fired.append("korean_carriage_with_beauty_categories")
        _attach_claim_evidence(ctx, evidence_ids, "korean_products_signal", "product_categories")
    # categories is None for an absent list AND for one that held only vertical
    # markers, which BUILD-CONTRACT 8.5 declares unknown rather than verified-empty.
    # The unknown state is keyed to the same inputs the criterion can score, so a
    # record cannot score below the neutral by disclosing more.
    unknown = (
        not fired
        and not positioning
        and ctx.categories is None
        and korean == _common.UNKNOWN
    )
    return fired, evidence_ids, unknown


def _c_b_kf3(ctx):
    fired, evidence_ids = [], set()
    if ctx.categories is None:
        return fired, evidence_ids, True
    if any(slug in BEAUTY_CORE for slug in ctx.categories):
        fired.append("beauty_categories_present")
    elif any(slug in BEAUTY_ADJACENT for slug in ctx.categories):
        fired.append("beauty_adjacent_categories_present")
    _attach_claim_evidence(ctx, evidence_ids, "product_categories")
    return fired, evidence_ids, False


def _c_b_cr1(ctx):
    fired, evidence_ids = [], set()
    company_type = ctx.field("company_type")
    if _common.is_unknown(company_type):
        return fired, evidence_ids, True
    fired.append("company_type_%s" % (company_type,))
    for item in _evidence_with(ctx, "company_type"):
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident:
            evidence_ids.add(ident)
    return fired, evidence_ids, False


def _published_quantity(value):
    """True when a quantity field carries a published number rather than nothing.

    Deliberately wider than _common.coerce_range, which returns None for the
    open-upper-bound spelling {"min": n}: "our minimum order is 5,000 AED" IS a
    published minimum even though it is not a comparable range. A boolean, an empty
    dict, "unknown" and an absent field are not published values.
    """
    if _common.is_unknown(value) or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            _common.coerce_range(value)
        except _common.DataError:
            return False
        return True
    if isinstance(value, dict):
        return any(not _common.is_unknown(value.get(bound)) for bound in ("min", "max"))
    return False


def _c_b_cr2(ctx):
    fired, evidence_ids = [], set()
    signal = ctx.tri("wholesale_signal")
    moq = ctx.field("buyer_moq")
    published_moq = _published_quantity(moq)
    if published_moq:
        fired.append("published_minimum_order")
        _attach_claim_evidence(ctx, evidence_ids, "buyer_moq")
    if signal is True:
        fired.append("wholesale_signal_true")
    elif signal is False:
        fired.append("wholesale_signal_false")
    pages = False
    for key in ("b2b_pricing_or_trade_account", "reseller_program_page"):
        pages = _fire_from_evidence(ctx, fired, evidence_ids, key, (key,)) or pages
    _attach_claim_evidence(ctx, evidence_ids, "wholesale_signal")
    # Unknown only when nothing this criterion reads was disclosed (config B-CR2
    # signals_note): a published minimum order or a page is a known positive, and a
    # false wholesale_signal is a known negative.
    unknown = (signal == _common.UNKNOWN) and not pages and not published_moq
    return fired, evidence_ids, unknown


def _c_b_cr3(ctx):
    fired, evidence_ids = [], set()
    for key in ("procurement_or_buying_page", "retail_chain_supplier_page"):
        _fire_from_evidence(ctx, fired, evidence_ids, key, (key,))
    _fire_from_evidence(
        ctx, fired, evidence_ids, "distributor_network_operated", ("distributor_network_operated",)
    )
    channels = _as_list(ctx.field("channels"))
    network = channels is not None and "distributor_network" in channels
    if network and "distributor_network_operated" not in fired:
        fired.append("distributor_network_operated")
    unknown = (
        _is_absent(ctx.field("sourcing_signals")) and channels is None and not fired
    )
    return fired, evidence_ids, unknown


def _c_b_si1(ctx):
    fired, evidence_ids = [], set()
    intent = ctx.field("sourcing_intent")
    if _common.is_unknown(intent):
        return fired, evidence_ids, True
    fired.append("sourcing_intent_%s" % (intent,))
    for item in _evidence_with(ctx, "sourcing_intent"):
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident:
            evidence_ids.add(ident)
    return fired, evidence_ids, False


def _c_b_si2(ctx):
    fired, evidence_ids = [], set()
    signals = _as_list(ctx.field("sourcing_signals"))
    if signals is None:
        return fired, evidence_ids, True
    for entry in signals:
        if isinstance(entry, str) and entry not in fired:
            fired.append(entry)
            for item in _evidence_with(ctx, entry):
                ident = item.get("evidence_id")
                if isinstance(ident, str) and ident:
                    evidence_ids.add(ident)
    return fired, evidence_ids, False


def _sourcing_claim_keys(config):
    keys = set(("sourcing_intent",))
    for criterion in config["buyer"]["dimensions"]["sourcing_intent"]["criteria"]:
        if criterion["criterion_id"] == "B-SI2":
            keys.update(criterion["signals"].keys())
    return keys


def _recency_bands(config):
    """B-SI3 age bands, derived from the config's own signal KEYS.

    The config encodes the bands only inside the key names
    (sourcing_signal_within_90d / _within_365d / _older_than_365d), so they are
    parsed from there rather than written as literals in this file (INV-29).
    Returns ([(max_age_days, key), ...] ascending, fallback_key).
    """
    bands, fallback = [], None
    for criterion in config["buyer"]["dimensions"]["sourcing_intent"]["criteria"]:
        if criterion["criterion_id"] != "B-SI3":
            continue
        for key in criterion.get("signals", {}):
            match = re.search(r"within_(\d+)d$", key)
            if match:
                bands.append((int(match.group(1)), key))
            elif "older_than" in key:
                fallback = key
    bands.sort()
    return bands, fallback


def _standing_sourcing_evidence_ids(ctx, claims):
    """Evidence ids of undated sourcing pages this run actually observed.

    An item qualifies when its claim is a sourcing claim, its source_date is unknown,
    and it carries an observed_at on or before the run's as_of. A future observed_at
    is refused rather than trusted: it would let a mis-stamped item claim a recency
    the run cannot vouch for.
    """
    out = []
    for item in _evidence_items(ctx.record):
        if item.get("claim") not in claims:
            continue
        if not _common.is_unknown(item.get("source_date")):
            continue
        observed_at = item.get("observed_at")
        if not isinstance(observed_at, str) or len(observed_at) < 10:
            continue
        observed_day = observed_at[:10]
        try:
            if _common.days_between(observed_day, ctx.as_of) < 0:
                continue
        except _common.DataError:
            continue
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident and ident not in out:
            out.append(ident)
    return out


def _c_b_si3(ctx):
    fired, evidence_ids = [], set()
    claims = _sourcing_claim_keys(ctx.config)
    newest_date = None
    newest_ids = []
    for item in _evidence_items(ctx.record):
        if item.get("claim") not in claims:
            continue
        source_date = item.get("source_date")
        if _common.is_unknown(source_date):
            continue
        text = str(source_date)
        if newest_date is None or text > newest_date:
            newest_date = text
            newest_ids = []
        if text == newest_date:
            ident = item.get("evidence_id")
            if isinstance(ident, str) and ident:
                newest_ids.append(ident)
    if newest_date is None:
        # A live supplier-facing page that publishes no machine-readable date is the
        # ordinary case, not an absence of information. It is decided by observed_at
        # (OUR reading time, bounded by the run's as_of), never by a page date this
        # package does not have, and it stays below every dated band in the config.
        standing_ids = _standing_sourcing_evidence_ids(ctx, claims)
        if standing_ids:
            fired.append("standing_sourcing_page_undated")
            evidence_ids.update(standing_ids)
            return fired, evidence_ids, False
        return fired, evidence_ids, True
    evidence_ids.update(newest_ids)
    age_days = max(0, _common.days_between(newest_date, ctx.as_of))
    bands, fallback = _recency_bands(ctx.config)
    for limit, key in bands:
        if age_days <= limit:
            fired.append(key)
            break
    else:
        if fallback is not None:
            fired.append(fallback)
    return fired, evidence_ids, False


def _c_b_mr1(ctx):
    fired, evidence_ids = [], set()
    country = ctx.field("country")
    if _common.is_unknown(country):
        return fired, evidence_ids, True
    country = str(country).upper()
    query = ctx.query
    region = [str(c).upper() for c in (_as_list(query.get("region_countries")) or [])]
    adjacent = [str(c).upper() for c in (_as_list(query.get("adjacent_region_countries")) or [])]
    # Graded band on one input: exactly one tier fires (SCORING-CONTRACT 1.4).
    if isinstance(query.get("country"), str) and country == str(query["country"]).upper():
        fired.append("country_exact_match")
    elif country in region:
        fired.append("country_in_requested_region")
    elif country in adjacent:
        fired.append("country_adjacent_market")
    for item in _evidence_with(ctx, "country"):
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident:
            evidence_ids.add(ident)
    return fired, evidence_ids, False


def _query_categories(query):
    raw = _as_list(query.get("product_categories")) or []
    out = []
    for slug in raw:
        token = _common.normalize_category(slug) if isinstance(slug, str) else None
        if _common.is_known_category(token) and token not in out:
            out.append(token)
    return out


def _c_b_mr2(ctx):
    fired, evidence_ids = [], set()
    if ctx.categories is None:
        return fired, evidence_ids, True
    relation = _common.category_relation(ctx.categories, _query_categories(ctx.query))
    if relation == "exact":
        fired.append("category_exact_match")
    elif relation == "parent":
        fired.append("category_parent_match")
    elif relation == "adjacent":
        fired.append("category_adjacent_match")
    for item in _evidence_with(ctx, "product_categories"):
        ident = item.get("evidence_id")
        if isinstance(ident, str) and ident:
            evidence_ids.add(ident)
    return fired, evidence_ids, False


def _channel_adjacency(config):
    pairs = config.get("channel_adjacency", {}).get("pairs", []) or []
    table = {}
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        left, right = pair[0], pair[1]
        table.setdefault(left, set()).add(right)
        table.setdefault(right, set()).add(left)
    return table


def _c_b_mr3(ctx):
    fired, evidence_ids = [], set()
    channels = _as_list(ctx.field("channels"))
    if channels is None:
        return fired, evidence_ids, True
    requested = _as_list(ctx.query.get("channels")) or []
    if set(channels) & set(requested):
        fired.append("channel_match")
    else:
        table = _channel_adjacency(ctx.config)
        for channel in channels:
            if set(table.get(channel, ())) & set(requested):
                fired.append("channel_partial_match")
                break
    _attach_claim_evidence(ctx, evidence_ids, "channels")
    return fired, evidence_ids, False


def _contact_channels(ctx):
    channels = ctx.field("contact_channels")
    if not isinstance(channels, list):
        return None
    return [c for c in channels if isinstance(c, dict)]


def _c_b_re1(ctx):
    fired, evidence_ids = [], set()
    channels = _contact_channels(ctx)
    if channels is None:
        return fired, evidence_ids, True
    signals = _criterion_signals(ctx.config, "reachability", "B-RE1")
    for channel in channels:
        key = "contact_channel_%s" % (channel.get("type"),)
        if key not in signals:
            key = "contact_channel_other"
        if key not in fired:
            fired.append(key)
        for ident in channel.get("evidence_ids") or []:
            if isinstance(ident, str) and ident:
                evidence_ids.add(ident)
    return fired, evidence_ids, False


def _normalised_channel_pair(channel, index):
    """(type, normalised value) per SCORING-CONTRACT 1.5 B-RE2."""
    kind = channel.get("type")
    value = channel.get("value")
    if not isinstance(value, str):
        return (kind, "\x00empty-%d" % (index,)), True
    if kind == "corporate_email":
        text = value.strip()
        if text.lower().startswith("mailto:"):
            text = text[7:]
        normalised = text.casefold()
    elif kind == "phone":
        normalised = re.sub(r"\D", "", value)
    elif kind in URL_CHANNEL_TYPES or kind == "other":
        domain = _common.canonical_domain(value) or ""
        normalised = domain + _url_path(value).rstrip("/").casefold()
    else:
        normalised = value.strip().casefold()
    if not normalised:
        return (kind, "\x00empty-%d" % (index,)), True
    return (kind, normalised), False


def _c_b_re2(ctx):
    fired, evidence_ids = [], set()
    channels = _contact_channels(ctx)
    if channels is None:
        return fired, evidence_ids, True
    pairs = []
    for index, channel in enumerate(channels):
        pair, empty = _normalised_channel_pair(channel, index)
        if empty:
            ctx.notes.append(
                "contact channel %r has no comparable normalised value; counted as its own "
                "distinct channel (SCORING-CONTRACT 1.5)" % (channel.get("value"),)
            )
        if pair not in pairs:
            pairs.append(pair)
    count = len(pairs)
    if count >= 3:
        fired.append("channels_3_plus")
    elif count == 2:
        fired.append("channels_2")
    elif count == 1:
        fired.append("channels_1")
    _attach_claim_evidence(ctx, evidence_ids, "contact_channels")
    return fired, evidence_ids, False


def _free_mail_domains(config):
    """SCORING-CONTRACT 8 ledger item 4: config first, then _common, else none."""
    domains = config.get("free_mail_domains")
    if domains is None:
        domains = getattr(_common, "FREE_MAIL_DOMAINS", None)
    if not domains:
        return frozenset()
    return frozenset(str(d).lower() for d in domains)


def _c_b_re3(ctx):
    fired, evidence_ids = [], set()
    channels = _contact_channels(ctx)
    if channels is None:
        return fired, evidence_ids, True
    canonical = ctx.field("canonical_domain")
    canonical = str(canonical).lower() if isinstance(canonical, str) else None
    aliases = set()
    for alias in _as_list(ctx.field("alias_domains")) or []:
        normalised = _common.canonical_domain(alias) if isinstance(alias, str) else None
        if normalised:
            aliases.add(normalised)
        elif isinstance(alias, str):
            aliases.add(alias.lower())
    free_mail = _free_mail_domains(ctx.config)
    for channel in channels:
        if channel.get("type") != "corporate_email":
            continue
        domain = _email_domain(channel.get("value") or "")
        if not domain:
            continue
        if (canonical is not None and domain == canonical and domain != _common.UNKNOWN) or domain in aliases:
            if "corporate_domain_email" not in fired:
                fired.append("corporate_domain_email")
                for ident in channel.get("evidence_ids") or []:
                    if isinstance(ident, str) and ident:
                        evidence_ids.add(ident)
        elif domain in free_mail:
            if "free_mail_domain_email" not in fired:
                fired.append("free_mail_domain_email")
        elif "other_corporate_domain_email" in _criterion_signals(ctx.config, "reachability", "B-RE3"):
            # Neither the company's own domain nor a free-mail host: a sibling company
            # domain, a group domain, or a near-identical spelling. It is still a
            # corporate address, and scoring it 0 while a gmail.com address scores 6
            # is backwards (it hit 3 of 20 candidates in a live run).
            if "other_corporate_domain_email" not in fired:
                fired.append("other_corporate_domain_email")
                for ident in channel.get("evidence_ids") or []:
                    if isinstance(ident, str) and ident:
                        evidence_ids.add(ident)
    return fired, evidence_ids, False


EVALUATORS = {
    "B-KF1": _c_b_kf1,
    "B-KF2": _c_b_kf2,
    "B-KF3": _c_b_kf3,
    "B-CR1": _c_b_cr1,
    "B-CR2": _c_b_cr2,
    "B-CR3": _c_b_cr3,
    "B-SI1": _c_b_si1,
    "B-SI2": _c_b_si2,
    "B-SI3": _c_b_si3,
    "B-MR1": _c_b_mr1,
    "B-MR2": _c_b_mr2,
    "B-MR3": _c_b_mr3,
    "B-RE1": _c_b_re1,
    "B-RE2": _c_b_re2,
    "B-RE3": _c_b_re3,
}


def _criterion_signals(config, dimension_key, criterion_id):
    for criterion in config["buyer"]["dimensions"][dimension_key]["criteria"]:
        if criterion["criterion_id"] == criterion_id:
            return criterion["signals"]
    return {}


# --------------------------------------------------------------------------
# adjustments - SCORING-CONTRACT 1.1 .. 1.5
# --------------------------------------------------------------------------


def _adjustments(ctx, dimension_key, fired_by_criterion, state_by_criterion):
    """Return the adjustment keys that fired, in config order."""
    out = []
    record = ctx.record
    query = ctx.query

    if dimension_key == "kbeauty_korea_fit":
        if (
            ctx.categories
            and not ctx.had_vertical_marker
            and not any(slug in VOCABULARY for slug in ctx.categories)
        ):
            out.append("non_beauty_business")
        korean = ctx.tri("korean_products_signal")
        if korean is False:
            out.append("korean_products_signal_false")
        elif korean == _common.UNKNOWN:
            out.append("korean_products_signal_unknown")

    elif dimension_key == "b2b_commercial_role":
        wholesale = ctx.tri("wholesale_signal")
        if record.get("company_type") == "retailer" and wholesale is False:
            out.append("retail_only_consumer_store")
        if record.get("company_type") == "retailer" and wholesale is not False:
            channels = _as_list(record.get("channels")) or []
            if any(channel in CHAIN_PROCUREMENT_CHANNELS for channel in channels):
                out.append("retail_chain_procurement")
        if wholesale is False:
            out.append("wholesale_signal_false")

    elif dimension_key == "sourcing_intent":
        if _evidence_with(ctx, "sourcing_closed_statement"):
            out.append("sourcing_closed_statement")
        if ctx.tri("partnership_signal") is False:
            out.append("partnership_signal_false")

    elif dimension_key == "market_relevance":
        country_known = not _common.is_unknown(record.get("country"))
        if query.get("country") and country_known and not fired_by_criterion.get("B-MR1"):
            out.append("country_mismatch")
        if (
            _query_categories(query)
            and ctx.categories
            and not fired_by_criterion.get("B-MR2")
        ):
            out.append("category_no_overlap")
        source_query = record.get("source_query")
        if isinstance(source_query, dict) and source_query.get("category_drift") is True:
            out.append("category_drift_flagged")

    elif dimension_key == "reachability":
        stale = record.get("stale") is True
        if not stale:
            best = _best_evidence(_evidence_with(ctx, "contact_channels"), ctx.as_of, ctx.config)
            stale = bool(best) and best.get("stale") is True
        if stale:
            out.append("stale_contact_page")
        channels = record.get("contact_channels")
        if isinstance(channels, list) and not channels:
            out.append("contact_channels_verified_empty")

    ordered = list(ctx.config["buyer"]["dimensions"][dimension_key].get("adjustments", {}).keys())
    return [key for key in ordered if key in out]


# --------------------------------------------------------------------------
# dimension / record scoring
# --------------------------------------------------------------------------


def _unknown_points(config, max_points):
    unknown = config["unknown"]
    factor = (_dec(unknown["neutral_base"]) / Decimal(100)) * _dec(unknown["penalty_factor"])
    return _dec(_common.round_half_up(_dec(max_points) * factor))


def _record_inputs(criterion, entity):
    """Config input paths that name a field of the scored record."""
    prefix = entity + "."
    out = []
    for path in criterion.get("inputs", []) or []:
        if not isinstance(path, str) or path.startswith("query."):
            continue
        text = path if path.startswith(prefix) else prefix + path
        if ":" in text:
            continue
        out.append(text)
    return out


def _unknown_input_paths(criterion, ctx, entity):
    prefix = entity + "."
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


def _score_dimension(ctx, dimension_key, out_key):
    config = ctx.config
    dimension = config["buyer"]["dimensions"][dimension_key]
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
            "dimension": out_key,
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
            unknown_inputs = _unknown_input_paths(criterion, ctx, ENTITY)
            penalties.append({
                "dimension": out_key,
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
            for name in _record_inputs(criterion, ENTITY):
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
        return None, details, penalties, sorted(dimension_signals), []

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
    return (score, breakdown), details, penalties, sorted(dimension_signals), fired_adjustments


def _material_claim_fields(config):
    return list(config["evidence"]["material_claims"][ENTITY])


def _confidence(ctx, evidence_quality_score):
    return _common.record_confidence(ctx.record, ENTITY, evidence_quality_score, ctx.config)


def _score_record(record, config, as_of, query, cond, schema):
    ctx = _Ctx(record, config, as_of, query, cond)
    key_map = config["buyer"]["dimension_score_key_map"]
    weights = config["buyer"]["weights"]

    dimension_scores, details, penalties = {}, [], []
    breakdown = {}
    weighted_total = Decimal(0)
    applicable_weight = Decimal(0)
    dropped_dimensions = []

    for dimension_key in config["buyer"]["dimensions"]:
        out_key = key_map[dimension_key]
        if config["buyer"]["dimensions"][dimension_key].get("computed_by") == "evidence_quality_function":
            score = int(_common.evidence_quality(record, _material_claim_fields(config), as_of, config=config))
            score = _clamp_int(score, 0, 100)
            info = {"raw_score": float(score), "adjustments": []}
        else:
            result, dimension_details, dimension_penalties, signals, _fired = _score_dimension(
                ctx, dimension_key, out_key
            )
            details.extend(dimension_details)
            penalties.extend(dimension_penalties)
            if result is None:
                # Every criterion of this dimension is inapplicable to this query, so the
                # dimension is dropped from BOTH the numerator and the denominator - the
                # same rule config_notes states for a single criterion. Counting it as 0
                # against a fixed /100 denominator would silently dock every candidate the
                # dimension's whole weight (15 points, for market_relevance).
                dropped_dimensions.append(out_key)
                continue
            score, info = result
            info["signals_fired"] = signals
        dimension_scores[out_key] = score
        weight = _dec(weights[dimension_key])
        applicable_weight += weight
        weighted_total += _dec(score) * weight
        info["score"] = score
        info["weight"] = int(weight)
        info["weighted_contribution"] = _common.round_half_up(_dec(score) * weight / Decimal(100), 2)
        breakdown[out_key] = info

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

    flag_limit = _common.unknown_flag_limit(config, "buyer")
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

    # Verification gaps follow the penalty labels and the flag note above, which names only
    # unpublished scoring inputs (SCORING-CONTRACT 0.4, scoring.config.json verification_gaps).
    for label in _common.verification_gaps(config, ENTITY, record, penalties,
                                           ctx.categories, _query_categories(ctx.query)):
        if label not in missing:
            missing.append(label)

    confidence = _confidence(ctx, dimension_scores.get("evidence_quality", 0))

    notes = [note for note in (record.get("notes") or []) if isinstance(note, str)]
    for note in ctx.notes:
        if note not in notes:
            notes.append(note)
    notes = _cap_notes(notes, schema)

    extensions = dict(record.get("extensions") or {})
    extensions["score_breakdown"] = {
        "weights": dict((key_map[k], int(_dec(v))) for k, v in weights.items()),
        "dimensions": breakdown,
    }

    scored = {
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
    return scored


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
        if key == "query.product_categories_absent":
            # Absent means no IN-VOCABULARY category: ["k_beauty"] names none (scoring-03).
            flags[key] = not _query_categories(query)
        elif isinstance(value, (list, tuple)):
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
            value = int(population[rank - 1])
            return max(value, int(thresholds["percentile_floor"])), "percentile"
        return int(thresholds["fixed"]), "fixed"
    return int(thresholds["fixed"]), "fixed"


def _record_id(record):
    for key in ("buyer_id", "seller_id", "id"):
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


def _vertical_gate_signals(config):
    """Positive K-Beauty signal keys, derived from the config, never listed here.

    Every signal of the criteria named by vertical_fit_gate whose point value is
    above 0. korean_products_signal_false is priced 0 and so is NOT positive
    evidence - which is the whole point of deriving the set instead of writing it.
    """
    gate = config.get("vertical_fit_gate") or {}
    keys = set()
    for path in gate.get("requires_any_positive_signal_from") or []:
        if not isinstance(path, str) or "." not in path:
            continue
        dimension_key, criterion_id = path.split(".", 1)
        dimension = config.get(ENTITY, {}).get("dimensions", {}).get(dimension_key) or {}
        for criterion in dimension.get("criteria") or []:
            if criterion.get("criterion_id") != criterion_id:
                continue
            for signal, points in (criterion.get("signals") or {}).items():
                try:
                    if _dec(points) > 0:
                        keys.add(signal)
                except Exception:  # pragma: no cover - a malformed price is not a crash
                    continue
    return keys


def _vertical_token(value):
    """Casefold and drop everything that is not a letter or a digit."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _vertical_gate_applies(config, query):
    gate = config.get("vertical_fit_gate") or {}
    if gate.get("entity") not in (None, ENTITY):
        return False
    token = _vertical_token((query or {}).get("vertical"))
    if not token:
        return False
    wanted = {_vertical_token(t) for t in (gate.get("applies_when_query_vertical_matches") or [])}
    wanted.discard("")
    return token in wanted


def _vertical_gate_failed(config, query, scored):
    """True when a K-Beauty run found no positive K-Beauty evidence on this record.

    This is NOT a rejection: the record stays in records[] with its full score and
    breakdown, and unknown.never_hard_reject_on_unknown is untouched. It only stops a
    high score becoming a reported LEAD when nothing on any page that was read
    evidences Korean carriage or K-Beauty positioning (PRD T09, PRD 6.2 DISC-02).
    """
    if not _vertical_gate_applies(config, query):
        return False
    positive = _vertical_gate_signals(config)
    if not positive:
        return False
    dimensions = (scored.get("extensions") or {}).get("score_breakdown", {}).get("dimensions", {})
    for info in dimensions.values():
        for signal in info.get("signals_fired") or []:
            if signal in positive:
                return False
    return True


def _company_type_excluded(record, query):
    requested = query.get("company_types")
    if not isinstance(requested, list) or not requested:
        return None
    company_type = record.get("company_type")
    if _common.is_unknown(company_type):
        return None
    if company_type in requested:
        return None
    return "company type %s outside the requested types (%s)" % (
        company_type,
        ", ".join(str(t) for t in requested),
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="score_buyer.py",
        description="Score buyer records against a query surface (PRD 6.3, SCORING-CONTRACT 1).",
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
    """Return (records, envelope_query, envelope_as_of)."""
    if isinstance(document, dict) and isinstance(document.get("records"), list):
        return document["records"], document.get("query"), document.get("as_of")
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
    except Exception as exc:  # ConfigError and friends map to exit 2
        return _common.die(str(exc), 2)

    if args.version:
        sys.stdout.write(
            "score_buyer.py skill_version=%s schema_version=%s score_version=%s\n"
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
    as_of = _common.resolve_as_of(records, as_of, config)

    schema = None
    try:
        schema = _common.load_schema(ENTITY, args.schema_dir)
    except Exception as exc:
        if args.validate:
            return _common.die("cannot load %s schema: %s" % (ENTITY, exc), 2)

    cond = _condition_flags(query, config)

    notes, excluded, scored_records = [], [], []
    _common.normalize_category_list(_as_list(query.get("product_categories")), notes, "query")
    partial = False
    candidates_found = len(records)

    for record in records:
        if not isinstance(record, dict):
            partial = True
            notes.append("unknown: input entry is not a JSON object; skipped (INV-35)")
            continue
        reason = _company_type_excluded(record, query)
        if args.hard_filter and reason is not None:
            excluded.append(_excluded_entry(record, reason))
            continue
        if not _has_material_evidence(record, config):
            excluded.append(_excluded_entry(record, _common.disc06_exclusion_reason(record)))
            continue
        try:
            scored = _score_record(record, config, as_of, query, cond, schema)
        except _common.DataError as exc:
            partial = True
            notes.append("%s: %s" % (_record_id(record), exc))
            continue
        scored_records.append((record, scored))

    if not args.hard_filter:
        notes.append(
            "hard filters were disabled for this run (--no-hard-filter, BUILD-CONTRACT R6.2.6); "
            "in a buyer run that suppresses the query company_type exclusion"
        )
    if not query:
        # market_relevance is dropped entirely and the company_type exclusion cannot
        # fire. The run still succeeds, but the operator must be told that the request
        # never constrained anything (INV-35: degrade, never abort - and never silently).
        partial = True
        notes.append(
            "no query surface supplied (--query): the company_type exclusion was skipped "
            "and every market_relevance criterion is inapplicable, so these scores are NOT "
            "comparable to a run that named a country, category or channel"
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
        if scored["qualified"] and _vertical_gate_failed(config, query, scored):
            scored["qualified"] = False
            note = (
                "vertical fit gate: the query asked for the %s vertical and no criterion of "
                "kbeauty_fit fired a positive Korean signal, so this record scores %d but is "
                "NOT reported as a qualified lead (scoring.config.json vertical_fit_gate, "
                "PRD T09). It is kept, scored and ranked; nothing was rejected."
                % (query.get("vertical"), score)
            )
            if note not in scored["notes"]:
                scored["notes"] = _cap_notes(list(scored["notes"]) + [note], schema)
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

    # excluded[] order: first failed rule_id asc, canonical_domain asc ("unknown"
    # last), input order as the final stable tie-break (SCORING-CONTRACT 5.5).
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

    flag_limit = _common.unknown_flag_limit(config, "buyer")
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
    # Validation already ran above: an invalid document never lands on the requested
    # --output path, where a chained command would pick it up (dry-run-09).
    target = _common.invalid_output_path(args.output) if (errors and args.output) else args.output
    stream = None
    handle = None
    if target:
        try:
            handle = open(target, "w", encoding="utf-8")
        except OSError as exc:
            return _common.die("cannot write --output: %s" % (exc,), 2)
        stream = handle
    try:
        _common.write_output(payload, args.pretty, stream)
    finally:
        if handle is not None:
            handle.close()

    if errors:
        # Error detail is printed even under --quiet (BUILD-CONTRACT 7.2, R7.3.3).
        _common.die("output failed validation (%d problems)" % (len(errors),), 1)
        for message in errors[:100]:
            _common.eprint("  " + message)
        if args.output:
            _common.eprint("  invalid document written to %s, not %s" % (target, args.output))
        return 1

    _common.eprint(
        "scored %d buyer record(s) as of %s; %d qualified at >= %d"
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
