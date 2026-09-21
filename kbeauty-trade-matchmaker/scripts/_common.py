"""Shared library for the kbeauty-trade-matchmaker scripts.

Implements the public API fixed by docs/BUILD-CONTRACT.md section 7.5, the entity
normalisation vocabularies of section 8, and the single shared evidence-quality
function of docs/SCORING-CONTRACT.md section 4 (the "extension rule" of 7.5 places
that one helper here so all three scorers share one implementation).

Build rules honoured by this module:
  * stdlib only, Python 3.9 - 3.14, no syntax newer than 3.9;
  * no network, no filesystem writes and no credential reads at import time;
  * no wall-clock read anywhere (the only time source is the caller's --as-of);
  * every scoring number is read from schemas/scoring.config.json at run time.

All code comments are English by contract (BUILD-CONTRACT 1.5).
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from functools import cmp_to_key

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(SCRIPT_DIR)
SCHEMA_DIR = os.path.join(PACKAGE_ROOT, "schemas")

SKILL_VERSION = "0.3.0"
SCHEMA_VERSION = "0.1.0"
UNKNOWN = "unknown"

#: Sentinel for "the field is not present at all" (distinct from None).
ABSENT = object()

#: Legal-form tokens stripped from both ends of a company name (BUILD-CONTRACT 8.3
#: step 5), already ordered longest-first so that "co ltd" is tried before "co"
#: and "trading llc" before "llc".
LEGAL_SUFFIXES = (
    "trading llc",
    "private limited",
    "co ltd",
    "pvt ltd",
    "fz llc",
    "l l c",
    "s a s",
    "s r l",
    "b v",
    "n v",
    "s a",
    "incorporated",
    "corporation",
    "limited",
    "gmbh",
    "corp",
    "dmcc",
    "llc",
    "ltd",
    "inc",
    "pty",
    "pte",
    "fze",
    "co",
    "주식회사",
    "유한회사",
    "합자회사",
    "합명회사",
)

#: Legal-form tokens stripped only from the END of a company name, longest-first.
#: These are trailing-only in the languages that use them, and several are ordinary
#: words or short fragments that would eat a real leading token if they were tried at
#: the start: Indonesian "Tbk" is a listing marker that always trails; Turkish "aş" is
#: also the noun "aş" and the stem of "aşmak", "a ş" is a single letter plus a single
#: letter, and "tic" (abbreviated "ticaret") is a three-letter fragment. Restricting
#: them to the tail is what keeps "Tic Tac Beauty" and "Aş Kozmetik" intact.
#:
#: "llp" and "pvt" sit here for the same reason. Indian and Singaporean names put them
#: at the tail ("Aurora Beauty Pvt Ltd", "Sunbright LLP"), and the compound forms
#: "pvt ltd" / "private limited" are still stripped from both ends by LEGAL_SUFFIXES,
#: so nothing that used to normalise stops doing so. Tried at the START they would eat
#: the first real token of "LLP Cosmetics" or "Pvt Beauty" - brand names in which the
#: leading three letters are not a legal form at all - and BUILD-CONTRACT 8.4 prefers a
#: missed merge to a wrong one.
LEGAL_FORMS_TRAILING = (
    "sanayi ve ticaret",
    "san ve tic",
    "anonim şirketi",
    "limited şirketi",
    "ltd şti",
    "a ş",
    "şti",
    "tbk",
    "aş",
    "tic",
    "llp",
    "pvt",
)

#: Legal-form tokens stripped only from the START of a company name. Indonesian
#: company names put the form first ("PT Cantik Indonesia", "CV Sinar Kosmetik"), and
#: both tokens are two letters that a tail-side strip could take off a real brand name
#: (a trailing "PT" or "CV" in a non-Indonesian name is not a legal form).
LEGAL_FORMS_LEADING = (
    "pt",
    "cv",
)

#: Korean legal forms that registries and Naver listings routinely write with no space
#: in front of or behind the name ("주식회사한빛코스메틱"). normalize_company_name strips
#: them as a leading / trailing SUBSTRING of the first / last token, not only as a
#: whole token, or dedupe key 2 misses the pair.
KOREAN_LEGAL_FORMS = ("주식회사", "유한회사", "합자회사", "합명회사")

#: Multi-part public suffixes embedded by BUILD-CONTRACT 8.2, matched longest-tail
#: first (a three-label entry such as "smartstore.naver.com" wins over any two-label
#: tail of it). Anything not listed falls through to _looks_like_public_suffix below
#: and then to "keep the last two labels".
#:
#: `canonical_domain` is the PRIMARY dedupe merge key (BUILD-CONTRACT 13.2, test T06)
#: and the seller_id / buyer_id stem, so an entry that is MISSING here merges two
#: unrelated companies into one record - the failure mode this list exists to prevent,
#: and a strictly worse one than failing to merge two records that are the same
#: company. Adding an entry can only ever make canonical_domain MORE specific, never
#: less, so the safe direction is to add.
#:
#: The second block is the shared-hosting block: platforms, site builders and
#: marketplaces that hand every tenant its own subdomain. Most are PSL PRIVATE-section
#: entries; the rest are hosts observed handing out tenant subdomains (naver.com's
#: storefront and blog hosts, for instance). They are listed on the same "more
#: specific, never less" ground, not as a claim about the PSL. Korean SMB cosmetics
#: factories in particular are commonly hosted on cafe24.com, imweb.me and modoo.at.
#: This is a hand-maintained subset, not the full PSL: the package is stdlib-only, has
#: no network at runtime and ships no PSL data file. Add a platform here (and a case to
#: tests/fixtures/normalize.cases.json) when one turns up in a run.
MULTI_PART_SUFFIXES = frozenset(
    [
        # --- ICANN section: registry-operated second levels --------------------
        "co.uk",
        "org.uk",
        "ac.uk",
        "me.uk",
        "ltd.uk",
        "plc.uk",
        "co.kr",
        "or.kr",
        "ne.kr",
        "re.kr",
        "go.kr",
        "pe.kr",
        "com.au",
        "net.au",
        "org.au",
        "co.jp",
        "or.jp",
        "ne.jp",
        "ac.jp",
        "go.jp",
        "gr.jp",
        "com.br",
        "com.cn",
        "net.cn",
        "org.cn",
        "com.hk",
        "com.sg",
        "com.my",
        "com.tr",
        "co.za",
        "com.mx",
        "co.nz",
        "com.ar",
        "com.ae",
        "co.in",
        "com.vn",
        "co.id",
        "com.ph",
        "com.sa",
        "com.qa",
        "com.kw",
        "com.bh",
        "com.om",
        "com.eg",
        "com.jo",
        "com.lb",
        "co.il",
        "com.pk",
        "com.bd",
        "com.lk",
        "com.np",
        "co.th",
        "in.th",
        "com.tw",
        "com.co",
        "com.pe",
        "com.uy",
        "com.ec",
        "com.ve",
        "com.do",
        "com.gt",
        "co.cr",
        "com.ng",
        "com.gh",
        "co.ke",
        "com.tn",
        "com.ua",
        "com.pl",
        "com.ro",
        "com.ru",
        "com.gr",
        "com.cy",
        "com.pt",
        "com.es",
        # --- shared hosting / site builders / tenant subdomains ----------------
        "cafe24.com",
        "imweb.me",
        "modoo.at",
        "creatorlink.net",
        "tistory.com",
        "smartstore.naver.com",
        "blog.naver.com",
        "post.naver.com",
        "myshopify.com",
        "wixsite.com",
        "editorx.io",
        "blogspot.com",
        "wordpress.com",
        "tumblr.com",
        "pages.dev",
        "workers.dev",
        "netlify.app",
        "vercel.app",
        "github.io",
        "gitlab.io",
        "web.app",
        "firebaseapp.com",
        "appspot.com",
        "herokuapp.com",
        "azurewebsites.net",
        "onrender.com",
        "webflow.io",
        "weebly.com",
        "squarespace.com",
        "bigcartel.com",
        "mystrikingly.com",
        "jimdosite.com",
        "jimdofree.com",
        "godaddysites.com",
        "business.site",
        "notion.site",
        "glitch.me",
        "carrd.co",
        "framer.website",
    ]
)

#: Labels that a registry, not a company, owns directly under a country-code TLD.
#: Used only by _looks_like_public_suffix: see the rationale there.
_REGISTRY_SECOND_LEVELS = frozenset(
    [
        "ac", "ad", "asso", "biz", "co", "com", "ed", "edu", "firm", "gen", "go",
        "gob", "gouv", "gov", "gr", "ind", "info", "lg", "ltd", "me", "mil", "mun",
        "ne", "net", "nom", "or", "org", "plc", "pro", "re", "res", "sch", "store",
        "tm", "web",
    ]
)

#: A two-letter, all-alphabetic last label - the shape of a country-code TLD.
_CCTLD_RE = re.compile(r"^[a-z]{2}$")

#: BUILD-CONTRACT 8.5 category tree: child slug -> parent slug.
CATEGORY_PARENTS = {
    # skincare
    "cleanser": "skincare",
    "toner": "skincare",
    "essence": "skincare",
    "serum": "skincare",
    "ampoule": "skincare",
    "moisturizer": "skincare",
    "eye_care": "skincare",
    "mask_sheet": "skincare",
    "wash_off_mask": "skincare",
    "exfoliator": "skincare",
    "facial_oil": "skincare",
    "face_mist": "skincare",
    "spot_treatment": "skincare",
    # suncare
    "sunscreen": "suncare",
    "sun_stick": "suncare",
    "sun_cushion": "suncare",
    "after_sun": "suncare",
    # makeup
    "base_makeup": "makeup",
    "cushion": "makeup",
    "foundation": "makeup",
    "concealer": "makeup",
    "face_powder": "makeup",
    "lip_makeup": "makeup",
    "eye_makeup": "makeup",
    "blush": "makeup",
    "brow": "makeup",
    "makeup_remover": "makeup",
    # haircare
    "shampoo": "haircare",
    "conditioner": "haircare",
    "hair_treatment": "haircare",
    "hair_essence": "haircare",
    "scalp_care": "haircare",
    "hair_color": "haircare",
    "hair_styling": "haircare",
    # bodycare
    "body_wash": "bodycare",
    "body_lotion": "bodycare",
    "body_scrub": "bodycare",
    "hand_care": "bodycare",
    "foot_care": "bodycare",
    "deodorant": "bodycare",
    "body_mist": "bodycare",
    # mens_grooming
    "mens_skincare": "mens_grooming",
    "shaving": "mens_grooming",
    # personal_care
    "oral_care": "personal_care",
    "feminine_care": "personal_care",
    "baby_care": "personal_care",
    "sanitizer": "personal_care",
    # fragrance
    "perfume": "fragrance",
    "home_fragrance": "fragrance",
    # nail
    "nail_polish": "nail",
    "nail_care": "nail",
    "nail_sticker": "nail",
    # beauty_device
    "led_mask": "beauty_device",
    "microcurrent_device": "beauty_device",
    "cleansing_device": "beauty_device",
    "hair_removal_device": "beauty_device",
    # beauty_tools
    "makeup_brush": "beauty_tools",
    "puff_sponge": "beauty_tools",
    "hair_tool": "beauty_tools",
    "nail_tool": "beauty_tools",
    # inner_beauty
    "beauty_supplement": "inner_beauty",
    "collagen_drink": "inner_beauty",
}

#: Level-1 parents of BUILD-CONTRACT 8.5.
CATEGORY_ROOTS = frozenset(CATEGORY_PARENTS.values())

#: parent -> frozenset of its children.
CATEGORY_CHILDREN = {}
for _child, _parent in CATEGORY_PARENTS.items():
    CATEGORY_CHILDREN.setdefault(_parent, set()).add(_child)
CATEGORY_CHILDREN = {k: frozenset(v) for k, v in CATEGORY_CHILDREN.items()}

#: Every slug of the 8.5 vocabulary (parents and children).
CATEGORY_VOCABULARY = frozenset(set(CATEGORY_PARENTS) | set(CATEGORY_ROOTS))

#: BUILD-CONTRACT 8.5 adjacency groups G1..G5.
_CATEGORY_ADJACENCY_GROUPS = (
    ("skincare", "suncare", "mens_grooming"),
    ("makeup", "nail", "beauty_tools"),
    ("haircare", "bodycare", "personal_care", "fragrance"),
    ("beauty_device", "beauty_tools"),
    ("inner_beauty", "personal_care"),
)

#: parent -> frozenset of adjacent parents (self excluded).
CATEGORY_ADJACENCY = {}
for _group in _CATEGORY_ADJACENCY_GROUPS:
    for _member in _group:
        CATEGORY_ADJACENCY.setdefault(_member, set()).update(m for m in _group if m != _member)
CATEGORY_ADJACENCY = {k: frozenset(v) for k, v in CATEGORY_ADJACENCY.items()}

#: BUILD-CONTRACT 8.5 synonyms. Keys are already in cleaned slug form.
#: The two vertical markers map to None: they are NOT categories.
CATEGORY_SYNONYMS = {
    "sun_cream": "sunscreen",
    "suncream": "suncare",
    "sunblock": "sunscreen",
    "sun_block": "sunscreen",
    "spf": "sunscreen",
    "sun_care": "suncare",
    "skin_care": "skincare",
    "hair_care": "haircare",
    "body_care": "bodycare",
    "colour_cosmetics": "makeup",
    "color_cosmetics": "makeup",
    "cosmetics": "makeup",
    "sheet_mask": "mask_sheet",
    "bb_cream": "base_makeup",
    "cc_cream": "base_makeup",
    "lipstick": "lip_makeup",
    "lip_tint": "lip_makeup",
    "lip_balm": "lip_makeup",
    "mens_cosmetics": "mens_grooming",
    "k_beauty": None,
    "kbeauty": None,
    # English compounds a page or an RFQ prints instead of the bare slug. A slug that
    # is NOT in the vocabulary after this table is dropped by the seller-side scorers
    # with a note (never scored as a verified mismatch, never a hard-filter reject).
    "sun_screen": "sunscreen",
    "sun_protection": "sunscreen",
    "uv_protection": "sunscreen",
    "sun_stick_spf": "sun_stick",
    "cleansing_foam": "cleanser",
    "foam_cleanser": "cleanser",
    "facial_cleanser": "cleanser",
    "face_wash": "cleanser",
    "cleansing_oil": "cleanser",
    "cleansing_water": "cleanser",
    "cleansing_balm": "cleanser",
    "micellar_water": "cleanser",
    "toner_pad": "toner",
    "toner_pads": "toner",
    "moisturiser": "moisturizer",
    "face_cream": "moisturizer",
    "moisturizing_cream": "moisturizer",
    "eye_cream": "eye_care",
    "sheet_masks": "mask_sheet",
    "lip_gloss": "lip_makeup",
    "hair_mask": "hair_treatment",
    "mist": "face_mist",
    # Korean spellings (keys are _slugify output with the underscores removed).
    "선크림": "sunscreen",
    "썬크림": "sunscreen",
    "선블록": "sunscreen",
    "썬블록": "sunscreen",
    "선스크린": "sunscreen",
    "자외선차단제": "sunscreen",
    "선스틱": "sun_stick",
    "썬스틱": "sun_stick",
    "선쿠션": "sun_cushion",
    "스킨케어": "skincare",
    "기초화장품": "skincare",
    "색조화장품": "makeup",
    "메이크업": "makeup",
    "헤어케어": "haircare",
    "바디케어": "bodycare",
    "토너": "toner",
    "토너패드": "toner",
    "에센스": "essence",
    "세럼": "serum",
    "앰플": "ampoule",
    "수분크림": "moisturizer",
    "아이크림": "eye_care",
    "마스크팩": "mask_sheet",
    "시트마스크": "mask_sheet",
    "클렌저": "cleanser",
    "클렌징폼": "cleanser",
    "폼클렌저": "cleanser",
    "클렌징오일": "cleanser",
    "클렌징워터": "cleanser",
    "미스트": "face_mist",
    "쿠션": "cushion",
    "쿠션팩트": "cushion",
    "파운데이션": "foundation",
    "컨실러": "concealer",
    "립스틱": "lip_makeup",
    "립틴트": "lip_makeup",
    "틴트": "lip_makeup",
    "립밤": "lip_makeup",
    "bb크림": "base_makeup",
    "cc크림": "base_makeup",
    "샴푸": "shampoo",
    "컨디셔너": "conditioner",
    "린스": "conditioner",
    "헤어트리트먼트": "hair_treatment",
    "바디워시": "body_wash",
    "바디로션": "body_lotion",
    "핸드크림": "hand_care",
    "향수": "perfume",
    "남성화장품": "mens_grooming",
    "케이뷰티": None,
    "k뷰티": None,
}

#: A trailing SPF / PA grade on a slug ("sunscreen_spf50", "sun_stick_spf50_pa") names a
#: product grade, not a category; it is stripped before the vocabulary lookup.
_CATEGORY_GRADE_SUFFIX_RE = re.compile(r"(_spf\d*)?(_pa\d*)?$")

#: The vertical markers of BUILD-CONTRACT 8.5 that normalize_category drops.
VERTICAL_MARKERS = frozenset(["k_beauty", "kbeauty"])

#: BUILD-CONTRACT 8.6 certification vocabulary, keyed on the punctuation-stripped
#: upper-case form. Every canonical token maps to itself so the round trip is
#: stable (normalize_certification is idempotent).
CERTIFICATION_SYNONYMS = {
    # canonical tokens, punctuation-stripped key -> canonical token
    "ISO22716": "ISO22716",
    "CGMP": "CGMP",
    "GMPKOREA": "GMP_KOREA",
    "ISO9001": "ISO9001",
    "ISO14001": "ISO14001",
    "COSMOS": "COSMOS",
    "ECOCERT": "ECOCERT",
    "FDAMOCRA": "FDA_MOCRA",
    "FDAREGISTERED": "FDA_REGISTERED",
    "CPNP": "CPNP",
    "CFDA": "CFDA",
    "KFDAFUNCTIONAL": "KFDA_FUNCTIONAL",
    "HALAL": "HALAL",
    "VEGAN": "VEGAN",
    "CRUELTYFREE": "CRUELTY_FREE",
    # synonyms
    #
    # A BARE "GMP" is deliberately NOT folded into CGMP. Korean factory sites print
    # "GMP 기준", "GMP 시설", "GMP 공정" as marketing copy far more often than they hold
    # the MFDS CGMP designation, and references/seller-discovery.md section 8 already
    # forbids reading that copy as a certificate. Folding it here would hand the bare
    # phrase S-CP4's full iso22716_or_cgmp_held points and let it satisfy HF-04's
    # superset test. GMP_CLAIMED is recordable, comparable to a query that also said
    # "GMP", and absent from score_seller.py's BASELINE_QUALITY_TOKENS and
    # OTHER_QUALITY_TOKENS, so it earns nothing on its own.
    "GMP": "GMP_CLAIMED",
    "MOCRA": "FDA_MOCRA",
    "FDA": "FDA_REGISTERED",
    "EUCPNP": "CPNP",
    "CPNPNOTIFICATION": "CPNP",
    "NMPA": "CFDA",
    "기능성화장품": "KFDA_FUNCTIONAL",
    "할랄": "HALAL",
    "LEAPINGBUNNY": "CRUELTY_FREE",
}

#: SCORING-CONTRACT section 8 ledger item 4 ("free_mail_domains - a constant in
#: _common.py or scoring.config.json"). The config does not carry it, so it lives
#: here. It is a vocabulary, not a tunable number, so INV-29 is unaffected.
FREE_MAIL_DOMAINS = frozenset(
    [
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "aol.com",
        "naver.com",
        "daum.net",
        "hanmail.net",
        "qq.com",
        "163.com",
        "126.com",
    ]
)

#: Membership table for seller.export_regions tokens (seller.schema.json). It is a
#: VOCABULARY, not a tunable number, so INV-29 is unaffected - and it is deliberately
#: one-directional: it answers "does this stated region cover this destination
#: country?" for S-EX1 and nothing else. A script must NEVER turn a region token into
#: a country list on a record: a maker that wrote "동남아" named no country, and
#: writing six alpha-2 codes into export_markets would invent facts the page does not
#: carry (BUILD-CONTRACT.md 8.7 - region expansion is the operator's job on the query
#: surface). Membership is intentionally generous at the edges; a region claim already
#: scores below a named country, so a loose edge cannot outrank real evidence.
EXPORT_REGION_COUNTRIES = {
    "SEA": frozenset(("BN", "ID", "KH", "LA", "MM", "MY", "PH", "SG", "TH", "TL", "VN")),
    "EA": frozenset(("CN", "HK", "JP", "KR", "MN", "MO", "TW")),
    "SA": frozenset(("AF", "BD", "BT", "IN", "LK", "MV", "NP", "PK")),
    "MENA": frozenset((
        "AE", "BH", "DZ", "EG", "IL", "IQ", "JO", "KW", "LB", "LY", "MA", "OM",
        "PS", "QA", "SA", "SY", "TN", "TR", "YE",
    )),
    "EU": frozenset((
        "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
        "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LT", "LU", "LV", "MT", "NL",
        "NO", "PL", "PT", "RO", "SE", "SI", "SK",
    )),
    "NA": frozenset(("CA", "MX", "US")),
    "LATAM": frozenset((
        "AR", "BO", "BR", "CL", "CO", "CR", "DO", "EC", "GT", "HN", "MX", "NI",
        "PA", "PE", "PY", "SV", "UY", "VE",
    )),
    "AFRICA": frozenset((
        "AO", "CI", "CM", "DZ", "EG", "ET", "GH", "KE", "MA", "MZ", "NG", "SN",
        "TN", "TZ", "UG", "ZA", "ZM", "ZW",
    )),
    "OCEANIA": frozenset(("AU", "FJ", "NZ", "PG")),
    "CIS": frozenset(("AM", "AZ", "BY", "GE", "KG", "KZ", "MD", "RU", "TJ", "TM", "UA", "UZ")),
}
#: "EUROPE" and "ASIA" are coarse spellings a maker may print; they resolve to the
#: union of their sub-regions so a coarse claim is never treated as no claim at all.
EXPORT_REGION_COUNTRIES["EUROPE"] = EXPORT_REGION_COUNTRIES["EU"] | EXPORT_REGION_COUNTRIES["CIS"]
EXPORT_REGION_COUNTRIES["ASIA"] = (
    EXPORT_REGION_COUNTRIES["SEA"] | EXPORT_REGION_COUNTRIES["EA"] | EXPORT_REGION_COUNTRIES["SA"]
)

#: MOQ / quantity unit synonyms, keyed on the NFKC + casefold form with whitespace and
#: periods removed. A VOCABULARY, not a tunable number (INV-29 unaffected). Every
#: spelling of a count of pieces - pcs, EA, 개, unit, pieces - is the same unit as
#: "units"; before this table a seller writing "pcs" against a query in "units" took the
#: unit-mismatch path, HF-03 was skipped and a confirmed 50,000 MOQ stayed qualified
#: against a 3,000 ceiling. Only a genuinely different basis (kg against units, sets
#: against units) is still a mismatch. An unlisted unit keeps its cleaned spelling.
UNIT_SYNONYMS = {
    "units": "units",
    "unit": "units",
    "pcs": "units",
    "pc": "units",
    "pieces": "units",
    "piece": "units",
    "ea": "units",
    "each": "units",
    "개": "units",
    "sets": "sets",
    "set": "sets",
    "세트": "sets",
    "kg": "kg",
    "kgs": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "킬로그램": "kg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "그램": "g",
    "ml": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
}

#: ISO-3166-1 alpha-2 name table: (code, English names and aliases, Korean names). A
#: VOCABULARY, not a tunable number. It covers every country any region token above can
#: name, every country the package's fixtures and references use, and the major K-Beauty
#: export markets, so a raw record carrying "United Kingdom", "UK" or "영국" normalises to
#: GB instead of "unknown" (which zeroed B-MR1 for an otherwise identical record).
#: normalize_country() matches on _country_key(), so spacing, case, accents, a leading
#: "the" and punctuation do not matter. Add a row here when a new market turns up.
_COUNTRY_NAME_ROWS = (
    ("AE", ("United Arab Emirates", "UAE", "Emirates"), ("아랍에미리트", "아랍에미레이트")),
    ("AF", ("Afghanistan",), ("아프가니스탄",)),
    ("AL", ("Albania",), ("알바니아",)),
    ("AM", ("Armenia",), ("아르메니아",)),
    ("AO", ("Angola",), ("앙골라",)),
    ("AR", ("Argentina",), ("아르헨티나",)),
    ("AT", ("Austria",), ("오스트리아",)),
    ("AU", ("Australia",), ("호주", "오스트레일리아")),
    ("AZ", ("Azerbaijan",), ("아제르바이잔",)),
    ("BA", ("Bosnia and Herzegovina", "Bosnia"), ("보스니아헤르체고비나", "보스니아")),
    ("BD", ("Bangladesh",), ("방글라데시",)),
    ("BE", ("Belgium",), ("벨기에",)),
    ("BG", ("Bulgaria",), ("불가리아",)),
    ("BH", ("Bahrain",), ("바레인",)),
    ("BN", ("Brunei", "Brunei Darussalam"), ("브루나이",)),
    ("BO", ("Bolivia",), ("볼리비아",)),
    ("BR", ("Brazil", "Brasil"), ("브라질",)),
    ("BT", ("Bhutan",), ("부탄",)),
    ("BY", ("Belarus",), ("벨라루스",)),
    ("CA", ("Canada",), ("캐나다",)),
    ("CH", ("Switzerland",), ("스위스",)),
    ("CI", ("Cote d'Ivoire", "Ivory Coast"), ("코트디부아르",)),
    ("CL", ("Chile",), ("칠레",)),
    ("CM", ("Cameroon",), ("카메룬",)),
    ("CN", ("China", "People's Republic of China", "PRC", "Mainland China"), ("중국",)),
    ("CO", ("Colombia",), ("콜롬비아",)),
    ("CR", ("Costa Rica",), ("코스타리카",)),
    ("CY", ("Cyprus",), ("키프로스",)),
    ("CZ", ("Czech Republic", "Czechia"), ("체코",)),
    ("DE", ("Germany", "Deutschland"), ("독일",)),
    ("DK", ("Denmark",), ("덴마크",)),
    ("DO", ("Dominican Republic",), ("도미니카공화국",)),
    ("DZ", ("Algeria",), ("알제리",)),
    ("EC", ("Ecuador",), ("에콰도르",)),
    ("EE", ("Estonia",), ("에스토니아",)),
    ("EG", ("Egypt",), ("이집트",)),
    ("ES", ("Spain", "Espana"), ("스페인",)),
    ("ET", ("Ethiopia",), ("에티오피아",)),
    ("FI", ("Finland",), ("핀란드",)),
    ("FJ", ("Fiji",), ("피지",)),
    ("FR", ("France",), ("프랑스",)),
    ("GB", ("United Kingdom", "UK", "Great Britain", "Britain", "England", "Scotland",
            "Wales", "Northern Ireland", "United Kingdom of Great Britain and Northern Ireland"),
     ("영국",)),
    # Bare "Georgia" is also a US state, so only the unambiguous country name is an alias.
    ("GE", ("Republic of Georgia", "Georgia (country)"), ("조지아",)),
    ("GH", ("Ghana",), ("가나",)),
    ("GR", ("Greece",), ("그리스",)),
    ("GT", ("Guatemala",), ("과테말라",)),
    ("HK", ("Hong Kong", "Hong Kong SAR"), ("홍콩",)),
    ("HN", ("Honduras",), ("온두라스",)),
    ("HR", ("Croatia",), ("크로아티아",)),
    ("HU", ("Hungary",), ("헝가리",)),
    ("ID", ("Indonesia",), ("인도네시아",)),
    ("IE", ("Ireland", "Republic of Ireland"), ("아일랜드",)),
    ("IL", ("Israel",), ("이스라엘",)),
    ("IN", ("India",), ("인도",)),
    ("IQ", ("Iraq",), ("이라크",)),
    ("IR", ("Iran",), ("이란",)),
    ("IS", ("Iceland",), ("아이슬란드",)),
    ("IT", ("Italy", "Italia"), ("이탈리아",)),
    ("JO", ("Jordan",), ("요르단",)),
    ("JP", ("Japan", "Nippon"), ("일본",)),
    ("KE", ("Kenya",), ("케냐",)),
    ("KG", ("Kyrgyzstan", "Kyrgyz Republic"), ("키르기스스탄",)),
    ("KH", ("Cambodia",), ("캄보디아",)),
    ("KP", ("North Korea", "DPRK", "Democratic People's Republic of Korea"),
     ("북한", "조선민주주의인민공화국")),
    ("KR", ("South Korea", "Korea", "Republic of Korea", "Korea, Republic of", "ROK"),
     ("한국", "대한민국", "남한")),
    ("KW", ("Kuwait",), ("쿠웨이트",)),
    ("KZ", ("Kazakhstan",), ("카자흐스탄",)),
    ("LA", ("Laos", "Lao PDR"), ("라오스",)),
    ("LB", ("Lebanon",), ("레바논",)),
    ("LK", ("Sri Lanka",), ("스리랑카",)),
    ("LT", ("Lithuania",), ("리투아니아",)),
    ("LU", ("Luxembourg",), ("룩셈부르크",)),
    ("LV", ("Latvia",), ("라트비아",)),
    ("LY", ("Libya",), ("리비아",)),
    ("MA", ("Morocco",), ("모로코",)),
    ("MD", ("Moldova",), ("몰도바",)),
    ("ME", ("Montenegro",), ("몬테네그로",)),
    ("MK", ("North Macedonia", "Macedonia"), ("북마케도니아",)),
    ("MM", ("Myanmar", "Burma"), ("미얀마",)),
    ("MN", ("Mongolia",), ("몽골",)),
    ("MO", ("Macau", "Macao"), ("마카오",)),
    ("MT", ("Malta",), ("몰타",)),
    ("MV", ("Maldives",), ("몰디브",)),
    ("MX", ("Mexico",), ("멕시코",)),
    ("MY", ("Malaysia",), ("말레이시아",)),
    ("MZ", ("Mozambique",), ("모잠비크",)),
    ("NG", ("Nigeria",), ("나이지리아",)),
    ("NI", ("Nicaragua",), ("니카라과",)),
    ("NL", ("Netherlands", "Holland"), ("네덜란드",)),
    ("NO", ("Norway",), ("노르웨이",)),
    ("NP", ("Nepal",), ("네팔",)),
    ("NZ", ("New Zealand",), ("뉴질랜드",)),
    ("OM", ("Oman",), ("오만",)),
    ("PA", ("Panama",), ("파나마",)),
    ("PE", ("Peru",), ("페루",)),
    ("PG", ("Papua New Guinea",), ("파푸아뉴기니",)),
    ("PH", ("Philippines",), ("필리핀",)),
    ("PK", ("Pakistan",), ("파키스탄",)),
    ("PL", ("Poland",), ("폴란드",)),
    ("PR", ("Puerto Rico",), ("푸에르토리코",)),
    ("PS", ("Palestine", "State of Palestine"), ("팔레스타인",)),
    ("PT", ("Portugal",), ("포르투갈",)),
    ("PY", ("Paraguay",), ("파라과이",)),
    ("QA", ("Qatar",), ("카타르",)),
    ("RO", ("Romania",), ("루마니아",)),
    ("RS", ("Serbia",), ("세르비아",)),
    ("RU", ("Russia", "Russian Federation"), ("러시아",)),
    ("SA", ("Saudi Arabia", "KSA", "Kingdom of Saudi Arabia"), ("사우디아라비아", "사우디")),
    ("SE", ("Sweden",), ("스웨덴",)),
    ("SG", ("Singapore",), ("싱가포르",)),
    ("SI", ("Slovenia",), ("슬로베니아",)),
    ("SK", ("Slovakia",), ("슬로바키아",)),
    ("SN", ("Senegal",), ("세네갈",)),
    ("SV", ("El Salvador",), ("엘살바도르",)),
    ("SY", ("Syria",), ("시리아",)),
    ("TH", ("Thailand",), ("태국",)),
    ("TJ", ("Tajikistan",), ("타지키스탄",)),
    ("TL", ("Timor-Leste", "East Timor"), ("동티모르",)),
    ("TM", ("Turkmenistan",), ("투르크메니스탄",)),
    ("TN", ("Tunisia",), ("튀니지",)),
    ("TR", ("Turkey", "Turkiye"), ("튀르키예", "터키")),
    ("TW", ("Taiwan",), ("대만",)),
    ("TZ", ("Tanzania",), ("탄자니아",)),
    ("UA", ("Ukraine",), ("우크라이나",)),
    ("UG", ("Uganda",), ("우간다",)),
    # Bare "America" also names the continents, so it is not an alias for US.
    ("US", ("United States", "United States of America", "USA"), ("미국",)),
    ("UY", ("Uruguay",), ("우루과이",)),
    ("UZ", ("Uzbekistan",), ("우즈베키스탄",)),
    ("VE", ("Venezuela",), ("베네수엘라",)),
    ("VN", ("Vietnam", "Viet Nam"), ("베트남",)),
    ("YE", ("Yemen",), ("예멘",)),
    ("ZA", ("South Africa",), ("남아프리카공화국", "남아공")),
    ("ZM", ("Zambia",), ("잠비아",)),
    ("ZW", ("Zimbabwe",), ("짐바브웨",)),
)


def _country_key(text):
    """Matching key for a country name: accents, case, a leading "the", spacing and
    punctuation removed ("The Netherlands" -> "netherlands", "Côte d'Ivoire" -> "cotedivoire")."""
    folded = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold().strip()
    folded = re.sub(r"^the\s+", "", folded)
    return re.sub(r"[^\w]|_", "", folded, flags=re.UNICODE)


#: Every officially assigned ISO-3166-1 alpha-2 code (249). A two-letter token outside it
#: ("XX", "EU", "UK") is not a country code; "UK" still resolves to GB through the name table.
ISO_3166_ALPHA2 = frozenset(
    """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ
    BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO
    DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU
    GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW
    KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU
    MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW
    PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF
    TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA
    ZM ZW""".split()
)

#: _country_key(name) -> alpha-2. Built once from _COUNTRY_NAME_ROWS.
COUNTRY_ALIASES = {}
for _code, _english, _korean in _COUNTRY_NAME_ROWS:
    for _name in (_code,) + _english + _korean:
        COUNTRY_ALIASES[_country_key(_name)] = _code




def disc06_exclusion_reason(record):
    """Why a record failed the DISC-06 evidence bar, in words a reviewer can act on.

    Two very different situations end up here and "no evidenced material claim"
    alone does not tell them apart: a company whose site no permitted retrieval
    method could open, and a company whose pages were read but carried nothing
    material. The first says "we could not look"; the second says "we looked and
    there was nothing". Neither is the "unverified" state - that one is SCORED and
    ranked with a marker (scoring.config.json evidence.no_evidence_note).
    """
    items = record.get("evidence") if isinstance(record, dict) else None
    count = len([it for it in items if isinstance(it, dict)]) if isinstance(items, list) else 0
    if count == 0:
        return "no evidenced material claim (no source could be opened)"
    return "no evidenced material claim (evidence covers no material claim)"

def unknown_flag_limit(config, entity):
    """max_unknown_dimensions_before_flag for one entity.

    scoring.config.json carries a global default plus an optional per-entity
    override. The seller and match limits are higher on purpose: in this vertical
    the BASELINE is that Korean makers do not publish commercial terms, so at the
    buyer's limit of 2 the "\ud655\uc778 \ud544\uc694 / needs verification" flag fired on
    every record of a real run and told a reviewer nothing.
    """
    unknown = config.get("unknown", {}) if isinstance(config, dict) else {}
    by_entity = unknown.get("max_unknown_dimensions_before_flag_by_entity")
    if isinstance(by_entity, dict) and entity in by_entity:
        try:
            return int(by_entity[entity])
        except (TypeError, ValueError):
            pass
    return int(unknown.get("max_unknown_dimensions_before_flag", 0))

def region_covers_country(regions, country_code):
    """True when any stated export_regions token covers `country_code`.

    Read-only membership test. It never returns a country list, so no caller can use
    it to expand a region into export_markets.
    """
    if not regions or not isinstance(country_code, str):
        return False
    code = country_code.strip().upper()
    if not code or code == UNKNOWN.upper():
        return False
    for token in regions:
        if not isinstance(token, str):
            continue
        members = EXPORT_REGION_COUNTRIES.get(token.strip().upper())
        if members and code in members:
            return True
    return False


#: Bare document kinds accepted by load_schema().
SCHEMA_NAMES = (
    "buyer",
    "seller",
    "rfq",
    "evidence",
    "match-result",
    "discovery-result",
    # A MEASUREMENT document, not a scored one: scripts/acceptance_report.py emits it and
    # no scorer reads it (references/calibration-notes.md section 7).
    "acceptance-report",
    # A COMPARISON document: scripts/diff_runs.py emits it and no scorer reads it
    # (references/data-contract.md section 9.4).
    "run-diff",
    # An AUDIT document: the re-check queue of references/evidence-policy.md 5.6 lists
    # the evidence to re-read. It changes no record, and no scorer reads it.
    "recheck-queue",
    # An EXPORT document: scripts/export_leads.py writes it for a TradeWith admin to
    # import and no scorer reads it (references/data-contract.md section 9.4).
    "tradewith-bulk-buyers",
    "outreach-draft",
)

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DATE_TIME_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?([Zz]|[+-][0-9]{2}:[0-9]{2})$"
)
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

_TRUE_TOKENS = frozenset(["true", "yes", "y", "1"])
_FALSE_TOKENS = frozenset(["false", "no", "n", "0"])
#: is_unknown() vocabulary. tri_state() additionally reads "n/a", "na", "-" and
#: "?" as unknown, which it does by falling through to its default branch.
_UNKNOWN_TOKENS = frozenset(["", "unknown"])

#: Fallback confidence bands. The config states them as prose in
#: output.render_confidence_as, which confidence_band() parses when it can.
_CONFIDENCE_BAND_FALLBACK = (("HIGH", Decimal("0.75")), ("MEDIUM", Decimal("0.5")))

_MAX_REF_DEPTH = 64

_CONFIG_CACHE = {}
_SCHEMA_CACHE = {}


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------


class KbtmError(Exception):
    """Base class for the three script-level error kinds."""


class UsageError(KbtmError):
    """Bad invocation or unreadable input -> exit 2."""


class DataError(KbtmError):
    """Input parses but violates the data contract -> exit 1."""


class ConfigError(KbtmError):
    """Scoring config missing or unreadable -> exit 2."""


# --------------------------------------------------------------------------
# Config and schema loading (always resolved from THIS file, never from cwd)
# --------------------------------------------------------------------------


def load_config(path=None):
    """Load and cache schemas/scoring.config.json (or an override path).

    Floats are parsed as Decimal so every tunable is exact (SCORING-CONTRACT 0.1).
    """
    if path is None:
        resolved = os.path.join(SCHEMA_DIR, "scoring.config.json")
    else:
        resolved = os.path.abspath(path)
    if resolved in _CONFIG_CACHE:
        return _CONFIG_CACHE[resolved]
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            config = json.load(fh, parse_float=Decimal)
    except (IOError, OSError) as exc:
        raise ConfigError("cannot read scoring config %s: %s" % (resolved, exc))
    except ValueError as exc:
        raise ConfigError("scoring config %s is not valid JSON: %s" % (resolved, exc))
    if not isinstance(config, dict):
        raise ConfigError("scoring config %s must be a JSON object" % resolved)
    _CONFIG_CACHE[resolved] = config
    return config


def load_schema(name, schema_dir=None):
    """Load a bundled schema by bare kind ("buyer") or filename ("buyer.schema.json")."""
    if not isinstance(name, str) or not name.strip():
        raise UsageError("schema name must be a non-empty string")
    directory = os.path.abspath(schema_dir) if schema_dir else SCHEMA_DIR
    bare = name.strip()
    if bare.endswith(".json"):
        filename = bare
    else:
        key = bare.replace("_", "-").lower()
        if key not in SCHEMA_NAMES:
            raise UsageError(
                "unknown schema name %r (expected one of: %s)" % (name, ", ".join(SCHEMA_NAMES))
            )
        filename = "%s.schema.json" % key
    resolved = os.path.join(directory, filename)
    if resolved in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[resolved]
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except (IOError, OSError) as exc:
        raise UsageError("cannot read schema %s: %s" % (resolved, exc))
    except ValueError as exc:
        raise UsageError("schema %s is not valid JSON: %s" % (resolved, exc))
    _SCHEMA_CACHE[resolved] = schema
    return schema


# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------


def read_input(args):
    """Read one JSON value from args.input, or from stdin when it is None or "-"."""
    path = getattr(args, "input", None)
    try:
        if path is None or path == "-":
            raw = sys.stdin.read()
        else:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
    except (IOError, OSError) as exc:
        raise UsageError("cannot read input %s: %s" % (path or "<stdin>", exc))
    except UnicodeDecodeError as exc:
        # UnicodeDecodeError is a ValueError, not an OSError, so it would otherwise
        # escape main() as a raw traceback (BUILD-CONTRACT R7.3.3, INV-35).
        raise UsageError("input %s is not valid UTF-8: %s" % (path or "<stdin>", exc))
    if not raw.strip():
        raise UsageError("input %s is empty" % (path or "<stdin>"))
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise UsageError("input %s is not valid JSON: %s" % (path or "<stdin>", exc))


class _JsonEncoder(json.JSONEncoder):
    """Serialise Decimal exactly; everything else still raises loudly."""

    def default(self, o):  # noqa: D102 - json.JSONEncoder hook
        if isinstance(o, Decimal):
            if o == o.to_integral_value():
                return int(o)
            return float(o)
        return json.JSONEncoder.default(self, o)


def write_output(obj, pretty, stream=None):
    """Serialize obj per BUILD-CONTRACT 3.8 and write it plus exactly one newline."""
    target = stream if stream is not None else sys.stdout
    if pretty:
        text = json.dumps(
            obj, ensure_ascii=False, indent=2, separators=(",", ": "), sort_keys=False, cls=_JsonEncoder
        )
    else:
        text = json.dumps(
            obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False, cls=_JsonEncoder
        )
    target.write(text)
    target.write("\n")


def invalid_output_path(path):
    """Where a document that failed self-validation is written instead of --output.

    "out/result.json" -> "out/result.invalid.json". The requested --output path is never
    written with an invalid document, so a chained command cannot pick one up.
    """
    root, ext = os.path.splitext(path)
    return "%s.invalid%s" % (root, ext or ".json")


def eprint(*parts, **kwargs):
    """Write one diagnostic line to stderr unless quiet=True."""
    if kwargs.get("quiet"):
        return
    sys.stderr.write(" ".join(str(p) for p in parts) + "\n")


def die(message, code=1):
    """Print 'ERROR: <message>' to stderr and return code (callers `return die(...)`)."""
    sys.stderr.write("ERROR: %s\n" % message)
    return code


# --------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------


def _to_decimal(value):
    """Convert a number to Decimal without importing binary float artifacts."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise DataError("a boolean is not a number")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except Exception:
            raise DataError("%r is not a number" % value)
    raise DataError("%r is not a number" % (value,))


def round_half_up(x, ndigits=0):
    """Decimal half-up rounding. Ties go away from zero. Never banker's rounding."""
    quantum = Decimal(1).scaleb(-int(ndigits))
    result = _to_decimal(x).quantize(quantum, rounding=ROUND_HALF_UP)
    if int(ndigits) == 0:
        return int(result)
    return float(result)


# --------------------------------------------------------------------------
# Entity normalisation (BUILD-CONTRACT 8.1 - 8.3)
# --------------------------------------------------------------------------


_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):(.*)$", re.DOTALL)


def split_url(text):
    """Split a URL into (scheme, netloc, path, query, fragment).

    A local RFC3986 splitter. BUILD-CONTRACT 8.1 step 4 describes the job in terms of
    the stdlib URL parser, but the scripts purity rule R7.11.1 forbids importing that
    module anywhere in scripts/, so the same split is done here with string operations
    only. The result matches the stdlib splitter for every input this package sees;
    the 8.1 worked examples are covered by the test suite.
    """
    rest = text if isinstance(text, str) else ""
    scheme = ""
    match = _SCHEME_RE.match(rest)
    if match:
        scheme, rest = match.group(1).lower(), match.group(2)
    netloc = ""
    if rest.startswith("//"):
        rest = rest[2:]
        cut = len(rest)
        for delimiter in ("/", "?", "#"):
            position = rest.find(delimiter)
            if position != -1 and position < cut:
                cut = position
        netloc, rest = rest[:cut], rest[cut:]
    fragment = ""
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
    query = ""
    if "?" in rest:
        rest, query = rest.split("?", 1)
    return scheme, netloc, rest, query, fragment


def join_url(scheme, netloc, path, query, fragment):
    """Inverse of split_url for the http(s) shapes this package emits."""
    url = "%s://%s%s" % (scheme, netloc, path or "")
    if query:
        url += "?" + query
    if fragment:
        url += "#" + fragment
    return url


def _split_host(value):
    """Return the bare host of a URL or host string, or None (BUILD-CONTRACT 8.1 steps 1-9)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    text = unicodedata.normalize("NFKC", text)
    if "://" not in text:
        text = "//" + text.lstrip("/")
    try:
        _scheme, netloc, path, _query, _fragment = split_url(text)
        host = netloc or path or ""
    except Exception:
        return None
    if "@" in host:  # strip credentials
        host = host.rsplit("@", 1)[1]
    if host.startswith("["):  # bracketed IPv6 literal
        return None
    if ":" in host:  # strip port
        host = host.split(":", 1)[0]
    for cut in ("/", "?", "#"):
        if cut in host:
            host = host.split(cut, 1)[0]
    host = host.strip().lower()
    while host.endswith("."):
        host = host[:-1]
    if not host:
        return None
    return host


def _punycode(host):
    """Punycode an internationalized host; fall back to the ASCII-lowercased form."""
    try:
        host.encode("ascii")
        return host
    except UnicodeEncodeError:
        pass
    try:
        return host.encode("idna").decode("ascii")
    except Exception:
        return host


def _looks_like_public_suffix(labels):
    """True when the two-label tail of `labels` has the shape of a registry suffix.

    The embedded MULTI_PART_SUFFIXES list can never be the full Public Suffix List
    (stdlib only, no network, no vendored PSL data file), so an unlisted suffix must
    fail in the direction that REFUSES to merge. "<registry label>.<cc>" - com.pk,
    com.sa, co.th, net.in - is the shape of the overwhelming majority of the ICANN
    multi-part suffixes, and every one of them that is missing from the list above
    would otherwise collapse an entire country's companies onto one merge key.

    Applied only to hosts of three labels or more, and only to widen the registrable
    domain by one label. That bounds the cost of a false positive: a company whose
    domain really is "<registry label>.<cc>" (web.de, art.mx) keeps that domain when
    it is the whole host, and a subdomain of it merely becomes its own entity - a
    missed merge, which BUILD-CONTRACT 8.4 prefers to a wrong one.
    """
    if len(labels) < 3:
        return False
    return labels[-2] in _REGISTRY_SECOND_LEVELS and _CCTLD_RE.match(labels[-1]) is not None


def _registrable_labels(labels):
    """Number of trailing labels that make up the registrable domain.

    When the host IS the suffix (`smartstore.naver.com`, `cafe24.com`) the suffix
    itself is returned, and canonical_domain then yields None rather than a merge key
    shared by every tenant of the platform - the tenant is in the path there, so no
    domain can identify it.
    """
    for size in (3, 2):  # longest embedded suffix first
        if len(labels) >= size and ".".join(labels[-size:]) in MULTI_PART_SUFFIXES:
            return size + 1 if len(labels) > size else size
    if _looks_like_public_suffix(labels):
        return 3
    return 2


def canonical_domain(url_or_host):
    """Registrable domain per BUILD-CONTRACT 8.1, or None when none can be derived."""
    try:
        host = _split_host(url_or_host)
        if host is None:
            return None
        host = _punycode(host)
        labels = host.split(".")
        if all(label.isdigit() for label in labels):  # IPv4 literal
            return None
        if len(labels) < 2:  # single-label host
            return None
        while labels and labels[0] == "www":  # strip www. repeatedly
            labels = labels[1:]
        if len(labels) < 2:
            return None
        registrable = ".".join(labels[-_registrable_labels(labels):])
        if registrable in MULTI_PART_SUFFIXES:  # the input was a public suffix
            return None
        if not _DOMAIN_RE.match(registrable):
            return None
        return registrable
    except Exception:
        return None


def domain_subdomain(url_or_host):
    """The labels in front of the registrable domain, "www" stripped; "" when none.

    Returns None when no registrable domain can be derived at all. dedupe_companies.py
    uses this to tell "two pages of one company" (no subdomain on at least one side)
    apart from "two tenants of one hosting platform" (two different subdomains under a
    registrable domain that the embedded suffix list does not know is a platform).
    """
    try:
        domain = canonical_domain(url_or_host)
        if domain is None:
            return None
        host = _punycode(_split_host(url_or_host) or "")
        labels = host.split(".")
        while labels and labels[0] == "www":
            labels = labels[1:]
        depth = len(domain.split("."))
        return ".".join(labels[:-depth]) if len(labels) > depth else ""
    except Exception:
        return None


def _strip_edge_tokens(tokens, vocabulary, edge="both"):
    """Strip vocabulary tokens from `edge`, longest match first, until stable.

    `edge` is "both" (the default, BUILD-CONTRACT 8.3 step 5), "end" or "start".
    """
    changed = True
    while changed and tokens:
        changed = False
        for phrase in vocabulary:
            parts = phrase.split(" ")
            size = len(parts)
            if size > len(tokens):
                continue
            if edge in ("both", "end") and tokens[-size:] == parts:
                tokens = tokens[:-size]
                changed = True
                break
            if edge in ("both", "start") and tokens[:size] == parts:
                tokens = tokens[size:]
                changed = True
                break
    return tokens


def _strip_legal_forms(tokens):
    """Apply the three legal-form vocabularies until none of them changes anything.

    They have to interleave rather than run once each: "PT Cantik Kosmetik Tbk" needs a
    leading and a trailing strip, and "X Kozmetik San. ve Tic. Ltd. Sti." only exposes
    its "san ve tic" tail after "ltd şti" has come off.
    """
    while tokens:
        before = tokens
        tokens = _strip_edge_tokens(tokens, LEGAL_SUFFIXES)
        tokens = _strip_edge_tokens(tokens, LEGAL_FORMS_TRAILING, edge="end")
        tokens = _strip_edge_tokens(tokens, LEGAL_FORMS_LEADING, edge="start")
        if tokens == before:
            break
    return tokens


def normalize_company_name(name):
    """Normalized company name per BUILD-CONTRACT 8.3. Never raises."""
    try:
        if not isinstance(name, str) or not name.strip():
            return ""
        text = unicodedata.normalize("NFKC", name)
        for marker in ("（주）", "(주)", "㈜", "（유）", "(유)", "（사）", "(사)"):
            text = text.replace(marker, " ")
        text = text.casefold()
        # Default casefolding maps Turkish "İ" (U+0130) to "i" + U+0307 COMBINING DOT
        # ABOVE, and U+0307 is not a \w character, so the next line would split
        # "ANONİM ŞİRKETİ" into ["anoni", "m", "şi", "rketi"] and no legal form would
        # ever match. Fold the pair back to a bare "i" first.
        text = text.replace("i" + "\u0307", "i")
        text = re.sub(r"[^\w\s]|_", " ", text, flags=re.UNICODE)
        tokens = text.split()
        tokens = _strip_legal_forms(tokens)
        tokens = _strip_attached_korean_forms(tokens)
        tokens = _strip_legal_forms(tokens)
        tokens = _strip_edge_tokens(tokens, ("the",))
        return " ".join(tokens)
    except Exception:
        return ""


def _strip_attached_korean_forms(tokens):
    """Strip a Korean legal form glued to the front of the first or the end of the last token."""
    changed = True
    while changed and tokens:
        changed = False
        for form in KOREAN_LEGAL_FORMS:
            if len(tokens[0]) > len(form) and tokens[0].startswith(form):
                tokens = [tokens[0][len(form):]] + tokens[1:]
                changed = True
            if len(tokens[-1]) > len(form) and tokens[-1].endswith(form):
                tokens = tokens[:-1] + [tokens[-1][: -len(form)]]
                changed = True
    return tokens


_HANGUL_RE = re.compile(r"[가-힣]")


def name_match_key(normalized_name):
    """Secondary dedupe key for a normalized company name.

    Korean names are spaced inconsistently ("한빛 코스메틱" / "한빛코스메틱") while the
    company is the same, so a name that contains Hangul compares with every space
    removed. A Latin-script name keeps its spaces: there a space separates words and
    removing it would fold unrelated names together. Returns "" for a non-string.
    """
    if not isinstance(normalized_name, str):
        return ""
    text = normalized_name.strip()
    if _HANGUL_RE.search(text):
        return re.sub(r"\s+", "", text)
    return text


#: Alias kept because seller.schema.json / buyer.schema.json describe this helper
#: as `_common.normalize_name`. BUILD-CONTRACT 7.5 names it normalize_company_name.
normalize_name = normalize_company_name


# --------------------------------------------------------------------------
# Tri-state, unknown and range helpers (BUILD-CONTRACT 3.2, 3.3)
# --------------------------------------------------------------------------


def tri_state(value):
    """Coerce to exactly True, False or "unknown"."""
    if value is True:
        return True
    if value is False:
        return False
    if isinstance(value, int) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return UNKNOWN
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        return UNKNOWN
    return UNKNOWN


def is_unknown(value):
    """True for None / "unknown" / "" / ABSENT. False, 0 and [] are KNOWN values."""
    if value is ABSENT or value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _UNKNOWN_TOKENS
    return False


def coerce_range(value):
    """Normalize a scalar / {min,max} / "unknown" into {"min": m, "max": M[, "unit"]}.

    Returns None for unknown, absent, and the open-upper-bound spelling {"min": n}.
    """
    if is_unknown(value):
        return None
    if isinstance(value, bool):
        raise DataError("a boolean is not a quantity")
    if isinstance(value, (int, float, Decimal)):
        number = _to_decimal(value)
        if number < 0:
            raise DataError("quantity %s is negative" % number)
        return {"min": _plain_number(number), "max": _plain_number(number)}
    if isinstance(value, str):
        number = _to_decimal(value)  # raises DataError when not numeric
        if number < 0:
            raise DataError("quantity %s is negative" % number)
        return {"min": _plain_number(number), "max": _plain_number(number)}
    if isinstance(value, dict):
        has_min = not is_unknown(value.get("min", ABSENT))
        has_max = not is_unknown(value.get("max", ABSENT))
        if not has_min and not has_max:
            return None
        if has_min and not has_max:
            # Open upper bound: not a comparable value (BUILD-CONTRACT 3.3).
            return None
        upper = _to_decimal(value.get("max"))
        lower = _to_decimal(value.get("min")) if has_min else Decimal(0)
        if lower < 0 or upper < 0:
            raise DataError("quantity range has a negative bound")
        if lower > upper:
            raise DataError("quantity range min %s is greater than max %s" % (lower, upper))
        out = {"min": _plain_number(lower), "max": _plain_number(upper)}
        unit = value.get("unit")
        if isinstance(unit, str) and unit.strip():
            out["unit"] = unit.strip()
        return out
    raise DataError("%r is not a quantity" % (value,))


def _plain_number(number):
    """Decimal -> int when integral, else float (JSON-friendly, stable)."""
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def range_satisfies_max(value, max_allowed):
    """HF-03 / HF-07 ceiling test. Returns True, False or "unknown"."""
    if is_unknown(value) or is_unknown(max_allowed):
        return UNKNOWN
    try:
        rng = coerce_range(value)
    except DataError:
        return UNKNOWN
    if rng is None:
        return UNKNOWN
    if isinstance(max_allowed, dict):
        ceiling_range = coerce_range(max_allowed)
        if ceiling_range is None:
            return UNKNOWN
        ceiling = _to_decimal(ceiling_range["max"])
    else:
        try:
            ceiling = _to_decimal(max_allowed)
        except DataError:
            return UNKNOWN
    return _to_decimal(rng["min"]) <= ceiling


# --------------------------------------------------------------------------
# Dates (no wall clock anywhere)
# --------------------------------------------------------------------------


def _date_part(value):
    """Return the YYYY-MM-DD part of a date or date-time string, or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) >= 10 and _DATE_RE.match(text[:10]):
        return text[:10]
    return None


def _as_date(value):
    from datetime import date

    part = _date_part(value)
    if part is None:
        raise DataError("%r is not a YYYY-MM-DD date" % (value,))
    try:
        return date(int(part[0:4]), int(part[5:7]), int(part[8:10]))
    except ValueError as exc:
        raise DataError("%r is not a valid calendar date: %s" % (value, exc))


def days_between(iso_a, iso_b):
    """Whole days from iso_a to iso_b; positive when iso_b is later."""
    return (_as_date(iso_b) - _as_date(iso_a)).days


def _iter_observed_at(node, out, depth=0):
    if depth > _MAX_REF_DEPTH:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "observed_at" and isinstance(value, str):
                part = _date_part(value)
                if part:
                    out.append(part)
            else:
                _iter_observed_at(value, out, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _iter_observed_at(item, out, depth + 1)


def resolve_as_of(records, explicit, config=None):
    """BUILD-CONTRACT 7.4 as-of resolution: explicit -> max observed_at -> config default."""
    if explicit is not None and str(explicit).strip():
        text = str(explicit).strip()
        if not _DATE_RE.match(text):
            raise UsageError("--as-of %r is not a YYYY-MM-DD date" % explicit)
        try:
            _as_date(text)  # reject 2026-13-40
        except DataError as exc:
            # A well-shaped but impossible date is still a bad flag value (7.3: exit 2).
            raise UsageError("--as-of %s" % (exc,))
        return text
    observed = []
    _iter_observed_at(records, observed)
    if observed:
        return max(observed)
    cfg = config if config is not None else load_config()
    default = cfg.get("as_of_default")
    if isinstance(default, str) and _DATE_RE.match(default):
        return default
    raise ConfigError("scoring config has no usable as_of_default")


# --------------------------------------------------------------------------
# Deterministic ordering
# --------------------------------------------------------------------------


def _sort_value(record, extractor):
    if callable(extractor):
        try:
            return extractor(record)
        except Exception:
            return ABSENT
    if isinstance(record, dict):
        return record.get(extractor, ABSENT)
    return getattr(record, extractor, ABSENT)


def _type_rank(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float, Decimal)):
        return 0
    if isinstance(value, str):
        return 1
    return 2


def _compare_values(a, b):
    rank_a, rank_b = _type_rank(a), _type_rank(b)
    if rank_a != rank_b:
        return -1 if rank_a < rank_b else 1
    if rank_a == 0:
        da, db = _to_decimal(int(a) if isinstance(a, bool) else a), _to_decimal(
            int(b) if isinstance(b, bool) else b
        )
        if da == db:
            return 0
        return -1 if da < db else 1
    if rank_a == 1:
        if a == b:
            return 0
        return -1 if a < b else 1
    sa = json.dumps(a, sort_keys=True, ensure_ascii=False, default=str)
    sb = json.dumps(b, sort_keys=True, ensure_ascii=False, default=str)
    if sa == sb:
        return 0
    return -1 if sa < sb else 1


def stable_sort(records, keys):
    """Deterministic multi-key sort returning a NEW list.

    Missing values and the literal "unknown" sort LAST in both directions; the
    sort is stable, so the input order is the final tie-break (INV-30).
    """
    specs = []
    for entry in keys or []:
        if isinstance(entry, (tuple, list)):
            extractor = entry[0]
            direction = entry[1] if len(entry) > 1 else "asc"
        else:
            extractor, direction = entry, "asc"
        specs.append((extractor, "desc" if str(direction).lower() == "desc" else "asc"))

    def comparator(left, right):
        for extractor, direction in specs:
            va = _sort_value(left, extractor)
            vb = _sort_value(right, extractor)
            ma, mb = is_unknown(va), is_unknown(vb)
            if ma and mb:
                continue
            if ma:
                return 1
            if mb:
                return -1
            result = _compare_values(va, vb)
            if result == 0:
                continue
            return -result if direction == "desc" else result
        return 0

    return sorted(list(records or []), key=cmp_to_key(comparator))


# --------------------------------------------------------------------------
# Dependency-free JSON-Schema-subset validator (BUILD-CONTRACT 7.5)
# --------------------------------------------------------------------------


def _pjoin(path, key):
    return str(key) if path == "$" else "%s.%s" % (path, key)


def _pidx(path, index):
    return "%s[%d]" % (path, index)


def _json_type_ok(instance, type_name):
    if type_name == "object":
        return isinstance(instance, dict)
    if type_name == "array":
        return isinstance(instance, list)
    if type_name == "string":
        return isinstance(instance, str)
    if type_name == "boolean":
        return isinstance(instance, bool)
    if type_name == "null":
        return instance is None
    if type_name == "integer":
        if isinstance(instance, bool):
            return False
        if isinstance(instance, int):
            return True
        return isinstance(instance, float) and float(instance).is_integer()
    if type_name == "number":
        if isinstance(instance, bool):
            return False
        return isinstance(instance, (int, float))
    return True  # unknown type name: ignored, never an error


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _resolve_ref(ref, root, errors, path):
    if not isinstance(ref, str) or not ref.startswith("#"):
        return None
    pointer = ref[1:]
    if pointer.startswith("/"):
        pointer = pointer[1:]
    node = root
    if pointer:
        for raw in pointer.split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif isinstance(node, list):
                try:
                    node = node[int(token)]
                except (ValueError, IndexError):
                    errors.append("%s: unresolvable $ref %s" % (path, ref))
                    return None
            else:
                errors.append("%s: unresolvable $ref %s" % (path, ref))
                return None
    return node


def _describe(value):
    text = _canonical(value)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def _validate_node(instance, schema, root, path, errors, depth):
    if depth > _MAX_REF_DEPTH:
        errors.append("%s: $ref recursion limit (%d) exceeded" % (path, _MAX_REF_DEPTH))
        return
    if schema is True:
        return
    if schema is False:
        errors.append("%s: no value is allowed here" % path)
        return
    if not isinstance(schema, dict):
        return

    # 2020-12: keywords alongside $ref apply IN ADDITION to the referenced schema.
    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root, errors, path)
        if isinstance(target, (dict, bool)):
            _validate_node(instance, target, root, path, errors, depth + 1)

    if "type" in schema:
        declared = schema["type"]
        names = declared if isinstance(declared, list) else [declared]
        if not any(_json_type_ok(instance, str(n)) for n in names):
            errors.append(
                "%s: %s is not of type %s"
                % (path, _describe(instance), " or ".join('"%s"' % n for n in names))
            )
            return

    if "enum" in schema and isinstance(schema["enum"], list):
        if not any(_canonical(instance) == _canonical(option) for option in schema["enum"]):
            errors.append(
                "%s: %s is not one of %s" % (path, _describe(instance), _describe(schema["enum"]))
            )

    if "const" in schema:
        if _canonical(instance) != _canonical(schema["const"]):
            errors.append("%s: %s is not the constant %s" % (path, _describe(instance), _describe(schema["const"])))

    if isinstance(instance, str):
        _validate_string(instance, schema, path, errors)
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        _validate_number(instance, schema, path, errors)
    if isinstance(instance, list):
        _validate_array(instance, schema, root, path, errors, depth)
    if isinstance(instance, dict):
        _validate_object(instance, schema, root, path, errors, depth)

    _validate_combinators(instance, schema, root, path, errors, depth)


def _validate_string(instance, schema, path, errors):
    if "minLength" in schema and len(instance) < schema["minLength"]:
        errors.append("%s: string is shorter than minLength %s" % (path, schema["minLength"]))
    if "maxLength" in schema and len(instance) > schema["maxLength"]:
        errors.append("%s: string is longer than maxLength %s" % (path, schema["maxLength"]))
    if "pattern" in schema:
        try:
            if re.search(schema["pattern"], instance) is None:
                errors.append("%s: %s does not match pattern %s" % (path, _describe(instance), schema["pattern"]))
        except re.error:
            pass  # an invalid pattern is a schema bug, not an instance error
    fmt = schema.get("format")
    if fmt == "date" and _DATE_RE.match(instance) is None:
        errors.append("%s: %s is not a date (YYYY-MM-DD)" % (path, _describe(instance)))
    elif fmt == "date-time" and _DATE_TIME_RE.match(instance) is None:
        errors.append("%s: %s is not an RFC3339 date-time" % (path, _describe(instance)))
    elif fmt == "uri" and _URI_RE.match(instance) is None:
        errors.append("%s: %s is not a URI" % (path, _describe(instance)))


def _validate_number(instance, schema, path, errors):
    value = _to_decimal(instance)
    if "minimum" in schema and value < _to_decimal(schema["minimum"]):
        errors.append("%s: %s is less than minimum %s" % (path, instance, schema["minimum"]))
    if "maximum" in schema and value > _to_decimal(schema["maximum"]):
        errors.append("%s: %s is greater than maximum %s" % (path, instance, schema["maximum"]))
    if "exclusiveMinimum" in schema and value <= _to_decimal(schema["exclusiveMinimum"]):
        errors.append(
            "%s: %s is not greater than exclusiveMinimum %s" % (path, instance, schema["exclusiveMinimum"])
        )
    if "exclusiveMaximum" in schema and value >= _to_decimal(schema["exclusiveMaximum"]):
        errors.append(
            "%s: %s is not less than exclusiveMaximum %s" % (path, instance, schema["exclusiveMaximum"])
        )


def _validate_array(instance, schema, root, path, errors, depth):
    if "minItems" in schema and len(instance) < schema["minItems"]:
        errors.append("%s: array has %d items, fewer than minItems %s" % (path, len(instance), schema["minItems"]))
    if "maxItems" in schema and len(instance) > schema["maxItems"]:
        errors.append("%s: array has %d items, more than maxItems %s" % (path, len(instance), schema["maxItems"]))
    if schema.get("uniqueItems") is True:
        seen = set()
        for index, item in enumerate(instance):
            marker = _canonical(item)
            if marker in seen:
                errors.append("%s: duplicate item %s violates uniqueItems" % (_pidx(path, index), _describe(item)))
            seen.add(marker)
    items = schema.get("items")
    if isinstance(items, list):
        for index, item in enumerate(instance):
            if index < len(items):
                _validate_node(item, items[index], root, _pidx(path, index), errors, depth + 1)
    elif items is not None:
        for index, item in enumerate(instance):
            _validate_node(item, items, root, _pidx(path, index), errors, depth + 1)


def _validate_object(instance, schema, root, path, errors, depth):
    for key in schema.get("required", []) or []:
        if key not in instance:
            errors.append("%s: %r is a required property" % (path, key))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            if key in instance:
                _validate_node(instance[key], subschema, root, _pjoin(path, key), errors, depth + 1)
    if "additionalProperties" in schema:
        extra = schema["additionalProperties"]
        known = set(properties.keys()) if isinstance(properties, dict) else set()
        leftovers = [key for key in instance.keys() if key not in known]
        if extra is False:
            for key in leftovers:
                errors.append("%s: %r is not an allowed property" % (path, key))
        elif isinstance(extra, dict):
            for key in leftovers:
                _validate_node(instance[key], extra, root, _pjoin(path, key), errors, depth + 1)


def _validate_combinators(instance, schema, root, path, errors, depth):
    for subschema in schema.get("allOf", []) or []:
        _validate_node(instance, subschema, root, path, errors, depth + 1)

    if isinstance(schema.get("anyOf"), list) and schema["anyOf"]:
        branch_errors = []
        for subschema in schema["anyOf"]:
            local = []
            _validate_node(instance, subschema, root, path, local, depth + 1)
            if not local:
                branch_errors = None
                break
            branch_errors.extend(local)
        if branch_errors:
            errors.append("%s: does not match any schema in anyOf" % path)
            errors.extend(branch_errors)

    if isinstance(schema.get("oneOf"), list) and schema["oneOf"]:
        matched = 0
        closest = None
        for subschema in schema["oneOf"]:
            local = []
            _validate_node(instance, subschema, root, path, local, depth + 1)
            if not local:
                matched += 1
            elif closest is None or len(local) < len(closest):
                closest = local
        if matched != 1:
            errors.append("%s: matched %d schemas, expected exactly 1" % (path, matched))
            if matched == 0 and closest:
                errors.extend(closest)

    if "if" in schema:
        probe = []
        _validate_node(instance, schema["if"], root, path, probe, depth + 1)
        branch = "then" if not probe else "else"
        if branch in schema:
            _validate_node(instance, schema[branch], root, path, errors, depth + 1)


def validate(instance, schema):
    """Dependency-free JSON-Schema-subset validator.

    Returns a list of human-readable error strings, each of the form
    '<json-path>: <message>', e.g. 'results[0].component_scores.product_fit: 101 is
    greater than maximum 100'. Returns [] when valid. NEVER raises, whatever the
    instance or schema contains (an unsupported keyword is ignored, not an error).

    Supported keyword subset:
      type, properties, required, additionalProperties, items,
      enum, const, oneOf, anyOf, allOf,
      if, then, else,
      minimum, maximum, exclusiveMinimum, exclusiveMaximum,
      minItems, maxItems, uniqueItems, minLength, maxLength, pattern,
      format (date, date-time, uri -- light regex checks only),
      $ref to local "#/$defs/..." (and "#/definitions/..."), $defs.
    Annotation-only keywords that are read but never constrain: $schema, $id,
    title, description, $comment, default.

    Semantics notes:
      - "integer" accepts int but NOT bool; "number" accepts int/float but NOT bool.
      - oneOf reports 'matched N schemas, expected exactly 1' with the sub-errors
        of the closest branch; anyOf reports the sub-errors of every branch.
      - additionalProperties:false lists each offending key by name.
      - pattern uses re.search (JSON Schema semantics), not re.fullmatch.
      - $ref recursion is depth-limited (64); exceeding the limit yields one error
        string rather than a RecursionError.
      - if/then/else is an APPLICATOR PAIR, not an assertion: validate the instance
        against `if`; a failing `if` produces NO errors and selects `else`, a
        passing `if` selects `then`. The selected subschema is then applied to the
        SAME instance and its errors are reported. `if` alone is a no-op.
      - The schemas are drafted against JSON Schema 2020-12: keywords that appear
        ALONGSIDE $ref in the same object are applied IN ADDITION to the referenced
        schema and are never discarded. Draft-07 "$ref replaces the whole schema"
        semantics are FORBIDDEN."""
    errors = []
    try:
        _validate_node(instance, schema, schema, "$", errors, 0)
    except Exception as exc:  # the contract forbids raising, whatever the input
        errors.append("$: validator error: %s" % exc)
    return errors


# --------------------------------------------------------------------------
# Rendering helpers (BUILD-CONTRACT 10.5)
# --------------------------------------------------------------------------


def confidence_band(confidence, config=None):
    """HIGH >= 0.75, MEDIUM >= 0.5, else LOW; unknown renders LOW."""
    if is_unknown(confidence):
        return "LOW"
    try:
        value = _to_decimal(confidence)
    except DataError:
        return "LOW"
    bands = _CONFIDENCE_BAND_FALLBACK
    try:
        cfg = config if config is not None else load_config()
        spec = cfg.get("output", {}).get("render_confidence_as")
        if isinstance(spec, str):
            found = re.findall(r"(HIGH|MEDIUM)\s*>=\s*([0-9.]+)", spec)
            if len(found) >= 2:
                bands = tuple((name, Decimal(number)) for name, number in found[:2])
    except Exception:
        bands = _CONFIDENCE_BAND_FALLBACK
    for name, floor in bands:
        if value >= floor:
            return name
    return "LOW"


def _format_number(value):
    if isinstance(value, Decimal):
        value = _plain_number(value)
    if isinstance(value, float) and float(value).is_integer():
        value = int(value)
    if isinstance(value, int):
        return "{:,}".format(value)
    return "{:,}".format(value)


def format_quantity(value, unit=None):
    """Render a quantity for human output per BUILD-CONTRACT 10.5."""
    if is_unknown(value):
        return UNKNOWN
    try:
        rng = coerce_range(value)
    except DataError:
        return UNKNOWN
    if rng is None:
        return UNKNOWN
    label = unit if unit is not None else rng.get("unit", "units")
    if rng["min"] == rng["max"]:
        text = _format_number(rng["min"])
    else:
        text = "%s-%s" % (_format_number(rng["min"]), _format_number(rng["max"]))
    if label:
        return "%s %s" % (text, label)
    return text


# --------------------------------------------------------------------------
# Vocabulary helpers (BUILD-CONTRACT 8.5, 8.6)
# --------------------------------------------------------------------------


def _slugify(token):
    text = unicodedata.normalize("NFKC", token).strip().casefold()
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def normalize_category(token):
    """Canonical category slug, or None for the vertical markers / an empty token."""
    if not isinstance(token, str) or not token.strip():
        return None
    slug = _slugify(token)
    if not slug:
        return None
    candidates = [slug]
    if _HANGUL_RE.search(slug):
        candidates.append(slug.replace("_", ""))
    graded = _CATEGORY_GRADE_SUFFIX_RE.sub("", slug)
    if graded and graded != slug:
        candidates.append(graded)
    if slug.endswith("s") and len(slug) > 3:
        candidates.append(slug[:-1])  # plural of a vocabulary slug ("serums", "toners")
    for candidate in candidates:
        if candidate in CATEGORY_SYNONYMS:
            return CATEGORY_SYNONYMS[candidate]  # may be None for a vertical marker
        if candidate in VERTICAL_MARKERS:
            return None
        if candidate in CATEGORY_VOCABULARY:
            return candidate
    return slug


def is_known_category(slug):
    """True when `slug` is a slug of the BUILD-CONTRACT 8.5 vocabulary."""
    return isinstance(slug, str) and slug in CATEGORY_VOCABULARY


def normalize_category_list(raw, notes, label):
    """Seller-side / query-side category normalisation.

    None = unknown; [] = verified empty; list = the in-vocabulary slugs. A vertical marker
    and a slug outside the 8.5 vocabulary are both DROPPED with a note: an unmapped slug is
    a term the vocabulary does not know, never evidence of a mismatch, so it must not reach
    category_no_match or HF-01 (INV-07). A non-empty list that keeps nothing is UNKNOWN.
    """
    if not isinstance(raw, list):
        return None
    out = []
    for slug in raw:
        token = normalize_category(slug) if isinstance(slug, str) else None
        if token is None:
            notes.append(
                "%s category token %r is a vertical marker, not a category; dropped before "
                "scoring (BUILD-CONTRACT 8.5)" % (label, slug)
            )
            continue
        if not is_known_category(token):
            notes.append(
                "%s category token %r is not in the BUILD-CONTRACT 8.5 vocabulary; dropped "
                "before scoring (unmapped, treated as not stated rather than as a mismatch)"
                % (label, slug)
            )
            continue
        if token not in out:
            out.append(token)
    if raw and not out:
        return None
    return out


def _category_parent(slug):
    if slug in CATEGORY_PARENTS:
        return CATEGORY_PARENTS[slug]
    if slug in CATEGORY_ROOTS:
        return slug
    return None


def _normalized_category_set(slugs):
    if isinstance(slugs, str):
        slugs = [slugs]
    out = []
    for raw in slugs or []:
        slug = normalize_category(raw)
        # An unmapped slug relates to nothing: two identical unknown words are not an
        # evidenced category match, and an unknown word is not an evidenced mismatch.
        if slug and is_known_category(slug) and slug not in out:
            out.append(slug)
    return out


def category_relation(a_slugs, b_slugs):
    """Best relation between two category sets: exact > parent > adjacent > none."""
    left = _normalized_category_set(a_slugs)
    right = _normalized_category_set(b_slugs)
    if not left or not right:
        return "none"
    best = "none"
    for a in left:
        for b in right:
            if a == b:
                return "exact"
            if CATEGORY_PARENTS.get(a) == b or CATEGORY_PARENTS.get(b) == a:
                best = "parent"
                continue
            if best == "parent":
                continue
            pa, pb = _category_parent(a), _category_parent(b)
            if pa and pb:
                if pa == pb or pb in CATEGORY_ADJACENCY.get(pa, frozenset()):
                    best = "adjacent"
    return best


def verification_gaps(config, entity, record, penalties, record_categories=None,
                      query_categories=None):
    """Labels of the configured verification gaps that apply to one scored record.

    SCORING-CONTRACT 0.4 / scoring.config.json verification_gaps. missing[] carries the
    penalty labels first and these after them: facts a counterparty needs before first
    contact that no criterion took the unknown path for. A gap never changes a score,
    never rejects and never emits an unknown_penalty_applied entry. Returns labels in
    config order; the caller de-duplicates against the penalty labels.
    """
    spec = (config.get("verification_gaps") or {}).get(entity) or []
    covered = set()
    for entry in penalties or []:
        if isinstance(entry, dict):
            for path in entry.get("unknown_inputs") or []:
                covered.add(path)
    labels = []
    for gap in spec:
        if not isinstance(gap, dict):
            continue
        field = gap.get("field")
        if field:
            value = record.get(field) if isinstance(record, dict) else None
            if (value is None or is_unknown(value)) and "%s.%s" % (entity, field) not in covered:
                labels.append(gap["label"])
            continue
        if gap.get("kind") == "requested_categories":
            if not query_categories or record_categories is None:
                continue
            relation = category_relation(record_categories, query_categories)
            if relation == "exact":
                continue
            base = gap["label_broader"] if relation == "parent" else gap["label_not_evidenced"]
            requested = ", ".join(slug.replace("_", " ") for slug in query_categories)
            labels.append("%s: %s" % (base, requested))
    return labels


def normalize_unit(value):
    """Canonical MOQ / quantity unit: UNIT_SYNONYMS applied after NFKC + casefold and
    whitespace / period removal. Absent or "unknown" means "units" (SCORING-CONTRACT 2.3)."""
    if value is None or is_unknown(value):
        return "units"
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[\s.]+", "", text)
    if not text:
        return "units"
    return UNIT_SYNONYMS.get(text, text)


def normalize_country(value):
    """ISO-3166-1 alpha-2 for a code or a country name, "unknown", or None when unconvertible.

    The name table is consulted before the two-letter shape test so that "UK" becomes GB;
    any other two-letter value is upper-cased only when it is an assigned ISO-3166-1 code
    (ISO_3166_ALPHA2), so "XX" or "EU" is unconvertible rather than a fake country
    (BUILD-CONTRACT 8.7).
    """
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    if is_unknown(text):
        return UNKNOWN
    code = COUNTRY_ALIASES.get(_country_key(text))
    if code:
        return code
    if re.match(r"^[A-Za-z]{2}$", text) and text.upper() in ISO_3166_ALPHA2:
        return text.upper()
    return None


def normalize_certification(token):
    """Canonical certification token per BUILD-CONTRACT 8.6. Never returns None."""
    if not isinstance(token, str) or not token.strip():
        return ""
    text = unicodedata.normalize("NFKC", token).strip().upper()
    key = re.sub(r"[^0-9A-Z가-힣]+", "", text, flags=re.UNICODE)
    if key in CERTIFICATION_SYNONYMS:
        return CERTIFICATION_SYNONYMS[key]
    underscored = re.sub(r"[^0-9A-Z가-힣]+", "_", text, flags=re.UNICODE).strip("_")
    return re.sub(r"_+", "_", underscored)


# --------------------------------------------------------------------------
# Evidence quality (SCORING-CONTRACT section 4 - one implementation, three callers)
# --------------------------------------------------------------------------


def _evidence_recency_multiplier(item, as_of, evidence_config):
    source_date = item.get("source_date")
    if is_unknown(source_date):
        return _to_decimal(evidence_config.get("unknown_source_date_multiplier", 0))
    try:
        age_days = max(0, days_between(source_date, as_of))
    except DataError:
        return _to_decimal(evidence_config.get("unknown_source_date_multiplier", 0))
    for bucket in evidence_config.get("recency_buckets", []) or []:
        limit = bucket.get("max_age_days")
        if limit is None or age_days <= int(limit):
            return _to_decimal(bucket.get("multiplier", 0))
    return _to_decimal(0)


def _evidence_age_days(item, as_of):
    """Age of one evidence item in days, or None when its source_date is unknown."""
    if is_unknown(item.get("source_date")):
        return None
    try:
        return max(0, days_between(item.get("source_date"), as_of))
    except DataError:
        return None


def _evidence_item_strength(item, as_of, evidence_config):
    tier_points = evidence_config.get("source_tier_points", {}) or {}
    tier = item.get("source_tier")
    points = tier_points.get(str(tier))
    if points is None:
        return 0
    return round_half_up(_to_decimal(points) * _evidence_recency_multiplier(item, as_of, evidence_config))


def _evidence_best_item(items, as_of, evidence_config):
    """argmax item_strength; ties: lower tier, then newer source_date, then smaller id."""

    def rank(item):
        strength = _evidence_item_strength(item, as_of, evidence_config)
        tier = item.get("source_tier")
        tier_key = int(tier) if isinstance(tier, int) and not isinstance(tier, bool) else 99
        date_part = _date_part(item.get("source_date")) or ""
        # newer first => invert by sorting on the negated ordinal; "" (unknown) sorts last
        date_key = "0" if not date_part else "1" + date_part
        return (-strength, tier_key, _InverseString(date_key), str(item.get("evidence_id") or ""))

    return sorted(items, key=rank)[0]


class _InverseString(str):
    """String wrapper whose ordering is reversed (descending source_date)."""

    def __lt__(self, other):
        return str.__gt__(self, other)

    def __gt__(self, other):
        return str.__lt__(self, other)

    def __le__(self, other):
        return str.__ge__(self, other)

    def __ge__(self, other):
        return str.__le__(self, other)


def unresolved_conflict_count(record):
    """Number of unresolved conflicts on a record (SCORING-CONTRACT 4.4).

    An unresolved conflict is a non-empty `conflicts_with` on an evidence item whose claim
    has no conflicts[] entry. It is counted ONCE PER UNORDERED PAIR of evidence ids:
    references/evidence-policy.md tells the agent to cross-link both items of a
    disagreeing pair, and dedupe_companies.py writes both directions, so counting items
    priced one reciprocal conflict as two (-20 instead of -10).
    """
    record = record if isinstance(record, dict) else {}
    resolved_fields = set()
    for conflict in record.get("conflicts") or []:
        if isinstance(conflict, dict) and isinstance(conflict.get("field"), str):
            resolved_fields.add(conflict["field"])
    pairs = set()
    items = record.get("evidence") if isinstance(record.get("evidence"), list) else []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        links = item.get("conflicts_with")
        if not isinstance(links, list) or not links or item.get("claim") in resolved_fields:
            continue
        ident = item.get("evidence_id")
        own = ident if isinstance(ident, str) and ident else "\x00item-%d" % (index,)
        for link in links:
            pairs.add(frozenset((own, str(link))))
    return len(pairs)


def record_confidence(record, entity, evidence_quality_score, config=None):
    """SCORING-CONTRACT 0.7 confidence, every constant read from config["confidence"].

    One implementation for all three scorers (INV-29): score_match.py previously carried
    its own copy with the constants typed in, so --config never reached match confidence.
    Never reads any score of the company except its evidence quality.
    """
    cfg = config if config is not None else load_config()
    conf = cfg["confidence"]
    coverage = conf["coverage_factor"]
    record = record if isinstance(record, dict) else {}
    claims = cfg["evidence"]["material_claims"][entity]
    unknown_claims = sum(1 for claim in claims if is_unknown(record.get(claim)))
    factor = _to_decimal(coverage["base"]) + _to_decimal(coverage["per_unknown_material_claim"]) * Decimal(
        unknown_claims
    )
    factor = max(_to_decimal(coverage["floor"]), factor)
    stale = _to_decimal(conf["stale_multiplier"]) if record.get("stale") is True else Decimal(1)
    conflicts = (
        _to_decimal(conf["unresolved_conflict_multiplier"]) if unresolved_conflict_count(record) else Decimal(1)
    )
    value = _to_decimal(evidence_quality_score) / Decimal(100) * factor * stale * conflicts
    rounded = _to_decimal(round_half_up(value, 2))
    rounded = max(_to_decimal(conf["min"]), min(Decimal(1), rounded))
    return float(rounded)


def evidence_quality(record, claim_set, as_of, config=None):
    """SCORING-CONTRACT section 4 evidence-quality sub-score, integer 0..100."""
    cfg = config if config is not None else load_config()
    evidence_config = cfg.get("evidence", {}) or {}
    record = record if isinstance(record, dict) else {}
    items = [item for item in (record.get("evidence") or []) if isinstance(item, dict)]
    if not items:
        return int(_to_decimal(evidence_config.get("no_evidence_score", 0)))

    claims = list(claim_set or [])
    by_claim = {}
    for item in items:
        by_claim.setdefault(item.get("claim"), []).append(item)

    covered = [claim for claim in claims if by_claim.get(claim)]
    if covered:
        total = Decimal(0)
        for claim in covered:
            best = _evidence_best_item(by_claim[claim], as_of, evidence_config)
            total += _to_decimal(_evidence_item_strength(best, as_of, evidence_config))
        source_strength = total / Decimal(len(covered))
    else:
        source_strength = Decimal(0)

    coverage = (
        Decimal(100) * Decimal(len(covered)) / Decimal(len(claims)) if claims else Decimal(0)
    )

    domains = set()
    for item in items:
        domain = canonical_domain(item.get("source_domain") or item.get("source_url"))
        if domain:
            domains.add(domain)
    domain_count = len(domains)
    corroboration = min(Decimal(100), max(Decimal(0), Decimal(50) * Decimal(domain_count - 1)))

    official_bonus = Decimal(0)
    for item in items:
        if item.get("is_official") is True and item.get("source_tier") == 1:
            official_bonus = _to_decimal(evidence_config.get("official_bonus", 0))
            break

    multi_config = evidence_config.get("multi_source_bonus", {}) or {}
    per_domain = _to_decimal(multi_config.get("per_additional_independent_domain", 0))
    multi_cap = _to_decimal(multi_config.get("max", 0))
    multi_source_bonus = min(multi_cap, max(Decimal(0), per_domain * Decimal(domain_count - 1)))

    stale_threshold = int(evidence_config.get("stale_threshold_days", 730))
    stale_penalty = Decimal(0)
    record_is_stale = record.get("stale") is True
    all_covered_stale = False
    if covered:
        all_covered_stale = True
        for claim in covered:
            best = _evidence_best_item(by_claim[claim], as_of, evidence_config)
            age = _evidence_age_days(best, as_of)
            if age is None or age <= stale_threshold:
                all_covered_stale = False
                break
    if record_is_stale or all_covered_stale:
        stale_penalty = _to_decimal(evidence_config.get("stale_penalty", 0))

    unresolved = unresolved_conflict_count(record)
    conflict_penalty_total = Decimal(0)
    if unresolved:
        per_conflict = _to_decimal(evidence_config.get("conflict_penalty", 0))
        floor = _to_decimal(evidence_config.get("conflict_penalty_max", 0))
        conflict_penalty_total = max(floor, per_conflict * Decimal(unresolved))

    inferred_penalty = Decimal(0)
    for claim in covered:
        claim_items = by_claim[claim]
        if claim_items and all(item.get("inferred") is True for item in claim_items):
            inferred_penalty = _to_decimal(evidence_config.get("inferred_penalty", 0))
            break

    components = evidence_config.get("components", {}) or {}
    w_source = _to_decimal(components.get("source_strength", {}).get("weight", 0))
    w_coverage = _to_decimal(components.get("coverage", {}).get("weight", 0))
    w_corroboration = _to_decimal(components.get("corroboration", {}).get("weight", 0))

    raw = (
        w_source * source_strength
        + w_coverage * coverage
        + w_corroboration * corroboration
        + official_bonus
        + multi_source_bonus
        + stale_penalty
        + conflict_penalty_total
        + inferred_penalty
    )
    return int(max(0, min(100, round_half_up(raw))))


# --------------------------------------------------------------------------
# Calibration review vocabularies and free-text personal-data detectors
# (scripts/make_review_sheet.py, scripts/acceptance_report.py)
#
# These are VOCABULARIES and shapes, not tunable numbers, so INV-29 is unaffected -
# the same ground on which FREE_MAIL_DOMAINS and EXPORT_REGION_COUNTRIES live here.
# Every tunable number the two calibration scripts use (minimum sample, score-band
# edges, threshold-sweep range) is read from scoring.config.json "calibration".
# --------------------------------------------------------------------------

#: The ten columns of a review sheet, in order. The last five are left empty for the
#: operator; the first five are copied from the scored document.
REVIEW_SHEET_COLUMNS = (
    "record_id",
    "entity_type",
    "company_name",
    "website",
    "country",
    "verdict",
    "reason_code",
    "note",
    "reviewer_role",
    "reviewed_on",
)

#: The columns the operator fills in. A blind sheet ships them empty.
REVIEW_OPERATOR_COLUMNS = ("verdict", "reason_code", "note", "reviewer_role", "reviewed_on")

#: Columns appended by `make_review_sheet.py --no-blind` only. They are exactly the
#: three facts that would anchor a reviewer on the rubric, which is why the default
#: sheet carries none of them.
REVIEW_UNBLINDED_COLUMNS = ("rank", "score", "qualified")

#: The `verdict` vocabulary. `unsure` is counted and reported separately, never folded
#: into either side of the Human Acceptance Rate (PRD 17).
REVIEW_VERDICTS = ("accept", "reject", "unsure")

#: The verdict that REQUIRES a reason_code.
REVIEW_VERDICT_NEEDS_REASON = "reject"

#: The `reason_code` vocabulary. Optional on `accept` / `unsure`, required on `reject`.
REVIEW_REASON_CODES = (
    "wrong_company_type",
    "wrong_vertical",
    "wrong_market",
    "inactive_or_unreachable",
    "duplicate",
    "evidence_wrong",
    "other",
)

#: The literal a sheet carries when the scored document does not hold the value at all.
#: Never blank: a blank cell means "the operator has not filled this in" (INV-02).
REVIEW_UNKNOWN_CELL = UNKNOWN

#: The literal a BLIND sheet carries in a column that would otherwise tell an excluded
#: record from a returned one. Distinct from REVIEW_UNKNOWN_CELL on purpose: "unknown"
#: is a fact about the record, "not_shown" is a fact about the sheet.
REVIEW_NEUTRAL_CELL = "not_shown"

#: Leading characters a spreadsheet reads as the start of a FORMULA rather than text.
#: A company name and a website come off a harvested public page, so either can begin
#: with one; `=cmd|'/c calc'!A1` in a company name is a working attack on the operator
#: who opens the sheet. Prefixing with an apostrophe is the standard neutralisation and
#: is visible to the reader, which is why the sheet does not silently drop the value.
CSV_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
CSV_FORMULA_GUARD = "'"

_EMAIL_LIKE_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
#: The "a [at] b.example" family an operator types to dodge a naive scanner.
#:
#: The "at" MUST be delimited - bracketed, or whitespace on both sides - or the scan
#: fires on the middle of "notification" and refuses a BPOM number. The whitespace form
#: additionally requires the local part to look like a mailbox (a dot, digit, or one of
#: `_%+-`) rather than an ordinary word, so the legitimate note "found at
#: gulfglow.example" passes while "sourcing.lead at gulfglow.example" does not. The
#: bracketed form needs no such guard: nobody writes "[at]" by accident.
_EMAIL_OBFUSCATED_RE = re.compile(
    r"(?:[A-Za-z0-9._%+\-]+(?:\s*[\[({<]\s*(?:at|골뱅이)\s*[\])}>]\s*)"
    r"|[A-Za-z0-9._%+\-]*[0-9._%+\-][A-Za-z0-9._%+\-]*\s+(?:at|골뱅이)\s+)"
    r"[A-Za-z0-9\-]+(?:\s*[\[({<]\s*(?:dot|점)\s*[\])}>]\s*|\s*\.\s*)"
    r"[A-Za-z0-9.\-]*[A-Za-z]{2,}",
    re.IGNORECASE,
)
#: Substrings removed before the telephone scan, because each is a long digit run that
#: is definitively not a number anybody dials.
_URL_ANYWHERE_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
#: A telephone number written INSIDE a URL ("www.x.example/010-1234-5678",
#: "...?tel=01012345678"). URLs are scrubbed before the telephone pass so that a
#: registry URL ending in a numeric id is not refused, which would otherwise let a
#: number glued to a URL walk past the scan. Only a separator-formatted trunk-prefix
#: number or a tel/phone/mobile query label counts here; a bare id run does not.
_PHONE_IN_URL_RE = re.compile(
    r"(?:(?<![0-9])0[0-9]{1,3}[-.][0-9]{3,4}[-.][0-9]{4}(?![0-9])"
    r"|(?:tel|phone|mobile|whatsapp)[=:/][+]?[0-9][0-9\-.]{6,}"
    # A slug label ("/team/jane-mobile-0501234567"): the label must start a word, so
    # "hotel-2024..." in a path is not read as "tel-2024...".
    r"|(?<![A-Za-z])(?:tel|phone|mobile|whatsapp)[_-][+]?[0-9][0-9\-.]{6,})",
    re.IGNORECASE,
)
_ISO_DATE_ANYWHERE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

#: A telephone SIGNAL, not merely a long number. Registration numbers, notification
#: numbers, certificate numbers, year ranges and money amounts are all long digit runs
#: that operators are explicitly told to record (a CDSCO registration certificate
#: number, a BPOM notification number, a registry URL), so a digit count alone refuses
#: the notes the protocol asks for. One of three things must be true instead:
#:   * an international prefix "+" immediately in front of the run;
#:   * a trunk-prefix "0" starting a run of at least 9 digits;
#:   * an explicit telephone label within _PHONE_LABEL_WINDOW characters in front.
_PHONE_RUN_RE = re.compile(r"(?<![0-9])(\+?)([0-9][0-9 ()./\-]{5,}[0-9])(?![0-9])")
_PHONE_LABEL_RE = re.compile(
    r"(?:tel|phone|mobile|cell|whatsapp|fax|hp|휴대|휴대폰|전화|연락처|핸드폰)"
    r"[\s.:：/\-]*$",
    re.IGNORECASE,
)
_PHONE_LABEL_WINDOW = 24
_PHONE_MIN_DIGITS = 7
_PHONE_TRUNK_MIN_DIGITS = 9


def personal_data_hits(text):
    """Labels of the personal-data shapes present in one free-text cell.

    INV-31 and INV-25 forbid a personal address, a direct dial or a named individual
    anywhere this package produces or consumes, notes included. A reviewer typing a
    contact into the `note` or `reviewer_role` column of a review sheet is the one path
    by which such a value could enter the calibration loop, so acceptance_report.py
    refuses the sheet rather than aggregating it. Returns [] for a clean cell; never
    raises.

    The scan is deliberately asymmetric between the two shapes it looks for. An "@"
    inside a word is almost never anything but an address, so email detection is broad
    and also catches the "a (at) b.example" obfuscation. A long run of digits, by
    contrast, is usually NOT a telephone number in this vertical: the discovery
    playbooks tell an operator to record a CDSCO registration certificate number, a BPOM
    notification number, an ISO certificate number, a registry URL and a trading period,
    and every one of those is a longer digit run than a phone number. Refusing those
    would refuse the notes the protocol asks for, so a telephone needs a telephone
    SIGNAL - a "+", a trunk-prefix "0" on a long enough run, or a nearby tel/phone/전화
    label - and a bare number passes. Input is NFKC-normalised first, so full-width
    digits cannot walk past the scan.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    normalised = unicodedata.normalize("NFKC", text)
    # A percent-encoded "%40" or "%2B" hides an address or a "+" prefix from the plain
    # scan, so the email and in-URL checks also read the decoded form. The free-text
    # telephone pass below does not: decoding "%20" would split a URL and expose its
    # numeric path segments as if they were prose.
    decoded = _percent_decode(normalised)
    hits = []
    if any(_EMAIL_LIKE_RE.search(form) or _EMAIL_OBFUSCATED_RE.search(form)
           for form in (normalised, decoded)):
        hits.append("an email address")
    for url in _URL_ANYWHERE_RE.findall(normalised):
        if _PHONE_IN_URL_RE.search(url) or _PHONE_IN_URL_RE.search(_percent_decode(url)):
            hits.append("a phone-number-like string")
            return hits
    scrubbed = _URL_ANYWHERE_RE.sub(" ", normalised)
    scrubbed = _ISO_DATE_ANYWHERE_RE.sub(" ", scrubbed)
    for match in _PHONE_RUN_RE.finditer(scrubbed):
        run = match.group(2)
        digits = [char for char in run if char.isdigit()]
        if len(digits) < _PHONE_MIN_DIGITS:
            continue
        if match.group(1) == "+":
            hits.append("a phone-number-like string")
            break
        if run[0] == "0" and len(digits) >= _PHONE_TRUNK_MIN_DIGITS:
            hits.append("a phone-number-like string")
            break
        start = max(0, match.start() - _PHONE_LABEL_WINDOW)
        if _PHONE_LABEL_RE.search(scrubbed[start:match.start()]):
            hits.append("a phone-number-like string")
            break
    return hits


def _percent_decode(text):
    """text with %XX escapes decoded (NFKC again), or text itself when there are none."""
    if "%" not in text:
        return text
    from urllib.parse import unquote

    return unicodedata.normalize("NFKC", unquote(text))


def _formula_lead(value):
    """True when a spreadsheet may read this cell as a formula.

    The raw first character is checked, and so is the first character after leading
    whitespace and NFKC folding: " =1+1" is text in most spreadsheets, but an importer
    that trims cells (as many CRM and admin import pages do) stores "=1+1", and a
    full-width "＝" folds to "=" in the same way.
    """
    if not isinstance(value, str) or not value:
        return False
    if value[0] in CSV_FORMULA_LEAD:
        return True
    folded = unicodedata.normalize("NFKC", value).lstrip()
    return bool(folded) and folded[0] in CSV_FORMULA_LEAD


def csv_safe_cell(value):
    """One CSV cell with any spreadsheet formula lead neutralised.

    A leading "=", "+", "-", "@", tab or carriage return makes a spreadsheet evaluate
    the cell instead of showing it, and `company_name` / `website` come straight off a
    harvested public page. The same holds after leading whitespace or a full-width
    form (see _formula_lead). The apostrophe prefix is the standard fix and leaves the
    original text readable.
    """
    if _formula_lead(value):
        return CSV_FORMULA_GUARD + value
    return value


def csv_needs_formula_guard(value):
    """True when csv_safe_cell would change this value."""
    return _formula_lead(value)


def blind_order_key(record_id):
    """The sha256 hex digest that orders a blind review sheet.

    Sorting on it shuffles the rows out of rank order deterministically, so the same
    document always produces the same sheet (INV-13) while the reviewer reads the
    candidates in an order that carries no information about the rubric.
    """
    import hashlib

    return hashlib.sha256(str(record_id).encode("utf-8")).hexdigest()


def calibration_config(config):
    """The `calibration` block of scoring.config.json, or a ConfigError."""
    block = config.get("calibration") if isinstance(config, dict) else None
    if not isinstance(block, dict):
        raise ConfigError("scoring config carries no 'calibration' block")
    return block
