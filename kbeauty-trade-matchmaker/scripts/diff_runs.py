#!/usr/bin/env python3
"""diff_runs.py - compare two scored runs of the same search or the same RFQ.

An operator tool outside every SKILL.md mode, in the same class as the calibration
scripts: it reads two documents a scorer already wrote and reports what changed between
them. It feeds nothing back into any score and has no config block.

What it reports, per record:

  * new / gone      - listed in one run's output and not in the other's;
  * newly_excluded  - returned before, excluded after (and newly_returned, the reverse);
  * exclusion_changes - excluded in both, with a different reason or rule set;
  * changed         - returned in both, with a different score, rank, dimension,
                      qualified flag, confidence or Missing line.

What it refuses (exit 1, one ERROR line, nothing on stdout):

  * a document that is not a discovery-result or match-result run, or that fails its
    own schema;
  * a run whose records disagree with its envelope on score_version (INV-23);
  * two runs scored with different score_versions: scores from two rubrics are not on
    one scale (BUILD-CONTRACT 12.3 rule 4, INV-23). There is no opt-in;
  * a discovery run against a match run, a buyer run against a seller run, or match
    runs for two different RFQs;
  * a record id that appears twice inside one run;
  * an explicit --as-of earlier than either input run.

Korean gloss: 두 번의 점수 실행 결과를 비교한다 — 점수를 바꾸지 않는다.

Records are paired by id first, then through `merged_from` (a dedupe merge can change
which id survives). A record whose id is the literal "unknown" is never paired. Only
the company name and canonical domain are copied from a record: no contact channel,
evidence, website or observed value reaches the diff.

Build rules of BUILD-CONTRACT.md 1.5 and 7.11 apply: stdlib only, Python 3.9 syntax, no
network, no wall-clock read, nothing at import time. Comments are English.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "diff_runs.py"
REPORT_KIND = "run-diff"

DISCOVERY = "discovery-result"
MATCH = "match-result"

# Where each kind keeps the fields a diff reads. Nothing else is copied.
_FIELDS = {
    DISCOVERY: {
        "list": "records",
        "name": "company_name",
        "excluded_id": "id",
        "excluded_name": "company_name",
        "score": "qualification_score",
        "dimensions": "dimension_scores",
    },
    MATCH: {
        "list": "results",
        "name": "seller_name",
        "excluded_id": "seller_id",
        "excluded_name": "seller_name",
        "score": "match_score",
        "dimensions": "component_scores",
    },
}

NOTE_MATCH_LISTING = (
    "A seller absent from one match run may have been cut by the threshold or by top_n "
    "rather than dropped as a candidate; new and gone mean listed / not listed in the "
    "run's output."
)
NOTE_AS_OF = (
    "The runs were scored at different as_of dates (%s, %s); recency and staleness can "
    "move scores with no change in evidence (BUILD-CONTRACT 12.3 rule 3)."
)
NOTE_REVERSED = "--before is dated later than --after; deltas read after minus before as given."
NOTE_SKILL_VERSION = (
    "The runs were produced by different skill versions (%s, %s); a normalization change "
    "between them can move canonical_domain or a dedupe merge with no change in the "
    "companies."
)
NOTE_UNKNOWN_IDS = (
    "%s carries %d record(s) with the id \"unknown\"; they cannot be paired and are "
    "listed under %s."
)
NOTE_PARTIAL = (
    "Run %s is partial; records absent from it may simply not have been reached."
)


# --------------------------------------------------------------------------
# loading and refusals
# --------------------------------------------------------------------------


def _read_json_file(path):
    class _Args(object):
        pass

    args = _Args()
    args.input = path
    return _common.read_input(args)


def _run_kind(document, label):
    if isinstance(document, list):
        raise _common.DataError(
            "%s is a bare records array, not a run; re-run the scorer without "
            "--records-only" % label
        )
    if not isinstance(document, dict):
        raise _common.DataError("%s is not a JSON object" % label)
    if "match_run_id" in document or "no_match" in document:
        return MATCH
    if "entity" in document and isinstance(document.get("records"), list):
        return DISCOVERY
    raise _common.DataError(
        "%s is neither a discovery-result nor a match-result document" % label
    )


def _validate_input(document, kind, label, schema_dir):
    """Refuse an input that fails its own schema, naming the first failing path."""
    errors = list(_common.validate(document, _common.load_schema(kind, schema_dir)))
    if not errors and kind == DISCOVERY:
        # discovery-result leaves records[] open (cross-file $ref is not supported), so
        # each record is checked against buyer / seller here, as validate_output does.
        record_schema = _common.load_schema(document["entity"], schema_dir)
        for index, record in enumerate(document["records"]):
            for message in _common.validate(record, record_schema):
                errors.append("records[%d].%s" % (index, message))
    if errors:
        raise _common.DataError(
            "%s is not a valid %s document (%d problem(s)); first: %s"
            % (label, kind, len(errors), errors[0])
        )


def _run_score_version(document, kind, label):
    """The one score_version a run carries; refuse a run that mixes several (INV-23)."""
    values = set([document["score_version"]])
    if kind == DISCOVERY:
        for record in document["records"]:
            if "score_version" in record:
                values.add(record["score_version"])
    if len(values) != 1:
        raise _common.DataError(
            "%s mixes score_version values %s in one run (INV-23); it is not a "
            "coherent scorer output" % (label, ", ".join(sorted(str(v) for v in values)))
        )
    return document["score_version"]


def _load(path, label, schema_dir):
    document = _read_json_file(path)
    kind = _run_kind(document, label)
    _validate_input(document, kind, label, schema_dir)
    version = _run_score_version(document, kind, label)
    return document, kind, version


def _entity(document, kind):
    return document["entity"] if kind == DISCOVERY else "seller"


# --------------------------------------------------------------------------
# record projection
# --------------------------------------------------------------------------


def _rule_ids(entry):
    rules = entry.get("failed_rules")
    if not isinstance(rules, list):
        return []
    ids = []
    for rule in rules:
        if isinstance(rule, dict) and isinstance(rule.get("rule_id"), str):
            if rule["rule_id"] not in ids:
                ids.append(rule["rule_id"])
    return sorted(ids)


def _entries(document, kind, label):
    """One flat entry per record, returned or excluded, in document order."""
    fields = _FIELDS[kind]
    id_key = "%s_id" % _entity(document, kind)
    out = []
    for index, record in enumerate(document[fields["list"]]):
        rank = record.get("rank")
        if kind == MATCH and rank is not None and rank != index + 1:
            # The list position is the rank the diff reports, so a record whose own
            # rank disagrees with it is refused rather than silently re-ranked.
            raise _common.DataError(
                "%s: results[%d].rank is %s but the record is at position %d; rank must "
                "equal index + 1 (match-result schema)" % (label, index, rank, index + 1)
            )
        merged = record.get("merged_from")
        out.append({
            "id": record.get(id_key, _common.UNKNOWN),
            "state": "returned",
            "name": record.get(fields["name"], _common.UNKNOWN),
            "domain": record.get("canonical_domain", _common.UNKNOWN),
            "position": index + 1,
            "merged_from": [m for m in merged if isinstance(m, str)]
            if isinstance(merged, list) else [],
            "record": record,
        })
    for entry in document["excluded"]:
        out.append({
            "id": entry.get(fields["excluded_id"], _common.UNKNOWN),
            "state": "excluded",
            "name": entry.get(fields["excluded_name"], _common.UNKNOWN),
            "domain": entry.get("canonical_domain", _common.UNKNOWN),
            "position": None,
            "merged_from": [],
            "record": entry,
        })
    seen = set()
    for entry in out:
        rid = entry["id"]
        if rid == _common.UNKNOWN:
            continue
        if rid in seen:
            raise _common.DataError(
                "%s: record id %s appears twice in one run" % (label, rid)
            )
        seen.add(rid)
    return out


def _qualified(record):
    value = record.get("qualified")
    return value if isinstance(value, bool) else _common.UNKNOWN


def _returned_facts(entry, kind, prefix=""):
    """score / rank / qualified of a returned entry, keys optionally prefixed."""
    record = entry["record"]
    facts = [(prefix + "score", record[_FIELDS[kind]["score"]])]
    if kind == MATCH:
        facts.append((prefix + "rank", entry["position"]))
    facts.append((prefix + "qualified", _qualified(record)))
    return facts


def _listing_item(entry, kind):
    item = {
        "record_id": entry["id"],
        "company_name": entry["name"],
        "canonical_domain": entry["domain"],
        "state": entry["state"],
    }
    if entry["state"] == "returned":
        for key, value in _returned_facts(entry, kind):
            item[key] = value
    else:
        item["reason_summary"] = entry["record"].get("reason_summary", _common.UNKNOWN)
        item["failed_rule_ids"] = _rule_ids(entry["record"])
    return item


# --------------------------------------------------------------------------
# pairing
# --------------------------------------------------------------------------


def _pair(before, after):
    """[(before_entry, after_entry, matched_by)] plus the unpaired ids of each side.

    Pass 1 pairs identical ids. Pass 2 follows merged_from: an unpaired after-record
    (ascending id) takes the smallest unpaired before-id it lists, then the same the
    other way round. Codepoint order everywhere, so the pairing is byte-stable.
    """
    b_by_id = dict((e["id"], e) for e in before if e["id"] != _common.UNKNOWN)
    a_by_id = dict((e["id"], e) for e in after if e["id"] != _common.UNKNOWN)
    pairs = []
    for rid in sorted(set(b_by_id) & set(a_by_id)):
        pairs.append((b_by_id[rid], a_by_id[rid], "id"))
    b_open = set(b_by_id) - set(a_by_id)
    a_open = set(a_by_id) - set(b_by_id)

    for rid in sorted(a_open):
        candidates = sorted(m for m in a_by_id[rid]["merged_from"] if m in b_open)
        if candidates:
            pairs.append((b_by_id[candidates[0]], a_by_id[rid], "merged_from"))
            b_open.discard(candidates[0])
            a_open.discard(rid)
    for rid in sorted(b_open):
        candidates = sorted(m for m in b_by_id[rid]["merged_from"] if m in a_open)
        if candidates:
            pairs.append((b_by_id[rid], a_by_id[candidates[0]], "merged_from"))
            a_open.discard(candidates[0])
            b_open.discard(rid)
    return pairs, b_open, a_open


def _merged_into(before_id, after):
    """The smallest after-id whose merged_from absorbed before_id, or None."""
    hosts = sorted(
        e["id"] for e in after
        if e["id"] != _common.UNKNOWN and before_id in e["merged_from"]
    )
    return hosts[0] if hosts else None


def _pair_head(b, a, matched_by):
    head = {"record_id": a["id"]}
    if matched_by != "id":
        head["before_id"] = b["id"]
    head["matched_by"] = matched_by
    head["company_name"] = a["name"]
    return head


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def _decimal_delta(before, after):
    return _common.round_half_up(Decimal(str(after)) - Decimal(str(before)), 2)


def _dimension_changes(before_dims, after_dims):
    """Changed dimensions only: before-order first, then after-only keys in after order."""
    before_dims = before_dims if isinstance(before_dims, dict) else {}
    after_dims = after_dims if isinstance(after_dims, dict) else {}
    keys = list(before_dims) + [k for k in after_dims if k not in before_dims]
    out = []
    for key in keys:
        old = before_dims.get(key, "absent")
        new = after_dims.get(key, "absent")
        if old == new:
            continue
        delta = new - old if isinstance(old, int) and isinstance(new, int) else None
        out.append({"dimension": key, "before": old, "after": new, "delta": delta})
    return out


def _missing_change(before_missing, after_missing):
    """Labels added / removed, in list order; None when either side carries no list."""
    if not isinstance(before_missing, list) or not isinstance(after_missing, list):
        return None
    added, removed = [], []
    for label in after_missing:
        if label not in before_missing and label not in added:
            added.append(label)
    for label in before_missing:
        if label not in after_missing and label not in removed:
            removed.append(label)
    if not added and not removed:
        return None
    return {"added": added, "removed": removed}


def _flip(before, after):
    if isinstance(before, bool) and isinstance(after, bool):
        return "gained" if after else "lost"
    return "undetermined"


def _compare_returned(b, a, kind, matched_by):
    fields = _FIELDS[kind]
    old, new = b["record"], a["record"]
    item = _pair_head(b, a, matched_by)
    changed = False

    s_old, s_new = old[fields["score"]], new[fields["score"]]
    if s_old != s_new:
        item["score"] = {"before": s_old, "after": s_new, "delta": s_new - s_old}
        changed = True
    if kind == MATCH:
        if old["base_score"] != new["base_score"]:
            item["base_score"] = {
                "before": old["base_score"],
                "after": new["base_score"],
                "delta": new["base_score"] - old["base_score"],
            }
            changed = True
        if b["position"] != a["position"]:
            item["rank"] = {"before": b["position"], "after": a["position"]}
            changed = True
    dims = _dimension_changes(old.get(fields["dimensions"]), new.get(fields["dimensions"]))
    if dims:
        item["dimensions"] = dims
        changed = True
    q_old, q_new = _qualified(old), _qualified(new)
    if q_old != q_new:
        item["qualified"] = {"before": q_old, "after": q_new, "flip": _flip(q_old, q_new)}
        changed = True
    c_old, c_new = old["confidence"], new["confidence"]
    if c_old != c_new:
        item["confidence"] = {
            "before": c_old, "after": c_new, "delta": _decimal_delta(c_old, c_new)
        }
        changed = True
    missing = _missing_change(old.get("missing"), new.get("missing"))
    if missing is not None:
        item["missing"] = missing
        changed = True
    # A pair joined through merged_from is reported even with equal fields: the id
    # change itself is what the operator needs to see.
    return item if changed or matched_by != "id" else None


# --------------------------------------------------------------------------
# document
# --------------------------------------------------------------------------


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _run_ref(document, kind, version):
    summary = document["summary"]
    partial = document.get("partial")
    skill = document.get("skill_version")
    return {
        "as_of": document["as_of"],
        "score_version": version,
        "skill_version": skill if isinstance(skill, str) and skill else _common.UNKNOWN,
        "match_run_id": document["match_run_id"] if kind == MATCH else None,
        "rfq_id": document["rfq_id"] if kind == MATCH else None,
        "threshold_used": summary["threshold_used"],
        "threshold_mode": summary["threshold_mode"],
        "partial": partial if isinstance(partial, bool) else _common.UNKNOWN,
        "returned": len(document[_FIELDS[kind]["list"]]),
        "excluded": len(document["excluded"]),
    }


def _listing_key(item):
    return (item["record_id"], item["canonical_domain"], item["company_name"], item["state"])


def _build(before_doc, after_doc, kind, version, as_of):
    before = _entries(before_doc, kind, "--before")
    after = _entries(after_doc, kind, "--after")
    pairs, b_open, a_open = _pair(before, after)

    new_items = [_listing_item(e, kind) for e in after
                 if e["id"] in a_open or e["id"] == _common.UNKNOWN]
    gone_items = []
    for e in before:
        if e["id"] in b_open or e["id"] == _common.UNKNOWN:
            item = _listing_item(e, kind)
            host = _merged_into(e["id"], after) if e["id"] != _common.UNKNOWN else None
            if host is not None:
                item["merged_into"] = host
            gone_items.append(item)

    newly_excluded, newly_returned, exclusion_changes, changed = [], [], [], []
    unchanged = 0
    for b, a, matched_by in pairs:
        if b["state"] == "returned" and a["state"] == "returned":
            item = _compare_returned(b, a, kind, matched_by)
            if item is None:
                unchanged += 1
            else:
                changed.append(item)
        elif b["state"] == "returned":
            item = _pair_head(b, a, matched_by)
            for key, value in _returned_facts(b, kind, "before_"):
                item[key] = value
            item["reason_summary"] = a["record"].get("reason_summary", _common.UNKNOWN)
            item["failed_rule_ids"] = _rule_ids(a["record"])
            newly_excluded.append(item)
        elif a["state"] == "returned":
            item = _pair_head(b, a, matched_by)
            item["before_reason_summary"] = b["record"].get("reason_summary", _common.UNKNOWN)
            item["before_failed_rule_ids"] = _rule_ids(b["record"])
            for key, value in _returned_facts(a, kind, "after_"):
                item[key] = value
            newly_returned.append(item)
        else:
            old_reason = b["record"].get("reason_summary", _common.UNKNOWN)
            new_reason = a["record"].get("reason_summary", _common.UNKNOWN)
            old_rules, new_rules = _rule_ids(b["record"]), _rule_ids(a["record"])
            if old_reason == new_reason and old_rules == new_rules and matched_by == "id":
                unchanged += 1
                continue
            item = _pair_head(b, a, matched_by)
            item["before_reason_summary"] = old_reason
            item["after_reason_summary"] = new_reason
            item["rules_added"] = sorted(r for r in new_rules if r not in old_rules)
            item["rules_removed"] = sorted(r for r in old_rules if r not in new_rules)
            exclusion_changes.append(item)

    new_items.sort(key=_listing_key)
    gone_items.sort(key=_listing_key)
    newly_excluded.sort(key=lambda i: i["record_id"])
    newly_returned.sort(key=lambda i: i["record_id"])
    exclusion_changes.sort(key=lambda i: i["record_id"])
    changed.sort(key=lambda i: (-abs(i["score"]["delta"]) if "score" in i else 0,
                                i["record_id"]))

    before_ref = _run_ref(before_doc, kind, version)
    after_ref = _run_ref(after_doc, kind, version)

    if kind == DISCOVERY:
        query_changed = _canonical(before_doc.get("query")) != _canonical(after_doc.get("query"))
        weights_changed = None
    else:
        query_changed = None
        weights_changed = (_canonical(before_doc.get("weights_used"))
                           != _canonical(after_doc.get("weights_used")))
    context = {
        "as_of_changed": before_ref["as_of"] != after_ref["as_of"],
        "threshold_changed": (before_ref["threshold_used"], before_ref["threshold_mode"])
        != (after_ref["threshold_used"], after_ref["threshold_mode"]),
        "query_changed": query_changed,
        "weights_changed": weights_changed,
    }

    summary = {
        "paired": len(pairs),
        "unchanged": unchanged,
        "new": len(new_items),
        "gone": len(gone_items),
        "newly_excluded": len(newly_excluded),
        "newly_returned": len(newly_returned),
        "exclusion_changed": len(exclusion_changes),
        "changed": len(changed),
        "score_changed": sum(1 for i in changed if "score" in i),
        "score_up": sum(1 for i in changed if "score" in i and i["score"]["delta"] > 0),
        "score_down": sum(1 for i in changed if "score" in i and i["score"]["delta"] < 0),
        "qualified_gained": sum(
            1 for i in changed if i.get("qualified", {}).get("flip") == "gained"),
        "qualified_lost": sum(
            1 for i in changed if i.get("qualified", {}).get("flip") == "lost"),
        "confidence_changed": sum(1 for i in changed if "confidence" in i),
        "rank_changed": sum(1 for i in changed if "rank" in i) if kind == MATCH else None,
        "missing_changed": sum(1 for i in changed if "missing" in i),
    }

    notes = []
    if kind == MATCH:
        notes.append(NOTE_MATCH_LISTING)
    if context["as_of_changed"]:
        notes.append(NOTE_AS_OF % (before_ref["as_of"], after_ref["as_of"]))
    if before_ref["as_of"] > after_ref["as_of"]:
        notes.append(NOTE_REVERSED)
    if before_ref["skill_version"] != after_ref["skill_version"]:
        notes.append(NOTE_SKILL_VERSION % (before_ref["skill_version"], after_ref["skill_version"]))
    for label, entries, section in (("--before", before, "gone"), ("--after", after, "new")):
        count = sum(1 for e in entries if e["id"] == _common.UNKNOWN)
        if count:
            notes.append(NOTE_UNKNOWN_IDS % (label, count, section))
    for label, ref in (("--before", before_ref), ("--after", after_ref)):
        if ref["partial"] is True:
            notes.append(NOTE_PARTIAL % label)

    return {
        "report_kind": REPORT_KIND,
        "schema_version": _common.SCHEMA_VERSION,
        "score_version": version,
        "skill_version": _common.SKILL_VERSION,
        "as_of": as_of,
        "kind": kind,
        "entity": _entity(after_doc, kind),
        "before": before_ref,
        "after": after_ref,
        "context": context,
        "summary": summary,
        "new": new_items,
        "gone": gone_items,
        "newly_excluded": newly_excluded,
        "newly_returned": newly_returned,
        "exclusion_changes": exclusion_changes,
        "changed": changed,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Compare two scored runs of the same search or RFQ and report what changed. "
            "Korean gloss: 두 번의 점수 실행 결과를 비교한다."
        ),
    )
    parser.add_argument("--before", default=None, metavar="PATH",
                        help="The older / reference run (discovery-result or match-result); - = stdin")
    parser.add_argument("--after", default=None, metavar="PATH",
                        help="The newer run of the same kind; - = stdin")
    parser.add_argument("-o", "--output", default=None, help="Diff path (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent=2 diff")
    parser.add_argument("--as-of", dest="as_of", default=None,
                        help="YYYY-MM-DD diff date (default: the later input as_of)")
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--no-validate", dest="validate", action="store_false", default=True,
                        help="Skip the run-diff output schema check (inputs are always checked)")
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


def _run(args):
    if not args.before:
        raise _common.UsageError("--before is required")
    if not args.after:
        raise _common.UsageError("--after is required")
    if args.before == "-" and args.after == "-":
        raise _common.UsageError("at most one of --before / --after may read stdin")
    if args.as_of is not None and not args.as_of.strip():
        raise _common.UsageError("--as-of is empty; give a YYYY-MM-DD date or omit the flag")
    if args.as_of is not None:
        _common.resolve_as_of([], args.as_of)

    before_doc, before_kind, before_version = _load(args.before, "--before", args.schema_dir)
    after_doc, after_kind, after_version = _load(args.after, "--after", args.schema_dir)

    # Refusal order follows acceptance_report (R7.12.7): the rubric version is the more
    # fundamental defect, so it is reported before a kind, entity or RFQ mismatch.
    if before_version != after_version:
        raise _common.DataError(
            "refusing to compare score_version %s with %s: scores from two rubric versions "
            "are not on one scale (BUILD-CONTRACT 12.3 rule 4, INV-23); re-score both runs "
            "on one version first" % (before_version, after_version)
        )
    if before_kind != after_kind:
        raise _common.DataError(
            "refusing to diff a %s against a %s: different rubrics on different populations"
            % (before_kind, after_kind)
        )
    kind = before_kind
    if _entity(before_doc, kind) != _entity(after_doc, kind):
        raise _common.DataError(
            "refusing to diff %s and %s runs: the rubrics have different dimensions"
            % (_entity(before_doc, kind), _entity(after_doc, kind))
        )
    if kind == MATCH and before_doc["rfq_id"] != after_doc["rfq_id"]:
        raise _common.DataError(
            "refusing to diff match runs for different RFQs (rfq_id %s vs %s): a match "
            "ranking answers one RFQ" % (before_doc["rfq_id"], after_doc["rfq_id"])
        )

    latest = max(before_doc["as_of"], after_doc["as_of"])
    if args.as_of is not None and args.as_of.strip() < latest:
        raise _common.DataError(
            "--as-of %s is earlier than input run date %s; a diff cannot be dated before a "
            "run it describes" % (args.as_of.strip(), latest)
        )
    as_of = args.as_of.strip() if args.as_of is not None else latest

    diff = _build(before_doc, after_doc, kind, before_version, as_of)

    errors = []
    if args.validate:
        try:
            schema = _common.load_schema(REPORT_KIND, args.schema_dir)
        except Exception as exc:
            errors.append("cannot load %s schema: %s" % (REPORT_KIND, exc))
        else:
            errors.extend(_common.validate(diff, schema))
    if errors:
        # Nothing is written, as in acceptance_report (R7.12.8): the output file is
        # opened only after the diff has passed its schema.
        _common.die("run diff failed validation (%d problems); nothing was written"
                    % (len(errors),), 1)
        for message in errors[:100]:
            _common.eprint("  " + message)
        return 1

    stream, handle = None, None
    if args.output:
        try:
            handle = open(args.output, "w", encoding="utf-8", newline="")
        except OSError as exc:
            return _common.die("cannot write --output: %s" % (exc,), 2)
        stream = handle
    try:
        _common.write_output(diff, args.pretty, stream)
    finally:
        if handle is not None:
            handle.close()

    summary = diff["summary"]
    _common.eprint(
        "diff %s (%s): %d paired, %d new, %d gone, %d newly excluded, %d newly returned, "
        "%d changed"
        % (kind, diff["entity"], summary["paired"], summary["new"], summary["gone"],
           summary["newly_excluded"], summary["newly_returned"], summary["changed"]),
        quiet=args.quiet,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
