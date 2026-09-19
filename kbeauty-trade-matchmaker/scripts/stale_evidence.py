#!/usr/bin/env python3
"""stale_evidence.py - list the evidence that should be re-read, most urgent first.

A standalone audit tool. It reads buyer, seller or RFQ records that are already on disk
(a discovery-result, a golden bundle, dedupe output, a match input, a list of records or
one record) and emits a `recheck-queue` document: which records to revisit, which
material claims no longer rest on current evidence, and which evidence items to re-open.

What it does not do:

  * It fetches nothing. Re-reading a source_url is the agent's job under
    references/evidence-policy.md section 5, after which the pipeline is re-run.
  * It changes no record and no score. No scorer reads this script, its constants or a
    recheck-queue, so nothing here can move score_version (INV-NEW-stale).

Korean gloss: 오래되었거나 상충하는 근거를 찾아 다시 확인할 순서대로 나열한다. 아무것도 가져오거나 고치지 않는다.

Age comes only from --as-of, which is required: a re-check queue is only meaningful
against the re-check date, and falling back to the newest observed_at (BUILD-CONTRACT
7.4 steps 2-4) would make every item look as fresh as the last reading. Build rules of
BUILD-CONTRACT.md 1.5 and 7.11 apply: stdlib only, Python 3.9 syntax, no network, no
wall-clock read, nothing at import time. Comments are English.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "stale_evidence.py"
REPORT_KIND = "recheck-queue"

#: An item is due for re-reading once its recency bucket (scoring.config.json
#: evidence.recency_buckets) is this label or a later one. It names a bucket instead of
#: restating a number, so the queue and the score always agree on what "old" means. No
#: scorer reads this constant.
DUE_FROM_BUCKET = "aging"

#: Record-level and item-level reason codes, most urgent first. The order is the queue's
#: priority: records the scorer already filters or penalises, then proven-old evidence,
#: then evidence the source itself marked stale, then unresolved conflicts, then
#: evidence that is merely due, and last an undated item whose last reading is due.
REASON_ORDER = (
    "site_unreachable",
    "record_flagged_stale",
    "past_stale_threshold",
    "source_flagged_stale",
    "unresolved_conflict",
    "aging",
    "undated",
)

#: The claim-level finding: a material claim that is covered but has no current item.
CLAIM_REASON = "no_current_evidence"

ENTITY_ORDER = ("buyer", "seller", "rfq")
ID_KEYS = {"buyer": "buyer_id", "seller": "seller_id", "rfq": "rfq_id"}

#: Longest locator the queue copies, per field: the input schemas' own maxLength
#: (buyer/seller/rfq ids, evidence.schema.json evidence_id, claim, source_url and
#: conflicts_with items). A longer value is refused rather than cut, because a cut id or
#: URL would point at something else.
LOCATOR_LIMITS = {
    "record_id": 128,
    "evidence_id": 128,
    "claim": 200,
    "source_url": 2048,
    "conflicts_with": 128,
}

#: Display-only labels copied onto a queue entry, with their schema maxLength. A longer
#: label is left out of the entry; the record id still names the record.
LABEL_LIMITS = (("company_name", 300), ("canonical_domain", 253))

#: How many offending evidence items a collapsed note names, and how many characters of
#: any echoed input value it quotes. notes[] is bounded (recheck-queue.schema.json: 200
#: notes of 1000 characters), so bad data is summarised once per kind instead of once per
#: item, and with these two numbers the longest note stays under 1000 characters.
NOTE_EXAMPLES = 10
ECHO_CHARS = 40

FINAL_NOTE = (
    "This queue lists what to re-read; it changed no record and no score. Re-reading is "
    "done by the agent under references/evidence-policy.md section 5, then the pipeline "
    "is re-run."
)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "List the evidence that should be re-read, most urgent first. It fetches "
            "nothing and changes no record or score. Korean gloss: 다시 확인할 근거를 "
            "우선순위대로 나열한다."
        ),
    )
    parser.add_argument("-i", "--input", default=None, help="Records JSON (default: stdin)")
    parser.add_argument("-o", "--output", default=None, help="Queue path (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent=2 output")
    parser.add_argument(
        "--as-of", dest="as_of", default=None,
        help="YYYY-MM-DD re-check date (required; the clock is never read)",
    )
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--no-validate", dest="validate", action="store_false", default=True)
    parser.add_argument("--quiet", action="store_true", help="Suppress the stderr summary")
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--top", default=None, metavar="N",
        help="List only the first N queued records; the summary still counts all of them",
    )
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
        return _run(args, config)
    except _common.UsageError as exc:
        return _common.die(str(exc), 2)
    except _common.ConfigError as exc:
        return _common.die(str(exc), 2)
    except _common.DataError as exc:
        return _common.die(str(exc), 1)
    except Exception as exc:
        return _common.die("%s: %s" % (type(exc).__name__, exc), 1)


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


def _policy(config):
    """Recency buckets, stale threshold and material claims from the evidence block."""
    evidence = config.get("evidence") if isinstance(config, dict) else None
    if not isinstance(evidence, dict):
        raise _common.ConfigError("scoring config carries no 'evidence' block")
    buckets = []
    for bucket in evidence.get("recency_buckets") or []:
        if not isinstance(bucket, dict) or not isinstance(bucket.get("label"), str):
            raise _common.ConfigError("evidence.recency_buckets holds a bucket without a label")
        limit = bucket.get("max_age_days")
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                raise _common.ConfigError(
                    "evidence.recency_buckets[%s].max_age_days is not a number" % bucket["label"]
                )
        buckets.append({"label": bucket["label"], "max_age_days": limit})
    labels = [b["label"] for b in buckets]
    if DUE_FROM_BUCKET not in labels:
        raise _common.ConfigError(
            "evidence.recency_buckets has no %r bucket; %s cannot tell which evidence is due "
            "(labels: %s)" % (DUE_FROM_BUCKET, SCRIPT_NAME, ", ".join(labels) or "none")
        )
    if "unknown" in labels:
        raise _common.ConfigError("evidence.recency_buckets may not use the label 'unknown'")
    threshold = evidence.get("stale_threshold_days")
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        raise _common.ConfigError("evidence.stale_threshold_days must be an integer")
    material = evidence.get("material_claims")
    if not isinstance(material, dict):
        raise _common.ConfigError("evidence.material_claims is missing")
    return {
        "buckets": buckets,
        "labels": labels,
        "due_index": labels.index(DUE_FROM_BUCKET),
        "threshold": threshold,
        "material": dict(
            (entity, [c for c in (material.get(entity) or []) if isinstance(c, str)])
            for entity in ENTITY_ORDER
        ),
    }


def _bucket_index(age, policy):
    for index, bucket in enumerate(policy["buckets"]):
        if bucket["max_age_days"] is None or age <= bucket["max_age_days"]:
            return index
    return len(policy["buckets"]) - 1


def _age(value, as_of):
    """Whole days from a date or date-time to as_of, clamped at 0; None when unknown."""
    if not isinstance(value, str) or _common.is_unknown(value):
        return None
    try:
        return max(0, _common.days_between(value, as_of))
    except _common.DataError:
        return None


def _clip(text):
    """At most ECHO_CHARS characters of an input-derived string."""
    if len(text) > ECHO_CHARS:
        text = text[:ECHO_CHARS - 3] + "..."
    return text


def _echo(value):
    """A short, quoted rendering of an input value for a note or an error line."""
    return _clip(repr(value))


def _locator(value, field, where):
    """A copied locator string, or "unknown" when absent; refused when over its limit."""
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    if len(value) > LOCATOR_LIMITS[field]:
        raise _common.DataError(
            "%s: %s %s is %d characters, longer than the %d the input schema allows; fix "
            "the record" % (where, field, _echo(value), len(value), LOCATOR_LIMITS[field])
        )
    return value


def _conflict_ids(item, where):
    """The evidence ids an item names in conflicts_with. A lone string counts as one id."""
    raw = item.get("conflicts_with")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    ids = set()
    for value in raw:
        if isinstance(value, str) and value:
            ids.add(_locator(value, "conflicts_with", where))
    return sorted(ids)


def _date_or_unknown(value):
    if isinstance(value, str) and len(value) >= 10:
        head = value[:10]
        try:
            _common.days_between(head, head)
        except _common.DataError:
            return "unknown"
        return head
    return "unknown"


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------


def _infer_entity(record):
    if "rfq_id" in record and "product_category" in record:
        return "rfq"
    if "seller_id" in record:
        return "seller"
    if "buyer_id" in record:
        return "buyer"
    return None


def _collect(doc):
    """[(entity, record)], plus notes, from any accepted record document."""
    notes = []
    if isinstance(doc, dict) and "report_kind" in doc:
        raise _common.DataError(
            "%s is a report, not a record document" % (doc.get("report_kind"),)
        )
    if isinstance(doc, dict) and ("match_run_id" in doc or "no_match" in doc):
        raise _common.DataError(
            "a match-result cannot be aged: its evidence_index carries no source_date, and "
            "its candidates carry no operational_status and no conflicts[]; pass the match "
            "input (rfq + records) or the seller records instead"
        )
    pairs = []
    if isinstance(doc, dict) and isinstance(doc.get("records"), list):
        forced = doc.get("entity") if doc.get("entity") in ("buyer", "seller") else None
        for index, record in enumerate(doc["records"]):
            pairs.append((forced, record, index))
        if isinstance(doc.get("rfq"), dict):
            pairs.append(("rfq", doc["rfq"], len(doc["records"])))
        excluded = doc.get("excluded")
        if isinstance(excluded, list) and excluded:
            notes.append(
                "%d excluded candidate(s) carry no evidence in this document; audit the "
                "pre-score records to cover them." % len(excluded)
            )
    elif isinstance(doc, list):
        pairs = [(None, record, index) for index, record in enumerate(doc)]
    elif isinstance(doc, dict) and _infer_entity(doc) is not None:
        pairs = [(None, doc, 0)]
    else:
        raise _common.DataError(
            "input is not a record document (expected records[], a list of records, or one "
            "buyer, seller or RFQ record)"
        )

    out = []
    seen = {}
    for forced, record, index in pairs:
        if not isinstance(record, dict):
            raise _common.DataError("record at input index %d is not a JSON object" % index)
        entity = forced or _infer_entity(record)
        if entity is None:
            raise _common.DataError(
                "record at input index %d has no buyer_id/seller_id/rfq_id; cannot tell which "
                "material-claim set applies" % index
            )
        # Every queued record must be findable again, so a missing, blank or non-string id
        # is refused whether the entity came from the bundle or from the record itself.
        raw_id = record.get(ID_KEYS[entity])
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise _common.DataError(
                "%s record at input index %d has no usable %s (got %s); every queued record "
                "must name its id" % (entity, index, ID_KEYS[entity], _echo(raw_id))
            )
        record_id = _locator(raw_id, "record_id", "%s record at input index %d" % (entity, index))
        key = (entity, record_id)
        if key in seen:
            raise _common.DataError(
                "%s %s appears twice (input indexes %d and %d); a duplicate would be "
                "queued twice - dedupe first" % (entity, record_id, seen[key], index)
            )
        seen[key] = index
        out.append({"entity": entity, "record": record, "record_id": record_id, "index": index})
    return out, notes


def _score_version_notes(doc, entries, config):
    """No refusal: the queue aggregates no score. It only says what it compared against."""
    found = set()
    values = [doc.get("score_version")] if isinstance(doc, dict) else []
    values.extend(entry["record"].get("score_version") for entry in entries)
    for value in values:
        if isinstance(value, str) and value not in ("unscored", "unknown", ""):
            found.add(value)
    current = config.get("score_version")
    others = sorted(v for v in found if v != current)
    if not others:
        return []
    named = ", ".join(_echo(v) for v in others[:NOTE_EXAMPLES])
    if len(others) > NOTE_EXAMPLES:
        named += " and %d more" % (len(others) - NOTE_EXAMPLES)
    return [
        "The input carries score_version %s; ages, buckets and the stale threshold here come "
        "from the current config (%s). Re-score after re-reading." % (named, current)
    ]


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


def _audit_record(entry, as_of, policy, problems):
    """Findings for one record, or None when it has nothing to re-read.

    Input problems that do not stop the run are appended to `problems` (a dict of lists)
    and summarised once per kind by _problem_notes, so bad data cannot overflow notes[].
    """
    record = entry["record"]
    entity = entry["entity"]
    label = "%s %s" % (entity, entry["record_id"])
    material = policy["material"][entity]
    raw_items = record.get("evidence")
    if raw_items is not None and not isinstance(raw_items, list):
        problems["not_list"].append(label)
        raw_items = []
    items = [item for item in (raw_items or []) if isinstance(item, dict)]

    resolved = set()
    if entity != "rfq":
        conflicts = record.get("conflicts")
        for conflict in conflicts if isinstance(conflicts, list) else []:
            field = conflict.get("field") if isinstance(conflict, dict) else None
            if isinstance(field, str) and field:
                resolved.add(field)

    views = []
    for index, item in enumerate(items):
        view = _item_view(item, as_of, policy, "%s evidence[%d]" % (label, index))
        if isinstance(item.get("source_date"), str) and not _common.is_unknown(
            item.get("source_date")
        ) and view["source_age"] is None:
            problems["bad_date"].append((label, view["evidence_id"], item.get("source_date")))
        views.append(view)

    by_claim = {}
    for view in views:
        by_claim.setdefault(view["claim"], []).append(view)

    # Record-level reasons are for buyers and sellers only: an RFQ has no stale flag, no
    # operational_status and no conflicts[] (rfq.schema.json), and no scorer penalises it.
    record_reasons = []
    if entity != "rfq":
        if record.get("operational_status") == "unreachable":
            record_reasons.append("site_unreachable")
        if record.get("stale") is True:
            record_reasons.append("record_flagged_stale")

    claims = []
    for claim, claim_views in by_claim.items():
        has_current = any(v["current"] for v in claim_views)
        for view in claim_views:
            if view["flagged"]:
                view["reasons"].append("source_flagged_stale")
            if view["conflicts_with"] and claim not in resolved:
                view["reasons"].append("unresolved_conflict")
            if not has_current and not view["current"]:
                # An age reason only when nothing fresher backs the same claim: a
                # superseded old item is not worth a trip (it no longer drives the score).
                if view["source_age"] is not None:
                    if view["source_age"] > policy["threshold"]:
                        view["reasons"].append("past_stale_threshold")
                    elif view["bucket_index"] >= policy["due_index"]:
                        view["reasons"].append("aging")
                elif not view["flagged"]:
                    view["reasons"].append("undated")
        if claim in material and not has_current:
            claims.append(_claim_finding(claim, claim_views, policy["due_index"]))

    queued = []
    for view in views:
        if not view["reasons"]:
            continue
        view["reasons"] = sorted(set(view["reasons"]), key=REASON_ORDER.index)
        view["material"] = view["claim"] in material
        queued.append(view)

    if not record_reasons and not claims and not queued:
        return None, views

    offset = len(REASON_ORDER)
    for view in queued:
        rank = REASON_ORDER.index(view["reasons"][0]) + (0 if view["material"] else offset)
        view["rank"] = rank
    queued.sort(key=lambda v: (
        v["rank"],
        0 if v["source_age"] is not None else 1,
        -(v["source_age"] or 0),
        v["evidence_id"],
    ))
    claim_order = dict((c, i) for i, c in enumerate(material))
    claims.sort(key=lambda c: (claim_order.get(c["claim"], len(material)), c["claim"]))

    candidates = [(REASON_ORDER.index(r), r) for r in record_reasons]
    candidates.extend((v["rank"], v["reasons"][0]) for v in queued)
    priority_rank, priority_reason = min(candidates) if candidates else (2 * offset, "undated")
    ages = [v["source_age"] for v in queued if v["source_age"] is not None]

    out = {
        "position": 0,
        "entity": entity,
        "record_id": entry["record_id"],
        "input_index": entry["index"],
    }
    for key, limit in LABEL_LIMITS:
        value = entry["record"].get(key)
        if isinstance(value, str) and value.strip() and len(value) <= limit:
            out[key] = value
    out["priority_reason"] = priority_reason
    out["record_reasons"] = sorted(record_reasons, key=REASON_ORDER.index)
    out["claims"] = claims
    out["items"] = [_render_item(v) for v in queued]
    sort_key = (
        priority_rank,
        -len(claims),
        0 if ages else 1,
        -(max(ages) if ages else 0),
        ENTITY_ORDER.index(entity),
        entry["record_id"],
        entry["index"],
    )
    return (sort_key, out, queued), views


def _item_view(item, as_of, policy, where):
    source_age = _age(item.get("source_date"), as_of)
    read_age = _age(item.get("observed_at"), as_of)
    bucket_index = _bucket_index(source_age, policy) if source_age is not None else None
    flagged = item.get("stale") is True
    if source_age is not None:
        fresh = bucket_index < policy["due_index"]
    else:
        # An undated page is not a stale page (evidence.unknown_source_date_note): it is
        # current while its last reading is, and due once that reading is. A reading
        # with no usable date cannot prove it is current.
        fresh = read_age is not None and _bucket_index(read_age, policy) < policy["due_index"]
    return {
        "item": item,
        "evidence_id": _locator(item.get("evidence_id"), "evidence_id", where),
        "claim": _locator(item.get("claim"), "claim", where),
        "source_url": _locator(item.get("source_url"), "source_url", where),
        "conflicts_with": _conflict_ids(item, where),
        "source_age": source_age,
        "read_age": read_age,
        "bucket_index": bucket_index,
        "bucket": policy["labels"][bucket_index] if bucket_index is not None else "unknown",
        "flagged": flagged,
        "current": fresh and not flagged,
        "reasons": [],
    }


def _claim_finding(claim, claim_views, due_index):
    dated = sorted(
        _date_or_unknown(v["item"].get("source_date"))
        for v in claim_views
        if v["source_age"] is not None
    )
    dated = [d for d in dated if d != "unknown"]
    return {
        "claim": claim,
        "reason": CLAIM_REASON,
        "evidence_ids": sorted(set(v["evidence_id"] for v in claim_views)),
        "newest_source_date": dated[-1] if dated else "unknown",
        "counts": {
            "old": sum(1 for v in claim_views
                       if v["bucket_index"] is not None and v["bucket_index"] >= due_index),
            "undated": sum(1 for v in claim_views if v["source_age"] is None),
            "flagged": sum(1 for v in claim_views if v["flagged"]),
        },
    }


def _render_item(view):
    item = view["item"]
    out = {
        "evidence_id": view["evidence_id"],
        "claim": view["claim"],
        "material": view["material"],
        "source_url": view["source_url"],
    }
    tier = item.get("source_tier")
    if isinstance(tier, int) and not isinstance(tier, bool) and 1 <= tier <= 5:
        out["source_tier"] = tier
    out["source_date"] = (
        _date_or_unknown(item.get("source_date")) if view["source_age"] is not None else "unknown"
    )
    out["age_days"] = view["source_age"] if view["source_age"] is not None else "unknown"
    out["recency_bucket"] = view["bucket"]
    out["last_read"] = (
        _date_or_unknown(item.get("observed_at")) if view["read_age"] is not None else "unknown"
    )
    out["days_since_read"] = view["read_age"] if view["read_age"] is not None else "unknown"
    out["reasons"] = list(view["reasons"])
    if "unresolved_conflict" in view["reasons"]:
        out["conflicts_with"] = list(view["conflicts_with"])
    return out


def _problem_notes(problems):
    """One note per kind of tolerated input problem, naming at most NOTE_EXAMPLES cases."""
    notes = []
    skipped = problems["not_list"]
    if skipped:
        named = ", ".join(_clip(label) for label in skipped[:NOTE_EXAMPLES])
        notes.append(
            "%d record(s) carry an evidence value that is not a list; it was skipped: %s%s."
            % (len(skipped), named, " and more" if len(skipped) > NOTE_EXAMPLES else "")
        )
    bad = problems["bad_date"]
    if bad:
        named = ", ".join("%s %s" % (_clip(label), _clip(evidence_id))
                          for label, evidence_id, _raw in bad[:NOTE_EXAMPLES])
        notes.append(
            "%d evidence item(s) carry a source_date that is not a date (first: %s) and were "
            "read as undated: %s%s." % (len(bad), _echo(bad[0][2]), named,
                                         " and more" if len(bad) > NOTE_EXAMPLES else "")
        )
    return notes


def _check_observed_at(entries, as_of):
    """INV-24: an evidence reading later than the re-check date is a data error."""
    for entry in entries:
        items = entry["record"].get("evidence")
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            observed = item.get("observed_at")
            if not isinstance(observed, str) or _common.is_unknown(observed):
                continue
            try:
                delta = _common.days_between(observed, as_of)
            except _common.DataError:
                continue
            if delta < 0:
                raise _common.DataError(
                    "%s %s evidence %s was observed at %s, after --as-of %s (INV-24); an "
                    "earlier --as-of would make old evidence look fresh"
                    % (entry["entity"], entry["record_id"], item.get("evidence_id"),
                       observed[:10], as_of)
                )


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def _parse_top(value):
    if value is None:
        return None
    text = str(value).strip()
    # isdigit() alone accepts "\u00b2" (superscript two), which int() then rejects.
    if not (text.isascii() and text.isdigit()) or int(text) < 1:
        raise _common.UsageError("--top %s must be a whole number of at least 1" % _echo(value))
    return int(text)


def _build_queue(doc, as_of, config, top):
    policy = _policy(config)
    entries, notes = _collect(doc)
    _check_observed_at(entries, as_of)
    notes.extend(_score_version_notes(doc, entries, config))

    problems = {"not_list": [], "bad_date": []}
    found = []
    by_reason = dict((code, 0) for code in REASON_ORDER)
    by_bucket = dict((label, 0) for label in policy["labels"])
    by_bucket["unknown"] = 0
    items_scanned = 0
    items_queued = 0
    undated_total = 0
    undated_current = 0
    claims_total = 0
    without_evidence = 0
    for entry in entries:
        result, views = _audit_record(entry, as_of, policy, problems)
        if not views:
            without_evidence += 1
        for view in views:
            items_scanned += 1
            by_bucket[view["bucket"]] += 1
            if view["source_age"] is None:
                undated_total += 1
                if not view["reasons"]:
                    undated_current += 1
        if result is None:
            continue
        sort_key, rendered, queued = result
        found.append((sort_key, rendered))
        for code in rendered["record_reasons"]:
            by_reason[code] += 1
        for view in queued:
            items_queued += 1
            for code in view["reasons"]:
                by_reason[code] += 1
        claims_total += len(rendered["claims"])

    found.sort(key=lambda pair: pair[0])
    queue = [rendered for _key, rendered in found]
    for position, rendered in enumerate(queue, 1):
        rendered["position"] = position
    listed = queue if top is None else queue[:top]

    notes.extend(_problem_notes(problems))
    if undated_current:
        notes.append(
            "%d undated evidence item(s) were not queued: an undated page is current while "
            "its last reading (observed_at) is, because an undated page is not a stale page "
            "(evidence.unknown_source_date_note). They are queued as 'undated' once that "
            "reading reaches the %s bucket." % (undated_current, DUE_FROM_BUCKET)
        )
    if len(listed) < len(queue):
        notes.append(
            "--top %d listed %d of %d queued record(s); the summary counts all of them."
            % (top, len(listed), len(queue))
        )
    notes.append(FINAL_NOTE)

    return {
        "report_kind": REPORT_KIND,
        "schema_version": _common.SCHEMA_VERSION,
        "score_version": config.get("score_version"),
        "skill_version": _common.SKILL_VERSION,
        "as_of": as_of,
        "policy": {
            "due_from_bucket": DUE_FROM_BUCKET,
            "stale_threshold_days": policy["threshold"],
            "recency_buckets": policy["buckets"],
            "reason_order": list(REASON_ORDER),
        },
        "summary": {
            "records_scanned": len(entries),
            "records_queued": len(queue),
            "records_listed": len(listed),
            "records_without_evidence": without_evidence,
            "evidence_items_scanned": items_scanned,
            "evidence_items_queued": items_queued,
            "evidence_items_undated": undated_total,
            "claims_without_current_evidence": claims_total,
            "by_reason": by_reason,
            "by_bucket": by_bucket,
        },
        "queue": listed,
        "notes": notes,
    }


def _run(args, config):
    top = _parse_top(args.top)
    # Read first, so an unreadable input is reported as such even without --as-of.
    doc = _common.read_input(args)
    if args.as_of is None or not str(args.as_of).strip():
        raise _common.UsageError(
            "%s needs --as-of YYYY-MM-DD, the re-check date; it never reads the clock and "
            "never guesses one" % SCRIPT_NAME
        )
    as_of = _common.resolve_as_of([], args.as_of, config)

    queue = _build_queue(doc, as_of, config, top)

    errors = []
    if args.validate:
        try:
            schema = _common.load_schema(REPORT_KIND, args.schema_dir)
        except Exception as exc:
            errors.append("cannot load %s schema: %s" % (REPORT_KIND, exc))
        else:
            errors.extend(_common.validate(queue, schema))
    if errors:
        # Nothing is written: a queue that fails its own schema would still be followed.
        _common.die("queue failed validation (%d problems); nothing was written"
                    % (len(errors),), 1)
        for message in errors[:100]:
            _common.eprint("  " + message)
        return 1

    stream, handle = None, None
    if args.output:
        try:
            handle = open(args.output, "w", encoding="utf-8")
        except OSError as exc:
            return _common.die("cannot write --output: %s" % (exc,), 2)
        stream = handle
    try:
        _common.write_output(queue, args.pretty, stream)
    finally:
        if handle is not None:
            handle.close()

    summary = queue["summary"]
    _common.eprint(
        "queued %d of %d record(s); %d of %d evidence item(s) to re-read"
        % (summary["records_queued"], summary["records_scanned"],
           summary["evidence_items_queued"], summary["evidence_items_scanned"]),
        quiet=args.quiet,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
