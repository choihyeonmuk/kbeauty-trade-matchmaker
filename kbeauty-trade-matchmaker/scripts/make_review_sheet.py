#!/usr/bin/env python3
"""make_review_sheet.py - turn a scored run into a CSV an operator fills in.

Half of the labelled-set protocol of references/calibration-notes.md section 7. It
reads one `discovery-result` or `match-result` document and writes the review sheet
whose filled-in form `scripts/acceptance_report.py` reads back, so PRD 17's
**Human Acceptance Rate** can be measured against the score the rubric gave.

Korean gloss: 점수화된 결과를 사람이 채점할 CSV 검수 시트로 변환한다.

BLIND BY DEFAULT. The sheet carries no score, no rank, no `qualified` flag and no
included-versus-excluded marker, and the rows are ordered by the sha256 digest of the
record id rather than by rank. That is the whole point of the exercise: a reviewer who
can see the rubric's answer tends to agree with it, and a label that only confirms the
score measures nothing. `--no-blind` restores rank order and appends `rank,score,
qualified` for the cases where an operator is auditing a specific ranking rather than
producing a labelled set.

Build rules honoured here (BUILD-CONTRACT.md 1.5, 7.11): stdlib only, Python 3.9
syntax, no network, no wall-clock read (the only time source is --as-of), nothing at
import time, CSV to stdout and diagnostics to stderr, byte-stable output for the same
input. All code comments are English.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "make_review_sheet.py"


# --------------------------------------------------------------------------
# document shapes
# --------------------------------------------------------------------------


def _document_kind(document):
    """"discovery-result" or "match-result"; a UsageError for anything else."""
    if not isinstance(document, dict):
        raise _common.UsageError(
            "input must be a discovery-result or match-result document object"
        )
    if "match_run_id" in document or "no_match" in document:
        return "match-result"
    if "entity" in document and isinstance(document.get("records"), list):
        return "discovery-result"
    raise _common.UsageError(
        "input is neither a discovery-result (entity + records[]) nor a match-result "
        "(match_run_id / no_match) document"
    )


def _entity_type(document, kind):
    if kind == "match-result":
        return "seller"
    entity = document.get("entity")
    if entity not in ("buyer", "seller"):
        raise _common.DataError(
            "discovery-result entity is %r; expected \"buyer\" or \"seller\"" % (entity,)
        )
    return entity


def _cell(value):
    """One sheet cell. An absent or unknown value is the literal word, never blank.

    A blank cell in a review sheet means "the operator has not answered yet"; writing a
    missing country as blank would make the two indistinguishable (INV-02).
    """
    if _common.is_unknown(value):
        return _common.REVIEW_UNKNOWN_CELL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return _common.REVIEW_UNKNOWN_CELL


def _rows_from_document(document, kind, entity, include_excluded):
    """[(record_id, row-dict)] for every record the sheet should carry.

    `rank`, `score` and `qualified` are collected for every row even in blind mode;
    _write_sheet decides whether they are emitted. That keeps the two modes reading the
    same document once, so a blind and a non-blind sheet can never disagree about a
    record.
    """
    rows = []
    if kind == "match-result":
        id_key, name_key, score_key = "seller_id", "seller_name", "match_score"
        returned = document.get("results")
    else:
        id_key = "%s_id" % entity
        name_key, score_key = "company_name", "qualification_score"
        returned = document.get("records")
    if not isinstance(returned, list):
        raise _common.DataError("input carries no scored record list")

    for position, record in enumerate(returned, 1):
        if not isinstance(record, dict):
            raise _common.DataError("scored record %d is not a JSON object" % position)
        record_id = record.get(id_key)
        if _common.is_unknown(record_id):
            raise _common.DataError("scored record %d carries no %s" % (position, id_key))
        rank = record.get("rank")
        rows.append(
            (
                str(record_id),
                {
                    "record_id": str(record_id),
                    "entity_type": entity,
                    "company_name": _cell(record.get(name_key)),
                    "website": _cell(record.get("website")),
                    "country": _cell(record.get("country")),
                    "rank": _cell(rank if not _common.is_unknown(rank) else position),
                    "score": _cell(record.get(score_key)),
                    "qualified": _cell(record.get("qualified")),
                    # Never written to the sheet; _write_sheet emits named columns only.
                    # _neutralise_partitioning_columns needs it to tell the two
                    # populations apart while it is making sure nothing else can.
                    "_origin": "returned",
                },
            )
        )

    if not include_excluded:
        return rows

    # An excluded record is listed so the sheet can measure FALSE EXCLUSIONS - the
    # candidates the rubric threw away that an operator would have contacted. In blind
    # mode it is indistinguishable from any other row, which is what makes the answer
    # worth having.
    excluded_id_key = "seller_id" if kind == "match-result" else "id"
    excluded_name_key = "seller_name" if kind == "match-result" else "company_name"
    for position, record in enumerate(document.get("excluded") or [], 1):
        if not isinstance(record, dict):
            raise _common.DataError("excluded record %d is not a JSON object" % position)
        record_id = record.get(excluded_id_key)
        if _common.is_unknown(record_id):
            raise _common.DataError(
                "excluded record %d carries no %s" % (position, excluded_id_key)
            )
        rows.append(
            (
                str(record_id),
                {
                    "record_id": str(record_id),
                    "entity_type": entity,
                    "company_name": _cell(record.get(excluded_name_key)),
                    "website": _cell(record.get("website")),
                    "country": _cell(record.get("country")),
                    # An excluded record was never ranked and carries no qualified flag
                    # (INV-32), so these stay the literal "unknown" rather than 0/false.
                    "rank": _common.REVIEW_UNKNOWN_CELL,
                    "score": _cell(record.get("qualification_score")),
                    "qualified": _common.REVIEW_UNKNOWN_CELL,
                    "_origin": "excluded",
                },
            )
        )
    return rows


def _order(rows, blind):
    """Blind: sha256(record_id) ascending. Non-blind: document order, unchanged."""
    if not blind:
        return list(rows)
    # The record id is the tie-break, so two ids with the same digest (which cannot
    # happen in practice) still order deterministically rather than by input position.
    return sorted(rows, key=lambda item: (_common.blind_order_key(item[0]), item[0]))


#: Columns copied from the scored document. `record_id` and `entity_type` are excluded:
#: the first is the join key and is never rewritten, the second is one value for the
#: whole document and so cannot separate anything.
DISPLAY_COLUMNS = ("company_name", "website", "country")


def _cell_shape(value):
    """How a cell READS, independent of what it says: filled, or a stand-in."""
    if value in ("", None):
        return "empty"
    if value == _common.REVIEW_UNKNOWN_CELL:
        return "unknown"
    if value == _common.REVIEW_NEUTRAL_CELL:
        return "not_shown"
    return "filled"


def _neutralise_partitioning_columns(rows, kind):
    """Blank any display column that would tell an excluded row from a returned one.

    A blind sheet hides the score, the rank and the qualified flag, but that is not
    enough on its own. `discovery-result.excluded[]` has no `country` field at all - the
    schema forbids it - so under `--include-excluded` the excluded rows were exactly the
    rows whose country cell read "unknown", and a reviewer who noticed would know which
    candidates the rubric had already thrown away. That is the anchoring the blind sheet
    exists to prevent.

    Two rules, both applied:

      * STRUCTURAL - a column the excluded shape cannot carry at all is neutralised
        whatever this particular document happens to contain, so a future run with
        different data cannot re-open the hole.
      * OBSERVED - a column whose cell SHAPES do not overlap between the two
        populations (every excluded row unknown and every returned row filled, or the
        reverse) is neutralised as well. This catches the data-dependent cases, such as
        a run in which no excluded record happens to carry a website.

    Neutralising writes REVIEW_NEUTRAL_CELL into that column on EVERY row, returned ones
    included: blanking it only on the excluded rows would be the same tell inverted.
    Returns the sorted list of neutralised column names so the caller can say so on
    stderr - a column the reviewer cannot see must not be a column they are not told
    about.
    """
    returned = [row for _rid, row in rows if row.get("_origin") == "returned"]
    excluded = [row for _rid, row in rows if row.get("_origin") == "excluded"]
    if not returned or not excluded:
        return []

    neutralised = set()
    if kind == "discovery-result":
        # discovery-result.schema.json $defs/discovery_excluded carries no `country`.
        neutralised.add("country")
    for column in DISPLAY_COLUMNS:
        shapes_returned = set(_cell_shape(row.get(column)) for row in returned)
        shapes_excluded = set(_cell_shape(row.get(column)) for row in excluded)
        if not (shapes_returned & shapes_excluded):
            neutralised.add(column)

    for column in neutralised:
        for _rid, row in rows:
            row[column] = _common.REVIEW_NEUTRAL_CELL
    return sorted(neutralised)


def _write_sheet(rows, blind, stream):
    columns = list(_common.REVIEW_SHEET_COLUMNS)
    if not blind:
        columns.extend(_common.REVIEW_UNBLINDED_COLUMNS)
    # lineterminator is pinned to LF: BUILD-CONTRACT 1.5 requires LF in every text file
    # this package writes, and the csv default of CRLF would make the golden fixture
    # platform-dependent.
    writer = csv.DictWriter(
        stream, fieldnames=columns, lineterminator="\n", extrasaction="ignore"
    )
    writer.writeheader()
    for record_id, row in rows:
        # The join key is never rewritten: acceptance_report.py matches on it exactly,
        # and an apostrophe in front would silently break every row of the report. An id
        # that WOULD need the guard is refused instead - ids are script-generated
        # `BUY-` / `SEL-` stems, so one that starts with a formula lead means the input
        # document is not what it claims to be.
        if _common.csv_needs_formula_guard(record_id):
            raise _common.DataError(
                "record_id %r starts with a spreadsheet formula character; it cannot be "
                "escaped without breaking the join key, so the sheet is refused" % (record_id,)
            )
        out = dict((column, "") for column in columns)
        for column in columns:
            if column in _common.REVIEW_OPERATOR_COLUMNS:
                continue  # the operator's five columns ship empty
            value = row.get(column, "")
            out[column] = value if column == "record_id" else _common.csv_safe_cell(value)
        writer.writerow(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Turn a scored discovery-result or match-result document into a blind CSV "
            "review sheet. Korean gloss: 점수화 결과를 블라인드 검수 시트로 만든다."
        ),
    )
    parser.add_argument("-i", "--input", default=None, help="Scored document (default: stdin)")
    parser.add_argument("-o", "--output", default=None, help="CSV path (default: stdout)")
    parser.add_argument("--as-of", dest="as_of", default=None, help="YYYY-MM-DD run date")
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--quiet", action="store_true", help="Suppress the stderr summary")
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--blind", dest="blind", action="store_true", default=True,
        help="Hide rank, score and qualified and shuffle the rows (the default)",
    )
    parser.add_argument(
        "--no-blind", dest="blind", action="store_false",
        help="Keep rank order and append rank,score,qualified",
    )
    parser.add_argument(
        "--include-excluded", dest="include_excluded", action="store_true",
        help="Also list excluded[] records, to measure false exclusions",
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
    document = _common.read_input(args)
    kind = _document_kind(document)
    entity = _entity_type(document, kind)
    # --as-of is validated for shape even though nothing in a sheet is time-dependent,
    # so that a typo is caught here rather than in the acceptance report (7.2).
    if args.as_of:
        _common.resolve_as_of([], args.as_of)
    rows = _rows_from_document(document, kind, entity, args.include_excluded)
    neutralised = []
    if args.blind and args.include_excluded:
        # Only here: a non-blind sheet already shows rank, score and qualified, so
        # nothing is being hidden and blanking a column would only lose information;
        # without --include-excluded there are no two populations to separate.
        neutralised = _neutralise_partitioning_columns(rows, kind)
    rows = _order(rows, args.blind)

    buffer = io.StringIO()
    _write_sheet(rows, args.blind, buffer)
    text = buffer.getvalue()

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        except OSError as exc:
            return _common.die("cannot write --output: %s" % (exc,), 2)
    else:
        sys.stdout.write(text)

    _common.eprint(
        "wrote %d review row(s) from a %s document (%s%s)"
        % (
            len(rows),
            kind,
            "blind" if args.blind else "not blind",
            ", excluded included" if args.include_excluded else "",
        ),
        quiet=args.quiet,
    )
    if neutralised:
        _common.eprint(
            "blind sheet: column(s) %s hold the literal '%s' on EVERY row. An excluded "
            "record cannot fill them the way a returned one does, so leaving them as "
            "read would have marked out the excluded rows and anchored the reviewer."
            % (", ".join(neutralised), _common.REVIEW_NEUTRAL_CELL),
            quiet=args.quiet,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
