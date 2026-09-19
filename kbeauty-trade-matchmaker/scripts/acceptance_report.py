#!/usr/bin/env python3
"""acceptance_report.py - join filled review sheets to scored runs and measure the rubric.

The second half of the labelled-set protocol of references/calibration-notes.md
section 7. It reads the CSVs `scripts/make_review_sheet.py` produced, joins them back to
the scored `discovery-result` / `match-result` documents they came from, and reports
whether the rubric **discriminates**: whether a record the rubric scored high is one a
trade operator actually accepts.

What it measures and what it does not:

  * It measures PRD 17's **Human Acceptance Rate** - the share of returned candidates an
    operator marks valid - and nothing else.
  * PRD 17's other metric, **RFQ Conversion**, needs outcome data from the application
    layer after real outreach. No document this package produces carries it, so no
    report here can claim it.

Korean gloss: 사람이 채점한 검수 시트를 점수 결과와 결합해 루브릭의 변별력을 측정한다.

The report is JSON on stdout with a deterministic key order, every rate rounded through
_common.round_half_up like every other number this package prints, and every tunable
(minimum sample, band edges, sweep range) read from scoring.config.json "calibration"
(INV-29). Build rules of BUILD-CONTRACT.md 1.5 and 7.11 apply: stdlib only, Python 3.9
syntax, no network, no wall-clock read, nothing at import time. Comments are English.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "acceptance_report.py"
REPORT_KIND = "acceptance-report"

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


# --------------------------------------------------------------------------
# scored documents
# --------------------------------------------------------------------------


def _read_json_file(path):
    class _Args(object):
        pass

    args = _Args()
    args.input = path
    return _common.read_input(args)


def _scored_kind(document, path):
    if not isinstance(document, dict):
        raise _common.DataError("%s is not a JSON object" % path)
    if "match_run_id" in document or "no_match" in document:
        return "match-result"
    if "entity" in document and isinstance(document.get("records"), list):
        return "discovery-result"
    raise _common.DataError(
        "%s is neither a discovery-result nor a match-result document" % path
    )


class _Population(object):
    """Everything the report needs from the scored side, already joined into one list.

    `returned` holds one dict per ranked record; `excluded` one per excluded record. The
    two are kept apart because the metrics ask different questions of them: acceptance
    rate and AUC are about the records the rubric RETURNED, false exclusions about the
    ones it threw away.
    """

    def __init__(self):
        self.returned = []
        self.excluded = []
        self.kind = None
        self.entity = None
        self.score_version = None
        self.thresholds = []
        self.threshold_modes = []
        self.dimension_keys = []


def _claim_id(seen, record_id, path):
    """Record one id as belonging to one --scored document, or refuse.

    The same record reaching the report twice - the same file passed twice, or a copy
    of a run under a second name - multiplies every count it appears in and silently
    doubles the accept+reject total that `min_sample` guards. A report that says it has
    60 decided reviews when it has 30 is worse than one that refuses.
    """
    if record_id in seen:
        raise _common.DataError(
            "record_id '%s' appears in two --scored documents (%s and %s); a record "
            "counted twice inflates every rate and defeats the minimum-sample guard"
            % (record_id, seen[record_id], path)
        )
    seen[record_id] = path


def _load_population(paths):
    population = _Population()
    kinds, entities, versions = [], [], []
    claimed = []

    for path in paths:
        document = _read_json_file(path)
        kind = _scored_kind(document, path)
        kinds.append(kind)
        if kind == "match-result":
            entity = "seller"
            id_key, name_key = "seller_id", "seller_name"
            score_key, dimension_key = "match_score", "component_scores"
            returned = document.get("results") or []
            excluded_id_key, excluded_name_key = "seller_id", "seller_name"
        else:
            entity = document.get("entity")
            if entity not in ("buyer", "seller"):
                raise _common.DataError(
                    "%s: discovery-result entity is %r; expected \"buyer\" or \"seller\""
                    % (path, entity)
                )
            id_key, name_key = "%s_id" % entity, "company_name"
            score_key, dimension_key = "qualification_score", "dimension_scores"
            returned = document.get("records") or []
            excluded_id_key, excluded_name_key = "id", "company_name"
        entities.append(entity)
        versions.append(document.get("score_version"))

        summary = document.get("summary") or {}
        if "threshold_used" in summary:
            population.thresholds.append(summary.get("threshold_used"))
        if "threshold_mode" in summary:
            population.threshold_modes.append(summary.get("threshold_mode"))

        for record in returned:
            if not isinstance(record, dict):
                raise _common.DataError("%s: a scored record is not a JSON object" % path)
            claimed.append((str(record.get(id_key)), path))
            dimensions = record.get(dimension_key) or {}
            if not isinstance(dimensions, dict):
                dimensions = {}
            for key in dimensions:
                if key not in population.dimension_keys:
                    population.dimension_keys.append(key)
            population.returned.append(
                {
                    "record_id": str(record.get(id_key)),
                    "company_name": record.get(name_key),
                    "country": record.get("country"),
                    "score": record.get(score_key),
                    "qualified": record.get("qualified"),
                    "dimensions": dimensions,
                }
            )

        for record in document.get("excluded") or []:
            if not isinstance(record, dict):
                raise _common.DataError("%s: an excluded record is not a JSON object" % path)
            claimed.append((str(record.get(excluded_id_key)), path))
            rules = [
                rule.get("rule_id")
                for rule in (record.get("failed_rules") or [])
                if isinstance(rule, dict) and rule.get("rule_id")
            ]
            population.excluded.append(
                {
                    "record_id": str(record.get(excluded_id_key)),
                    "company_name": record.get(excluded_name_key),
                    "country": record.get("country"),
                    "exclusion_reason": record.get("reason_summary"),
                    "failed_rule_ids": rules,
                }
            )

    # INV-23 applies to one output; the same principle applies to one report. A rate
    # computed over two rubric versions is a number about nothing: the records were not
    # scored by the same function, so their scores are not on one scale.
    distinct_versions = sorted(set(str(v) for v in versions))
    if len(distinct_versions) > 1:
        raise _common.DataError(
            "refusing to mix score_version %s in one report: scores from two rubric "
            "versions are not comparable (BUILD-CONTRACT 12.3 rule 4, INV-23)"
            % (", ".join(distinct_versions),)
        )
    distinct_kinds = sorted(set(kinds))
    if len(distinct_kinds) > 1:
        raise _common.DataError(
            "refusing to mix %s documents in one report: a discovery score and a match "
            "score are different rubrics on different populations"
            % (" and ".join(distinct_kinds),)
        )
    distinct_entities = sorted(set(entities))
    if len(distinct_entities) > 1:
        raise _common.DataError(
            "refusing to mix %s populations in one report: the buyer and seller rubrics "
            "have different dimensions" % (" and ".join(distinct_entities),)
        )

    # After the cross-document consistency checks, not before: a report over two rubric
    # versions is a more fundamental refusal than a repeated record, and naming the
    # wrong one first sends the reader to fix the wrong thing.
    seen_ids = {}
    for record_id, path in claimed:
        _claim_id(seen_ids, record_id, path)

    population.kind = distinct_kinds[0]
    population.entity = distinct_entities[0]
    population.score_version = versions[0]
    return population


# --------------------------------------------------------------------------
# review sheets
# --------------------------------------------------------------------------


def _read_reviews(paths, known_ids, as_of):
    """{record_id: {"verdict", "reason_code", ...}}, or a DataError naming the defect.

    Every check here refuses rather than repairing. A calibration report is evidence for
    a weight or threshold decision; silently dropping a malformed row would change the
    denominator of the very rate the decision rests on.
    """
    reviews = {}
    notes = []
    for path in paths:
        try:
            # utf-8-sig: an operator's spreadsheet often writes a BOM. Shipped files
            # never carry one (BUILD-CONTRACT 1.5) but an input sheet is not ours.
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = list(reader.fieldnames or [])
                rows = list(reader)
        except (IOError, OSError) as exc:
            raise _common.UsageError("cannot read --reviews %s: %s" % (path, exc))
        except UnicodeDecodeError as exc:
            raise _common.UsageError("--reviews %s is not valid UTF-8: %s" % (path, exc))

        absent = [c for c in _common.REVIEW_SHEET_COLUMNS if c not in fieldnames]
        if absent:
            raise _common.DataError(
                "%s is not a review sheet: column(s) %s are absent"
                % (path, ", ".join(absent))
            )

        for line, row in enumerate(rows, 2):  # line 1 is the header
            where = "%s line %d" % (path, line)
            # A spreadsheet that saves "a,b,c\n,,,\n" is not handing back a broken
            # sheet, it is handing back a sheet with a trailing blank line. Skip a row
            # in which EVERY cell is empty; a row with any content but no record_id is
            # still a defect.
            if not any((value or "").strip() for value in row.values()):
                continue
            record_id = (row.get("record_id") or "").strip()
            if not record_id:
                raise _common.DataError("%s: record_id is empty" % where)
            if record_id not in known_ids:
                raise _common.DataError(
                    "%s: record_id '%s' matches no record in the scored document(s); the "
                    "sheet and the run have drifted apart" % (where, record_id)
                )

            for column in ("note", "reviewer_role"):
                hits = _common.personal_data_hits(row.get(column) or "")
                if hits:
                    raise _common.DataError(
                        "%s: %s contains %s; a reviewer is identified by a ROLE LABEL and "
                        "no personal contact may enter the calibration loop (INV-31)"
                        % (where, column, hits[0])
                    )

            reviewed_on = (row.get("reviewed_on") or "").strip()
            if reviewed_on:
                if not _DATE_RE.match(reviewed_on):
                    raise _common.DataError(
                        "%s: reviewed_on '%s' is not a YYYY-MM-DD date" % (where, reviewed_on)
                    )
                try:
                    _common.days_between(reviewed_on, as_of)
                except _common.DataError:
                    raise _common.DataError(
                        "%s: reviewed_on '%s' is not a valid calendar date"
                        % (where, reviewed_on)
                    )
                if reviewed_on > as_of:
                    raise _common.DataError(
                        "%s: reviewed_on %s is later than the report's as_of %s"
                        % (where, reviewed_on, as_of)
                    )

            verdict = (row.get("verdict") or "").strip().casefold()
            reason_code = (row.get("reason_code") or "").strip().casefold()
            if not verdict:
                if reason_code:
                    raise _common.DataError(
                        "%s: reason_code '%s' without a verdict" % (where, reason_code)
                    )
                continue  # an unfilled row is simply not reviewed
            if verdict not in _common.REVIEW_VERDICTS:
                raise _common.DataError(
                    "%s: verdict '%s' is not one of %s"
                    % (where, verdict, ", ".join(_common.REVIEW_VERDICTS))
                )
            if reason_code and reason_code not in _common.REVIEW_REASON_CODES:
                raise _common.DataError(
                    "%s: reason_code '%s' is not one of %s"
                    % (where, reason_code, ", ".join(_common.REVIEW_REASON_CODES))
                )
            if verdict == _common.REVIEW_VERDICT_NEEDS_REASON and not reason_code:
                raise _common.DataError(
                    "%s: verdict 'reject' requires a reason_code" % (where,)
                )

            entry = {
                "verdict": verdict,
                "reason_code": reason_code or None,
                "reviewer_role": (row.get("reviewer_role") or "").strip() or None,
                "reviewed_on": reviewed_on or None,
            }
            previous = reviews.get(record_id)
            if previous is None:
                reviews[record_id] = entry
                continue
            if previous["verdict"] != entry["verdict"]:
                raise _common.DataError(
                    "%s: record_id '%s' was reviewed twice with conflicting verdicts "
                    "('%s' and '%s'); resolve it before reporting"
                    % (where, record_id, previous["verdict"], entry["verdict"])
                )
            if previous["reason_code"] != entry["reason_code"]:
                raise _common.DataError(
                    "%s: record_id '%s' was reviewed twice with verdict '%s' but "
                    "conflicting reason_codes (%s and %s); resolve it before reporting"
                    % (where, record_id, entry["verdict"],
                       previous["reason_code"] or "none", entry["reason_code"] or "none")
                )
            note = (
                "duplicate review row for '%s' with the same verdict '%s'; the first "
                "row was kept" % (record_id, entry["verdict"])
            )
            if note not in notes:
                notes.append(note)
    return reviews, notes


# --------------------------------------------------------------------------
# arithmetic
# --------------------------------------------------------------------------


def _rate(numerator, denominator, decimals):
    """numerator / denominator rounded half-up, or None when the denominator is 0."""
    if not denominator:
        return None
    return _common.round_half_up(Decimal(numerator) / Decimal(denominator), decimals)


def _verdict_counts(rows):
    counts = {"accept": 0, "reject": 0, "unsure": 0}
    for row in rows:
        verdict = row.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def _group_block(rows, decimals):
    """The shape every breakdown row shares, so a reader compares like with like."""
    counts = _verdict_counts(rows)
    reviewed = counts["accept"] + counts["reject"] + counts["unsure"]
    return {
        "n": len(rows),
        "reviewed": reviewed,
        "accept": counts["accept"],
        "reject": counts["reject"],
        "unsure": counts["unsure"],
        "acceptance_rate": _rate(
            counts["accept"], counts["accept"] + counts["reject"], decimals
        ),
    }


def _auc(accept_scores, reject_scores, decimals):
    """P(score of an accepted record > score of a rejected one), ties counted 0.5.

    The Mann-Whitney form: every accept x reject pair is compared once, so the value is
    the probability that a randomly chosen accepted record outranks a randomly chosen
    rejected one. 0.5 is "this number tells a reviewer nothing"; 1.0 is a perfect
    separation. Returns (value, reason) with value None and a reason when either class
    is empty, because an AUC over one class is not a small number, it is no number.
    """
    if not accept_scores:
        return None, "no accepted record carries a score"
    if not reject_scores:
        return None, "no rejected record carries a score"
    total = Decimal(0)
    for accepted in accept_scores:
        for rejected in reject_scores:
            if accepted > rejected:
                total += Decimal(1)
            elif accepted == rejected:
                total += Decimal("0.5")
    pairs = Decimal(len(accept_scores)) * Decimal(len(reject_scores))
    return _common.round_half_up(total / pairs, decimals), None


def _numeric(value):
    """The value as a Decimal when it is a usable score, else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    return None


def _country_sort_key(entry):
    country = entry.get("country")
    unknown = 1 if (not isinstance(country, str) or country == _common.UNKNOWN) else 0
    return (unknown, str(country))


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def _build_report(population, reviews, as_of, min_sample, calibration, notes):
    decimals = int(calibration.get("rate_decimals", 4))

    returned = []
    for record in population.returned:
        row = dict(record)
        review = reviews.get(record["record_id"])
        row["verdict"] = review["verdict"] if review else None
        row["reason_code"] = review["reason_code"] if review else None
        returned.append(row)

    excluded = []
    for record in population.excluded:
        row = dict(record)
        review = reviews.get(record["record_id"])
        row["verdict"] = review["verdict"] if review else None
        row["reason_code"] = review["reason_code"] if review else None
        excluded.append(row)

    counts = _verdict_counts(returned)
    reviewed = counts["accept"] + counts["reject"] + counts["unsure"]
    decided = counts["accept"] + counts["reject"]
    insufficient = decided < min_sample

    thresholds = sorted(set(t for t in population.thresholds if isinstance(t, int)))
    threshold_used = thresholds[0] if len(thresholds) == 1 else None
    modes = sorted(set(m for m in population.threshold_modes if isinstance(m, str)))
    threshold_mode = modes[0] if len(modes) == 1 else None
    if threshold_used is None and thresholds:
        notes.append(
            "the scored documents disagree on threshold_used (%s), so the report states "
            "none" % (", ".join(str(t) for t in thresholds),)
        )

    if insufficient:
        notes.append(
            "insufficient sample: %d decided review(s) (accept + reject, `unsure` "
            "excluded) is below the configured minimum of %d, so this report CANNOT "
            "justify a weight or threshold change. Read it as a direction to look in, "
            "not as evidence." % (decided, min_sample)
        )
    if not reviewed:
        notes.append("no review row carried a verdict; every rate below is null")

    summary = {
        "score_version": population.score_version,
        "as_of": as_of,
        "source_kind": population.kind,
        "entity": population.entity,
        "threshold_used": threshold_used,
        "threshold_mode": threshold_mode,
        "min_sample": min_sample,
        "returned": len(returned),
        "excluded": len(excluded),
        "reviewed": reviewed,
        "reviewed_excluded": len([r for r in excluded if r["verdict"]]),
        "review_coverage": _rate(reviewed, len(returned), decimals),
        "accept": counts["accept"],
        "reject": counts["reject"],
        "unsure": counts["unsure"],
        "human_acceptance_rate": _rate(counts["accept"], decided, decimals),
        "insufficient_sample": insufficient,
    }

    # "Unknown is not false" is the package's headline rule (BUILD-CONTRACT 3.2), and
    # folding an absent or "unknown" qualified flag into qualified_false would report a
    # record the rubric never judged as one the rubric rejected - inventing a negative
    # out of a gap, in the one document whose job is to measure the flag's precision.
    by_qualified = {
        "qualified_true": _group_block(
            [r for r in returned if r.get("qualified") is True], decimals
        ),
        "qualified_false": _group_block(
            [r for r in returned if r.get("qualified") is False], decimals
        ),
        "qualified_unknown": _group_block(
            [r for r in returned
             if r.get("qualified") is not True and r.get("qualified") is not False],
            decimals,
        ),
    }

    by_score_band = []
    for pair in calibration.get("score_bands") or []:
        low, high = int(pair[0]), int(pair[1])
        rows = []
        for record in returned:
            score = _numeric(record.get("score"))
            if score is not None and Decimal(low) <= score <= Decimal(high):
                rows.append(record)
        block = {"band": "%d-%d" % (low, high), "min": low, "max": high}
        block.update(_group_block(rows, decimals))
        by_score_band.append(block)

    sweep_config = calibration.get("threshold_sweep") or {}
    start = int(sweep_config.get("start", 50))
    stop = int(sweep_config.get("stop", 90))
    step = int(sweep_config.get("step", 5)) or 1
    # RECALL DENOMINATOR: every record an operator accepted, returned or excluded.
    # An accepted record the rubric put in excluded[] is a lead the run lost; it is at
    # or above no threshold, so counting it here is what makes recall mean "the share
    # of everything worth contacting that this cut-off would have kept". Leaving it out
    # measured recall against the run's own output and could never fall below 1.0 at
    # the bottom of the sweep.
    total_accept = counts["accept"] + len(
        [r for r in excluded if r.get("verdict") == "accept"]
    )
    threshold_sweep = []
    for value in range(start, stop + 1, step):
        at_or_above = [
            r
            for r in returned
            if _numeric(r.get("score")) is not None
            and _numeric(r.get("score")) >= Decimal(value)
        ]
        above_counts = _verdict_counts(at_or_above)
        threshold_sweep.append(
            {
                "threshold": value,
                "n_at_or_above": len(at_or_above),
                "reviewed_at_or_above": above_counts["accept"]
                + above_counts["reject"]
                + above_counts["unsure"],
                "accept": above_counts["accept"],
                "reject": above_counts["reject"],
                "precision": _rate(
                    above_counts["accept"],
                    above_counts["accept"] + above_counts["reject"],
                    decimals,
                ),
                "recall": _rate(above_counts["accept"], total_accept, decimals),
            }
        )

    # A sweep row BELOW the cut-off the run itself applied is only measurable when the
    # run returned its below-threshold records. score_match.py does not: it keeps only
    # `match_score >= threshold` in results[], so those rows describe the surviving
    # population rather than what a lower cut-off would have returned. score_buyer.py
    # and score_seller.py do return them - `qualified` is a flag there, not a filter -
    # so a discovery report's sweep is measurable across the whole range and gets no
    # such note.
    if (
        population.kind == "match-result"
        and threshold_used is not None
        and start < threshold_used
    ):
        notes.append(
            "threshold_sweep rows below %d cannot be measured from this run: a match "
            "run keeps only candidates at or above its threshold in results[], so no "
            "record below %d was ever available to review. Re-run score_match.py with "
            "a lower --threshold to measure them."
            % (threshold_used, threshold_used)
        )

    accepted = [r for r in returned if r.get("verdict") == "accept"]
    rejected = [r for r in returned if r.get("verdict") == "reject"]

    def _scores(rows, extractor):
        out = []
        for row in rows:
            value = _numeric(extractor(row))
            if value is not None:
                out.append(value)
        return out

    overall_value, overall_reason = _auc(
        _scores(accepted, lambda r: r.get("score")),
        _scores(rejected, lambda r: r.get("score")),
        decimals,
    )
    distinct_overall = len(
        set(
            str(_numeric(r.get("score")))
            for r in returned
            if _numeric(r.get("score")) is not None
        )
    )
    by_dimension = []
    for key in population.dimension_keys:
        value, reason = _auc(
            _scores(accepted, lambda r, k=key: (r.get("dimensions") or {}).get(k)),
            _scores(rejected, lambda r, k=key: (r.get("dimensions") or {}).get(k)),
            decimals,
        )
        # calibration-notes section 3 found the two heaviest buyer dimensions resolving
        # to 2 and 3 distinct values across 20 real companies. A dimension that takes one
        # value cannot separate anything, whatever its weight, so the count is reported
        # beside the AUC rather than left for a reader to infer from a 0.5.
        distinct = len(
            set(
                str(_numeric((r.get("dimensions") or {}).get(key)))
                for r in returned
                if _numeric((r.get("dimensions") or {}).get(key)) is not None
            )
        )
        by_dimension.append(
            {
                "dimension": key,
                "auc": value,
                "reason": reason,
                "distinct_values": distinct,
            }
        )

    discrimination = {
        "metric": "P(score of an accepted record > score of a rejected one), ties 0.5",
        "n_accept": len(accepted),
        "n_reject": len(rejected),
        "overall": {
            "auc": overall_value,
            "reason": overall_reason,
            "distinct_values": distinct_overall,
        },
        "by_dimension": by_dimension,
    }

    countries = {}
    for record in returned:
        country = record.get("country")
        key = country if isinstance(country, str) and country else _common.UNKNOWN
        countries.setdefault(key, []).append(record)
    by_country = []
    for key in sorted(countries):
        block = {"country": key}
        block.update(_group_block(countries[key], decimals))
        by_country.append(block)
    by_country.sort(key=_country_sort_key)

    by_reason_code = []
    for code in _common.REVIEW_REASON_CODES:
        in_returned = len([r for r in returned if r.get("reason_code") == code])
        in_excluded = len([r for r in excluded if r.get("reason_code") == code])
        by_reason_code.append(
            {
                "reason_code": code,
                "n": in_returned + in_excluded,
                "returned": in_returned,
                "excluded": in_excluded,
            }
        )

    false_exclusions = []
    for record in excluded:
        if record.get("verdict") != "accept":
            continue
        false_exclusions.append(
            {
                "record_id": record["record_id"],
                "company_name": record.get("company_name"),
                "exclusion_reason": record.get("exclusion_reason"),
                "failed_rule_ids": list(record.get("failed_rule_ids") or []),
                "reason_code": record.get("reason_code"),
            }
        )
    false_exclusions.sort(key=lambda entry: entry["record_id"])

    if not returned:
        notes.append(
            "no scored document returned a ranked record (a no_match match run, or a "
            "discovery run that excluded every candidate): every acceptance rate and "
            "every AUC below is null, and this report measures false exclusions only"
        )

    notes.append(
        "this report measures PRD 17 Human Acceptance Rate only. RFQ Conversion needs "
        "outcome data from the application layer after real outreach and is not "
        "derivable from any document this package produces."
    )

    return {
        "report_kind": REPORT_KIND,
        "schema_version": _common.SCHEMA_VERSION,
        "score_version": population.score_version,
        "skill_version": _common.SKILL_VERSION,
        "as_of": as_of,
        "summary": summary,
        "by_qualified": by_qualified,
        "by_score_band": by_score_band,
        "threshold_sweep": threshold_sweep,
        "discrimination": discrimination,
        "by_country": by_country,
        "by_reason_code": by_reason_code,
        "false_exclusions": false_exclusions,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Join filled review sheets to scored runs and measure Human Acceptance Rate. "
            "Korean gloss: 검수 시트와 점수 결과를 결합해 사람 수용률을 측정한다."
        ),
    )
    parser.add_argument(
        "--scored", action="append", default=None, metavar="PATH",
        help="A scored discovery-result or match-result document (repeatable)",
    )
    parser.add_argument(
        "--reviews", action="append", default=None, metavar="PATH",
        help="A filled review sheet CSV (repeatable)",
    )
    parser.add_argument("-o", "--output", default=None, help="Report path (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent=2 report")
    parser.add_argument("--as-of", dest="as_of", default=None, help="YYYY-MM-DD report date")
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--no-validate", dest="validate", action="store_false", default=True)
    parser.add_argument("--quiet", action="store_true", help="Suppress the stderr summary")
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--min-sample", dest="min_sample", type=int, default=None,
        help="Override calibration.min_sample from the scoring config",
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


def _run(args, config):
    if not args.scored:
        raise _common.UsageError("--scored is required (repeat it for several runs)")
    if not args.reviews:
        raise _common.UsageError("--reviews is required (repeat it for several sheets)")

    calibration = _common.calibration_config(config)
    min_sample = args.min_sample if args.min_sample is not None else int(
        calibration.get("min_sample", 0)
    )
    if min_sample < 0:
        raise _common.UsageError("--min-sample must not be negative")

    population = _load_population(args.scored)

    as_of = args.as_of
    if as_of is not None:
        _common.resolve_as_of([], as_of)  # rejects a malformed --as-of (exit 2)
    else:
        stamps = sorted(
            set(
                str(value)
                for value in [
                    _read_json_file(path).get("as_of") for path in args.scored
                ]
                if isinstance(value, str) and _DATE_RE.match(value)
            )
        )
        if not stamps:
            raise _common.UsageError(
                "no --as-of given and no scored document carries a usable as_of"
            )
        as_of = stamps[-1]

    if not population.returned and not population.excluded:
        raise _common.DataError(
            "the scored document(s) carry no record at all, returned or excluded; "
            "there is nothing for a review sheet to have labelled"
        )

    known_ids = set(r["record_id"] for r in population.returned)
    known_ids.update(r["record_id"] for r in population.excluded)
    reviews, notes = _read_reviews(args.reviews, known_ids, as_of)

    report = _build_report(population, reviews, as_of, min_sample, calibration, notes)

    errors = []
    if args.validate:
        try:
            schema = _common.load_schema(REPORT_KIND, args.schema_dir)
        except Exception as exc:
            errors.append("cannot load %s schema: %s" % (REPORT_KIND, exc))
        else:
            errors.extend(_common.validate(report, schema))

    if errors:
        # Nothing is written. A scorer emits its document even on exit 1 (R7.3.2) so an
        # operator can see WHICH record broke; a calibration report has no such use -
        # it is a single aggregate that is either trustworthy or not, and a file on
        # disk that failed its own schema is the one thing likely to be quoted anyway.
        _common.die("report failed validation (%d problems); nothing was written"
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
        _common.write_output(report, args.pretty, stream)
    finally:
        if handle is not None:
            handle.close()

    summary = report["summary"]
    _common.eprint(
        "reviewed %d of %d returned record(s); accept %d / reject %d / unsure %d; "
        "human_acceptance_rate %s"
        % (
            summary["reviewed"],
            summary["returned"],
            summary["accept"],
            summary["reject"],
            summary["unsure"],
            summary["human_acceptance_rate"],
        ),
        quiet=args.quiet,
    )
    if summary["insufficient_sample"]:
        _common.eprint(
            "WARNING: insufficient sample (%d decided, minimum %d); this report cannot "
            "justify a weight or threshold change"
            % (summary["accept"] + summary["reject"], summary["min_sample"]),
            quiet=args.quiet,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
