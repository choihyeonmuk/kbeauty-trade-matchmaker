#!/usr/bin/env python3
"""Merge duplicate buyer or seller records (BUILD-CONTRACT 7.7 and 8.4).

Key precedence (8.4): canonical_domain first, then normalized_name + country when at
least one record has no domain, then no merge. Merges are computed as connected
components over that relation, so the result never depends on input order (INV-15),
and two different known domains are never folded together (R8.4.2, INV-16).

Merge semantics M1..M8 are implemented as written: evidence is unioned on
(source_url, claim), a known value is never overwritten by "unknown" (INV-17), every
known-vs-known disagreement lands in conflicts[], and merged_from[] / alias_domains[]
keep the merge reversible in principle (INV-18).

Two deliberate local decisions, per the BUILD-CONTRACT 1.4 SHOULD rule:
  * A record carrying no id is referenced in the merge report as "input[<index>]" so
    that M8 reversibility still holds for un-identified raw candidates.
  * M7 invalidates the scores of a merged record. A record whose status is QUALIFIED,
    MATCH_CANDIDATE or READY_FOR_REVIEW is therefore regressed to VERIFIED with a
    note, because INV-37 forbids those states on an unscored document. The backward
    edge is one the skill owns (BUILD-CONTRACT 9.1).

All code comments are English by contract (BUILD-CONTRACT 1.5).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402
import normalize_company  # noqa: E402

#: M5 union lists (BUILD-CONTRACT 8.4).
_UNION_LIST_FIELDS = (
    "product_categories",
    "certifications",
    "export_markets",
    "sourcing_signals",
    "channels",
    "korean_brands_carried",
    "contact_channels",
    "regulatory_registrations",
)

#: Fields handled by dedicated merge rules rather than the M2 per-field rule.
_STRUCTURAL_FIELDS = frozenset(
    [
        "schema_version",
        "score_version",
        "buyer_id",
        "seller_id",
        "evidence",
        "conflicts",
        "merged_from",
        "alias_domains",
        "notes",
        "stale",
        "operational_status",
        "qualification_score",
        "dimension_scores",
        "dimension_details",
        "unknown_penalty_applied",
        "missing",
        "qualified",
        "confidence",
        "as_of",
        "status",
        "source_query",
        "extensions",
    ]
)

#: Scored fields dropped when a merge invalidates the score (M7, INV-23).
_SCORE_FIELDS = ("dimension_scores", "dimension_details", "unknown_penalty_applied", "missing", "qualified")

#: States that require a scored document (INV-37) and must regress after a merge.
_SCORED_STATES = frozenset(["QUALIFIED", "MATCH_CANDIDATE", "READY_FOR_REVIEW"])

#: Keys whose values are evidence-id lists and must follow a re-key (M1).
_EVIDENCE_ID_KEYS = ("evidence_ids", "winning_evidence_ids", "losing_evidence_ids", "conflicts_with")

_MAX_NOTES = 50
_MAX_CONFLICTS = 40
_MAX_ALIAS_DOMAINS = 50
_MAX_EVIDENCE_IDS = 50


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _record_id(record, index):
    """Stable handle for a record: its own id, else a positional label."""
    for key in ("seller_id", "buyer_id"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "input[%d]" % index


def _id_field(entity):
    return "seller_id" if entity == "seller" else "buyer_id"


def _evidence_items(record, claim=None):
    items = [item for item in (record.get("evidence") or []) if isinstance(item, dict)]
    if claim is None:
        return items
    return [item for item in items if item.get("claim") == claim]


def _item_confidence(item):
    value = item.get("confidence")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _item_tier(item):
    value = item.get("source_tier")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 99


def _best_backing(items):
    """M2 ordering: highest confidence first, then the LOWER source_tier number."""
    if not items:
        return None
    ordered = sorted(
        items,
        key=lambda item: (
            -_item_confidence(item),
            _item_tier(item),
            _common._InverseString(str(item.get("source_date") or "")),
            str(item.get("evidence_id") or ""),
        ),
    )
    return ordered[0]


def _backing_ids(items):
    ids = []
    for item in items:
        value = item.get("evidence_id")
        if isinstance(value, str) and value and value not in ids:
            ids.append(value)
    return ids[:_MAX_EVIDENCE_IDS]


def _material_claims(config, entity):
    claims = (config.get("evidence", {}) or {}).get("material_claims", {}) or {}
    return list(claims.get(entity) or [])


def _evidenced_claim_count(record, claims):
    covered = set()
    for item in _evidence_items(record):
        if item.get("claim") in claims:
            covered.add(item.get("claim"))
    return len(covered)


def _tier1_count(record):
    return sum(1 for item in _evidence_items(record) if _item_tier(item) == 1)


def _known(value):
    """A value counts as known unless it is absent, None or "unknown" (3.2)."""
    return not _common.is_unknown(value)


def _add_note(notes, text):
    if text not in notes:
        notes.append(text)


def _sorted_union(values):
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    try:
        return sorted(out)
    except TypeError:
        return sorted(out, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))


def _union_list(field, left, right):
    """M5 union with a deterministic order."""
    combined = list(left or []) + list(right or [])
    if field == "contact_channels":
        seen = {}
        for channel in combined:
            if not isinstance(channel, dict):
                continue
            key = (channel.get("type"), channel.get("value"))
            if key not in seen:
                seen[key] = channel
        return [seen[key] for key in sorted(seen.keys(), key=lambda k: (str(k[0]), str(k[1])))]
    if field == "regulatory_registrations":
        seen = {}
        for entry in combined:
            key = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                seen[key] = entry
        return [seen[key] for key in sorted(seen.keys())]
    return _sorted_union(combined)


# --------------------------------------------------------------------------
# Evidence merge (M1) and reference re-keying
# --------------------------------------------------------------------------


def _next_evidence_id(used):
    counter = 1
    while True:
        candidate = "EV-%03d" % counter
        if candidate not in used:
            return candidate
        counter += 1


def _remap_evidence_ids(node, remap, depth=0):
    """Rewrite every evidence-id reference in a record copy, in place."""
    if depth > 64 or not remap:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _EVIDENCE_ID_KEYS and isinstance(value, list):
                node[key] = [remap.get(item, item) if isinstance(item, str) else item for item in value]
            else:
                _remap_evidence_ids(value, remap, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _remap_evidence_ids(item, remap, depth + 1)


def _merge_evidence(survivor, absorbed):
    """M1: union on (source_url, claim); newer observed_at wins a tie. Returns remap."""
    merged = []
    by_key = {}
    used_ids = set()
    absorbed_origin = []
    for item in _evidence_items(survivor):
        copy = dict(item)
        merged.append(copy)
        by_key[(copy.get("source_url"), copy.get("claim"))] = copy
        if isinstance(copy.get("evidence_id"), str):
            used_ids.add(copy["evidence_id"])

    remap = {}
    for item in _evidence_items(absorbed):
        key = (item.get("source_url"), item.get("claim"))
        old_id = item.get("evidence_id") if isinstance(item.get("evidence_id"), str) else None
        existing = by_key.get(key)
        if existing is not None:
            # Same claim from the same URL: keep the newer observation, keep the
            # survivor's evidence_id so existing references stay resolvable.
            newer = str(item.get("observed_at") or "") > str(existing.get("observed_at") or "")
            if newer:
                kept_id = existing.get("evidence_id")
                existing.clear()
                existing.update(item)
                if kept_id is not None:
                    existing["evidence_id"] = kept_id
            if old_id and existing.get("evidence_id") and old_id != existing["evidence_id"]:
                remap[old_id] = existing["evidence_id"]
            if newer:
                absorbed_origin.append(existing)
            continue
        copy = dict(item)
        if old_id and old_id in used_ids:
            new_id = _next_evidence_id(used_ids)
            copy["evidence_id"] = new_id
            remap[old_id] = new_id
        if isinstance(copy.get("evidence_id"), str):
            used_ids.add(copy["evidence_id"])
        merged.append(copy)
        absorbed_origin.append(copy)
        by_key[key] = copy
    return merged, remap, absorbed_origin


# --------------------------------------------------------------------------
# Field merge (M2 - M7)
# --------------------------------------------------------------------------


def _make_conflict(field, winning_value, losing_value, win_ids, lose_ids, resolution, note):
    return {
        "field": field,
        "winning_value": winning_value,
        "losing_value": losing_value,
        "winning_evidence_ids": win_ids,
        "losing_evidence_ids": lose_ids,
        "resolution": resolution,
        "note": note[:500],
    }


def _resolution_label(win_item, lose_item):
    """Name the BUILD-CONTRACT 4.8 tie-break that actually decided the field."""
    if win_item is None:
        return "manual"
    if lose_item is None:
        return "higher_confidence"
    if win_item.get("is_official") is True and lose_item.get("is_official") is not True:
        return "official_source"
    if _item_confidence(win_item) != _item_confidence(lose_item):
        return "higher_confidence"
    if str(win_item.get("source_date") or "") != str(lose_item.get("source_date") or ""):
        return "more_recent"
    return "manual"


def _merge_scalar_field(field, survivor, absorbed, out, conflicts):
    """M2 / M3 / M4 for one non-structural, non-union field."""
    left = survivor.get(field, _common.ABSENT)
    right = absorbed.get(field, _common.ABSENT)
    left_known = _known(left)
    right_known = _known(right)

    if not right_known:
        return  # M3: a known value is never overwritten by unknown/absent
    if not left_known:
        out[field] = right
        return
    if json.dumps(left, sort_keys=True, ensure_ascii=False, default=str) == json.dumps(
        right, sort_keys=True, ensure_ascii=False, default=str
    ):
        return

    left_items = _evidence_items(survivor, field)
    right_items = _evidence_items(absorbed, field)
    left_best = _best_backing(left_items)
    right_best = _best_backing(right_items)

    if right_best is not None and left_best is None:
        winner, loser = "absorbed", "survivor"
    elif left_best is not None and right_best is None:
        winner, loser = "survivor", "absorbed"
    elif left_best is None and right_best is None:
        winner, loser = "survivor", "absorbed"
    else:
        left_key = (-_item_confidence(left_best), _item_tier(left_best))
        right_key = (-_item_confidence(right_best), _item_tier(right_best))
        winner = "survivor" if left_key <= right_key else "absorbed"
        loser = "absorbed" if winner == "survivor" else "survivor"

    if winner == "survivor":
        win_value, lose_value = left, right
        win_item, lose_item = left_best, right_best
        win_ids, lose_ids = _backing_ids(left_items), _backing_ids(right_items)
    else:
        out[field] = right
        win_value, lose_value = right, left
        win_item, lose_item = right_best, left_best
        win_ids, lose_ids = _backing_ids(right_items), _backing_ids(left_items)

    note = "merge: %s value kept; losing value preserved here (BUILD-CONTRACT 8.4 M2/M4)" % (
        "higher-confidence evidenced" if win_item is not None else "surviving record's"
    )
    conflicts.append(
        _make_conflict(field, win_value, lose_value, win_ids, lose_ids, _resolution_label(win_item, lose_item), note)
    )


def _merge_union_field(field, survivor, absorbed, out, conflicts):
    """M5 union, with the verified-empty-loses-to-non-empty case recorded."""
    left = survivor.get(field, _common.ABSENT)
    right = absorbed.get(field, _common.ABSENT)
    left_present = isinstance(left, list)
    right_present = isinstance(right, list)
    if not left_present and not right_present:
        return
    if not right_present:
        return
    if not left_present:
        out[field] = list(right)
        return
    merged = _union_list(field, left, right)
    out[field] = merged
    if left == [] and right:
        conflicts.append(
            _make_conflict(
                field,
                merged,
                [],
                _backing_ids(_evidence_items(absorbed, field)),
                _backing_ids(_evidence_items(survivor, field)),
                "more_recent",
                "merge: a verified-empty list lost to a non-empty one (BUILD-CONTRACT 8.4 M5)",
            )
        )
    elif right == [] and left:
        conflicts.append(
            _make_conflict(
                field,
                merged,
                [],
                _backing_ids(_evidence_items(survivor, field)),
                _backing_ids(_evidence_items(absorbed, field)),
                "more_recent",
                "merge: a verified-empty list lost to a non-empty one (BUILD-CONTRACT 8.4 M5)",
            )
        )


def _cross_link_conflicts(evidence, conflicts):
    """Add reciprocal conflicts_with links for every recorded known-vs-known conflict."""
    by_id = {}
    for item in evidence:
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str):
            by_id[item["evidence_id"]] = item
    for conflict in conflicts:
        winners = conflict.get("winning_evidence_ids") or []
        losers = conflict.get("losing_evidence_ids") or []
        if not winners or not losers:
            continue
        for own_ids, other_ids in ((winners, losers), (losers, winners)):
            for own in own_ids:
                item = by_id.get(own)
                if item is None:
                    continue
                links = [link for link in (item.get("conflicts_with") or []) if isinstance(link, str)]
                for other in other_ids:
                    if other != own and other not in links:
                        links.append(other)
                if links:
                    item["conflicts_with"] = links[:_MAX_EVIDENCE_IDS]


def _latest_evidence_date(record, claim):
    best = ""
    for item in _evidence_items(record, claim):
        marker = str(item.get("observed_at") or item.get("source_date") or "")
        if marker > best:
            best = marker
    return best


def _website_depth(value):
    """Path depth of an http(s) website URL; None when it is not usable as one."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.lower().startswith(("http://", "https://")):
        return None
    try:
        # BUILD-CONTRACT R7.11.1 forbids urllib anywhere in scripts/: the local
        # splitter in _common is the only URL parser this package may use.
        path = _common.split_url(text)[2] or "/"
    except Exception:
        return None
    segments = [segment for segment in path.split("/") if segment]
    return len(segments)


def _prefer_shallowest_website(survivor, absorbed, out):
    """M5b: keep the least deep `website` in a merge group (ties keep the survivor)."""
    current = out.get("website")
    current_depth = _website_depth(current)
    best, best_depth = current, current_depth
    for candidate in (survivor.get("website"), absorbed.get("website")):
        depth = _website_depth(candidate)
        if depth is None:
            continue
        if best_depth is None or depth < best_depth:
            best, best_depth = candidate, depth
    if best is not None and best != current:
        out["website"] = best


def _fold(survivor, absorbed, survivor_id, absorbed_id, entity, as_of):
    """Fold `absorbed` into `survivor`, returning (merged record, conflicts)."""
    absorbed = json.loads(json.dumps(absorbed, ensure_ascii=False))  # deep copy, no mutation
    out = dict(survivor)
    conflicts = []

    # M1: evidence union first, so every later reference can be re-keyed.
    merged_evidence, remap, absorbed_origin = _merge_evidence(survivor, absorbed)
    _remap_evidence_ids(absorbed, remap)
    for item in absorbed_origin:
        # Only items of absorbed origin are re-keyed: a survivor id is never rewritten.
        _remap_evidence_ids(item, remap)
    for item in absorbed.get("evidence") or []:
        # The absorbed copy keeps its items so per-field lookups below resolve to
        # the ids the merged document actually carries.
        if isinstance(item, dict) and item.get("evidence_id") in remap:
            item["evidence_id"] = remap[item["evidence_id"]]
    out["evidence"] = merged_evidence

    # M2 / M3 / M4 and M5.
    fields = list(survivor.keys()) + [key for key in absorbed.keys() if key not in survivor]
    for field in fields:
        if field in _STRUCTURAL_FIELDS:
            continue
        if field in _UNION_LIST_FIELDS:
            _merge_union_field(field, survivor, absorbed, out, conflicts)
        else:
            _merge_scalar_field(field, survivor, absorbed, out, conflicts)

    # M5b: `website` is rendered verbatim as the candidate's "Website:" line
    # (references/output-format.md 10.5), so a merge group must not publish a deep
    # product/collection URL as a company website just because that record happened
    # to be the survivor. Prefer the shallowest path in the group; ties keep the
    # survivor's value, so the choice stays deterministic.
    _prefer_shallowest_website(survivor, absorbed, out)

    # BUILD-CONTRACT 4.8 rule 1: the two disagreeing evidence items are cross-linked.
    _cross_link_conflicts(out.get("evidence") or [], conflicts)

    # M6: provenance.
    merged_from = list(out.get("merged_from") or [])
    for candidate in [absorbed_id] + list(absorbed.get("merged_from") or []):
        if isinstance(candidate, str) and candidate and candidate != survivor_id and candidate not in merged_from:
            merged_from.append(candidate)
    out["merged_from"] = sorted(merged_from)

    aliases = list(out.get("alias_domains") or []) + list(absorbed.get("alias_domains") or [])
    for domain in (survivor.get("canonical_domain"), absorbed.get("canonical_domain")):
        if isinstance(domain, str) and _known(domain):
            aliases.append(domain)
    canonical = out.get("canonical_domain")
    aliases = sorted({alias for alias in aliases if isinstance(alias, str) and alias and alias != canonical})
    if aliases:
        out["alias_domains"] = aliases[:_MAX_ALIAS_DOMAINS]

    # M7: flags.
    stale_values = [record.get("stale") for record in (survivor, absorbed)]
    if all(value is True for value in stale_values):
        out["stale"] = True
    elif any("stale" in record for record in (survivor, absorbed)):
        out["stale"] = False

    left_status = survivor.get("operational_status")
    right_status = absorbed.get("operational_status")
    if _known(right_status) and not _known(left_status):
        out["operational_status"] = right_status
    elif _known(right_status) and _known(left_status) and right_status != left_status:
        if _latest_evidence_date(absorbed, "operational_status") > _latest_evidence_date(
            survivor, "operational_status"
        ):
            out["operational_status"] = right_status
            win_value, lose_value = right_status, left_status
        else:
            win_value, lose_value = left_status, right_status
        conflicts.append(
            _make_conflict(
                "operational_status",
                win_value,
                lose_value,
                _backing_ids(_evidence_items(absorbed if win_value == right_status else survivor, "operational_status")),
                _backing_ids(_evidence_items(survivor if win_value == right_status else absorbed, "operational_status")),
                "more_recent",
                "merge: the most recently evidenced operational_status was kept (BUILD-CONTRACT 8.4 M7)",
            )
        )

    # Conflicts already recorded on either record survive alongside the new ones.
    existing = list(survivor.get("conflicts") or []) + list(absorbed.get("conflicts") or [])
    all_conflicts = []
    for entry in existing + conflicts:
        marker = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
        if marker not in [json.dumps(e, sort_keys=True, ensure_ascii=False, default=str) for e in all_conflicts]:
            all_conflicts.append(entry)
    if all_conflicts:
        out["conflicts"] = all_conflicts[:_MAX_CONFLICTS]

    # Notes union plus the merge trail.
    notes = [note for note in (survivor.get("notes") or []) if isinstance(note, str)]
    for note in absorbed.get("notes") or []:
        # Prefix the absorbed record's notes with its id: a note such as "no website
        # URL on the record" would otherwise contradict the merged record.
        if isinstance(note, str) and note:
            _add_note(notes, "%s: %s" % (absorbed_id, note))
    _add_note(notes, "merged with %s on %s (BUILD-CONTRACT 8.4)" % (absorbed_id, as_of))

    # M7: a merge invalidates every score; the record must be re-scored.
    if survivor.get("score_version") != "unscored" or absorbed.get("score_version") != "unscored":
        _add_note(notes, "scores invalidated by merge; re-score required (BUILD-CONTRACT 8.4 M7)")
    out["score_version"] = "unscored"
    out["qualification_score"] = 0
    for field in _SCORE_FIELDS:
        out.pop(field, None)
    if "confidence" in survivor or "confidence" in absorbed:
        out["confidence"] = 0
    if out.get("status") in _SCORED_STATES:
        out["status"] = "VERIFIED"
        _add_note(notes, "status regressed to VERIFIED: a merged record is unscored (INV-37)")
    if "as_of" in survivor or "as_of" in absorbed:
        out["as_of"] = as_of
    if "schema_version" not in out and "schema_version" in absorbed:
        out["schema_version"] = absorbed["schema_version"]
    if "source_query" not in out and "source_query" in absorbed:
        out["source_query"] = absorbed["source_query"]
    if isinstance(absorbed.get("extensions"), dict):
        extensions = dict(absorbed["extensions"])
        extensions.update(out.get("extensions") or {})
        out["extensions"] = extensions

    out["notes"] = notes[:_MAX_NOTES]
    return out, conflicts


# --------------------------------------------------------------------------
# Component construction (BUILD-CONTRACT 8.4 key precedence)
# --------------------------------------------------------------------------


class _UnionFind(object):
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, index):
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if ra < rb:
            self.parent[rb] = ra
        else:
            self.parent[ra] = rb
        return True

    def members(self, size):
        groups = {}
        for index in range(size):
            groups.setdefault(self.find(index), []).append(index)
        return groups


def _known_domain(record):
    domain = record.get("canonical_domain")
    return domain if isinstance(domain, str) and _known(domain) else None


def _known_country(record):
    country = record.get("country")
    return country if isinstance(country, str) and _known(country) else None


def _component_domains(records, indices):
    return {d for d in (_known_domain(records[i]) for i in indices) if d}


def _component_countries(records, indices):
    return {c for c in (_known_country(records[i]) for i in indices) if c}


def _record_subdomain(record):
    """The labels in front of the record's registrable domain, or None if unknown.

    `website` is authoritative; an alias domain is consulted only when it resolves to
    the same registrable domain, so an unrelated alias can never invent a subdomain.
    """
    domain = _known_domain(record)
    if not domain:
        return None
    candidates = [record.get("website")]
    candidates.extend(record.get("alias_domains") or [])
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        if _common.canonical_domain(candidate) != domain:
            continue
        subdomain = _common.domain_subdomain(candidate)
        if subdomain is not None:
            return subdomain
    return None


def _names_disagree(left, right):
    """True when both normalized names are known and neither contains the other."""
    left_name = _common.name_match_key(left.get("normalized_name"))
    right_name = _common.name_match_key(right.get("normalized_name"))
    if not left_name or not right_name:
        return False
    if left_name == right_name:
        return False
    return left_name not in right_name and right_name not in left_name


def shared_hosting_block(left, right):
    """R8.4.2 in the direction the embedded suffix list cannot guarantee.

    canonical_domain is derived from a hand-maintained suffix list (_common.py
    MULTI_PART_SUFFIXES), never the full Public Suffix List, so a hosting platform
    that is not on that list hands every tenant the SAME registrable domain and key 1
    would fold unrelated companies into one record. A false merge destroys two
    candidates and publishes a company that does not exist; a missed merge only leaves
    a duplicate in the list, so this refuses the merge and reports the pair instead.

    The refusal is deliberately narrow, so ordinary "same company, two pages" merges
    are untouched: it fires only on the shape that shared hosting actually has - BOTH
    records under a DIFFERENT non-empty subdomain of the same registrable domain, with
    two company names that are not variants of each other. A root domain on either
    side (`acme.example` merging with `shop.acme.example`), an undeterminable
    subdomain, or a name that contains the other all merge as before.
    """
    left_subdomain = _record_subdomain(left)
    right_subdomain = _record_subdomain(right)
    if not left_subdomain or not right_subdomain:
        return False
    if left_subdomain == right_subdomain:
        return False
    return _names_disagree(left, right)


def suggest_merges(records):
    """R8.4.3: same normalized_name + known equal country, different known domains."""
    pairs = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            left, right = records[i], records[j]
            name = _common.name_match_key(left.get("normalized_name"))
            if not name or name != _common.name_match_key(right.get("normalized_name")):
                continue
            left_country, right_country = _known_country(left), _known_country(right)
            if not left_country or left_country != right_country:
                continue
            left_domain, right_domain = _known_domain(left), _known_domain(right)
            if not left_domain or not right_domain or left_domain == right_domain:
                continue
            pairs.append((i, j))
    return pairs


def build_components(records, ids, strict_country):
    """Return (groups, keys, blocked) per BUILD-CONTRACT 8.4.

    Candidate name+country pairs are walked in record-id order, never in input
    order, so an ambiguous candidate (no domain of its own, two possible homes)
    always lands in the same component (7.7 order independence, INV-15).

    `blocked` holds the (i, j) index pairs that share a registrable domain but were
    refused by shared_hosting_block; the caller reports them as suggested merges.
    """
    size = len(records)
    finder = _UnionFind(size)
    blocked = []

    # Key 1: canonical_domain.
    by_domain = {}
    for index, record in enumerate(records):
        domain = _known_domain(record)
        if domain:
            by_domain.setdefault(domain, []).append(index)
    for domain in sorted(by_domain):
        indices = sorted(by_domain[domain], key=lambda i: ids[i])
        for position, left in enumerate(indices):
            for right in indices[position + 1:]:
                if shared_hosting_block(records[left], records[right]):
                    blocked.append((left, right))
                    continue
                finder.union(left, right)

    # Key 2: normalized_name + country, only when a domain is missing on one side.
    candidate_pairs = [(i, j) for i in range(size) for j in range(i + 1, size)]
    candidate_pairs.sort(key=lambda pair: (ids[pair[0]], ids[pair[1]]))
    for i, j in candidate_pairs:
        left, right = records[i], records[j]
        # name_match_key: a Hangul name compares with its spaces removed (scoring-10).
        name = _common.name_match_key(left.get("normalized_name"))
        if not name or name != _common.name_match_key(right.get("normalized_name")):
            continue
        left_domain, right_domain = _known_domain(left), _known_domain(right)
        left_country, right_country = _known_country(left), _known_country(right)
        if left_domain and right_domain:
            continue  # same domain: already handled by key 1
        if left_country and right_country and left_country != right_country:
            continue  # R8.4.1: never merge two known, different countries
        if strict_country and not (left_country and right_country):
            continue  # strict mode also blocks an unknown country
        root_i, root_j = finder.find(i), finder.find(j)
        if root_i == root_j:
            continue
        members = [k for k in range(size) if finder.find(k) in (root_i, root_j)]
        if len(_component_domains(records, members)) > 1:
            continue  # R8.4.2: a merge may never fold two known domains together
        if len(_component_countries(records, members)) > 1:
            continue  # R8.4.1 under transitivity
        finder.union(i, j)

    groups = finder.members(size)
    resolved_keys = {}
    for root, indices in groups.items():
        if len(indices) < 2:
            continue
        with_domain = [i for i in indices if _known_domain(records[i])]
        if len(with_domain) > 1 and len(_component_domains(records, indices)) == 1:
            resolved_keys[root] = "canonical_domain"
        else:
            resolved_keys[root] = "normalized_name+country"
    return groups, resolved_keys, blocked


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dedupe_companies.py",
        description="Merge duplicate buyer or seller records by canonical_domain, then "
        "normalized_name + country (BUILD-CONTRACT 7.7, 8.4).",
    )
    parser.add_argument("-i", "--input", default=None, help='input JSON path ("-" = stdin)')
    parser.add_argument("-o", "--output", default=None, help="output JSON path (default stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    parser.add_argument("--as-of", dest="as_of", default=None, help="run date, YYYY-MM-DD")
    parser.add_argument("--config", default=None, help="alternative scoring.config.json")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None, help="alternative schemas/")
    parser.add_argument(
        "--no-validate", dest="validate", action="store_false", help="skip output self-validation"
    )
    parser.add_argument("--quiet", action="store_true", help="suppress informational stderr output")
    parser.add_argument("--version", action="store_true", help="print versions and exit")
    parser.add_argument("--entity", choices=("buyer", "seller", "auto"), default="auto", help="record kind")
    parser.add_argument(
        "--strict-country",
        dest="strict_country",
        action="store_true",
        help="block a name-based merge when either country is unknown (default)",
    )
    parser.add_argument(
        "--no-strict-country",
        dest="strict_country",
        action="store_false",
        help="allow a name-based merge when one country is unknown (single-country runs only)",
    )
    parser.add_argument(
        "--report-only", dest="report_only", action="store_true", help="emit the merge plan only"
    )
    parser.set_defaults(validate=True, strict_country=True)
    return parser


def _print_version(args):
    config = _common.load_config(args.config)
    sys.stdout.write(
        "dedupe_companies.py skill_version=%s schema_version=%s score_version=%s\n"
        % (_common.SKILL_VERSION, _common.SCHEMA_VERSION, config.get("score_version", "unknown"))
    )
    return 0


def _emit(payload, args):
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            _common.write_output(payload, args.pretty, handle)
    else:
        _common.write_output(payload, args.pretty)


def _self_validate(records, entity, args):
    """Warn (never fail) when a complete merged record does not satisfy its schema."""
    if not args.validate:
        return
    try:
        schema = _common.load_schema(entity, args.schema_dir)
    except _common.UsageError as exc:
        _common.eprint("WARN: %s" % exc, quiet=args.quiet)
        return
    for index, record in enumerate(records):
        if "schema_version" not in record:
            continue
        for message in _common.validate(record, schema):
            _common.eprint(
                "WARN: record %d (%s): %s" % (index, _record_id(record, index), message),
                quiet=args.quiet,
            )


def dedupe(records, entity, as_of, config, strict_country):
    """Run the merge. Returns (records, merges, merges_suggested)."""
    ids = [_record_id(record, index) for index, record in enumerate(records)]
    groups, keys, blocked = build_components(records, ids, strict_country)
    claims = _material_claims(config, entity)

    # Contradictory merge: one domain, two known countries, strict mode (exit 1).
    for root, indices in groups.items():
        if len(indices) < 2:
            continue
        countries = _component_countries(records, indices)
        if len(countries) > 1 and keys.get(root) == "canonical_domain":
            if strict_country:
                raise _common.DataError(
                    "contradictory merge: canonical_domain %s carries countries %s; "
                    "re-run with --no-strict-country to merge anyway"
                    % (sorted(_component_domains(records, indices))[0], ", ".join(sorted(countries)))
                )

    merged_records = []
    merges = []
    owner_id = {}  # input index -> id of the merged record that now carries it
    for root in sorted(groups, key=lambda r: min(ids[i] for i in groups[r])):
        indices = sorted(groups[root], key=lambda i: ids[i])
        if len(indices) == 1:
            record = records[indices[0]]
            owner_id[indices[0]] = ids[indices[0]]
            merged_records.append(record)
            continue
        ordered = sorted(
            indices,
            key=lambda i: (-_evidenced_claim_count(records[i], claims), -_tier1_count(records[i]), ids[i]),
        )
        survivor_index = ordered[0]
        survivor = records[survivor_index]
        survivor_id = ids[survivor_index]
        for index in indices:
            owner_id[index] = survivor_id
        merge_conflicts = []
        absorbed_ids = []
        for index in sorted(indices, key=lambda i: ids[i]):
            if index == survivor_index:
                continue
            survivor, new_conflicts = _fold(survivor, records[index], survivor_id, ids[index], entity, as_of)
            merge_conflicts.extend(new_conflicts)
            absorbed_ids.append(ids[index])
        merged_records.append(survivor)
        merges.append(
            {
                "survivor_id": survivor_id,
                "absorbed_ids": absorbed_ids,
                "key": keys.get(root, "canonical_domain"),
                "conflicts": merge_conflicts[:_MAX_CONFLICTS],
            }
        )

    # R8.4.3: same name + country, different known domains -> suggest, never merge.
    merges_suggested = []
    survivor_ids = [_record_id(record, index) for index, record in enumerate(merged_records)]
    by_id = dict(zip(survivor_ids, merged_records))
    for i, j in suggest_merges(merged_records):
        left_id, right_id = survivor_ids[i], survivor_ids[j]
        for own, other in ((i, j), (j, i)):
            record = merged_records[own]
            notes = [note for note in (record.get("notes") or []) if isinstance(note, str)]
            _add_note(
                notes,
                "possible duplicate of %s (same normalized name + country, different domain) "
                "— operator confirmation required" % survivor_ids[other],
            )
            record["notes"] = notes[:_MAX_NOTES]
        merges_suggested.append(
            {
                "record_ids": sorted([left_id, right_id]),
                "key": "normalized_name+country",
                "reason": "same normalized_name and country, different canonical_domain; "
                "operator confirmation required (BUILD-CONTRACT 8.4 R8.4.3)",
            }
        )

    # Shared-hosting refusal: same registrable domain, two different tenant
    # subdomains, two different company names -> report, never merge.
    reported = {tuple(entry["record_ids"]) for entry in merges_suggested}
    for i, j in blocked:
        left_id, right_id = owner_id.get(i, ids[i]), owner_id.get(j, ids[j])
        if left_id == right_id:
            continue  # a name-based merge legitimately reunited them later
        pair = tuple(sorted([left_id, right_id]))
        if pair in reported:
            continue
        reported.add(pair)
        for own, other in ((left_id, right_id), (right_id, left_id)):
            record = by_id.get(own)
            if record is None:
                continue
            notes = [note for note in (record.get("notes") or []) if isinstance(note, str)]
            _add_note(
                notes,
                "possible duplicate of %s (same registrable domain %s, different subdomain, "
                "different company name — the domain may be shared hosting) — NOT merged; "
                "operator confirmation required"
                % (other, _known_domain(record) or _common.UNKNOWN),
            )
            record["notes"] = notes[:_MAX_NOTES]
        merges_suggested.append(
            {
                "record_ids": list(pair),
                "key": "canonical_domain",
                "reason": "same canonical_domain but a different subdomain and a different "
                "company name on each side; the registrable domain may be a hosting platform "
                "the embedded suffix list does not know, and a wrong merge publishes a company "
                "that does not exist (BUILD-CONTRACT 8.4 R8.4.2, INV-16)",
            }
        )
    merges_suggested = sorted(merges_suggested, key=lambda entry: entry["record_ids"])

    merged_records = _common.stable_sort(
        merged_records, [("canonical_domain", "asc"), ("company_name", "asc")]
    )
    return merged_records, merges, merges_suggested


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.version:
            return _print_version(args)

        config = _common.load_config(args.config)
        data = _common.read_input(args)
        envelope = {}
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            envelope = data
            raw_records = data["records"]
        elif isinstance(data, list):
            raw_records = data
        elif isinstance(data, dict):
            raw_records = [data]
        else:
            raise _common.DataError("input must be a record object, an array or an envelope")

        records = []
        kinds = []
        for record in raw_records:
            normalized, kind = normalize_company.normalize_record(record, args.entity)
            records.append(normalized)
            kinds.append(kind)

        if args.entity in ("buyer", "seller"):
            entity = args.entity
        elif envelope.get("entity") in ("buyer", "seller"):
            entity = envelope["entity"]
        else:
            entity = kinds[0] if kinds else "buyer"

        # BUILD-CONTRACT 7.4: --as-of -> envelope as_of -> max observed_at -> config.
        as_of = _common.resolve_as_of(records, args.as_of or envelope.get("as_of"), config)

        merged, merges, merges_suggested = dedupe(
            records, entity, as_of, config, args.strict_country
        )

        payload = {
            "schema_version": _common.SCHEMA_VERSION,
            "as_of": as_of,
            "entity": entity,
            "records": [] if args.report_only else merged,
            "merges": merges,
            "merges_suggested": merges_suggested,
            "stats": {
                "input_count": len(records),
                "output_count": len(merged),
                "merge_count": len(merges),
            },
        }
        if not args.report_only:
            _self_validate(merged, entity, args)
        _emit(payload, args)
        _common.eprint(
            "deduped %d record(s) into %d (%d merge group(s))" % (len(records), len(merged), len(merges)),
            quiet=args.quiet,
        )
        return 0
    except _common.UsageError as exc:
        return _common.die(str(exc), 2)
    except _common.ConfigError as exc:
        return _common.die(str(exc), 2)
    except _common.DataError as exc:
        return _common.die(str(exc), 1)
    except (IOError, OSError) as exc:
        return _common.die(str(exc), 2)
    except Exception as exc:  # never let a raw traceback escape main (BUILD-CONTRACT 7.5)
        return _common.die("%s: %s" % (type(exc).__name__, exc), 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
