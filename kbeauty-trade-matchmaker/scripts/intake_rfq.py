#!/usr/bin/env python3
"""intake_rfq.py - turn a buyer's free-text request into an RFQ, and list what to ask back.

Mode 3 starts from an RFQ document. A real buyer sends one line in a chat window. The
agent reads that line and writes an `rfq-intake` INPUT document: the message text, and
for every RFQ field it found, the value plus the verbatim `quote` it was read from. This
script does the part that must not be left to judgement:

  * every quote must occur in the message. A field whose quote is not there is refused,
    so a value the buyer never wrote cannot enter the RFQ (exit 1, nothing written);
  * a region word ("GCC", "Europe") never becomes a country code, a number without a
    unit is held instead of being read as units, and a price without a currency is
    dropped to "unknown" - each becomes a question for the buyer instead of a guess;
  * what is still missing is listed as questions, in a fixed order, in English and Korean;
  * the RFQ it emits is validated against rfq.schema.json before anything is written.

What it does not do:

  * It reads no free text. Extraction is the agent's step; this script only checks it.
  * It scores nothing. The readiness number is score_match.py's own function, called on
    the emitted RFQ, so the figure here is the figure Mode 3 will print. No scorer reads
    this script or an rfq-intake document, so nothing here can move score_version.
  * It does not copy the message. A chat message names people and numbers; the report
    carries its length and SHA-256, plus the quotes - phrases of at most 200 characters,
    each refused if it carries an address or telephone shape (INV-31). A personal NAME
    inside a quote is not detectable here; quoting the requirement and not the greeting
    is the agent's duty.

Korean gloss: 바이어의 자유 서술 요청을 RFQ 로 옮기고, 빠졌거나 모호한 항목을 되물을 질문으로 나열한다.
메시지에 없는 값은 받지 않는다.

Build rules of BUILD-CONTRACT.md 1.5 and 7.11 apply: stdlib only, Python 3.9 syntax, no
network, no wall-clock read, nothing at import time. Comments are English.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402
import score_match  # noqa: E402

SCRIPT_NAME = "intake_rfq.py"
REPORT_KIND = "rfq-intake"

#: RFQ fields the agent may state, with the shape check each value gets. Everything
#: else on an RFQ (status, scores, evidence) is set here, never by the agent.
LIST_FIELDS = ("product_categories_extra", "product_forms", "required_certifications",
               "preferred_certifications", "required_seller_countries",
               "excluded_seller_countries")
FIELDS = ("destination_country", "destination_region", "product_category",
          "product_description", "quantity", "max_moq", "target_price", "commercial_model",
          "timeline", "max_lead_time_days") + LIST_FIELDS

COMMERCIAL_MODELS = ("branded", "private_label", "oem_odm", "either")

#: Question order. A blocking reason means no RFQ is emitted at all.
REASON_ORDER = (
    "missing_required",
    "region_not_country",
    "unit_unstated",
    "unit_mismatch",
    "moq_exceeds_quantity",
    "currency_unstated",
    "timeline_in_past",
    "unmapped_category",
    "unmapped_certification",
    "confirm_inferred",
    "missing",
)
BLOCKING_REASONS = frozenset(["missing_required"])

LABELS = {
    "destination_country": ("destination country", "도착 국가"),
    "destination_region": ("destination region", "도착 지역"),
    "product_category": ("product category", "제품 카테고리"),
    "product_categories_extra": ("other acceptable categories", "추가 허용 카테고리"),
    "product_description": ("product description", "제품 설명"),
    "product_forms": ("formulation", "제형"),
    "quantity": ("order quantity", "주문 수량"),
    "max_moq": ("highest acceptable MOQ", "수용 가능한 최대 MOQ"),
    "target_price": ("target unit price", "목표 단가"),
    "commercial_model": ("commercial model (branded / private label / OEM-ODM)",
                         "거래 방식 (브랜드 완제품 / PB / OEM·ODM)"),
    "required_certifications": ("required certifications", "필수 인증"),
    "preferred_certifications": ("preferred certifications", "선호 인증"),
    "timeline": ("delivery deadline", "납기"),
    "max_lead_time_days": ("longest acceptable lead time", "수용 가능한 최대 리드타임"),
    "required_seller_countries": ("required seller countries", "셀러 국가 조건"),
    "excluded_seller_countries": ("excluded seller countries", "제외할 셀러 국가"),
    "buyer_id": ("buyer record", "바이어 레코드"),
}

#: The Korean lines put the label before a colon and the quote after one, so no particle
#: ever has to agree with a word the template cannot see.
TEMPLATES = {
    "missing_required": (
        "Which product do you need? Without a {label} the request cannot be matched.",
        "어떤 제품이 필요하신가요? {label} 없이는 매칭할 수 없습니다."),
    "region_not_country": (
        "You wrote \"{quote}\". Which country will the goods be delivered to?",
        "이렇게 쓰셨습니다: \"{quote}\". 물품이 실제로 도착할 국가는 어디인가요?"),
    "unit_unstated": (
        "You wrote \"{quote}\" for the {label}. Is that a number of pieces, sets, or an amount of money?",
        "{label} 항목에 이렇게 쓰셨습니다: \"{quote}\". 개수인가요, 세트 수인가요, 금액인가요?"),
    "unit_mismatch": (
        "The order quantity is in {a} and the MOQ ceiling is in {b}. Which unit should both use?",
        "주문 수량 단위: {a}. 최대 MOQ 단위: {b}. 어느 단위로 통일할까요?"),
    "moq_exceeds_quantity": (
        "The MOQ ceiling ({moq}) is higher than the order quantity ({quantity}). Which is right?",
        "최대 MOQ: {moq}. 주문 수량: {quantity}. MOQ 쪽이 더 큰데, 어느 쪽이 맞나요?"),
    "currency_unstated": (
        "You wrote \"{quote}\" for the {label}. In which currency?",
        "{label} 항목에 이렇게 쓰셨습니다: \"{quote}\". 어느 통화 기준인가요?"),
    "timeline_in_past": (
        "The {label} reads {value}, which is before {as_of}. What is the date you need?",
        "{label}: {value}. 기준일 {as_of} 이전 날짜입니다. 필요한 날짜가 언제인가요?"),
    "unmapped_category": (
        "\"{value}\" is not a category this package knows. Which standard category is closest?",
        "\"{value}\": 이 패키지가 아는 카테고리가 아닙니다. 가장 가까운 표준 카테고리는 무엇인가요?"),
    "unmapped_certification": (
        "\"{value}\" is not a certification this package can check. What is its official name?",
        "\"{value}\": 이 패키지가 대조할 수 있는 인증이 아닙니다. 정식 명칭이 무엇인가요?"),
    "confirm_inferred": (
        "From \"{quote}\" we read the {label} as {value}. Is that right?",
        "\"{quote}\" → {label}: {value}. 이렇게 읽었는데 맞나요?"),
    "missing": (
        "Please tell us the {label}.",
        "{label}: 알려 주세요."),
}

_ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_CERT_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
_RFQ_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:#-]{0,127}$")

#: A quote is a phrase, not the message: long enough to be the buyer's words, short enough
#: that a question or a note quoting it stays inside the report schema's own limits.
QUOTE_MIN, QUOTE_MAX = 2, 200
REGION_MAX = 80
#: Largest amount accepted. Above 2**53 a JSON consumer reading doubles loses digits.
NUMBER_MAX = 10 ** 12
#: Number words that scale the digits beside them ("5천", "5k", "5 ribu"). Beside one of
#: these the value's trailing zeros are not expected in the quote.
_MULTIPLIER_RE = re.compile(
    r"[천만억백]|\d\s*k\b|thousand|million|lakh|crore|ribu|juta|\bbin\b|milyon", re.IGNORECASE)
_NUMERIC_FIELDS = ("quantity", "max_moq", "target_price", "max_lead_time_days")


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Check an agent-written rfq-intake input against the buyer's message, emit the "
            "RFQ and the questions to ask back. A value whose quote is not in the message "
            "is refused. Korean gloss: 메시지에 없는 값은 받지 않고, 빠진 항목은 질문으로 돌려준다."
        ),
    )
    parser.add_argument("-i", "--input", default=None, help="rfq-intake input JSON (default: stdin)")
    parser.add_argument("-o", "--output", default=None, help="Report path (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent=2 output")
    parser.add_argument(
        "--as-of", dest="as_of", default=None,
        help="YYYY-MM-DD intake date (required; the clock is never read)",
    )
    parser.add_argument("--config", default=None, help="Alternative scoring config")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None)
    parser.add_argument("--no-validate", dest="validate", action="store_false", default=True)
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
# Quotes
# --------------------------------------------------------------------------


def _fold(text):
    """Comparison form of message and quote: NFKC, casefold, whitespace runs to one space."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _quotes(entry, field):
    raw = entry.get("quote")
    quotes = raw if isinstance(raw, list) else [raw]
    if not quotes or not all(isinstance(q, str) and q.strip() for q in quotes):
        raise _common.DataError(
            "fields.%s needs a non-empty `quote`, the words of the message it was read from" % field)
    for quote in quotes:
        if not QUOTE_MIN <= len(_fold(quote)) <= QUOTE_MAX:
            raise _common.DataError(
                "fields.%s quote must be %d..%d characters: a phrase, not a letter and not the "
                "whole message" % (field, QUOTE_MIN, QUOTE_MAX))
    return quotes


def _cuts_a_word(folded_message, folded_quote):
    """True when every occurrence starts or ends inside an ASCII word ("on" in "toner").
    Only ASCII is tested: Korean attaches particles to a word, so 인도네시아 in 인도네시아에
    is an honest quote."""
    def ascii_word(ch):
        return ch.isascii() and ch.isalnum()
    start = folded_message.find(folded_quote)
    while start != -1:
        end = start + len(folded_quote)
        before = folded_message[start - 1] if start else " "
        after = folded_message[end] if end < len(folded_message) else " "
        if not (ascii_word(before) and ascii_word(folded_quote[0])) \
                and not (ascii_word(after) and ascii_word(folded_quote[-1])):
            return False
        start = folded_message.find(folded_quote, start + 1)
    return True


def _digits(text):
    return re.sub(r"[^0-9]", "", unicodedata.normalize("NFKC", text))


def _numbers_of(value):
    if isinstance(value, dict):
        return [value[k] for k in ("min", "max") if isinstance(value.get(k), (int, float))
                and not isinstance(value.get(k), bool)]
    return [value] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


def _number_in_quotes(number, quotes):
    """The digits of a stated number must be in its own quote: 50000 is not "5,000 pcs".
    Beside a number word ("5천", "5k") the trailing zeros are not expected."""
    text = ("%f" % number).rstrip("0").rstrip(".") if isinstance(number, float) else str(number)
    wanted = _digits(text)
    for quote in quotes:
        have = _digits(quote)
        if wanted and wanted in have:
            return True
        if _MULTIPLIER_RE.search(unicodedata.normalize("NFKC", quote)) and wanted.rstrip("0") \
                and wanted.rstrip("0") in have:
            return True
    return False


def _check_quotes(fields, message_text):
    folded = _fold(message_text)
    absent = []
    for field in FIELDS:
        entry = fields.get(field)
        if entry is None:
            continue
        quotes = _quotes(entry, field)
        for quote in quotes:
            if _fold(quote) not in folded or _cuts_a_word(folded, _fold(quote)):
                absent.append("fields.%s quote %r" % (field, quote))
            hits = _common.personal_data_hits(quote)
            if hits:
                raise _common.DataError(
                    "fields.%s quote carries %s; quote the requirement, not the contact (INV-31)"
                    % (field, ", ".join(hits)))
        # A value the agent worked out ("6 weeks" -> 42 days) is shown back to the buyer by
        # confirm_inferred instead, so only a literally stated number is held to its quote.
        if field in _NUMERIC_FIELDS and entry.get("inferred") is not True:
            for number in _numbers_of(entry.get("value")):
                if _is_amount(number) and not _number_in_quotes(number, quotes):
                    raise _common.DataError(
                        "fields.%s value %s is not the number in its quote %r; state what the "
                        "buyer wrote, or mark it inferred so it is confirmed" % (field, number, quotes[0]))
    if absent:
        raise _common.DataError(
            "not in the message, so not accepted: %s. A field is read from the buyer's own "
            "words or it stays unknown" % "; ".join(absent))


# --------------------------------------------------------------------------
# Field readers. Each returns the RFQ value, or raises DataError on a bad shape.
# --------------------------------------------------------------------------


def _is_amount(value):
    """A finite number in 0..NUMBER_MAX. The range is tested before isfinite(), which
    raises on an int too large for a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return 0 <= value <= NUMBER_MAX


def _number(value, where):
    if not _is_amount(value):
        raise _common.DataError("%s must be a number from 0 to %d, got %r" % (where, NUMBER_MAX, value))
    return value


def _quantity(value, where):
    if isinstance(value, dict):
        if set(value) - {"min", "max"} or "min" not in value or "max" not in value:
            raise _common.DataError("%s range must be exactly {min, max}" % where)
        low, high = _number(value["min"], where + ".min"), _number(value["max"], where + ".max")
        if low > high:
            raise _common.DataError("%s range has min above max" % where)
        return {"min": low, "max": high}
    return _number(value, where)


def _upper(value):
    high = value["max"] if isinstance(value, dict) else value
    return high


def _date(value, where):
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise _common.DataError("%s must be YYYY-MM-DD, got %r" % (where, value))
    _common.days_between(value, value)  # refuses an impossible calendar date
    return value


def _timeline(value):
    if isinstance(value, dict):
        if set(value) != {"start", "end"}:
            raise _common.DataError("fields.timeline window must be exactly {start, end}")
        start, end = _date(value["start"], "fields.timeline.start"), _date(value["end"], "fields.timeline.end")
        if _common.days_between(start, end) < 0:
            raise _common.DataError("fields.timeline window ends before it starts")
        return {"start": start, "end": end}
    return _date(value, "fields.timeline")


def _countries(values, where):
    out = []
    for raw in values:
        code = _common.normalize_country(raw) if isinstance(raw, str) else None
        if code is None or code == _common.UNKNOWN:
            raise _common.DataError("%s: %r is not a country" % (where, raw))
        if code not in out:
            out.append(code)
    return out


def _list(entry, field):
    value = entry.get("value")
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise _common.DataError("fields.%s value must be a list of strings" % field)
    # Only "no certification is required" is a statement a buyer makes with an empty list.
    if not value and field != "required_certifications":
        raise _common.DataError("fields.%s is empty; leave the field out instead" % field)
    return value


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


class _Questions(object):
    def __init__(self):
        self.rows = []

    def ask(self, reason, field, **values):
        label_en, label_ko = LABELS[field]
        english, korean = TEMPLATES[reason]
        self.rows.append({
            "reason": reason,
            "field": field,
            "blocking": reason in BLOCKING_REASONS,
            "text_en": english.format(label=label_en, **values),
            "text_ko": korean.format(label=label_ko, **values),
        })

    def ordered(self):
        field_rank = dict((name, i) for i, name in enumerate(
            FIELDS + tuple(f for f in score_match._READINESS_FIELDS if f not in FIELDS)))
        rows = sorted(self.rows, key=lambda r: (REASON_ORDER.index(r["reason"]),
                                                field_rank[r["field"]], r["text_en"]))
        for index, row in enumerate(rows):
            row["question_id"] = "Q-%02d" % (index + 1)
        return [dict((k, row[k]) for k in ("question_id", "reason", "field", "blocking",
                                           "text_en", "text_ko")) for row in rows]


def _first_quote(entry):
    raw = entry["quote"]
    return raw[0] if isinstance(raw, list) else raw


def _amount(fields, field, questions, notes):
    """(value, unit) for quantity / max_moq; a number with no stated unit is held."""
    entry = fields.get(field)
    if entry is None:
        return None, None
    where = "fields.%s.value" % field
    value = _quantity(entry.get("value"), where) if field == "quantity" else _number(entry.get("value"), where)
    unit = entry.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise _common.DataError("fields.%s.unit must be a string" % field)
    # normalize_unit reads "unknown" and an empty token as "units", the scorer's default.
    # At intake that would be the guess this script exists to refuse.
    if unit is None or _common.is_unknown(unit) or not re.sub(r"[\s.]+", "", unicodedata.normalize("NFKC", unit)):
        questions.ask("unit_unstated", field, quote=_first_quote(entry))
        notes.append("%s was written as \"%s\" with no unit; held until the buyer states one"
                     % (field, _first_quote(entry)))
        return None, None
    canonical = _common.normalize_unit(unit)
    if canonical not in set(_common.UNIT_SYNONYMS.values()) and not re.match(r"^[a-z]{3}$", canonical):
        raise _common.DataError(
            "fields.%s.unit %r is neither a unit this package knows nor a currency code" % (field, unit))
    return value, canonical


def _build(doc, as_of):
    fields = doc["fields"]
    questions, notes, rfq_notes = _Questions(), [], []
    rfq = {}
    provenance = {}

    for field in FIELDS:
        entry = fields.get(field)
        if entry is None:
            continue
        provenance[field] = {"quote": entry["quote"], "inferred": entry.get("inferred") is True}

    def inferred(field, shown):
        if fields[field].get("inferred") is True:
            questions.ask("confirm_inferred", field, quote=_first_quote(fields[field]), value=shown)

    # Destination. A region word is recorded and asked about; it never fills the country.
    if "destination_country" in fields:
        raw = fields["destination_country"].get("value")
        code = _common.normalize_country(raw) if isinstance(raw, str) else None
        if code is None or code == _common.UNKNOWN:
            raise _common.DataError(
                "fields.destination_country: %r is not a country. A region word goes in "
                "fields.destination_region and is asked about, never expanded" % (raw,))
        rfq["destination_country"] = code
        inferred("destination_country", code)
    else:
        rfq["destination_country"] = _common.UNKNOWN
    if "destination_region" in fields:
        region = fields["destination_region"].get("value")
        if not isinstance(region, str) or not region.strip() or len(region) > REGION_MAX:
            raise _common.DataError("fields.destination_region value must be 1..%d characters" % REGION_MAX)
        if not any(_fold(region) in _fold(q) for q in _quotes(fields["destination_region"], "destination_region")):
            raise _common.DataError("fields.destination_region value must be the region word inside its own quote")
        if _common.personal_data_hits(region):
            raise _common.DataError("fields.destination_region carries %s (INV-31)"
                                    % ", ".join(_common.personal_data_hits(region)))
        notes.append("buyer named the region \"%s\"; not expanded into countries" % region.strip())
        if rfq["destination_country"] == _common.UNKNOWN:
            questions.ask("region_not_country", "destination_country",
                          quote=_first_quote(fields["destination_region"]))
    elif rfq["destination_country"] == _common.UNKNOWN:
        questions.ask("missing", "destination_country")

    # Category. The one field without which no RFQ is emitted.
    if "product_category" in fields:
        raw = fields["product_category"].get("value")
        slug = _common.normalize_category(raw) if isinstance(raw, str) else None
        if not slug or not _SLUG_RE.match(slug):
            raise _common.DataError("fields.product_category: %r is not a category slug" % (raw,))
        rfq["product_category"] = slug
        if not _common.is_known_category(slug):
            questions.ask("unmapped_category", "product_category", value=slug)
        inferred("product_category", slug)
    else:
        questions.ask("missing_required", "product_category")

    for field in ("product_categories_extra", "product_forms"):
        if field in fields:
            slugs = []
            for raw in _list(fields[field], field):
                slug = _common.normalize_category(raw) if field == "product_categories_extra" else raw
                if not slug or not _SLUG_RE.match(slug):
                    raise _common.DataError("fields.%s: %r is not a slug" % (field, raw))
                if slug not in slugs:
                    slugs.append(slug)
            rfq[field] = slugs
            inferred(field, ", ".join(slugs))

    if "product_description" in fields:
        text = fields["product_description"].get("value")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise _common.DataError("fields.product_description value must be 1..4000 characters")
        hits = _common.personal_data_hits(text)
        if hits:
            raise _common.DataError("fields.product_description carries %s (INV-31)" % ", ".join(hits))
        rfq["product_description"] = text.strip()
    else:
        questions.ask("missing", "product_description")

    # Amounts. moq_unit denominates both, so two different units cannot both be kept.
    quantity, quantity_unit = _amount(fields, "quantity", questions, notes)
    max_moq, moq_unit = _amount(fields, "max_moq", questions, notes)
    if quantity is not None and max_moq is not None and quantity_unit != moq_unit:
        questions.ask("unit_mismatch", "quantity", a=quantity_unit, b=moq_unit)
        notes.append("quantity was stated in %s and max_moq in %s; quantity held until one unit is agreed"
                     % (quantity_unit, moq_unit))
        quantity = None
    if quantity is not None and max_moq is not None and max_moq > _upper(quantity):
        questions.ask("moq_exceeds_quantity", "max_moq", moq=_common.format_quantity(max_moq, moq_unit),
                      quantity=_common.format_quantity(quantity, quantity_unit))
    rfq["quantity"] = quantity if quantity is not None else _common.UNKNOWN
    rfq["max_moq"] = max_moq
    unit = moq_unit or quantity_unit
    if unit:
        rfq["moq_unit"] = unit
    if "quantity" not in fields:
        questions.ask("missing", "quantity")
    else:
        if quantity is not None:
            inferred("quantity", _common.format_quantity(quantity, quantity_unit))
    if "max_moq" not in fields:
        questions.ask("missing", "max_moq")
    elif max_moq is not None:
        inferred("max_moq", _common.format_quantity(max_moq, moq_unit))

    # Price. Never compared across currencies, so a bare number is not a price yet.
    if "target_price" in fields:
        value = fields["target_price"].get("value")
        if not isinstance(value, dict) or set(value) - {"min", "max", "currency"} \
                or not (("min" in value) or ("max" in value)):
            raise _common.DataError("fields.target_price value must be {min and/or max, currency}")
        price = dict((k, _number(value[k], "fields.target_price.%s" % k)) for k in ("min", "max") if k in value)
        if "min" in price and "max" in price and price["min"] > price["max"]:
            raise _common.DataError("fields.target_price range has min above max")
        currency = value.get("currency")
        if isinstance(currency, str) and _CURRENCY_RE.match(currency.strip().upper()):
            price["currency"] = currency.strip().upper()
            rfq["target_price"] = price
            inferred("target_price", "%s %s" % (price["currency"], _common.format_quantity(
                price if "min" in price and "max" in price else price.get("max", price.get("min")))))
        else:
            rfq["target_price"] = _common.UNKNOWN
            questions.ask("currency_unstated", "target_price", quote=_first_quote(fields["target_price"]))
            notes.append("target price was written as \"%s\" with no currency; held"
                         % _first_quote(fields["target_price"]))
    else:
        rfq["target_price"] = _common.UNKNOWN
        questions.ask("missing", "target_price")

    # Commercial model. "either" is the schema's no-constraint value, not a guess.
    if "commercial_model" in fields:
        model = fields["commercial_model"].get("value")
        if model not in COMMERCIAL_MODELS:
            raise _common.DataError("fields.commercial_model must be one of %s" % ", ".join(COMMERCIAL_MODELS))
        rfq["commercial_model"] = model
        inferred("commercial_model", model)
    else:
        rfq["commercial_model"] = "either"
        rfq_notes.append("commercial model not stated; set to \"either\", which constrains nothing")
        questions.ask("missing", "commercial_model")

    for field in ("required_certifications", "preferred_certifications"):
        if field not in fields:
            continue
        tokens = []
        for raw in _list(fields[field], field):
            token = _common.normalize_certification(raw)
            if not _CERT_RE.match(token):
                raise _common.DataError("fields.%s: %r is not a certification token" % (field, raw))
            if token not in _known_certifications():
                questions.ask("unmapped_certification", field, value=token)
            if token not in tokens:
                tokens.append(token)
        rfq[field] = tokens
        if tokens:
            inferred(field, ", ".join(tokens))
        else:
            questions.ask("confirm_inferred", field, quote=_first_quote(fields[field]), value="none")
    if "required_certifications" not in fields:
        questions.ask("missing", "required_certifications")

    if "timeline" in fields:
        timeline = _timeline(fields["timeline"].get("value"))
        rfq["timeline"] = timeline
        end = timeline["end"] if isinstance(timeline, dict) else timeline
        if _common.days_between(as_of, end) < 0:
            questions.ask("timeline_in_past", "timeline", value=end, as_of=as_of)
        inferred("timeline", end)
    else:
        rfq["timeline"] = _common.UNKNOWN
        questions.ask("missing", "timeline")

    if "max_lead_time_days" in fields:
        rfq["max_lead_time_days"] = _number(fields["max_lead_time_days"].get("value"),
                                            "fields.max_lead_time_days.value")
        inferred("max_lead_time_days", "%s days" % _common.format_quantity(rfq["max_lead_time_days"]))

    for field in ("required_seller_countries", "excluded_seller_countries"):
        if field in fields:
            rfq[field] = _countries(_list(fields[field], field), "fields.%s" % field)
            inferred(field, ", ".join(rfq[field]))

    return rfq, questions, notes, rfq_notes, provenance


def _known_certifications():
    return frozenset(_common.CERTIFICATION_SYNONYMS.values())


def _envelope(doc, rfq, as_of, notes, provenance):
    """The rfq.schema.json document. Everything the buyer did not state is set here."""
    full = {
        "schema_version": _common.SCHEMA_VERSION,
        "score_version": "unscored",
        "rfq_id": doc["rfq_id"],
        "buyer_id": doc.get("buyer_id"),
    }
    full.update(rfq)
    full.update({
        "status": "draft",
        "qualification_score": 0,
        "confidence": 0,
        "unknown_penalty_applied": [],
        "evidence": [],
        "as_of": as_of,
        "notes": ["Built by %s from a buyer message; every stated field is quoted under "
                  "extensions.intake." % SCRIPT_NAME] + notes,
        "extensions": {"intake": provenance},
    })
    return full


def _read_document(doc):
    if not isinstance(doc, dict):
        raise _common.DataError("input must be a JSON object")
    unknown = sorted(set(doc) - {"schema_version", "rfq_id", "buyer_id", "message", "fields"})
    if unknown:
        raise _common.DataError("unknown top-level key(s): %s" % ", ".join(unknown))
    rfq_id = doc.get("rfq_id")
    if not isinstance(rfq_id, str) or not _RFQ_ID_RE.match(rfq_id):
        raise _common.DataError("rfq_id must match %s" % _RFQ_ID_RE.pattern)
    buyer_id = doc.get("buyer_id")
    if buyer_id is not None and not (isinstance(buyer_id, str) and _RFQ_ID_RE.match(buyer_id)
                                     and not _common.personal_data_hits(buyer_id)):
        raise _common.DataError("buyer_id must be a record id matching %s, or null; it is never a "
                                "contact (INV-31)" % _RFQ_ID_RE.pattern)
    message = doc.get("message")
    if not isinstance(message, dict) or set(message) - {"text", "language", "channel"}:
        raise _common.DataError("message must be {text, language?, channel?}")
    text = message.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise _common.DataError("message.text must be 1..8000 characters")
    for key in ("language", "channel"):
        value = message.get(key)
        if value is not None and not (isinstance(value, str) and re.match(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$", value)):
            raise _common.DataError("message.%s must be a short token such as \"en\" or \"chat\"" % key)
    fields = doc.get("fields")
    if not isinstance(fields, dict):
        raise _common.DataError("fields must be an object keyed by RFQ field name")
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise _common.DataError("fields carries key(s) the agent may not set: %s" % ", ".join(unknown))
    for field, entry in fields.items():
        if not isinstance(entry, dict) or set(entry) - {"value", "quote", "inferred", "unit"}:
            raise _common.DataError("fields.%s must be {value, quote, inferred?, unit?}" % field)
        if "value" not in entry:
            raise _common.DataError("fields.%s has no value" % field)
        if "unit" in entry and field not in ("quantity", "max_moq"):
            raise _common.DataError("fields.%s takes no unit" % field)
        if "inferred" in entry and not isinstance(entry["inferred"], bool):
            raise _common.DataError("fields.%s.inferred must be true or false" % field)
    return text


def _run(args, config):
    doc = _common.read_input(args)
    if args.as_of is None or not str(args.as_of).strip():
        raise _common.UsageError(
            "%s needs --as-of YYYY-MM-DD, the intake date; it never reads the clock and "
            "never guesses one" % SCRIPT_NAME
        )
    as_of = _common.resolve_as_of([], args.as_of, config)

    text = _read_document(doc)
    _check_quotes(doc["fields"], text)
    rfq_fields, questions, notes, rfq_notes, provenance = _build(doc, as_of)
    asked = questions.ordered()
    blocked = any(q["blocking"] for q in asked)
    if not blocked:
        notes = notes + rfq_notes

    rfq = None if blocked else _envelope(doc, rfq_fields, as_of, notes, provenance)
    if doc.get("buyer_id") is None and not blocked:
        # Readiness counts buyer_id; it is the operator's link to make, not the buyer's to
        # answer, so it is a note on the report only and deliberately not on the RFQ.
        notes = notes + ["buyer_id is null; link the RFQ to a buyer record to complete readiness"]

    report = {
        "report_kind": REPORT_KIND,
        "schema_version": _common.SCHEMA_VERSION,
        "skill_version": _common.SKILL_VERSION,
        "as_of": as_of,
        "rfq_id": doc["rfq_id"],
        "message": {
            "language": doc["message"].get("language") or _common.UNKNOWN,
            "channel": doc["message"].get("channel") or _common.UNKNOWN,
            "length": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
        "ready_for_matching": not blocked,
        "stated_fields": [f for f in FIELDS if f in doc["fields"]],
        "rfq": rfq,
        "questions": asked,
        "notes": notes,
    }
    if rfq is not None:
        score, detail = score_match._rfq_readiness(rfq)
        report["readiness"] = {"qualification_score": score, "readiness_detail": detail}

    errors = []
    if args.validate:
        for kind, target in (("rfq", rfq), (REPORT_KIND, report)):
            if target is None:
                continue
            try:
                schema = _common.load_schema(kind, args.schema_dir)
            except Exception as exc:
                errors.append("cannot load %s schema: %s" % (kind, exc))
            else:
                errors.extend("%s: %s" % (kind, message) for message in _common.validate(target, schema))
    if errors:
        for message in errors[:100]:
            _common.eprint("  " + message)
        return _common.die("intake failed validation (%d problems); nothing was written" % (len(errors),), 1)

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

    _common.eprint(
        "%d field(s) accepted, %d question(s) to ask back%s"
        % (len(report["stated_fields"]), len(asked),
           "; no RFQ emitted, the product category is missing" if blocked else ""),
        quiet=args.quiet,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
