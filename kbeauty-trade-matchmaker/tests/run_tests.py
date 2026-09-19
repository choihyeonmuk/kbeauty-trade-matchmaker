#!/usr/bin/env python3
"""Golden test harness for the kbeauty-trade-matchmaker skill package.

Stdlib only (python3.9 - 3.14), no pip dependency, no network, no wall-clock
dependency: every script invocation is pinned to --as-of 2026-09-12.

Run it from the package root or from inside tests/:

    python3 tests/run_tests.py
    cd tests && python3 run_tests.py

What it does, in order (BUILD-CONTRACT.md 13.1):

  1. schema self-check      - schemas/*.json parse, every $ref resolves, every
                              keyword is inside the _common.validate subset,
                              shared $defs agree structurally, versions agree
                              with scoring.config.json.
  2. fixture validation     - every fixture document validates against its
                              schema, using scripts/_common.validate when the
                              scripts are present and an equivalent built-in
                              subset validator otherwise.
  3. script pipeline        - normalize_company.py, dedupe_companies.py,
                              score_buyer.py, score_seller.py, score_match.py
                              and validate_output.py are run over the fixtures
                              and their output is diffed against
                              tests/fixtures/expected/.
  4. PRD 16 cases T01-T10   - plus the extra edge cases listed in tests/cases.md.
  5. safety checks          - no send capability, no auto-send path, no
                              personal-contact harvesting, no wall clock in the
                              scoring path, no placeholder tokens, stdlib only.

Exit code is 0 when every case passes and 1 otherwise.

Options:
  --allow-missing-scripts   report the script-dependent cases as SKIP instead of
                            FAIL when scripts/ has not been written yet. Without
                            it a missing script is a failure, which is the
                            correct default for a build gate.
  -v / --verbose            print the detail line of every case, not only the
                            failures.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
EXPECTED = os.path.join(FIXTURES, "expected")
SCHEMA_DIR = os.path.join(PKG_ROOT, "schemas")
SCRIPT_DIR = os.path.join(PKG_ROOT, "scripts")
AS_OF = "2026-09-12"
THRESHOLD = 70

PIPELINE_SCRIPTS = ["_common.py", "normalize_company.py", "dedupe_companies.py",
                    "score_buyer.py", "score_seller.py", "score_match.py",
                    "validate_output.py"]

# Keyword subset that scripts/_common.validate() must support (BUILD-CONTRACT 7.5).
SUPPORTED_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "oneOf", "anyOf", "allOf", "if", "then", "else",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern",
    "format", "$ref", "$defs", "definitions",
}
ANNOTATION_KEYWORDS = {"$schema", "$id", "title", "description", "$comment", "default",
                       "examples"}

# BUILD-CONTRACT 3.7: $defs whose definitions must agree between schema files.
SHARED_DEFS = ["evidence", "numeric_range", "quantity_value", "price_range", "timeline_value",
               "tri_state", "country_code", "category_list", "score_0_100", "points_0_100",
               "confidence_value", "score_version", "entity_status", "contact_channel",
               "unknown_penalty", "dimension_detail", "conflict", "evidence_id_list",
               "canonical_domain"]


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
class Report(object):
    def __init__(self, verbose=False):
        self.rows = []
        self.verbose = verbose

    def add(self, status, name, detail=""):
        self.rows.append((status, name, detail))
        if status == "FAIL":
            print("FAIL  %s" % name)
            for line in str(detail).splitlines():
                print("        %s" % line)
        elif status == "SKIP":
            print("SKIP  %s%s" % (name, ("  (%s)" % detail) if detail else ""))
        elif self.verbose:
            print("ok    %s%s" % (name, ("  (%s)" % detail) if detail else ""))

    def ok(self, name, detail=""):
        self.add("PASS", name, detail)

    def fail(self, name, detail=""):
        self.add("FAIL", name, detail)

    def skip(self, name, detail=""):
        self.add("SKIP", name, detail)

    def check(self, name, condition, detail=""):
        if condition:
            self.ok(name)
        else:
            self.fail(name, detail)
        return bool(condition)

    def counts(self):
        p = sum(1 for s, _, _ in self.rows if s == "PASS")
        f = sum(1 for s, _, _ in self.rows if s == "FAIL")
        s = sum(1 for s, _, _ in self.rows if s == "SKIP")
        return p, f, s


# ---------------------------------------------------------------------------
# built-in JSON-Schema-subset validator (fallback for scripts/_common.validate)
# ---------------------------------------------------------------------------
def _typename(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "unknown"


def _type_ok(value, expected):
    t = _typename(value)
    if expected == "number":
        return t in ("integer", "number")
    if expected == "integer":
        return t == "integer"
    return t == expected


_FORMAT_RE = {
    "date": re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    "date-time": re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                            r"([.][0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"),
    "uri": re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:"),
}


def builtin_validate(instance, schema, root=None, path="", depth=0):
    """Dependency-free validator over the BUILD-CONTRACT 7.5 keyword subset."""
    if root is None:
        root = schema
    if depth > 64:
        return ["%s: $ref recursion limit exceeded" % (path or "<root>")]
    if not isinstance(schema, dict):
        return []
    errors = []
    here = path or "<root>"

    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        if target is None:
            errors.append("%s: unresolvable $ref %s" % (here, schema["$ref"]))
        else:
            errors.extend(builtin_validate(instance, target, root, path, depth + 1))
        # 2020-12: sibling keywords apply in addition to the reference.

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(instance, t) for t in types):
            errors.append("%s: %r is not of type %s" % (here, _short(instance), "/".join(types)))
    if "const" in schema and instance != schema["const"]:
        errors.append("%s: %r is not the constant %r" % (here, _short(instance), schema["const"]))
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: %r is not one of %r" % (here, _short(instance), schema["enum"]))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: shorter than minLength %s" % (here, schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append("%s: longer than maxLength %s" % (here, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append("%s: %r does not match %s" % (here, _short(instance),
                                                        schema["pattern"]))
        fmt = schema.get("format")
        if fmt in _FORMAT_RE and not _FORMAT_RE[fmt].search(instance):
            errors.append("%s: %r is not a valid %s" % (here, _short(instance), fmt))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: %s is less than minimum %s" % (here, instance, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: %s is greater than maximum %s" % (here, instance,
                                                                 schema["maximum"]))
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append("%s: %s <= exclusiveMinimum" % (here, instance))
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append("%s: %s >= exclusiveMaximum" % (here, instance))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("%s: fewer than minItems %s" % (here, schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append("%s: more than maxItems %s" % (here, schema["maxItems"]))
        if schema.get("uniqueItems"):
            seen = []
            for item in instance:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key in seen:
                    errors.append("%s: duplicate item %s" % (here, _short(item)))
                    break
                seen.append(key)
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(builtin_validate(item, schema["items"], root,
                                               "%s[%d]" % (path, i), depth + 1))

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required property '%s'" % (here, key))
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors.extend(builtin_validate(value, props[key], root,
                                               ("%s.%s" % (path, key)) if path else key,
                                               depth + 1))
            elif schema.get("additionalProperties") is False:
                errors.append("%s: additional property '%s' is not allowed" % (here, key))
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(builtin_validate(value, schema["additionalProperties"], root,
                                               ("%s.%s" % (path, key)) if path else key,
                                               depth + 1))

    for combiner in ("allOf",):
        for sub in schema.get(combiner, []):
            errors.extend(builtin_validate(instance, sub, root, path, depth + 1))
    if "anyOf" in schema:
        subs = [builtin_validate(instance, s, root, path, depth + 1) for s in schema["anyOf"]]
        if not any(not e for e in subs):
            errors.append("%s: does not match any anyOf branch" % here)
    if "oneOf" in schema:
        subs = [builtin_validate(instance, s, root, path, depth + 1) for s in schema["oneOf"]]
        matched = sum(1 for e in subs if not e)
        if matched != 1:
            errors.append("%s: matched %d oneOf schemas, expected exactly 1" % (here, matched))
    if "if" in schema:
        cond = builtin_validate(instance, schema["if"], root, path, depth + 1)
        branch = schema.get("then") if not cond else schema.get("else")
        if branch is not None:
            errors.extend(builtin_validate(instance, branch, root, path, depth + 1))
    return errors


def _resolve_ref(ref, root):
    if not ref.startswith("#/"):
        return None
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _short(value):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 60 else text[:57] + "..."


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_schema(kind):
    return read_json(os.path.join(SCHEMA_DIR, "%s.schema.json" % kind))


def missing_scripts():
    return [n for n in PIPELINE_SCRIPTS if not os.path.isfile(os.path.join(SCRIPT_DIR, n))]


def run_script(name, args, stdin_data=None):
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, name)] + list(args)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # leave no __pycache__ behind in scripts/
    proc = subprocess.run(cmd, input=stdin_data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, cwd=PKG_ROOT,
                          env=env)
    return proc.returncode, proc.stdout, proc.stderr


def pick_validator(report):
    """Prefer scripts/_common.validate (BUILD-CONTRACT 7.5); fall back to the built-in."""
    if os.path.isfile(os.path.join(SCRIPT_DIR, "_common.py")):
        sys.path.insert(0, SCRIPT_DIR)
        sys.dont_write_bytecode = True  # leave no __pycache__ in a directory tests/ does not own
        try:
            import _common  # noqa: F401
            if hasattr(_common, "validate"):
                report.ok("validator source", "scripts/_common.validate")
                return _common.validate
        except Exception as exc:  # pragma: no cover - defensive
            report.fail("validator source", "scripts/_common.py could not be imported: %s" % exc)
    report.skip("validator source",
                "scripts/_common.py unavailable; using the built-in subset validator")
    return builtin_validate


READINESS_FIELDS = ["destination_country", "product_category", "product_description",
                    "quantity", "max_moq", "commercial_model", "required_certifications",
                    "timeline", "target_price", "buyer_id"]


def rfq_readiness(rfq):
    """SCORING-CONTRACT 2.9: filled = present and not "unknown" (arrays non-empty,
    required_certifications counts as filled whenever the key is present)."""
    filled, missing_fields = 0, []
    for field in READINESS_FIELDS:
        value = rfq.get(field, None)
        if field == "required_certifications":
            ok = field in rfq and rfq[field] is not None
        elif isinstance(value, list):
            ok = bool(value)
        else:
            ok = value is not None and value != "unknown"
        if ok:
            filled += 1
        else:
            missing_fields.append(field)
    return filled, sorted(missing_fields)


def index_by(items, key):
    return dict((item[key], item) for item in items if key in item)


def strip_annotations(node):
    if isinstance(node, dict):
        return dict((k, strip_annotations(v)) for k, v in sorted(node.items())
                    if k not in ("description", "title", "$comment", "examples"))
    if isinstance(node, list):
        return [strip_annotations(x) for x in node]
    return node


def walk_schema(node, fn, path="$"):
    """Call fn(keyword, path) for every schema keyword, skipping property names."""
    if isinstance(node, dict):
        for key, value in node.items():
            fn(key, path)
            child = "%s.%s" % (path, key)
            if key in ("properties", "$defs", "definitions"):
                if isinstance(value, dict):
                    for sub_key, sub in value.items():
                        walk_schema(sub, fn, "%s.%s" % (child, sub_key))
            elif key in ("items", "then", "else", "if", "additionalProperties", "not"):
                if isinstance(value, dict):
                    walk_schema(value, fn, child)
            elif key in ("oneOf", "anyOf", "allOf"):
                if isinstance(value, list):
                    for i, sub in enumerate(value):
                        walk_schema(sub, fn, "%s[%d]" % (child, i))


def collect_refs(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                out.append(value)
            else:
                collect_refs(value, out)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, out)


# ---------------------------------------------------------------------------
# 1. schema self-check (BUILD-CONTRACT 13.1)
# ---------------------------------------------------------------------------
def phase_schema_selfcheck(report):
    config_path = os.path.join(SCHEMA_DIR, "scoring.config.json")
    try:
        config = read_json(config_path)
    except Exception as exc:
        report.fail("schemas: scoring.config.json parses", str(exc))
        return None
    report.ok("schemas: scoring.config.json parses")

    schemas = {}
    for path in sorted(glob.glob(os.path.join(SCHEMA_DIR, "*.schema.json"))):
        name = os.path.basename(path)
        try:
            schemas[name] = read_json(path)
            report.ok("schemas: %s parses" % name)
        except Exception as exc:
            report.fail("schemas: %s parses" % name, str(exc))

    for name, schema in schemas.items():
        refs = []
        collect_refs(schema, refs)
        bad = [r for r in refs if _resolve_ref(r, schema) is None]
        report.check("schemas: %s every $ref resolves in-file" % name, not bad,
                     "unresolvable: %s" % sorted(set(bad)))

        unsupported = []

        def note(keyword, path, acc=unsupported):
            if keyword in SUPPORTED_KEYWORDS or keyword in ANNOTATION_KEYWORDS:
                return
            acc.append("%s at %s" % (keyword, path))

        walk_schema(schema, note)
        report.check("schemas: %s uses only the _common.validate keyword subset" % name,
                     not unsupported, "unsupported: %s" % sorted(set(unsupported)))

    # shared $defs agree structurally between files (a narrowing needs a $comment)
    for def_name in SHARED_DEFS:
        holders = [(n, s["$defs"][def_name]) for n, s in schemas.items()
                   if def_name in s.get("$defs", {})]
        if len(holders) < 2:
            continue
        base_name, base = holders[0]
        mismatches = []
        for other_name, other in holders[1:]:
            if strip_annotations(base) != strip_annotations(other):
                if "$comment" in other or "$comment" in base:
                    continue  # a deliberate, documented narrowing
                mismatches.append("%s vs %s" % (base_name, other_name))
        report.check("schemas: $defs/%s agrees across files" % def_name, not mismatches,
                     "; ".join(mismatches))

    versions = set()
    for name, schema in schemas.items():
        sv = schema.get("properties", {}).get("schema_version", {})
        const = sv.get("const")
        if const:
            versions.add(const)
    report.check("schemas: schema_version agrees with scoring.config.json",
                 not versions or versions == {config.get("schema_version")},
                 "schemas say %s, config says %s" % (sorted(versions),
                                                     config.get("schema_version")))
    report.check("schemas: score_version pattern matches the config value",
                 re.match(r"^kbtm-score-[0-9]+\.[0-9]+\.[0-9]+$", config.get("score_version", "")),
                 "config score_version = %r" % config.get("score_version"))

    # B-RE1 signal parity: every buyer contact_channel.type must be priced.
    # Without this, a new channel type silently falls through to
    # contact_channel_other (20 of 70) and a live wholesale application form
    # scores below a generic contact page.
    buyer_schema = schemas.get("buyer.schema.json", {})
    channel_def = buyer_schema.get("$defs", {}).get("contact_channel", {})
    channel_types = channel_def.get("properties", {}).get("type", {}).get("enum", [])
    re1 = {}
    for criterion in (config.get("buyer", {}).get("dimensions", {})
                      .get("reachability", {}).get("criteria", [])):
        if criterion.get("criterion_id") == "B-RE1":
            re1 = criterion.get("signals", {})
    unpriced = [t for t in channel_types
                if "contact_channel_%s" % (t,) not in re1]
    report.check("schemas: every buyer contact_channel.type has a B-RE1 signal",
                 bool(channel_types) and not unpriced,
                 "unpriced types: %s (they fall through to contact_channel_other)"
                 % (unpriced or "contact_channel enum not found",))

    # A criterion table must sum to its dimension's max_points, or the
    # dimension renormalises against a denominator nobody declared.
    for entity in ("buyer", "seller"):
        broken = []
        for key, dimension in config.get(entity, {}).get("dimensions", {}).items():
            criteria = dimension.get("criteria") or []
            if not criteria:
                continue  # evidence_quality is computed, not tabulated
            total = sum(c.get("max_points", 0) for c in criteria)
            if total != dimension.get("max_points"):
                broken.append("%s.%s: criteria sum to %s, dimension declares %s"
                              % (entity, key, total, dimension.get("max_points")))
        report.check("schemas: %s criterion max_points sum to their dimension" % entity,
                     not broken, "; ".join(broken))
    return config


# ---------------------------------------------------------------------------
# 2. fixture validation
# ---------------------------------------------------------------------------
FIXTURE_KINDS = [
    ("buyers.golden.json", "buyer", "records", "buyer_id"),
    ("sellers.golden.json", "seller", "records", "seller_id"),
    ("rfq.134.json", "rfq", None, "rfq_id"),
    ("rfq.no-match.json", "rfq", None, "rfq_id"),
    ("rfq.id-halal.json", "rfq", None, "rfq_id"),
]


def phase_fixture_validation(report, validate):
    evidence_schema = load_schema("evidence")
    for filename, kind, records_key, id_key in FIXTURE_KINDS:
        path = os.path.join(FIXTURES, filename)
        if not os.path.isfile(path):
            report.fail("fixtures: %s exists" % filename, "missing")
            continue
        doc = read_json(path)
        schema = load_schema(kind)
        records = doc[records_key] if records_key else [doc]
        bad = []
        for rec in records:
            errs = validate(rec, schema)
            if errs:
                bad.append("%s: %s" % (rec.get(id_key, "?"), errs[:4]))
        report.check("fixtures: %s validates against %s.schema.json" % (filename, kind),
                     not bad, "\n".join(bad[:10]))
        ev_bad = []
        for rec in records:
            for item in rec.get("evidence", []):
                errs = validate(item, evidence_schema)
                if errs:
                    ev_bad.append("%s/%s: %s" % (rec.get(id_key, "?"),
                                                 item.get("evidence_id"), errs[:3]))
        report.check("fixtures: %s evidence validates against evidence.schema.json" % filename,
                     not ev_bad, "\n".join(ev_bad[:10]))

    # counts required by the roadmap (20 buyers + 20 sellers)
    buyers = read_json(os.path.join(FIXTURES, "buyers.golden.json"))["records"]
    sellers = read_json(os.path.join(FIXTURES, "sellers.golden.json"))["records"]
    report.check("fixtures: 20 golden buyer records", len(buyers) == 20, str(len(buyers)))
    report.check("fixtures: 20 golden seller records", len(sellers) == 20, str(len(sellers)))
    report.check("fixtures: buyer countries span UAE/UK/US/JP/SG/DE",
                 {"AE", "GB", "US", "JP", "SG", "DE"} <= set(b["country"] for b in buyers),
                 sorted(set(b["country"] for b in buyers)))

    # every company is fictional: every host sits under the reserved .example TLD
    bad_hosts = set()
    for rec in buyers + sellers:
        for url in [rec.get("website")] + [e.get("source_url") for e in rec.get("evidence", [])]:
            if not url:
                continue
            host = url.split("://", 1)[-1].split("/", 1)[0].split(":")[0].lower()
            if not host.endswith(".example"):
                bad_hosts.add(host)
    report.check("fixtures: every company domain is fictional (.example TLD)",
                 not bad_hosts, sorted(bad_hosts))

    # INV-01: every material claim is either evidenced or unknown/absent
    config = read_json(os.path.join(SCHEMA_DIR, "scoring.config.json"))
    material = config["evidence"]["material_claims"]
    problems = []
    for kind, recs, id_key in (("buyer", buyers, "buyer_id"), ("seller", sellers, "seller_id")):
        for rec in recs:
            claims = set(e["claim"] for e in rec.get("evidence", []))
            for field in material[kind]:
                value = rec.get(field, "unknown")
                known = not (value is None or value == "unknown")
                if known and field not in claims:
                    problems.append("%s: %s is known but unevidenced" % (rec[id_key], field))
    report.check("fixtures: INV-01 material claims are evidenced or unknown", not problems,
                 "\n".join(problems[:10]))

    # INV-37: a VERIFIED record needs a material claim evidenced at source_tier <= 3
    state_problems = []
    for kind, recs, id_key in (("buyer", buyers, "buyer_id"), ("seller", sellers, "seller_id")):
        claims = set(material[kind])
        for rec in recs:
            has_url = any(e.get("source_url") for e in rec.get("evidence", []))
            verified = any(e["claim"] in claims and e["source_tier"] <= 3
                           for e in rec.get("evidence", []))
            status = rec.get("status")
            if status in ("DISCOVERED", "VERIFIED") and not has_url:
                state_problems.append("%s: %s needs an evidence item with a source_url"
                                      % (rec[id_key], status))
            if status == "VERIFIED" and not verified:
                state_problems.append("%s: VERIFIED needs a material claim at source_tier <= 3"
                                      % rec[id_key])
            if status not in ("DISCOVERED", "VERIFIED"):
                state_problems.append("%s: a raw fixture may only be DISCOVERED or VERIFIED, "
                                      "got %r" % (rec[id_key], status))
    report.check("fixtures: INV-37 entity status entry conditions hold", not state_problems,
                 "\n".join(state_problems[:10]))

    # INV-24: observed_at is never after as_of, source_date never after observed_at
    time_problems = []
    for rec in buyers + sellers:
        for item in rec.get("evidence", []):
            observed = item["observed_at"][:10]
            if observed > AS_OF:
                time_problems.append("%s observed_at %s > as_of" % (item["evidence_id"], observed))
            sd = item["source_date"]
            if sd != "unknown" and sd > observed:
                time_problems.append("%s source_date %s > observed_at" % (item["evidence_id"], sd))
    report.check("fixtures: INV-24 observation times are consistent", not time_problems,
                 "\n".join(time_problems[:10]))

    # INV-31: no named individual / personal contact anywhere in the fixtures
    blob = ""
    for filename in sorted(os.listdir(FIXTURES)):
        if filename.endswith(".json"):
            with open(os.path.join(FIXTURES, filename), encoding="utf-8") as fh:
                blob += fh.read()
    personal = re.findall(r"\b[a-z]+\.[a-z]+@[a-z0-9.-]+", blob)
    report.check("fixtures: INV-31 no pattern-guessed personal email addresses", not personal,
                 sorted(set(personal))[:10])

    # the match envelopes must stay in step with the standalone fixtures
    for envelope_name, rfq_name in (("match-134.input.json", "rfq.134.json"),
                                    ("match-no-match.input.json", "rfq.no-match.json")):
        env = read_json(os.path.join(FIXTURES, envelope_name))
        report.check("fixtures: %s embeds %s unchanged" % (envelope_name, rfq_name),
                     env["rfq"] == read_json(os.path.join(FIXTURES, rfq_name)),
                     "embedded RFQ differs from the standalone fixture")
        report.check("fixtures: %s embeds sellers.golden.json unchanged" % envelope_name,
                     env["records"] == sellers,
                     "embedded seller records differ from sellers.golden.json")

    # the ID/halal envelope carries its own six-seller set, so only the RFQ is shared
    env = read_json(os.path.join(FIXTURES, "match-id-halal.input.json"))
    report.check("fixtures: match-id-halal.input.json embeds rfq.id-halal.json unchanged",
                 env["rfq"] == read_json(os.path.join(FIXTURES, "rfq.id-halal.json")),
                 "embedded RFQ differs from the standalone fixture")


# ---------------------------------------------------------------------------
# 3. script pipeline
# ---------------------------------------------------------------------------
def diff_expected(expected, actual, path=""):
    """Compare the named fields of `expected` against `actual`. Returns messages."""
    out = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return ["%s: expected an object, got %s" % (path or "<root>", _typename(actual))]
        for key, want in expected.items():
            if key.startswith("__"):
                continue
            if key not in actual:
                out.append("%s.%s: missing from the script output" % (path, key))
                continue
            out.extend(diff_expected(want, actual[key], "%s.%s" % (path, key)))
    elif isinstance(expected, list):
        if not isinstance(actual, list):
            return ["%s: expected an array, got %s" % (path or "<root>", _typename(actual))]
        if expected != actual:
            out.append("%s: expected %s, got %s" % (path, _short(expected), _short(actual)))
    else:
        if isinstance(expected, float) or isinstance(actual, float):
            same = abs(float(expected) - float(actual)) < 1e-9
        else:
            same = expected == actual
        if not same:
            out.append("%s: expected %r, got %r" % (path, expected, actual))
    return out


def criterion_ids(record):
    return sorted(u["criterion_id"] for u in record.get("unknown_penalty_applied", [])
                  if not str(u.get("criterion_id", "")).startswith("HF-"))


def hard_filter_labels(record):
    return set(u["label"] for u in record.get("unknown_penalty_applied", [])
               if str(u.get("criterion_id", "")).startswith("HF-"))


def observed_record(record):
    """Project a scored record onto the fields the expectation documents name."""
    hf_labels = hard_filter_labels(record)
    return {
        "qualification_score": record.get("qualification_score"),
        "qualified": record.get("qualified"),
        "confidence": record.get("confidence"),
        "dimension_scores": record.get("dimension_scores"),
        "unknown_criteria": criterion_ids(record),
        "missing": [m for m in record.get("missing", []) if m not in hf_labels],
    }


def observed_candidate(candidate):
    hf_labels = hard_filter_labels(candidate)
    hf = candidate.get("hard_filter", {})
    return {
        "seller_name": candidate.get("seller_name"),
        "base_score": candidate.get("base_score"),
        "match_score": candidate.get("match_score"),
        "rerank_applied": candidate.get("rerank", {}).get("applied"),
        "rerank_delta": candidate.get("rerank", {}).get("delta"),
        "component_scores": candidate.get("component_scores"),
        "weighted_contributions": candidate.get("weighted_contributions"),
        "confidence": candidate.get("confidence"),
        "hard_filter": {"passed": hf.get("passed"),
                        "rules_evaluated": hf.get("rules_evaluated"),
                        "rules_skipped_unknown": hf.get("rules_skipped_unknown")},
        "unknown_criteria": criterion_ids(candidate),
        "missing": [m for m in candidate.get("missing", []) if m not in hf_labels],
        "qualified": candidate.get("qualified"),
    }


class Pipeline(object):
    """Holds the parsed script outputs so the T-cases can assert over them."""

    def __init__(self):
        self.normalized = None
        self.dedupe = None
        self.buyers_uae = None
        self.buyers_uk = None
        self.sellers = None
        self.match_134 = None
        self.match_nomatch = None
        self.match_idhalal = None


def run_discovery(report, pipeline, attr, script, input_name, query_name, expected_name,
                  id_key):
    expected = read_json(os.path.join(EXPECTED, expected_name))
    code, out, err = run_script(script, [
        "--input", os.path.join(FIXTURES, input_name),
        "--query", os.path.join(FIXTURES, query_name),
        "--as-of", AS_OF, "--pretty"])
    report.check("%s (%s): exits 0" % (script, query_name), code == 0,
                 "exit %d\n%s" % (code, err.strip()[:800]))
    try:
        envelope = json.loads(out)
    except ValueError as exc:
        # R7.3.2: a script that exits 1 still writes its document to stdout, so the
        # remaining cases can be asserted. Only an unparseable stdout stops us.
        report.fail("%s (%s): emits JSON on stdout" % (script, query_name), str(exc))
        return
    setattr(pipeline, attr, envelope)

    report.check("%s (%s): summary matches expected" % (script, query_name),
                 not diff_expected(expected["summary"], envelope.get("summary", {}), "summary"),
                 "\n".join(diff_expected(expected["summary"], envelope.get("summary", {}),
                                         "summary")))
    records = index_by(envelope.get("records", []), id_key)
    problems = []
    for rid, want in expected["records"].items():
        if rid not in records:
            problems.append("%s: absent from records[]" % rid)
            continue
        want_fields = dict((k, v) for k, v in want.items()
                           if k in ("qualification_score", "qualified", "confidence",
                                    "dimension_scores", "unknown_criteria", "missing"))
        problems.extend(diff_expected(want_fields, observed_record(records[rid]), rid))
    report.check("%s (%s): every scored record matches expected" % (script, query_name),
                 not problems, "\n".join(problems[:20]))

    order = [r[id_key] for r in envelope.get("records", [])]
    report.check("%s (%s): INV-30 ordering (score desc, domain asc)" % (script, query_name),
                 order == expected["order"],
                 "expected %s\n     got %s" % (expected["order"], order))

    excluded = index_by(envelope.get("excluded", []), "id")
    problems = []
    for rid, want in expected["excluded"].items():
        if rid not in excluded:
            problems.append("%s: expected in excluded[]" % rid)
            continue
        got = excluded[rid]
        if want["reason_summary"] not in (got.get("reason_summary") or ""):
            problems.append("%s: reason_summary %r does not carry %r"
                            % (rid, got.get("reason_summary"), want["reason_summary"]))
        want_rules = want["failed_rule_ids"]
        got_rules = [f["rule_id"] for f in got.get("failed_rules", [])]
        if want_rules and got_rules[:1] != want_rules[:1]:
            problems.append("%s: first failed rule %s, expected %s"
                            % (rid, got_rules[:1], want_rules[:1]))
    for rid in excluded:
        if rid not in expected["excluded"]:
            problems.append("%s: unexpectedly excluded" % rid)
    report.check("%s (%s): exclusions match expected" % (script, query_name), not problems,
                 "\n".join(problems[:20]))

    # INV-13 determinism: the same inputs produce byte-identical output
    code2, out2, _ = run_script(script, [
        "--input", os.path.join(FIXTURES, input_name),
        "--query", os.path.join(FIXTURES, query_name),
        "--as-of", AS_OF, "--pretty"])
    report.check("%s (%s): INV-13 byte-identical on a re-run" % (script, query_name),
                 code2 == code and out2 == out, "second run differed")


def run_match(report, pipeline, attr, envelope_name, expected_name, extra_args):
    expected = read_json(os.path.join(EXPECTED, expected_name))
    args = ["--input", os.path.join(FIXTURES, envelope_name), "--as-of", AS_OF, "--pretty"]
    args.extend(extra_args)
    code, out, err = run_script("score_match.py", args)
    label = envelope_name
    report.check("score_match.py (%s): exits 0" % label, code == 0,
                 "exit %d\n%s" % (code, err.strip()[:800]))
    try:
        result = json.loads(out)
    except ValueError as exc:
        report.fail("score_match.py (%s): emits JSON on stdout" % label, str(exc))
        return
    setattr(pipeline, attr, result)

    problems = diff_expected(expected["summary"], result.get("summary", {}), "summary")
    report.check("score_match.py (%s): summary matches expected" % label, not problems,
                 "\n".join(problems[:20]))

    results = index_by(result.get("results", []), "seller_id")
    problems = []
    for sid, want in expected["results"].items():
        if sid not in results:
            problems.append("%s: absent from results[]" % sid)
            continue
        want_fields = dict((k, v) for k, v in want.items()
                           if not k.startswith("zero_weight"))
        problems.extend(diff_expected(want_fields, observed_candidate(results[sid]), sid))
        for rule_id in want.get("zero_weight_unknown_penalty_rule_ids", []):
            ids = [u.get("criterion_id") for u in results[sid].get("unknown_penalty_applied", [])]
            if rule_id not in ids:
                problems.append("%s: INV-07 expects a zero-weight unknown_penalty for %s"
                                % (sid, rule_id))
    report.check("score_match.py (%s): every candidate matches expected" % label, not problems,
                 "\n".join(problems[:20]))

    order = [c["seller_id"] for c in result.get("results", [])]
    report.check("score_match.py (%s): INV-30 ordering" % label, order == expected["order"],
                 "expected %s\n     got %s" % (expected["order"], order))

    excluded = index_by(result.get("excluded", []), "seller_id")
    problems = []
    for sid, want in expected["excluded"].items():
        if sid not in excluded:
            problems.append("%s: expected in excluded[]" % sid)
            continue
        got_rules = [f["rule_id"] for f in excluded[sid].get("failed_rules", [])]
        if want["failed_rule_ids"] and got_rules[:1] != want["failed_rule_ids"][:1]:
            problems.append("%s: first failed rule %s, expected %s"
                            % (sid, got_rules[:1], want["failed_rule_ids"][:1]))
        if "match_score" in excluded[sid]:
            problems.append("%s: INV-32 an excluded seller must carry no match_score" % sid)
        if not got_rules:
            problems.append("%s: INV-32 an excluded seller needs >=1 failed_rules entry" % sid)
    for sid in excluded:
        if sid not in expected["excluded"]:
            problems.append("%s: unexpectedly excluded" % sid)
    report.check("score_match.py (%s): exclusions match expected" % label, not problems,
                 "\n".join(problems[:20]))

    code2, out2, _ = run_script("score_match.py", args)
    report.check("score_match.py (%s): INV-13 byte-identical on a re-run" % label,
                 code2 == code and out2 == out, "second run differed")


def phase_pipeline(report, pipeline, allow_missing):
    absent = missing_scripts()
    if absent:
        message = "scripts/ is missing: %s" % ", ".join(absent)
        names = ["normalize_company.py", "dedupe_companies.py", "score_buyer.py",
                 "score_seller.py", "score_match.py", "validate_output.py"]
        for name in names:
            if allow_missing:
                report.skip("pipeline: %s" % name, message)
            else:
                report.fail("pipeline: %s" % name, message)
        return False

    # --- normalize_company.py -------------------------------------------------
    args = ["--input", os.path.join(FIXTURES, "buyers.golden.json"), "--entity", "buyer",
            "--as-of", AS_OF, "--pretty"]
    code, out, err = run_script("normalize_company.py", args)
    if code != 0:
        report.fail("normalize_company.py: exits 0", "exit %d\n%s" % (code, err.strip()[:500]))
    else:
        report.ok("normalize_company.py: exits 0")
        records = json.loads(out)
        records = records["records"] if isinstance(records, dict) else records
        pipeline.normalized = records
        by_id = index_by(records, "buyer_id")
        dup = by_id.get("BUY-www-luminaglow-example", {})
        report.check("normalize_company.py: www host folds to the registrable domain",
                     dup.get("canonical_domain") == "luminaglow.example",
                     "canonical_domain = %r" % dup.get("canonical_domain"))
        report.check("normalize_company.py: the www host is kept in alias_domains",
                     "www.luminaglow.example" in (dup.get("alias_domains") or []),
                     "alias_domains = %r" % dup.get("alias_domains"))
        code2, out2, _ = run_script("normalize_company.py", args)
        report.check("normalize_company.py: INV-15 idempotent", out2 == out, "second run differed")
        with tempfile.TemporaryDirectory() as tmp:
            once = os.path.join(tmp, "once.json")
            with open(once, "w", encoding="utf-8") as fh:
                fh.write(out)
            code3, out3, _ = run_script("normalize_company.py", [
                "--input", once, "--entity", "buyer", "--as-of", AS_OF, "--pretty"])
            report.check("normalize_company.py: INV-15 stable when re-applied to its own output",
                         code3 == 0 and out3 == out, "re-application changed the document")

        cases = read_json(os.path.join(FIXTURES, "normalize.cases.json"))
        problems = []
        for case in cases["canonical_domain"]:
            rc, so, _ = run_script("normalize_company.py",
                                   ["--name", "Test Company", "--url", case["input"]])
            want = case["expected"] or "unknown"
            try:
                got = json.loads(so).get("canonical_domain")
            except ValueError:
                got = "<not JSON>"
            if rc != 0 or got != want:
                problems.append("%r -> %r (expected %r)" % (case["input"], got, want))
        report.check("normalize_company.py: BUILD-CONTRACT 8.1 canonical_domain cases",
                     not problems, "\n".join(problems[:10]))
        problems = []
        for case in cases["normalize_company_name"]:
            rc, so, _ = run_script("normalize_company.py",
                                   ["--name", case["input"], "--url", "https://example.example/"])
            try:
                got = json.loads(so).get("normalized_name")
            except ValueError:
                got = "<not JSON>"
            if rc != 0 or got != case["expected"]:
                problems.append("%r -> %r (expected %r)" % (case["input"], got, case["expected"]))
        report.check("normalize_company.py: BUILD-CONTRACT 8.3 normalized_name cases",
                     not problems, "\n".join(problems[:10]))

    # --- dedupe_companies.py --------------------------------------------------
    expected = read_json(os.path.join(EXPECTED, "dedupe.expected.json"))
    args = ["--input", os.path.join(FIXTURES, "buyers.golden.json"), "--entity", "buyer",
            "--as-of", AS_OF, "--pretty"]
    code, out, err = run_script("dedupe_companies.py", args)
    if code != 0:
        report.fail("dedupe_companies.py: exits 0", "exit %d\n%s" % (code, err.strip()[:500]))
    else:
        report.ok("dedupe_companies.py: exits 0")
        envelope = json.loads(out)
        pipeline.dedupe = envelope
        records = envelope.get("records", [])
        report.check("dedupe_companies.py: output_count matches expected",
                     len(records) == expected["stats"]["output_count"],
                     "%d records, expected %d" % (len(records),
                                                  expected["stats"]["output_count"]))
        merges = envelope.get("merges", [])
        want = expected["merges"][0]
        found = [m for m in merges if m.get("survivor_id") == want["survivor_id"]]
        report.check("dedupe_companies.py: T06 the www/non-www pair merges",
                     len(merges) == 1 and found
                     and want["absorbed_ids"][0] in (found[0].get("absorbed_ids") or [])
                     and found[0].get("key") == "canonical_domain",
                     "merges = %s" % _short(merges))
        survivor = index_by(records, "buyer_id").get(want["survivor_id"], {})
        want_s = expected["survivor"]
        problems = []
        if want_s["merged_from_includes"][0] not in (survivor.get("merged_from") or []):
            problems.append("INV-18 merged_from = %r" % survivor.get("merged_from"))
        if want_s["alias_domains_include"][0] not in (survivor.get("alias_domains") or []):
            problems.append("INV-18 alias_domains = %r" % survivor.get("alias_domains"))
        if survivor.get("score_version") != want_s["score_version"]:
            problems.append("M7 score_version = %r" % survivor.get("score_version"))
        if survivor.get("qualification_score") != want_s["qualification_score"]:
            problems.append("M7 qualification_score = %r" % survivor.get("qualification_score"))
        cats = set(survivor.get("product_categories") or [])
        if not set(want_s["product_categories_include"]) <= cats:
            problems.append("M5 product_categories = %r" % sorted(cats))
        channels = survivor.get("contact_channels") or []
        if len(channels) != want_s["contact_channel_type_count"]:
            problems.append("M5 contact_channels count = %d" % len(channels))
        evidence = survivor.get("evidence") or []
        if len(evidence) < want_s["evidence_id_count_min"]:
            problems.append("M1 evidence count = %d" % len(evidence))
        if len(set(e["evidence_id"] for e in evidence)) != len(evidence):
            problems.append("M1 evidence_id values collide after the merge")
        report.check("dedupe_companies.py: the survivor keeps the merge provenance",
                     not problems, "\n".join(problems))
        problems = []
        for pair in expected["must_not_merge"]:
            ids = set(pair["ids"])
            for merge in merges:
                absorbed = set(merge.get("absorbed_ids") or []) | {merge.get("survivor_id")}
                if ids <= absorbed:
                    problems.append("%s merged: %s" % (sorted(ids), pair["why"]))
        report.check("dedupe_companies.py: INV-16 no merge across distinct domains",
                     not problems, "\n".join(problems))
        code2, out2, _ = run_script("dedupe_companies.py", args)
        report.check("dedupe_companies.py: INV-15 idempotent", out2 == out, "second run differed")

    # --- score_buyer.py / score_seller.py ------------------------------------
    run_discovery(report, pipeline, "buyers_uae", "score_buyer.py", "buyers.golden.json",
                  "query-buyer-uae-kbeauty.json", "buyers.uae.discovery.expected.json",
                  "buyer_id")
    run_discovery(report, pipeline, "buyers_uk", "score_buyer.py", "buyers.golden.json",
                  "query-buyer-uk-sunscreen.json", "buyers.uk.discovery.expected.json",
                  "buyer_id")
    run_discovery(report, pipeline, "sellers", "score_seller.py", "sellers.golden.json",
                  "query-seller-sunscreen-oem.json", "sellers.discovery.expected.json",
                  "seller_id")

    # --- score_match.py -------------------------------------------------------
    run_match(report, pipeline, "match_134", "match-134.input.json", "match-134.expected.json",
              ["--rerank-input", os.path.join(FIXTURES, "rerank-134.json")])
    run_match(report, pipeline, "match_nomatch", "match-no-match.input.json",
              "match-no-match.expected.json", [])
    run_match(report, pipeline, "match_idhalal", "match-id-halal.input.json",
              "match-id-halal.expected.json", [])

    # --- RFQ readiness (SCORING-CONTRACT 2.9 / BUILD-CONTRACT 5.4) ------------
    readiness = read_json(os.path.join(EXPECTED, "rfq.readiness.expected.json"))
    for name in ("rfq.134.json", "rfq.no-match.json"):
        want = readiness[name]
        rfq = read_json(os.path.join(FIXTURES, name))
        filled, missing_fields = rfq_readiness(rfq)
        problems = []
        if filled != want["readiness_detail"]["filled"]:
            problems.append("filled = %d, expected %d" % (filled,
                                                          want["readiness_detail"]["filled"]))
        if missing_fields != want["readiness_detail"]["missing_fields"]:
            problems.append("missing_fields = %s, expected %s"
                            % (missing_fields, want["readiness_detail"]["missing_fields"]))
        if filled * 10 != want["qualification_score"]:
            problems.append("round_half_up(100 x %d / 10) != %d"
                            % (filled, want["qualification_score"]))
        report.check("readiness: %s scores %d/100" % (name, want["qualification_score"]),
                     not problems, "\n".join(problems))
        result = pipeline.match_134 if name == "rfq.134.json" else pipeline.match_nomatch
        emitted = (result or {}).get("rfq_constraints") or {}
        if "qualification_score" in emitted or "readiness_detail" in emitted:
            report.check("readiness: %s block echoed by score_match.py agrees" % name,
                         emitted.get("qualification_score") == want["qualification_score"],
                         "got %r" % emitted.get("qualification_score"))

    # --- validate_output.py (BUILD-CONTRACT 13.1) -----------------------------
    with tempfile.TemporaryDirectory() as tmp:
        samples = []
        buyers = read_json(os.path.join(FIXTURES, "buyers.golden.json"))["records"]
        sellers = read_json(os.path.join(FIXTURES, "sellers.golden.json"))["records"]
        for kind, doc in (("buyer", buyers[0]), ("seller", sellers[0]),
                          ("evidence", buyers[0]["evidence"][0])):
            path = os.path.join(tmp, "%s.json" % kind)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False)
            samples.append((kind, path))
        samples.append(("rfq", os.path.join(FIXTURES, "rfq.134.json")))
        for attr, kind in (("buyers_uae", "discovery-result"), ("match_134", "match-result")):
            produced = getattr(pipeline, attr)
            if produced:
                path = os.path.join(tmp, "%s.json" % kind)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(produced, fh, ensure_ascii=False)
                samples.append((kind, path))
        for kind, path in samples:
            code, out, err = run_script("validate_output.py",
                                        ["--input", path, "--schema", kind, "--strict",
                                         "--invariants", "--as-of", AS_OF])
            detail = err.strip()[:400] or out.strip()[:400]
            report.check("validate_output.py --strict --invariants: %s fixture" % kind,
                         code == 0, "exit %d\n%s" % (code, detail))
    return True


# ---------------------------------------------------------------------------
# 4. PRD 16 cases T01-T10
# ---------------------------------------------------------------------------
def phase_cases(report, pipeline, allow_missing):
    def need(*objects):
        if all(objects):
            return True
        return False

    def unavailable(case):
        if allow_missing:
            report.skip(case, "depends on scripts/ output")
        else:
            report.fail(case, "no script output to assert against")

    # ---- T01 buyer broad: retail-only companies score low --------------------
    case = "T01 buyer broad (UAE, K-Beauty): retail-only scores low"
    if not need(pipeline.buyers_uae):
        unavailable(case)
    else:
        records = index_by(pipeline.buyers_uae.get("records", []), "buyer_id")
        retail = records.get("BUY-marinaretail-example")
        problems = []
        if not retail:
            problems.append("BUY-marinaretail-example is absent from records[]")
        else:
            if (retail.get("dimension_scores") or {}).get("b2b_role") != 0:
                problems.append("b2b_role = %r, expected 0 (retail_only_consumer_store -25 and "
                                "wholesale_signal_false -15 stack to clamp the dimension)"
                                % (retail.get("dimension_scores") or {}).get("b2b_role"))
            if retail.get("qualified") is not False:
                problems.append("qualified = %r, expected false" % retail.get("qualified"))
            trade_ids = ["BUY-gulfglow-example", "BUY-dunesourcing-example"]
            for tid in trade_ids:
                other = records.get(tid)
                if other and other["qualification_score"] <= retail["qualification_score"]:
                    problems.append("%s (%d) does not outrank the retail-only company (%d)"
                                    % (tid, other["qualification_score"],
                                       retail["qualification_score"]))
        report.check(case, not problems, "\n".join(problems))

    # ---- T02 buyer product: UK sun/skin care carriers rank first --------------
    case = "T02 buyer product (UK, sunscreen): suncare carriers rank first"
    if not need(pipeline.buyers_uk):
        unavailable(case)
    else:
        envelope = pipeline.buyers_uk
        order = [r["buyer_id"] for r in envelope.get("records", [])]
        excluded = set(e["id"] for e in envelope.get("excluded", []))
        problems = []
        if not order or order[0] != "BUY-britsun-example":
            problems.append("rank 1 is %r, expected BUY-britsun-example" % (order[:1]))
        for weak in ("BUY-albionbeautyhall-example", "BUY-cascademarket-example"):
            if weak in order and order.index(weak) < order.index("BUY-britsun-example"):
                problems.append("%s carries no suncare signal yet outranks the UK "
                                "suncare wholesaler" % weak)
        if excluded and "BUY-quietharbour-example" not in excluded:
            problems.append("the unevidenced candidate should not be ranked")
        if "BUY-luminaglow-example" not in order[:3]:
            problems.append("the UK sunscreen distributor should be in the top three: %s"
                            % order[:3])
        report.check(case, not problems, "\n".join(problems))

    # ---- T03 seller strict MOQ: a confirmed 5,000 MOQ is hard-rejected --------
    case = "T03 seller strict MOQ: confirmed MOQ 5,000 is hard-rejected"
    if not need(pipeline.match_134):
        unavailable(case)
    else:
        excluded = index_by(pipeline.match_134.get("excluded", []), "seller_id")
        results = index_by(pipeline.match_134.get("results", []), "seller_id")
        entry = excluded.get("SEL-daehansuncare-example")
        problems = []
        if not entry:
            problems.append("SEL-daehansuncare-example is not in excluded[]")
        else:
            rules = [f["rule_id"] for f in entry.get("failed_rules", [])]
            if "HF-03" not in rules:
                problems.append("failed_rules = %r, expected HF-03" % rules)
            if "match_score" in entry:
                problems.append("an excluded seller must carry no match_score")
            if entry.get("unknown_penalty_applied"):
                problems.append("the rejection must be driven by a KNOWN value, so "
                                "unknown_penalty_applied must stay empty")
            failure = [f for f in entry.get("failed_rules", []) if f["rule_id"] == "HF-03"]
            if failure:
                if failure[0].get("observed_value") != 5000:
                    problems.append("observed_value = %r, expected 5000"
                                    % failure[0].get("observed_value"))
                if failure[0].get("required_value") != 3000:
                    problems.append("required_value = %r, expected 3000"
                                    % failure[0].get("required_value"))
                if "5,000" not in (failure[0].get("reason") or ""):
                    problems.append("reason %r does not use en-US thousands grouping"
                                    % failure[0].get("reason"))
        if "SEL-daehansuncare-example" in results:
            problems.append("the rejected seller must not appear in results[] (INV-08)")
        report.check(case, not problems, "\n".join(problems))

    # ---- T04 unknown MOQ: penalised, never rejected ---------------------------
    case = "T04 unknown MOQ: penalised, never rejected"
    if not need(pipeline.match_134):
        unavailable(case)
    else:
        results = index_by(pipeline.match_134.get("results", []), "seller_id")
        candidate = results.get("SEL-yeonhwalab-example")
        problems = []
        if not candidate:
            problems.append("SEL-yeonhwalab-example must survive with an unknown MOQ")
        else:
            hf = candidate.get("hard_filter", {})
            if "HF-03" not in (hf.get("rules_skipped_unknown") or []):
                problems.append("HF-03 must be listed in rules_skipped_unknown, got %r"
                                % hf.get("rules_skipped_unknown"))
            if hf.get("failed_rules"):
                problems.append("an unknown input must never fail a hard filter (INV-07)")
            penalties = index_by(candidate.get("unknown_penalty_applied", []), "criterion_id")
            entry = penalties.get("S-OP1")
            if not entry:
                problems.append("INV-07 requires a matching unknown_penalty for S-OP1")
            else:
                if entry.get("applied_points") != 17:
                    problems.append("S-OP1 applied_points = %r, expected 17 "
                                    "(round_half_up(55 x 0.30))" % entry.get("applied_points"))
                if entry.get("unknown_inputs") != ["seller.moq"]:
                    problems.append("unknown_inputs = %r" % entry.get("unknown_inputs"))
            if "MOQ against the buyer ceiling" not in (candidate.get("missing") or []):
                problems.append("INV-03 requires the label on the Missing: line, got %r"
                                % candidate.get("missing"))
            if candidate.get("component_scores", {}).get("operation_fit") != 43:
                problems.append("operation_fit = %r, expected 43"
                                % candidate.get("component_scores", {}).get("operation_fit"))
        report.check(case, not problems, "\n".join(problems))

    # ---- T05 false-claim prevention ------------------------------------------
    case = "T05 false-claim prevention: no unevidenced demand claim"
    forbidden = [r"currently\s+looking", r"actively\s+looking", r"we\s+have\s+a\s+buyer",
                 r"active\s+demand", r"buyer\s+is\s+searching", r"지금\s*찾고\s*있", r"바이어가\s*찾"]
    problems = []
    blobs = []
    for attr in ("match_134", "match_nomatch", "buyers_uae", "buyers_uk", "sellers"):
        produced = getattr(pipeline, attr)
        if produced:
            blobs.append((attr, json.dumps(produced, ensure_ascii=False)))
    for name, blob in blobs:
        for pattern in forbidden:
            if re.search(pattern, blob, re.IGNORECASE):
                problems.append("%s contains a demand claim matching /%s/" % (name, pattern))
    if pipeline.match_134:
        for candidate in pipeline.match_134.get("results", []):
            rationale = candidate.get("rationale", [])
            if candidate.get("hard_filter", {}).get("passed") and len(rationale) < 2:
                problems.append("%s: INV-20 needs >= 2 rationale items, got %d"
                                % (candidate["seller_id"], len(rationale)))
            for item in rationale:
                if not item.get("evidence_ids"):
                    problems.append("%s: a rationale statement carries no evidence_id"
                                    % candidate["seller_id"])
    if not blobs:
        unavailable(case)
    else:
        report.check(case, not problems, "\n".join(problems[:10]))

    # ---- T06 duplicate domains ------------------------------------------------
    case = "T06 duplicate domains: company.example and www.company.example are one entity"
    if not need(pipeline.dedupe):
        unavailable(case)
    else:
        records = index_by(pipeline.dedupe.get("records", []), "buyer_id")
        problems = []
        if "BUY-www-luminaglow-example" in records:
            problems.append("the www duplicate is still a separate record")
        survivor = records.get("BUY-luminaglow-example")
        if not survivor:
            problems.append("the survivor BUY-luminaglow-example is missing")
        elif "BUY-www-luminaglow-example" not in (survivor.get("merged_from") or []):
            problems.append("merged_from does not record the absorbed id")
        if pipeline.buyers_uae:
            scored = index_by(pipeline.buyers_uae.get("records", []), "buyer_id")
            for bid in ("BUY-luminaglow-example", "BUY-www-luminaglow-example"):
                rec = scored.get(bid)
                if rec and (rec.get("dimension_scores") or {}).get("evidence_quality", 0) > 95:
                    problems.append("%s: a single-domain record must not reach a "
                                    "corroboration-inflated evidence_quality" % bid)
        report.check(case, not problems, "\n".join(problems))

    # ---- T07 stale site -------------------------------------------------------
    case = "T07(a) stale but reachable: kept, flagged, low confidence"
    if not need(pipeline.buyers_uae):
        unavailable(case)
    else:
        records = index_by(pipeline.buyers_uae.get("records", []), "buyer_id")
        stale = records.get("BUY-palefade-example")
        problems = []
        if not stale:
            problems.append("a stale-but-reachable record must be kept, not dropped")
        else:
            if stale.get("stale") is not True:
                problems.append("stale = %r, expected true" % stale.get("stale"))
            if stale.get("confidence", 1) >= 0.5:
                problems.append("confidence = %r, expected below 0.5 (LOW)"
                                % stale.get("confidence"))
        report.check(case, not problems, "\n".join(problems))

    case = "T07(b) evidenced unreachable: kept in discovery, HF-06 in a match run"
    if not need(pipeline.buyers_uae, pipeline.match_134):
        unavailable(case)
    else:
        buyers = index_by(pipeline.buyers_uae.get("records", []), "buyer_id")
        dead = buyers.get("BUY-northgateimport-example")
        excluded = index_by(pipeline.match_134.get("excluded", []), "seller_id")
        problems = []
        if not dead:
            problems.append("discovery does not apply HF-06, so the record must still surface")
        elif dead.get("operational_status") != "unreachable":
            problems.append("operational_status = %r" % dead.get("operational_status"))
        entry = excluded.get("SEL-sopoongworks-example")
        if not entry:
            problems.append("the unreachable seller must be excluded from the match run")
        else:
            rules = [f["rule_id"] for f in entry.get("failed_rules", [])]
            if "HF-06" not in rules:
                problems.append("failed_rules = %r, expected HF-06" % rules)
        report.check(case, not problems, "\n".join(problems))

    # ---- T08 evidence conflict -----------------------------------------------
    case = "T08 evidence conflict: official source wins, both items survive"
    if not need(pipeline.buyers_uae):
        unavailable(case)
    else:
        records = index_by(pipeline.buyers_uae.get("records", []), "buyer_id")
        rec = records.get("BUY-pacificglowdist-example")
        problems = []
        if not rec:
            problems.append("BUY-pacificglowdist-example is absent")
        else:
            if rec.get("company_type") != "distributor":
                problems.append("company_type = %r, expected the official value 'distributor'"
                                % rec.get("company_type"))
            conflicts = rec.get("conflicts") or []
            entry = [c for c in conflicts if c.get("field") == "company_type"]
            if not entry:
                problems.append("the conflict note was dropped")
            else:
                if entry[0].get("losing_value") != "retailer":
                    problems.append("losing_value = %r" % entry[0].get("losing_value"))
                if entry[0].get("resolution") != "official_source":
                    problems.append("resolution = %r" % entry[0].get("resolution"))
            ids = set(e["evidence_id"] for e in rec.get("evidence", []))
            if not {"EV-001", "EV-009"} <= ids:
                problems.append("both sides of the conflict must be kept: %s" % sorted(ids))
            if (rec.get("dimension_scores") or {}).get("evidence_quality") != 100:
                problems.append("a RESOLVED conflict must cost nothing: evidence_quality = %r"
                                % (rec.get("dimension_scores") or {}).get("evidence_quality"))
        report.check(case, not problems, "\n".join(problems))

    case = "T08(b) unresolved conflict: penalised and confidence x0.85"
    if not need(pipeline.buyers_uae):
        unavailable(case)
    else:
        records = index_by(pipeline.buyers_uae.get("records", []), "buyer_id")
        rec = records.get("BUY-kantoimport-example")
        problems = []
        if not rec:
            problems.append("BUY-kantoimport-example is absent")
        else:
            if (rec.get("dimension_scores") or {}).get("evidence_quality") != 64:
                problems.append("evidence_quality = %r, expected 64 (conflict_penalty -10)"
                                % (rec.get("dimension_scores") or {}).get("evidence_quality"))
            if rec.get("confidence") != 0.41:
                problems.append("confidence = %r, expected 0.41 (0.64 x 0.75 x 0.85)"
                                % rec.get("confidence"))
        report.check(case, not problems, "\n".join(problems))

    # ---- T09 non-K-Beauty drift ----------------------------------------------
    case = "T09 non-K-Beauty wholesaler: zero K-Beauty fit, never a top candidate"
    if not need(pipeline.buyers_uae):
        unavailable(case)
    else:
        envelope = pipeline.buyers_uae
        records = index_by(envelope.get("records", []), "buyer_id")
        order = [r["buyer_id"] for r in envelope.get("records", [])]
        rec = records.get("BUY-desertbulk-example")
        problems = []
        if not rec:
            problems.append("BUY-desertbulk-example is absent")
        else:
            if (rec.get("dimension_scores") or {}).get("kbeauty_fit") != 0:
                problems.append("kbeauty_fit = %r, expected 0 (non_beauty_business -60 and "
                                "korean_products_signal_false -25)"
                                % (rec.get("dimension_scores") or {}).get("kbeauty_fit"))
            if rec.get("qualified") is not False:
                problems.append("qualified = %r, expected false" % rec.get("qualified"))
            if "BUY-desertbulk-example" in order[:10]:
                problems.append("a beauty-unrelated wholesaler must not reach the top ten")
        report.check(case, not problems, "\n".join(problems))

    # ---- T10 no match ---------------------------------------------------------
    case = "T10 no qualified match: explicit no_match, never a silent empty list"
    if not need(pipeline.match_nomatch):
        unavailable(case)
    else:
        result = pipeline.match_nomatch
        expected = read_json(os.path.join(EXPECTED, "match-no-match.expected.json"))
        no_match = result.get("no_match") or {}
        problems = []
        if no_match.get("is_no_match") is not True:
            problems.append("is_no_match = %r, expected true" % no_match.get("is_no_match"))
        if result.get("results"):
            problems.append("INV-21 requires results == [] when is_no_match is true")
        if not (no_match.get("reason") or "").strip():
            problems.append("reason must be non-empty and name the binding constraint")
        binding = no_match.get("binding_rule_ids") or []
        want_first = expected["no_match"]["binding_rule_ids_by_frequency"][0]
        if binding[:1] != [want_first]:
            problems.append("binding_rule_ids = %r, expected %s first (it rejected %d sellers)"
                            % (binding, want_first,
                               expected["no_match"]["rejection_counts"][want_first]))
        if len(result.get("excluded", [])) != expected["summary"]["excluded_count"]:
            problems.append("excluded_count = %d, expected %d"
                            % (len(result.get("excluded", [])),
                               expected["summary"]["excluded_count"]))
        report.check(case, not problems, "\n".join(problems))

    # ---- extra edge cases -----------------------------------------------------
    extra_cases(report, pipeline, allow_missing)


def extra_cases(report, pipeline, allow_missing):
    def unavailable(case):
        if allow_missing:
            report.skip(case, "depends on scripts/ output")
        else:
            report.fail(case, "no script output to assert against")

    sellers = index_by((pipeline.sellers or {}).get("records", []), "seller_id")
    matched = index_by((pipeline.match_134 or {}).get("results", []), "seller_id")

    case = "E01 MOQ range compares on min (BUILD-CONTRACT 3.3)"
    if not sellers:
        unavailable(case)
    else:
        rec = sellers.get("SEL-nuriskinlab-example")
        got = (rec or {}).get("dimension_scores", {}).get("operational_fit")
        report.check(case, got == 95,
                     "operational_fit = %r, expected 95 (moq {min:1000,max:5000} clears "
                     "half of the 3,000 ceiling on its min)" % got)

    case = "E02 open-ended MOQ ('from 2,000') is unknown, not 2,000"
    if not sellers:
        unavailable(case)
    else:
        rec = sellers.get("SEL-dalbitlabs-example") or {}
        ids = [u.get("criterion_id") for u in rec.get("unknown_penalty_applied", [])]
        report.check(case, "S-OP1" in ids and rec.get("dimension_scores", {})
                     .get("operational_fit") == 57,
                     "unknown criteria %r, operational_fit %r"
                     % (ids, rec.get("dimension_scores", {}).get("operational_fit")))

    case = "E03 MOQ unit mismatch: unknown MOQ plus the -10 adjustment"
    if not sellers:
        unavailable(case)
    else:
        rec = sellers.get("SEL-kkotgilcos-example") or {}
        ids = [u.get("criterion_id") for u in rec.get("unknown_penalty_applied", [])]
        report.check(case, "S-OP1" in ids and rec.get("dimension_scores", {})
                     .get("operational_fit") == 47,
                     "unknown criteria %r, operational_fit %r"
                     % (ids, rec.get("dimension_scores", {}).get("operational_fit")))

    case = "E04 verified-empty certification list rejects under HF-04"
    if not pipeline.match_134:
        unavailable(case)
    else:
        excluded = index_by(pipeline.match_134.get("excluded", []), "seller_id")
        entry = excluded.get("SEL-haneulbio-example") or {}
        rules = [f["rule_id"] for f in entry.get("failed_rules", [])]
        report.check(case, rules[:1] == ["HF-04"],
                     "failed_rules = %r; only an exhaustive, official list may reject" % rules)

    case = "E05 unverified certification list never rejects (HF-04 not applicable)"
    if not pipeline.match_134:
        unavailable(case)
    else:
        candidate = matched.get("SEL-yeonhwalab-example") or {}
        hf = candidate.get("hard_filter", {})
        report.check(case,
                     bool(candidate) and "HF-04" not in (hf.get("rules_evaluated") or [])
                     and "HF-04" not in (hf.get("rules_skipped_unknown") or []),
                     "HF-04 appeared in %r / %r" % (hf.get("rules_evaluated"),
                                                    hf.get("rules_skipped_unknown")))

    case = "E06 unknown seller country skips HF-08 and pairs a zero-weight penalty (INV-07)"
    if not pipeline.match_134:
        unavailable(case)
    else:
        candidate = matched.get("SEL-hwadamglobal-example") or {}
        hf = candidate.get("hard_filter", {})
        ids = [u.get("criterion_id") for u in candidate.get("unknown_penalty_applied", [])]
        report.check(case,
                     "HF-08" in (hf.get("rules_skipped_unknown") or []) and "HF-08" in ids,
                     "rules_skipped_unknown %r, unknown_penalty criteria %r"
                     % (hf.get("rules_skipped_unknown"), ids))

    case = "E07 a sibling category scores but does not clear HF-01"
    if not pipeline.match_134:
        unavailable(case)
    else:
        excluded = index_by(pipeline.match_134.get("excluded", []), "seller_id")
        entry = excluded.get("SEL-muljilcosmetic-example") or {}
        rules = [f["rule_id"] for f in entry.get("failed_rules", [])]
        report.check(case, rules[:1] == ["HF-01"], "failed_rules = %r" % rules)

    case = "E08 contact_channels present-and-empty is a verified negative, not unknown"
    if not pipeline.buyers_uae:
        unavailable(case)
    else:
        records = index_by(pipeline.buyers_uae.get("records", []), "buyer_id")
        rec = records.get("BUY-straitswholesale-example") or {}
        ids = [u.get("criterion_id") for u in rec.get("unknown_penalty_applied", [])]
        report.check(case,
                     rec.get("dimension_scores", {}).get("reachability") == 0
                     and not [i for i in ids if i.startswith("B-RE")],
                     "reachability = %r, unknown criteria = %r"
                     % (rec.get("dimension_scores", {}).get("reachability"), ids))

    case = "E09 evidence that covers no material claim scores 0 and is excluded (DISC-06)"
    if not pipeline.buyers_uae:
        unavailable(case)
    else:
        excluded = index_by(pipeline.buyers_uae.get("excluded", []), "id")
        entry = excluded.get("BUY-quietharbour-example") or {}
        report.check(case, "no evidenced material claim" in (entry.get("reason_summary") or ""),
                     "reason_summary = %r" % entry.get("reason_summary"))

    case = "E10 a candidate with no evidenced fit reason is gated by HF-00"
    if not pipeline.match_134:
        unavailable(case)
    else:
        excluded = index_by(pipeline.match_134.get("excluded", []), "seller_id")
        entry = excluded.get("SEL-saeromtrading-example") or {}
        rules = [f["rule_id"] for f in entry.get("failed_rules", [])]
        report.check(case, rules[:1] == ["HF-00"],
                     "failed_rules = %r, expected the PRD 15.3 evidence gate" % rules)

    case = "E11 rerank is bounded, evidenced and never reorders past max_delta"
    if not pipeline.match_134:
        unavailable(case)
    else:
        problems = []
        for candidate in pipeline.match_134.get("results", []):
            rerank = candidate.get("rerank") or {}
            delta = rerank.get("delta", 0)
            if abs(delta) > 5:
                problems.append("%s: |delta| = %s > max_delta 5" % (candidate["seller_id"], delta))
            if rerank.get("applied") and not rerank.get("evidence_ids"):
                problems.append("%s: an applied rerank needs >= 1 evidence_id"
                                % candidate["seller_id"])
            if rerank.get("applied") is False and delta != 0:
                problems.append("%s: applied=false must force delta 0" % candidate["seller_id"])
            if candidate.get("match_score") != max(0, min(100, candidate.get("base_score", 0)
                                                          + delta)):
                problems.append("%s: match_score != clamp(base_score + delta)"
                                % candidate["seller_id"])
        hanbit = matched.get("SEL-hanbitcos-example") or {}
        if hanbit and (hanbit.get("base_score"), hanbit.get("match_score")) != (97, 99):
            problems.append("the SCORING-CONTRACT 6.2 rerank example must give base 97 -> 99, "
                            "got %r -> %r" % (hanbit.get("base_score"), hanbit.get("match_score")))
        report.check(case, not problems, "\n".join(problems))

    case = "E12 INV-22 every referenced evidence_id resolves"
    if not pipeline.match_134:
        unavailable(case)
    else:
        index = set(e.get("evidence_id") for e in pipeline.match_134.get("evidence_index", []))
        problems = []
        for candidate in pipeline.match_134.get("results", []):
            referenced = set(candidate.get("evidence_ids") or [])
            for item in candidate.get("rationale", []):
                referenced |= set(item.get("evidence_ids") or [])
            for item in candidate.get("risks", []):
                referenced |= set(item.get("evidence_ids") or [])
            referenced |= set((candidate.get("rerank") or {}).get("evidence_ids") or [])
            dangling = sorted(referenced - index)
            if dangling:
                problems.append("%s: %s not in evidence_index" % (candidate["seller_id"],
                                                                  dangling))
        report.check(case, not problems, "\n".join(problems[:10]))

    case = "E13 INV-23 one score_version across the whole run"
    if not pipeline.match_134:
        unavailable(case)
    else:
        versions = {pipeline.match_134.get("score_version")}
        for attr in ("buyers_uae", "buyers_uk", "sellers"):
            produced = getattr(pipeline, attr)
            if produced:
                versions.add(produced.get("score_version"))
                for rec in produced.get("records", []):
                    versions.add(rec.get("score_version"))
        report.check(case, len(versions) == 1 and "unscored" not in versions,
                     "score_version values seen: %s" % sorted(str(v) for v in versions))

    case = "E15 SCORING-CONTRACT 3.5 summary counters are self-consistent"
    if not pipeline.match_134:
        unavailable(case)
    else:
        problems = []
        for label, result in (("match-134", pipeline.match_134),
                              ("no-match", pipeline.match_nomatch)):
            if not result:
                continue
            summary = result.get("summary", {})
            considered = summary.get("candidates_considered")
            passed = summary.get("passed_hard_filter")
            returned = summary.get("returned")
            excluded = summary.get("excluded_count")
            if not (considered >= passed >= returned):
                problems.append("%s: candidates_considered >= passed_hard_filter >= returned "
                                "fails (%r, %r, %r)" % (label, considered, passed, returned))
            if excluded != considered - passed:
                problems.append("%s: excluded_count %r != candidates_considered %r - "
                                "passed_hard_filter %r" % (label, excluded, considered, passed))
            if excluded != len(result.get("excluded", [])):
                problems.append("%s: excluded_count %r != len(excluded) %d"
                                % (label, excluded, len(result.get("excluded", []))))
        report.check(case, not problems, "\n".join(problems))

    case = ("E19 INV-19 validate_output refuses a document that cannot render its "
            "per-candidate lines")
    if not (pipeline.buyers_uae and pipeline.match_134):
        unavailable(case)
    elif missing_scripts():
        report.skip(case, "scripts/ incomplete")
    else:
        problems = []
        with tempfile.TemporaryDirectory() as tmp:
            # Strip exactly the fields BUILD-CONTRACT 10.5 needs to print the required
            # lines. The stripped documents stay schema-shaped, which is the whole point:
            # before this check they sailed through --strict --invariants at exit 0.
            stripped_discovery = json.loads(json.dumps(pipeline.buyers_uae))
            for record in stripped_discovery.get("records", []):
                for field in ("website", "contact_channels", "missing"):
                    record.pop(field, None)
            stripped_match = json.loads(json.dumps(pipeline.match_134))
            for candidate in stripped_match.get("results", []):
                for field in ("website", "contact_channels", "oem_odm", "company_type"):
                    candidate.pop(field, None)
            for kind, doc in (("discovery-result", stripped_discovery),
                              ("match-result", stripped_match)):
                path = os.path.join(tmp, "stripped-%s.json" % kind)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, ensure_ascii=False)
                code, out, err = run_script(
                    "validate_output.py",
                    ["--input", path, "--schema", kind, "--strict", "--invariants",
                     "--as-of", AS_OF, "--json"])
                if code == 0:
                    problems.append("%s: a document with no renderable Website:/Contact:/"
                                    "Type:/Missing: line exited 0" % kind)
                if "INV-19" not in (out + err):
                    problems.append("%s: exited %d but reported no INV-19 failure"
                                    % (kind, code))
        report.check(case, not problems, "\n".join(problems))

    case = ("E20 S-CP3 prices an ID/BPOM notification registered > in_progress > absent")
    if not pipeline.match_idhalal:
        unavailable(case)
    else:
        scored = index_by(pipeline.match_idhalal.get("results", []), "seller_id")
        ladder = ["SEL-ahyeonlab-example", "SEL-durimcos-example", "SEL-maruhwabio-example"]
        fits = [(scored.get(sid) or {}).get("component_scores", {}).get("compliance_fit")
                for sid in ladder]
        order = [c["seller_id"] for c in pipeline.match_idhalal.get("results", [])]
        problems = []
        if None in fits:
            problems.append("one of %s is absent from results[]" % ladder)
        elif not (fits[0] > fits[1] > fits[2]):
            problems.append("compliance_fit %s is not strictly decreasing across %s"
                            % (fits, ladder))
        if order[:3] != ladder:
            problems.append("ranked order %s, expected %s" % (order[:3], ladder))
        absent = scored.get("SEL-maruhwabio-example") or {}
        if "S-CP3" not in [u.get("criterion_id")
                           for u in absent.get("unknown_penalty_applied", [])]:
            problems.append("an absent regulatory_registrations array must take the S-CP3 "
                            "unknown penalty, not read as 'none found'")
        report.check(case, not problems, "\n".join(problems))

    case = "E21 HALAL required: HF-04 rejects only the verified list that lacks the token"
    if not pipeline.match_idhalal:
        unavailable(case)
    else:
        excluded = index_by(pipeline.match_idhalal.get("excluded", []), "seller_id")
        scored = index_by(pipeline.match_idhalal.get("results", []), "seller_id")
        problems = []
        rejected = excluded.get("SEL-cheonglimoem-example") or {}
        if [f["rule_id"] for f in rejected.get("failed_rules", [])][:1] != ["HF-04"]:
            problems.append("an exhaustive official list without HALAL must fail HF-04: %r"
                            % [f["rule_id"] for f in rejected.get("failed_rules", [])])
        unverified = scored.get("SEL-baraecos-example") or {}
        hf = unverified.get("hard_filter", {})
        if not unverified:
            problems.append("the unverified-list seller must stay in results[]")
        elif ("HF-04" in (hf.get("rules_evaluated") or [])
              or "HF-04" in (hf.get("rules_skipped_unknown") or [])):
            problems.append("HF-04 is not applicable to an unverified list, so it belongs in "
                            "neither array: %r / %r" % (hf.get("rules_evaluated"),
                                                        hf.get("rules_skipped_unknown")))
        elif unverified.get("component_scores", {}).get("compliance_fit", 100) >= (
                (scored.get("SEL-maruhwabio-example") or {})
                .get("component_scores", {}).get("compliance_fit", 0)):
            problems.append("a missing HALAL must be penalised below a held one")
        report.check(case, not problems, "\n".join(problems))

    case = "E22 a seller that excludes ID is rejected by HF-05, not down-ranked"
    if not pipeline.match_idhalal:
        unavailable(case)
    else:
        excluded = index_by(pipeline.match_idhalal.get("excluded", []), "seller_id")
        entry = excluded.get("SEL-hanaraexport-example") or {}
        rules = [f["rule_id"] for f in entry.get("failed_rules", [])]
        report.check(case, rules[:1] == ["HF-05"], "failed_rules = %r" % rules)

    case = "E14 INV-35 a partial run degrades instead of aborting"
    for attr, name in (("buyers_uae", "score_buyer.py"), ("sellers", "score_seller.py"),
                       ("match_134", "score_match.py")):
        produced = getattr(pipeline, attr)
        if produced is None:
            continue
        if "partial" not in produced:
            report.fail(case, "%s output carries no `partial` flag" % name)
            break
    else:
        if any(getattr(pipeline, a) for a in ("buyers_uae", "sellers", "match_134")):
            report.ok(case)
        else:
            unavailable(case)


# ---------------------------------------------------------------------------
# 5. safety / static checks
# ---------------------------------------------------------------------------
SEND_PATTERNS = [r"\bsmtplib\b", r"\bSMTP\b", r"\bsendmail\b", r"\bsend_email\b",
                 r"\bsendgrid\b", r"\bmailgun\b", r"\bpostmark\b", r"ses\.send",
                 r"send_message\(", r"gmail[ _-]?(?:api|client|service|scope|send)"]
NEGATION_MARKERS = ["forbidden", "must not", "never", "no send", "prohibited", "not ship",
                    "no transport", "inv-10", "no email", "does not send", "cannot send",
                    "no dispatch", "not a dispatch", "no message transport", "금지"]
PLACEHOLDER_PATTERNS = [r"\bTODO\b", r"\bFIXME\b", r"\bXXX\b", r"NotImplementedError",
                        r"pass\s+#\s*stub", r"test\.skip", r"\.only\(", r"__INLINE_"]
WALL_CLOCK_PATTERNS = [r"datetime\.now", r"datetime\.utcnow", r"date\.today", r"time\.time\("]
STDLIB_OK = set("""argparse base64 collections copy csv dataclasses datetime decimal difflib
enum functools glob gzip hashlib heapq hmac html http io itertools json logging math os pathlib
pprint random re shutil signal socket ssl string subprocess sys tempfile textwrap time types
typing unicodedata urllib uuid warnings zipfile abc contextlib operator statistics""".split())


def package_files(exts=(".py", ".md", ".json", ".sh")):
    for root, dirs, files in os.walk(PKG_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for name in sorted(files):
            if name.endswith(exts):
                yield os.path.join(root, name)


# ---------------------------------------------------------------------------
# 4b. field regressions - the four blockers the two live web trials exposed
#
# Every case below is a defect that was observed on the real public web, not a
# hypothetical. They are kept apart from phase_cases() so a future reader can see
# at a glance which assertions are paid for in field evidence. The narrative and
# the numbers live in references/calibration-notes.md.
# ---------------------------------------------------------------------------
def _write_temp_json(document, prefix):
    handle, path = tempfile.mkstemp(suffix=".json", prefix=prefix)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(document, fh, ensure_ascii=False)
    return path


def _zero_kbeauty_buyer():
    """A maximal B2B buyer with NO Korean or K-Beauty evidence anywhere.

    Modelled on the FD-35 live record: a UAE trading company whose
    korean_products_signal and product_categories were both unknown - no Korean or
    K-Beauty reference on either page that was read - which nevertheless scored 70,
    qualified, and outranked seven companies with evidenced named Korean portfolios.
    Every other dimension here is deliberately pushed to its ceiling, so the case
    tests the GATE and not a weak record that would have failed anyway.
    """
    def ev(eid, claim, value, path, quote, date="2026-07-01"):
        return {
            "schema_version": "0.1.0", "evidence_id": eid, "claim": claim, "value": value,
            "source_url": "https://nokorean.example/" + path,
            "source_domain": "nokorean.example", "source_type": "official_site",
            "source_tier": 1, "is_official": True, "observed_at": "2026-09-10T09:00:00Z",
            "source_date": date, "confidence": 0.9, "quote_or_summary": quote,
            "retrieval_method": "page_fetch",
        }
    return {
        "schema_version": "0.1.0", "score_version": "unscored",
        "buyer_id": "BUY-nokorean-example",
        "company_name": "No Korean Carriage Trading LLC",
        "website": "https://nokorean.example/",
        "canonical_domain": "nokorean.example",
        "country": "AE", "company_type": "distributor", "status": "VERIFIED",
        "qualification_score": 0, "confidence": 0,
        "wholesale_signal": True, "partnership_signal": True,
        "sourcing_intent": "high",
        "sourcing_signals": ["open_call_for_suppliers", "partnership_page",
                             "trade_show_attendance"],
        "channels": ["wholesale", "distributor_network", "ecommerce"],
        "contact_channels": [
            {"type": "partnership_form", "value": "https://nokorean.example/partnership"},
            {"type": "corporate_email", "value": "sourcing@nokorean.example"},
        ],
        "operational_status": "active", "stale": False,
        "evidence": [
            ev("EV-K01", "company_type", "distributor", "about",
               "A UAE distributor of imported consumer brands."),
            ev("EV-K02", "wholesale_signal", True, "wholesale",
               "Wholesale trade accounts are available to licensed retailers."),
            ev("EV-K03", "sourcing_intent", "high", "news/open-call",
               "Open call for suppliers: we are accepting new brands this season.",
               "2026-07-15"),
            ev("EV-K04", "partnership_signal", True, "partnership",
               "Brand partnership enquiries are reviewed by our sourcing team.",
               "2026-07-15"),
            ev("EV-K05", "country", "AE", "contact", "Head office: Dubai, United Arab Emirates."),
            ev("EV-K06", "contact_channels",
               ["https://nokorean.example/partnership"], "contact",
               "Partnership form and sourcing address published on the contact page."),
        ],
        "notes": [],
    }


def phase_field_regressions(report, allow_missing):
    scripts_present = all(
        os.path.isfile(os.path.join(SCRIPT_DIR, name)) for name in PIPELINE_SCRIPTS
    )
    if not scripts_present:
        note = "scripts/ not present"
        for name in ("FR-01 no --query degrades loudly",
                     "FR-02 canonical_domain never merges shared hosting",
                     "FR-03 a bare GMP is never promoted to CGMP",
                     "FR-04 zero K-Beauty evidence cannot qualify",
                     "FR-05 an unpublished minimum order reaches missing[]",
                     "FR-06 requested category evidence reaches missing[]",
                     "FR-07 seller minimum order reaches missing[]",
                     "FR-08 Korean labels are complete"):
            (report.skip if allow_missing else report.fail)("field regression: %s" % name, note)
        return

    sys.path.insert(0, SCRIPT_DIR)
    sys.dont_write_bytecode = True
    import _common  # noqa: F401  (already imported by pick_validator in practice)

    # -- FR-01 ---------------------------------------------------------------
    # Live seller trial: score_seller.py was run with no --query and silently
    # scored every candidate as if the RFQ had imposed nothing. Eleven of eleven
    # records came back "qualified" and the operator had no way to see that five
    # hard filters and five criteria had never been evaluated.
    problems = []
    for script, fixture, must_name in (
        ("score_seller.py", "sellers.golden.json",
         ["HF-01", "HF-03", "HF-08", "S-PF1", "S-CP1"]),
        ("score_buyer.py", "buyers.golden.json",
         ["market_relevance"]),
    ):
        code, out, _err = run_script(script, [
            "--input", os.path.join(FIXTURES, fixture), "--as-of", AS_OF])
        if code != 0:
            problems.append("%s: exit %d with no --query (it must degrade, not abort)"
                            % (script, code))
            continue
        try:
            envelope = json.loads(out)
        except ValueError as exc:
            problems.append("%s: stdout is not JSON (%s)" % (script, exc))
            continue
        if envelope.get("partial") is not True:
            problems.append("%s: partial = %r, expected true (INV-35)"
                            % (script, envelope.get("partial")))
        notes = " ".join(n for n in (envelope.get("notes") or []) if isinstance(n, str))
        if "--query" not in notes:
            problems.append("%s: no envelope note names the missing --query" % script)
        for token in must_name:
            if token not in notes:
                problems.append("%s: the degradation note never names %s" % (script, token))
    report.check("field regression: FR-01 a run with no --query is marked partial and "
                 "names what it skipped", not problems, "\n".join(problems[:10]))

    # score_buyer.py additionally drops market_relevance from BOTH sides of the
    # fraction rather than scoring it 0, and says so on every record.
    problems = []
    code, out, _err = run_script("score_buyer.py", [
        "--input", os.path.join(FIXTURES, "buyers.golden.json"), "--as-of", AS_OF])
    if code != 0:
        problems.append("exit %d" % code)
    else:
        envelope = json.loads(out)
        for record in envelope.get("records", []):
            if "market_relevance" in (record.get("dimension_scores") or {}):
                problems.append("%s: market_relevance scored 0 instead of being dropped"
                                % record.get("buyer_id"))
                break
            notes = " ".join(n for n in (record.get("notes") or []) if isinstance(n, str))
            if "renormalis" not in notes:
                problems.append("%s: no per-record renormalisation note"
                                % record.get("buyer_id"))
                break
    report.check("field regression: FR-01b an inapplicable dimension leaves the "
                 "denominator, never scores 0", not problems, "\n".join(problems[:10]))

    # FR-01c: the criterion level of the same defect. INAPPLICABLE ("the query never
    # asked") and UNKNOWN ("the company never published it") are different states with
    # different arithmetic, and the live run collapsed both into a scored 0: every
    # query-reading criterion returned 0 points inside a full denominator, which reads
    # on the page as "this maker fails on certifications" when nothing was ever asked.
    # An inapplicable criterion must leave BOTH sides of its dimension's fraction, take
    # no unknown penalty, and say so - and the operator must see the warning on stderr,
    # not only a `partial` flag buried in the envelope.
    problems = []
    for script, fixture, query_criteria in (
        ("score_seller.py", "sellers.golden.json", {"S-PF1", "S-PF2", "S-CP1", "S-CP2", "S-CP3"}),
        ("score_buyer.py", "buyers.golden.json", {"B-MR1", "B-MR2", "B-MR3"}),
    ):
        code, out, err = run_script(script, [
            "--input", os.path.join(FIXTURES, fixture), "--as-of", AS_OF])
        if code != 0:
            problems.append("%s: exit %d with no --query" % (script, code))
            continue
        if "WARNING: partial run" not in err:
            problems.append("%s: nothing on stderr says the run is partial; the operator reads "
                            "the confident \"N qualified at >= T\" line and nothing else" % script)
        envelope = json.loads(out)
        marked_inapplicable = 0
        for record in envelope.get("records", []):
            rid = record.get("seller_id") or record.get("buyer_id")
            details = [d for d in (record.get("dimension_details") or []) if isinstance(d, dict)]
            for detail in details:
                if detail.get("criterion_id") not in query_criteria:
                    continue
                state = detail.get("state")
                if state != "inapplicable":
                    problems.append("%s %s: %s is %r with no query surface - an unscorable "
                                    "criterion is INAPPLICABLE, never a scored 0 and never the "
                                    "unknown path" % (script, rid, detail.get("criterion_id"), state))
                else:
                    marked_inapplicable += 1
            for entry in record.get("unknown_penalty_applied") or []:
                if isinstance(entry, dict) and entry.get("criterion_id") in query_criteria:
                    problems.append("%s %s: %s took an unknown penalty; it is the QUERY that is "
                                    "absent, not the company's data (SCORING-CONTRACT 0.4)"
                                    % (script, rid, entry.get("criterion_id")))
            by_dimension = {}
            for detail in details:
                by_dimension.setdefault(detail.get("dimension"), []).append(detail)
            scored_dimensions = (record.get("extensions") or {}).get(
                "score_breakdown", {}).get("dimensions", {})
            for dimension_key in sorted(by_dimension):
                rows = by_dimension[dimension_key]
                info = scored_dimensions.get(dimension_key)
                if not isinstance(info, dict) or "raw_score" not in info:
                    continue  # dropped from the fraction entirely (FR-01b), or computed
                applicable = [r for r in rows if r.get("state") != "inapplicable"]
                if len(applicable) == len(rows):
                    continue
                earned = sum(float(r.get("earned_points") or 0) for r in applicable)
                applicable_max = sum(float(r.get("max_points") or 0) for r in applicable)
                whole_max = sum(float(r.get("max_points") or 0) for r in rows)
                if applicable_max <= 0 or whole_max <= 0:
                    continue
                renormalised = earned / applicable_max * 100.0
                zeroed = earned / whole_max * 100.0
                raw = float(info["raw_score"])
                if abs(raw - renormalised) > 0.02:
                    problems.append("%s %s %s: raw_score %.2f is neither the renormalised "
                                    "%.2f nor anything else this suite can derive"
                                    % (script, rid, dimension_key, raw, renormalised))
                elif earned > 0 and abs(raw - zeroed) <= 0.02 and abs(zeroed - renormalised) > 0.02:
                    problems.append("%s %s %s: scored over the WHOLE denominator (%.2f), so the "
                                    "inapplicable criteria were charged as zeros"
                                    % (script, rid, dimension_key, zeroed))
        if not marked_inapplicable:
            problems.append("%s: no criterion came back inapplicable, so this case proves "
                            "nothing about the state it is meant to protect" % script)
    report.check("field regression: FR-01c a criterion the missing query cannot score is "
                 "INAPPLICABLE, never a scored 0 and never an unknown penalty",
                 not problems, "\n".join(problems[:10]))

    # -- FR-02 ---------------------------------------------------------------
    # Live seller trial: two unrelated Korean OEM companies published on the same
    # shared-hosting platform. canonical_domain() reduced both to the platform
    # suffix, dedupe_companies.py merged them into one record, and one real
    # manufacturer disappeared from the run.
    problems = []
    distinct_pairs = [
        ("https://alpha-oem.pages.dev/", "https://beta-oem.pages.dev/"),
        ("https://shop-alpha.cafe24.com/", "https://shop-beta.cafe24.com/"),
        ("https://alpha-lab.github.io/", "https://beta-lab.github.io/"),
        ("https://alpha-store.myshopify.com/", "https://beta-store.myshopify.com/"),
    ]
    for left, right in distinct_pairs:
        a, b = _common.canonical_domain(left), _common.canonical_domain(right)
        if a is None or b is None:
            problems.append("%s / %s: canonical_domain returned None for a real site" % (left, right))
        elif a == b:
            problems.append("%s and %s collapsed to the same key %r" % (left, right, a))
    # The platform itself is never a merge key.
    for platform in ("https://pages.dev/", "https://cafe24.com/",
                     "https://smartstore.naver.com/", "https://github.io/"):
        if _common.canonical_domain(platform) is not None:
            problems.append("%s: the bare platform must never become a canonical_domain (got %r)"
                            % (platform, _common.canonical_domain(platform)))
    # www/non-www on an ordinary registrable domain must still collapse (T06).
    if _common.canonical_domain("https://www.foo.example/") != _common.canonical_domain("https://foo.example/"):
        problems.append("www/non-www on an ordinary domain stopped collapsing (T06 regression)")
    report.check("field regression: FR-02 canonical_domain keeps shared-hosting "
                 "tenants apart", not problems, "\n".join(problems[:10]))

    # End to end: two tenants of one shared host must survive dedupe as two records.
    def tenant(slug, host):
        # canonical_domain is deliberately NOT declared: dedupe must DERIVE it from
        # the website, which is what makes this case load-bearing. With the key
        # written out by hand the merge would be decided by a string the fixture
        # supplied, and a regression inside canonical_domain() would go unseen.
        return {
            "schema_version": "0.1.0", "score_version": "unscored",
            "seller_id": "SEL-%s-example" % slug,
            "company_name": "%s Cosmetic Lab Co., Ltd." % slug.title(),
            "website": "https://%s.%s/" % (slug, host),
            "country": "KR", "company_type": "oem_odm", "status": "VERIFIED",
            "qualification_score": 0, "confidence": 0, "evidence": [], "notes": [],
        }
    document = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": "seller",
                "records": [tenant("alpha", "pages.dev"), tenant("beta", "pages.dev")]}
    path = _write_temp_json(document, "kbtm-sharedhost-")
    try:
        code, out, err = run_script("dedupe_companies.py",
                                    ["--input", path, "--entity", "seller", "--as-of", AS_OF])
        problems = []
        if code != 0:
            problems.append("exit %d: %s" % (code, err.strip()[:200]))
        else:
            envelope = json.loads(out)
            records = envelope.get("records", [])
            if len(records) != 2:
                problems.append("%d records survived, expected 2" % len(records))
            if envelope.get("merges"):
                problems.append("merged: %s" % _short(envelope["merges"]))
            # The derived key itself is the assertion: collapsing both tenants onto
            # the bare platform suffix is the FD-02 defect, and it stays invisible if
            # only the record count is checked (dedupe also guards on the normalised
            # company name, so two differently-named tenants survive either way).
            derived = sorted(r.get("canonical_domain") for r in records)
            if derived != ["alpha.pages.dev", "beta.pages.dev"]:
                problems.append("derived canonical_domain = %r, expected the two tenant "
                                "hosts kept apart" % (derived,))
        report.check("field regression: FR-02b two tenants of one shared host are "
                     "never merged", not problems, "\n".join(problems[:10]))
    finally:
        os.unlink(path)

    # FR-02c: the fallback for a platform the embedded suffix list does NOT know.
    # _common.MULTI_PART_SUFFIXES can never be the full Public Suffix List (stdlib only,
    # no network, no vendored PSL file), so the tenants of an unlisted platform really do
    # reduce to one registrable domain - which is precisely where a false merge would be
    # invisible. The refusal has to happen one layer up, in dedupe: two different
    # companies under two different subdomains of one registrable domain are REPORTED as
    # a possible duplicate, never merged (BUILD-CONTRACT 8.4 R8.4.2). A missed merge
    # leaves a duplicate in the list; a false merge publishes a company that does not
    # exist and deletes a real one, which is the trade the live trial paid for.
    def _site(seller_id, website, name):
        # canonical_domain is deliberately not declared, for the FR-02b reason: the key
        # has to be DERIVED from the website or the merge is decided by a string the
        # fixture supplied.
        return {
            "schema_version": "0.1.0", "score_version": "unscored",
            "seller_id": seller_id, "company_name": name, "website": website,
            "country": "KR", "company_type": "oem_odm", "status": "VERIFIED",
            "qualification_score": 0, "confidence": 0, "evidence": [], "notes": [],
        }

    problems = []
    left_host = "https://alpha-lab.hostplatform.example/"
    right_host = "https://beta-lab.hostplatform.example/"
    if _common.canonical_domain(left_host) != _common.canonical_domain(right_host):
        problems.append("hostplatform.example is now a known suffix, so this case no longer "
                        "exercises the unknown-suffix fallback; pick a host that is not on the "
                        "embedded list")
    records = [
        _site("SEL-alpha-lab-example", left_host, "Alpha Lab Cosmetic Co., Ltd."),
        _site("SEL-beta-lab-example", right_host, "Beta Lab Cosmetic Co., Ltd."),
        # Control: one company on two of its own pages. The refusal above must stay
        # narrow enough that this still merges, or "never merge" has just become "never
        # dedupe" and every duplicate ships.
        _site("SEL-alphalab-root-example", "https://alphalab.example/",
              "Alphalab Cosmetic Co., Ltd."),
        _site("SEL-alphalab-shop-example", "https://shop.alphalab.example/",
              "Alphalab Cosmetic Co., Ltd."),
    ]
    document = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": "seller",
                "records": records}
    path = _write_temp_json(document, "kbtm-unlistedhost-")
    try:
        code, out, err = run_script("dedupe_companies.py",
                                    ["--input", path, "--entity", "seller", "--as-of", AS_OF])
        if code != 0:
            problems.append("exit %d: %s" % (code, err.strip()[:200]))
        else:
            envelope = json.loads(out)
            survivors = [r.get("seller_id") for r in envelope.get("records", [])]
            for wanted in ("SEL-alpha-lab-example", "SEL-beta-lab-example"):
                if wanted not in survivors:
                    problems.append("%s did not survive: two different companies on one unlisted "
                                    "platform were folded into one record" % wanted)
            merged_ids = set()
            for entry in envelope.get("merges") or []:
                merged_ids.update(entry.get("absorbed_ids") or [])
                merged_ids.add(entry.get("survivor_id"))
            if merged_ids & {"SEL-alpha-lab-example", "SEL-beta-lab-example"}:
                problems.append("a merge group contains a tenant: %s" % _short(envelope["merges"]))
            if "SEL-alphalab-shop-example" in survivors:
                problems.append("the control pair (one company, two of its own pages) stopped "
                                "merging: the refusal is no longer narrow, it is a dedupe outage")
            suggested = [entry for entry in (envelope.get("merges_suggested") or [])
                         if sorted(entry.get("record_ids") or []) ==
                         ["SEL-alpha-lab-example", "SEL-beta-lab-example"]]
            if not suggested:
                problems.append("the refused pair was not reported for operator confirmation; a "
                                "silent refusal is a duplicate nobody will ever reconcile")
            elif suggested[0].get("key") != "canonical_domain":
                problems.append("the refused pair is reported under key %r, expected "
                                "canonical_domain" % suggested[0].get("key"))
        report.check("field regression: FR-02c an unlisted hosting suffix refuses the merge "
                     "and reports it, never guesses", not problems, "\n".join(problems[:10]))
    finally:
        os.unlink(path)

    # -- FR-03 ---------------------------------------------------------------
    # Live seller trial: Korean factory sites print "GMP 기준" / "GMP 시설" as
    # marketing copy far more often than they hold the MFDS CGMP designation.
    # Folding a bare GMP into CGMP handed that copy S-CP4's full points and let it
    # satisfy HF-04's superset test against a query that required CGMP.
    problems = []
    bare = _common.normalize_certification("GMP")
    if bare == "CGMP":
        problems.append('normalize_certification("GMP") returned CGMP')
    if bare != _common.normalize_certification("gmp"):
        problems.append("bare GMP is not normalised idempotently across case")
    for real in ("CGMP", "cGMP", "CGMP "):
        if _common.normalize_certification(real) != "CGMP":
            problems.append("%r no longer normalises to CGMP" % real)
    if _common.normalize_certification("ISO 22716") != "ISO22716":
        problems.append('"ISO 22716" no longer normalises to ISO22716')
    # The bare token must also earn nothing: it is absent from both S-CP4 groups.
    sys.path.insert(0, SCRIPT_DIR)
    import score_seller as _score_seller
    if bare in _score_seller.BASELINE_QUALITY_TOKENS:
        problems.append("%s is inside BASELINE_QUALITY_TOKENS (it would earn S-CP4's 10)" % bare)
    if bare in _score_seller.OTHER_QUALITY_TOKENS:
        problems.append("%s is inside OTHER_QUALITY_TOKENS (it would earn S-CP4's 6)" % bare)
    report.check("field regression: FR-03 a bare GMP is never promoted to CGMP",
                 not problems, "\n".join(problems[:10]))

    # FR-03b: the same defect one layer out, where an operator would actually meet it.
    # The normalizer is only half the guard - what the live trial produced was a RECORD
    # that held the MFDS designation nobody had evidenced. So: a seller whose entire
    # certification text is the bare marketing token must not fire S-CP4's
    # iso22716_or_cgmp_held signal, must earn nothing for it, and must FAIL a query that
    # requires CGMP instead of satisfying it through a promoted token. Inferring a
    # certification from marketing copy is forbidden wherever it happens
    # (references/evidence-policy.md), and the normalizer is a "wherever".
    def _gmp_only_seller(verified):
        def ev(eid, claim, value, path, quote):
            return {
                "schema_version": "0.1.0", "evidence_id": eid, "claim": claim, "value": value,
                "source_url": "https://gmpclaimlab.example/" + path,
                "source_domain": "gmpclaimlab.example", "source_type": "official_site",
                "source_tier": 1, "is_official": True, "observed_at": "2026-09-10T09:00:00Z",
                "source_date": "2026-06-01", "confidence": 0.9, "quote_or_summary": quote,
                "retrieval_method": "page_fetch",
            }
        record = {
            "schema_version": "0.1.0", "score_version": "unscored",
            "seller_id": "SEL-gmpclaimlab-example",
            "company_name": "Gmpclaim Cosmetic Lab Co., Ltd.",
            "website": "https://gmpclaimlab.example/",
            "canonical_domain": "gmpclaimlab.example",
            "country": "KR", "company_type": "manufacturer", "status": "VERIFIED",
            "qualification_score": 0, "confidence": 0,
            "certifications": ["GMP"], "oem_odm": True,
            "product_categories": ["skincare", "sunscreen"],
            "evidence": [
                ev("EV-G01", "company_type", "manufacturer", "about",
                   "Own production facility in the Republic of Korea."),
                ev("EV-G02", "certifications", ["GMP"], "factory",
                   "GMP \uae30\uc900\uc758 \uc0dd\uc0b0 \uc2dc\uc124 (a GMP-standard production "
                   "facility) - marketing copy, no certificate number and no issuing body."),
                ev("EV-G03", "oem_odm", True, "oem",
                   "OEM and ODM production for overseas brand owners."),
                ev("EV-G04", "product_categories", ["skincare", "sunscreen"], "products",
                   "Skincare and sunscreen production lines."),
            ],
            "notes": [],
        }
        if verified:
            # The only shape in which an absent certification can reject anything (E04):
            # an official source presented the list as exhaustive.
            record["certifications_verified"] = True
        return record

    problems = []
    document = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": "seller",
                "records": [_gmp_only_seller(False)]}
    path = _write_temp_json(document, "kbtm-gmponly-")
    try:
        code, out, err = run_script("score_seller.py", [
            "--input", path,
            "--query", os.path.join(FIXTURES, "query-seller-sunscreen-oem.json"),
            "--as-of", AS_OF])
        if code != 0:
            problems.append("exit %d: %s" % (code, err.strip()[:200]))
        else:
            records = json.loads(out).get("records", [])
            if len(records) != 1:
                problems.append("%d records returned, expected the record to be kept and scored"
                                % len(records))
            else:
                record = records[0]
                fired = []
                for info in (record.get("extensions") or {}).get(
                        "score_breakdown", {}).get("dimensions", {}).values():
                    fired.extend(info.get("signals_fired") or [])
                for signal in ("iso22716_or_cgmp_held", "other_quality_certification_held"):
                    if signal in fired:
                        problems.append("a bare GMP fired %s: the marketing token was read as a "
                                        "held certification" % signal)
                for detail in record.get("dimension_details") or []:
                    if detail.get("criterion_id") != "S-CP4":
                        continue
                    if float(detail.get("earned_points") or 0) != 0.0:
                        problems.append("S-CP4 earned %r points on a bare GMP"
                                        % detail.get("earned_points"))
    finally:
        os.unlink(path)

    # ... and it must not satisfy a query that REQUIRES the designation.
    document = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": "seller",
                "records": [_gmp_only_seller(True)]}
    with open(os.path.join(FIXTURES, "query-seller-sunscreen-oem.json"), encoding="utf-8") as fh:
        query = dict(json.load(fh))
    query["required_certifications"] = ["CGMP"]
    record_path = _write_temp_json(document, "kbtm-gmponly-")
    query_path = _write_temp_json(query, "kbtm-cgmpquery-")
    try:
        code, out, err = run_script("score_seller.py", [
            "--input", record_path, "--query", query_path, "--as-of", AS_OF])
        if code != 0:
            problems.append("exit %d: %s" % (code, err.strip()[:200]))
        else:
            envelope = json.loads(out)
            if envelope.get("records"):
                problems.append("the record passed a query requiring CGMP while holding only a "
                                "bare GMP: HF-04's superset test was satisfied by a promoted token")
            excluded = envelope.get("excluded") or []
            rules = [r.get("rule_id") for entry in excluded
                     for r in (entry.get("failed_rules") or [])]
            if rules[:1] != ["HF-04"]:
                problems.append("failed_rules = %r, expected HF-04" % (rules,))
            observed = [r.get("observed_value") for entry in excluded
                        for r in (entry.get("failed_rules") or [])]
            if any("CGMP" in (value or []) for value in observed if isinstance(value, list)):
                problems.append("the held list reported as %r contains CGMP" % (observed,))
    finally:
        os.unlink(record_path)
        os.unlink(query_path)
    report.check("field regression: FR-03b a record whose only certification text is a bare "
                 "GMP never holds CGMP", not problems, "\n".join(problems[:10]))

    # -- FR-04 ---------------------------------------------------------------
    # Live buyer trial (FD-35): a UAE trading company with korean_products_signal
    # and product_categories BOTH unknown scored 70, qualified, and ranked above
    # seven companies with evidenced named Korean portfolios.
    document = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": "buyer",
                "records": [_zero_kbeauty_buyer()]}
    path = _write_temp_json(document, "kbtm-nokbeauty-")
    try:
        code, out, err = run_script("score_buyer.py", [
            "--input", path, "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"),
            "--as-of", AS_OF])
        problems = []
        if code != 0:
            problems.append("exit %d: %s" % (code, err.strip()[:200]))
        else:
            envelope = json.loads(out)
            records = envelope.get("records", [])
            if len(records) != 1:
                problems.append("%d records returned, expected 1 - the record must be "
                                "KEPT and scored, never rejected" % len(records))
            else:
                record = records[0]
                if record.get("qualified") is not False:
                    problems.append("qualified = %r, expected false (score was %r)"
                                    % (record.get("qualified"), record.get("qualification_score")))
                notes = " ".join(n for n in (record.get("notes") or []) if isinstance(n, str))
                if "vertical fit gate" not in notes:
                    problems.append("no note explains why a %r score is not a lead"
                                    % record.get("qualification_score"))
                fired = []
                for info in (record.get("extensions") or {}).get(
                        "score_breakdown", {}).get("dimensions", {}).values():
                    fired.extend(info.get("signals_fired") or [])
                for signal in ("korean_products_signal_true", "korean_brands_named_3_plus",
                               "korean_category_page", "kbeauty_specialist_positioning"):
                    if signal in fired:
                        problems.append("the fixture is not a zero-K-Beauty record: %s fired"
                                        % signal)
            if envelope.get("summary", {}).get("qualified_count") != 0:
                problems.append("summary.qualified_count = %r, expected 0"
                                % envelope.get("summary", {}).get("qualified_count"))
        report.check("field regression: FR-04 a candidate with zero K-Beauty evidence "
                     "is kept and scored but never qualifies", not problems,
                     "\n".join(problems[:10]))
    finally:
        os.unlink(path)

    # The gate is a K-Beauty-run rule, not a global one: with no vertical on the
    # query the same record must qualify normally, or the gate has become a
    # silent hard reject.
    document = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": "buyer",
                "records": [_zero_kbeauty_buyer()]}
    record_path = _write_temp_json(document, "kbtm-nokbeauty-")
    query_path = _write_temp_json({"country": "AE", "country_name": "United Arab Emirates"},
                                  "kbtm-noveritcal-")
    try:
        code, out, err = run_script("score_buyer.py", [
            "--input", record_path, "--query", query_path, "--as-of", AS_OF])
        problems = []
        if code != 0:
            problems.append("exit %d: %s" % (code, err.strip()[:200]))
        else:
            records = json.loads(out).get("records", [])
            if len(records) != 1 or records[0].get("qualified") is not True:
                problems.append("qualified = %r on a query with no vertical; the gate must "
                                "not fire outside a K-Beauty run"
                                % (records[0].get("qualified") if records else None))
        report.check("field regression: FR-04b the vertical gate fires only on a "
                     "K-Beauty query", not problems, "\n".join(problems[:10]))
    finally:
        os.unlink(record_path)
        os.unlink(query_path)

    # FR-04c: the gate must not be dodgeable by SPELLING the dimension unknown. Absence
    # and the literal "unknown" are one state (BUILD-CONTRACT 3.2), so a record that
    # declares korean_products_signal "unknown" has to land exactly where the record that
    # never mentions Korea lands - kept, scored, above the threshold, and NOT a qualified
    # lead. If declaring it scored higher than saying nothing, the cheapest way past a
    # K-Beauty run would be to write "unknown" in the field, and "knowing less scores
    # more" - the shape the live buyer trial found - would be back under a new spelling.
    def _score_zero_kbeauty(record, label):
        document = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": "buyer",
                    "records": [record]}
        path = _write_temp_json(document, "kbtm-gate-")
        try:
            code, out, err = run_script("score_buyer.py", [
                "--input", path,
                "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"),
                "--as-of", AS_OF])
        finally:
            os.unlink(path)
        if code != 0:
            return None, ["%s: exit %d: %s" % (label, code, err.strip()[:200])]
        return json.loads(out), []

    declared = _zero_kbeauty_buyer()
    declared["korean_products_signal"] = _common.UNKNOWN
    problems, scored = [], {}
    for label, record in (("absent", _zero_kbeauty_buyer()), ("declared-unknown", declared)):
        envelope, errors = _score_zero_kbeauty(record, label)
        problems.extend(errors)
        if envelope is None:
            continue
        summary = envelope.get("summary") or {}
        records = envelope.get("records") or []
        if len(records) != 1:
            problems.append("%s: %d records, expected the record to be kept and scored"
                            % (label, len(records)))
            continue
        scored[label] = records[0]
        score = records[0].get("qualification_score")
        threshold = summary.get("threshold_used")
        if not isinstance(score, int) or not isinstance(threshold, int):
            problems.append("%s: score %r / threshold %r are not both integers"
                            % (label, score, threshold))
        elif score < threshold:
            problems.append("%s: the record scores %d against a threshold of %d, so it would "
                            "fall out on the score alone and this case tests nothing"
                            % (label, score, threshold))
        if records[0].get("qualified") is not False:
            problems.append("%s: qualified = %r at score %r"
                            % (label, records[0].get("qualified"), score))
        if summary.get("qualified_count") != 0:
            problems.append("%s: summary.qualified_count = %r, expected 0"
                            % (label, summary.get("qualified_count")))
    if len(scored) == 2:
        for field in ("qualification_score",):
            absent_value = scored["absent"].get(field)
            declared_value = scored["declared-unknown"].get(field)
            if isinstance(absent_value, int) and isinstance(declared_value, int) \
                    and declared_value > absent_value:
                problems.append("declaring the dimension unknown scored %s %d against %d for "
                                "saying nothing at all: knowing less scores more"
                                % (field, declared_value, absent_value))
        absent_fit = (scored["absent"].get("dimension_scores") or {}).get("kbeauty_fit")
        declared_fit = (scored["declared-unknown"].get("dimension_scores") or {}).get("kbeauty_fit")
        if isinstance(absent_fit, int) and isinstance(declared_fit, int) \
                and declared_fit > absent_fit:
            problems.append("kbeauty_fit is %d when the dimension is declared unknown against %d "
                            "when it is simply absent; the two are one state"
                            % (declared_fit, absent_fit))
    report.check("field regression: FR-04c declaring the K-Beauty dimension unknown is not a "
                 "way past the vertical gate", not problems, "\n".join(problems[:10]))


    # -- FR-05 .. FR-08 --------------------------------------------------------
    # Live claude.ai run (2026-09-14): five UAE buyers rendered "Missing: none" although
    # three published no minimum order and one gated its catalogue behind a login.
    # missing[] carried only penalty labels, and neither fact takes the unknown path.
    config = read_json(os.path.join(PKG_ROOT, "schemas", "scoring.config.json"))
    moq_label = "Published minimum order"
    problems = []
    code, out, _err = run_script("score_buyer.py", [
        "--input", os.path.join(FIXTURES, "buyers.golden.json"),
        "--query", os.path.join(FIXTURES, "query-buyer-uk-sunscreen.json"),
        "--as-of", AS_OF])
    try:
        uk = json.loads(out) if code == 0 else {}
    except ValueError:
        uk = {}
    by_id = dict((r.get("buyer_id"), r) for r in uk.get("records", []))
    if not by_id:
        problems.append("score_buyer.py over the UK sunscreen query: exit %d, no records" % code)
    covered_seen = False
    for rid, record in sorted(by_id.items()):
        missing = record.get("missing") or []
        penalties = record.get("unknown_penalty_applied") or []
        penalty_labels = []
        for entry in penalties:
            if entry.get("label") not in penalty_labels:
                penalty_labels.append(entry.get("label"))
        if missing[:len(penalty_labels)] != penalty_labels:
            problems.append("%s: missing[] does not start with the penalty labels" % rid)
        if any(entry.get("label") == moq_label for entry in penalties):
            problems.append("%s: a verification gap emitted a penalty entry" % rid)
        covered = any("buyer.buyer_moq" in (entry.get("unknown_inputs") or [])
                      for entry in penalties)
        covered_seen = covered_seen or covered
        published = record.get("buyer_moq") not in (None, "unknown")
        want = not covered and not published
        if (moq_label in missing) != want:
            problems.append("%s: %r in missing[] is %s, expected %s"
                            % (rid, moq_label, moq_label in missing, want))
        if missing.count(moq_label) > 1:
            problems.append("%s: %r is listed twice" % (rid, moq_label))
    if by_id and not covered_seen:
        problems.append("no golden record exercises the penalty-covered path any more")
    report.check("field regression: FR-05 an unpublished minimum order reaches missing[] once, "
                 "after the penalty labels, and never as a penalty", not problems,
                 "\n".join(problems[:10]))

    problems = []
    requested = [_common.normalize_category("sunscreen")]
    shown = ", ".join(slug.replace("_", " ") for slug in requested)
    for rid, record in sorted(by_id.items()):
        cats = record.get("product_categories")
        lines = [m for m in (record.get("missing") or []) if m.startswith("Requested category")]
        expected_lines = []
        if isinstance(cats, list):
            tokens = [tok for tok in (_common.normalize_category(c) for c in cats
                                      if isinstance(c, str)) if tok]
            if tokens or not cats:
                relation = _common.category_relation(tokens, requested)
                if relation == "parent":
                    expected_lines = ["Requested category confirmed only at a broader level: %s" % shown]
                elif relation != "exact":
                    expected_lines = ["Requested category not evidenced: %s" % shown]
        if lines != expected_lines:
            problems.append("%s: requested-category lines %r, expected %r"
                            % (rid, lines, expected_lines))
    pairs = sorted((child, parent) for child, parent in _common.CATEGORY_PARENTS.items()
                   if isinstance(child, str) and isinstance(parent, str))
    if pairs:
        child, parent = pairs[0]
        got = _common.verification_gaps(config, "buyer", {"buyer_moq": 12}, [], [parent], [child])
        want = ["Requested category confirmed only at a broader level: %s"
                % child.replace("_", " ")]
        if got != want:
            problems.append("parent-level match %s -> %s: got %r, expected %r"
                            % (parent, child, got, want))
    else:
        problems.append("_common.CATEGORY_PARENTS is empty; the parent path is untested")
    report.check("field regression: FR-06 a requested category matched only broadly, or not at "
                 "all, reaches missing[] with the requested slug", not problems,
                 "\n".join(problems[:10]))

    problems = []
    seller_cases = (
        ({}, [], ["Minimum order quantity"]),
        ({"moq": "unknown"}, [], ["Minimum order quantity"]),
        ({"moq": 3000}, [], []),
        ({}, [{"label": "MOQ against the buyer ceiling", "unknown_inputs": ["seller.moq"]}], []),
    )
    for record, penalties, want in seller_cases:
        got = _common.verification_gaps(config, "seller", record, penalties,
                                        ["sunscreen"], ["sunscreen"])
        if got != want:
            problems.append("seller %r, penalty inputs %r: got %r, expected %r"
                            % (record, [e["unknown_inputs"] for e in penalties], got, want))
    report.check("field regression: FR-07 an unpublished seller MOQ reaches missing[] unless a "
                 "penalty already names it", not problems, "\n".join(problems[:10]))

    problems = []
    with open(os.path.join(PKG_ROOT, "references", "output-format.md"), encoding="utf-8") as fh:
        render_doc = fh.read()
    for label in ("Summary", "Query", "Candidates found", "Qualified", "As of", "Score version",
                  "Top Candidates", "Website", "Country", "Type", "Why", "MOQ",
                  "Certifications", "Export markets", "Contact", "Evidence", "Missing",
                  "Excluded / low fit", "Recommended next action", "Destination", "Product",
                  "Commercial model", "Required", "Threshold", "Matches", "Product Fit",
                  "Model Fit", "Compliance", "Market Fit", "Evidence Quality", "Risks",
                  "Excluded", "No qualified match"):
        if "`%s`→`" % label not in render_doc:
            problems.append("no fixed Korean label for %r" % label)
    if "never a mix" not in render_doc:
        problems.append("the rule against mixing Korean and English labels is missing")
    report.check("field regression: FR-08 every rendered label has a fixed Korean mapping and "
                 "the map forbids mixing languages", not problems, "\n".join(problems[:10]))


# ---------------------------------------------------------------------------
# 4c. calibration tooling - the labelled-set loop
#
# make_review_sheet.py turns a scored run into a blind CSV an operator fills in;
# acceptance_report.py joins the filled sheets back and measures PRD 17's Human
# Acceptance Rate against the score the rubric gave. Neither script can change a
# score, so nothing here touches score_version. The protocol these cases enforce is
# references/calibration-notes.md section 7.
# ---------------------------------------------------------------------------
CAL_SCRIPTS = ["make_review_sheet.py", "acceptance_report.py"]
REVIEWS_BUYERS = os.path.join(FIXTURES, "reviews.buyers.uae.csv")
REVIEWS_MATCH = os.path.join(FIXTURES, "reviews.match-134.csv")


def _read_text(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_temp_text(text, prefix, suffix=".csv"):
    handle, path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    with os.fdopen(handle, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def _rewrite_review(text, record_id, changes):
    """Return `text` with one row's columns replaced; for the refusal cases."""
    reader = csv.DictReader(io.StringIO(text))
    columns = list(reader.fieldnames)
    rows = list(reader)
    for row in rows:
        if row["record_id"] == record_id:
            row.update(changes)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def _refusal(report, name, args, expect, label):
    """One refusal path: exit 1, exactly one ERROR: line, and it says why."""
    code, out, err = run_script(name, args)
    error_lines = [ln for ln in err.splitlines() if ln.startswith("ERROR:")]
    problems = []
    if code != 1:
        problems.append("exit %d, expected 1" % code)
    if len(error_lines) != 1:
        problems.append("%d 'ERROR:' lines, R7.3.3 requires exactly 1" % len(error_lines))
    if "Traceback (most recent call last)" in err:
        problems.append("raw traceback escaped main()")
    if not any(expect in ln for ln in error_lines):
        problems.append("no ERROR line carries %r; got %s" % (expect, error_lines[:1]))
    report.check("calibration: refuses %s" % label, not problems, "\n".join(problems))


def _sheet_shape(value):
    """How a review-sheet cell READS: filled, or one of the three stand-ins."""
    if value in ("", None):
        return "empty"
    if value == "unknown":
        return "unknown"
    if value == "not_shown":
        return "not_shown"
    return "filled"


def _calibration_blind_partition(report, buyers_out, match_path, match_out, temp):
    """H1: no single column may tell an excluded row from a returned one.

    `discovery-result.excluded[]` has no `country` field, so before the fix the excluded
    rows were exactly the rows whose country cell read "unknown" - a blind sheet that
    told the reviewer which candidates the rubric had already thrown away. The assertion
    is generic rather than country-specific: for EVERY column, the set of cell shapes on
    the excluded rows must overlap the set on the returned rows.
    """
    for label, scored_text, scored_path in (
            ("buyer", buyers_out, None), ("match", match_out, match_path)):
        document = json.loads(scored_text)
        if scored_path is None:
            scored_path = _write_temp_text(scored_text, "kbtm-cal-part-", ".json")
            temp.append(scored_path)
        if "results" in document:
            returned_ids = set(r["seller_id"] for r in document["results"])
            excluded_ids = set(r["seller_id"] for r in document["excluded"])
        else:
            returned_ids = set(r["buyer_id"] for r in document["records"])
            excluded_ids = set(r["id"] for r in document["excluded"])
        code, sheet, err = run_script("make_review_sheet.py", [
            "--input", scored_path, "--as-of", AS_OF, "--include-excluded"])
        if code != 0:
            report.fail("calibration: H1 blind sheet (%s) builds" % label, err.strip()[:300])
            continue
        rows = _csv_rows(sheet)
        columns = sheet.splitlines()[0].split(",")
        partitioning = []
        for column in columns:
            shapes_returned = set(_sheet_shape(r[column]) for r in rows
                                  if r["record_id"] in returned_ids)
            shapes_excluded = set(_sheet_shape(r[column]) for r in rows
                                  if r["record_id"] in excluded_ids)
            if not shapes_returned or not shapes_excluded:
                continue
            if not (shapes_returned & shapes_excluded):
                partitioning.append("%s: returned=%s excluded=%s"
                                    % (column, sorted(shapes_returned),
                                       sorted(shapes_excluded)))
        report.check("calibration: H1 no column of the blind %s sheet separates excluded "
                     "from returned rows" % label, not partitioning,
                     "\n".join(partitioning))
        if label == "buyer":
            report.check("calibration: H1 the discovery country column is neutralised "
                         "for every row",
                         all(r["country"] == "not_shown" for r in rows),
                         "country values: %s"
                         % sorted(set(r["country"] for r in rows))[:5])
            report.check("calibration: H1 the neutralisation is announced on stderr",
                         "not_shown" in err and "country" in err, err.strip()[:200])
    # A sheet with no excluded rows keeps its real country column: there is no second
    # population to separate, and blanking it would lose information for nothing.
    plain_path = _write_temp_text(buyers_out, "kbtm-cal-plain-", ".json")
    temp.append(plain_path)
    code, sheet, _err = run_script("make_review_sheet.py", [
        "--input", plain_path, "--as-of", AS_OF])
    countries = set(r["country"] for r in _csv_rows(sheet))
    report.check("calibration: H1 without --include-excluded the country column is real",
                 code == 0 and "not_shown" not in countries and "AE" in countries,
                 "countries: %s" % sorted(countries)[:6])


def _calibration_formula_injection(report, buyers_out, temp):
    """H3: a harvested company name may not become a spreadsheet formula."""
    document = json.loads(buyers_out)
    document["records"][0]["company_name"] = "=cmd|'/c calc'!A1"
    document["records"][1]["company_name"] = "@SUM(1+1)*cmd"
    path = _write_temp_json(document, "kbtm-cal-formula-")
    temp.append(path)
    code, sheet, err = run_script("make_review_sheet.py", [
        "--input", path, "--as-of", AS_OF])
    rows = index_by(_csv_rows(sheet), "record_id")
    problems = []
    if code != 0:
        problems.append("exit %d: %s" % (code, err.strip()[:200]))
    for record, want in ((document["records"][0], "'=cmd|'/c calc'!A1"),
                         (document["records"][1], "'@SUM(1+1)*cmd")):
        got = rows.get(record["buyer_id"], {}).get("company_name")
        if got != want:
            problems.append("%s: company_name is %r, expected %r"
                            % (record["buyer_id"], got, want))
    report.check("calibration: H3 a formula-leading company name is escaped, not executed",
                 not problems, "\n".join(problems))

    # The join key is never escaped, so an id that would need it is refused outright.
    document = json.loads(buyers_out)
    document["records"][0]["buyer_id"] = "=BUY-evil-example"
    path = _write_temp_json(document, "kbtm-cal-formula-id-")
    temp.append(path)
    _refusal(report, "make_review_sheet.py", ["--input", path, "--as-of", AS_OF],
             "starts with a spreadsheet formula character",
             "a record_id that would need a formula guard")


def _calibration_detector_edges(report, buyers_path, base, temp):
    """M5: the personal-data scan must pass the notes the protocol asks operators for."""
    allowed = [
        "CDSCO registration certificate 1234567890 issued 2024",
        "BPOM notification NA18200100123",
        "active 2019 - 2024, ISO 22716 cert 9001-2015",
        "registry https://cdsco.example/rc/1234567890123",
        "annual turnover 15 000 000 KRW over 2020/2021/2022",
    ]
    text = base
    for index, note in enumerate(allowed):
        text = _rewrite_review(
            text, sorted(index_by(_csv_rows(base), "record_id"))[index], {"note": note})
    path = _write_temp_text(text, "kbtm-cal-allowed-")
    temp.append(path)
    code, out, err = run_script("acceptance_report.py", [
        "--scored", buyers_path, "--reviews", path, "--as-of", AS_OF])
    report.check("calibration: M5 registration numbers, certificate numbers, year ranges "
                 "and registry URLs are not read as personal data",
                 code == 0 and bool(out.strip()),
                 "exit %d\n%s" % (code, err.strip()[:400]))

    for label, changes, expect in (
            ("an obfuscated email in a note",
             {"note": "ask their buyer, name [at] gulfglow.example"},
             "contains an email address"),
            ("a full-width phone number in a note",
             {"note": "ＴＥＬ ０１０１２３４５６７８"},
             "contains a phone-number-like string"),
            ("a phone number glued to a URL in a note",
             {"note": "see www.gulfglow.example/010-1234-5678"},
             "contains a phone-number-like string")):
        path = _write_temp_text(
            _rewrite_review(base, "BUY-gulfglow-example", changes), "kbtm-cal-m5-")
        temp.append(path)
        _refusal(report, "acceptance_report.py",
                 ["--scored", buyers_path, "--reviews", path, "--as-of", AS_OF],
                 expect, label)


def _calibration_report_edges(report, buyers_path, buyers_out, match_path, base, temp):
    """M4 / M6 / M7 / L10 / L15: the report's own edges."""
    code, match_report, err = run_script("acceptance_report.py", [
        "--scored", match_path, "--reviews", REVIEWS_MATCH, "--as-of", AS_OF])
    if code != 0:
        report.fail("calibration: M4 the match report builds", err.strip()[:300])
        return
    golden = json.loads(match_report)
    sweep = index_by(golden["threshold_sweep"], "threshold")
    report.check("calibration: M4 recall counts accepted-but-EXCLUDED records in its "
                 "denominator", sweep[50]["recall"] == 0.8 and sweep[85]["recall"] == 0.6,
                 "recall@50=%s recall@85=%s (5 accepted records, 4 of them returned)"
                 % (sweep[50]["recall"], sweep[85]["recall"]))
    report.check("calibration: M4 a match report says its sub-threshold sweep rows are "
                 "unmeasurable",
                 any("threshold_sweep rows below 70" in n for n in golden["notes"]),
                 "notes[] carries no such line")
    _bcode, buyer_report, _berr = run_script("acceptance_report.py", [
        "--scored", buyers_path, "--reviews", REVIEWS_BUYERS, "--as-of", AS_OF])
    buyer_golden = json.loads(buyer_report)
    report.check("calibration: M4 a discovery report does NOT, because it returns its "
                 "below-threshold records",
                 not any("threshold_sweep rows below" in n for n in buyer_golden["notes"]),
                 "a discovery report claimed its sweep was unmeasurable")

    # M6: qualified absent is not qualified false.
    document = json.loads(buyers_out)
    for record in document["records"]:
        if record["buyer_id"] == "BUY-gulfglow-example":
            del record["qualified"]
    path = _write_temp_json(document, "kbtm-cal-qunknown-")
    temp.append(path)
    code, out, err = run_script("acceptance_report.py", [
        "--scored", path, "--reviews", REVIEWS_BUYERS, "--as-of", AS_OF])
    parsed = json.loads(out) if code == 0 else {}
    buckets = parsed.get("by_qualified", {})
    report.check("calibration: M6 a record with no qualified flag lands in "
                 "qualified_unknown, never qualified_false",
                 code == 0
                 and buckets.get("qualified_unknown", {}).get("n") == 1
                 and buckets.get("qualified_unknown", {}).get("accept") == 1
                 and buckets.get("qualified_true", {}).get("n") == 6
                 and buckets.get("qualified_false", {}).get("n") == 12,
                 "exit %d buckets=%s\n%s"
                 % (code, dict((k, v.get("n")) for k, v in buckets.items()),
                    err.strip()[:300]))

    # M7: a spreadsheet's trailing blank line is a blank line, not a broken sheet.
    padded = base + ",,,,,,,,,\n"
    path = _write_temp_text(padded, "kbtm-cal-blankrow-")
    temp.append(path)
    code, out, err = run_script("acceptance_report.py", [
        "--scored", buyers_path, "--reviews", path, "--as-of", AS_OF])
    _base_code, baseline, _base_err = run_script("acceptance_report.py", [
        "--scored", buyers_path, "--reviews", REVIEWS_BUYERS, "--as-of", AS_OF])
    report.check("calibration: M7 a trailing all-empty CSV row is skipped, not fatal",
                 code == 0 and out == baseline,
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # L10: a report that fails its own schema is not written anywhere.
    bad_dir = tempfile.mkdtemp(prefix="kbtm-cal-badschema-")
    schema_path = os.path.join(bad_dir, "acceptance-report.schema.json")
    with open(schema_path, "w", encoding="utf-8") as fh:
        json.dump({"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "type": "object", "required": ["a_field_no_report_has"]}, fh)
    out_path = os.path.join(bad_dir, "report.json")
    code, out, err = run_script("acceptance_report.py", [
        "--scored", buyers_path, "--reviews", REVIEWS_BUYERS, "--as-of", AS_OF,
        "--schema-dir", bad_dir, "--output", out_path])
    problems = []
    if code != 1:
        problems.append("exit %d, expected 1" % code)
    if out.strip():
        problems.append("stdout is not empty")
    if os.path.exists(out_path):
        problems.append("--output was written anyway")
    if "nothing was written" not in err:
        problems.append("stderr does not say nothing was written")
    report.check("calibration: L10 a report that fails its schema is not written at all",
                 not problems, "\n".join(problems))
    try:
        os.unlink(schema_path)
        os.rmdir(bad_dir)
    except OSError:
        pass

    # L15: an all-accept sheet leaves the reject class empty, so every AUC is null.
    rows = _csv_rows(base)
    columns = base.splitlines()[0].split(",")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        if row["verdict"]:
            row["verdict"], row["reason_code"] = "accept", ""
        writer.writerow(row)
    path = _write_temp_text(buffer.getvalue(), "kbtm-cal-allaccept-")
    temp.append(path)
    code, out, err = run_script("acceptance_report.py", [
        "--scored", buyers_path, "--reviews", path, "--as-of", AS_OF])
    parsed = json.loads(out) if code == 0 else {}
    discrimination = parsed.get("discrimination", {})
    overall = discrimination.get("overall", {})
    dimensions = discrimination.get("by_dimension", [])
    report.check("calibration: L15 an empty reject class yields a null AUC with a stated "
                 "reason, and distinct_values is still reported",
                 code == 0
                 and overall.get("auc") is None
                 and "no rejected record" in (overall.get("reason") or "")
                 and overall.get("distinct_values") == 17
                 and bool(dimensions)
                 and all(d["auc"] is None and d["reason"] for d in dimensions)
                 and any(d["distinct_values"] == 2 for d in dimensions),
                 "exit %d overall=%s\n%s" % (code, overall, err.strip()[:300]))


def _calibration_no_match(report, temp):
    """L15: a no_match match run returns nothing, so the report measures exclusions only."""
    code, out, err = run_script("score_match.py", [
        "--input", os.path.join(FIXTURES, "match-no-match.input.json"),
        "--as-of", AS_OF, "--pretty"])
    if code != 0:
        report.fail("calibration: L15 the no-match run is scoreable", err.strip()[:300])
        return
    scored_path = _write_temp_text(out, "kbtm-cal-nomatch-", ".json")
    temp.append(scored_path)
    document = json.loads(out)
    report.check("calibration: L15 the no-match fixture really returns nothing",
                 document["results"] == [] and bool(document["excluded"]),
                 "results=%d excluded=%d"
                 % (len(document["results"]), len(document["excluded"])))

    code, sheet, err = run_script("make_review_sheet.py", [
        "--input", scored_path, "--as-of", AS_OF, "--include-excluded"])
    report.check("calibration: L15 a no-match run still yields a review sheet of its "
                 "exclusions", code == 0 and len(_csv_rows(sheet)) == len(document["excluded"]),
                 "exit %d, %d rows" % (code, len(_csv_rows(sheet or ""))))
    rows = _csv_rows(sheet)
    first = rows[0]["record_id"]
    filled = _rewrite_review(sheet, first, {
        "verdict": "accept", "reason_code": "", "note": "",
        "reviewer_role": "trade operator", "reviewed_on": AS_OF})
    reviews_path = _write_temp_text(filled, "kbtm-cal-nomatch-rev-")
    temp.append(reviews_path)
    code, out, err = run_script("acceptance_report.py", [
        "--scored", scored_path, "--reviews", reviews_path, "--as-of", AS_OF])
    parsed = json.loads(out) if code == 0 else {}
    summary = parsed.get("summary", {})
    report.check("calibration: L15 a no-match report succeeds, reports null rates and "
                 "measures false exclusions only",
                 code == 0
                 and summary.get("returned") == 0
                 and summary.get("human_acceptance_rate") is None
                 and summary.get("review_coverage") is None
                 and len(parsed.get("false_exclusions") or []) == 1
                 and any("measures false exclusions only" in n
                         for n in parsed.get("notes") or []),
                 "exit %d summary=%s\n%s" % (code, summary, err.strip()[:300]))

    # A document with no record at all, returned or excluded, has nothing to measure.
    empty = json.loads(_read_text(scored_path))
    empty["excluded"] = []
    empty_path = _write_temp_json(empty, "kbtm-cal-empty-")
    temp.append(empty_path)
    _refusal(report, "acceptance_report.py",
             ["--scored", empty_path, "--reviews", reviews_path, "--as-of", AS_OF],
             "carry no record at all", "a scored document with no record at all")


def phase_calibration(report, allow_missing):
    absent = [n for n in CAL_SCRIPTS if not os.path.isfile(os.path.join(SCRIPT_DIR, n))]
    if absent or missing_scripts():
        note = "scripts absent: %s" % ", ".join(absent or missing_scripts())
        if allow_missing:
            report.skip("calibration: tooling cases", note)
            return
        report.fail("calibration: tooling cases", note)
        return

    temp = []
    try:
        # --- the two scored runs the sheets were cut from --------------------------
        code, buyers_out, err = run_script("score_buyer.py", [
            "--input", os.path.join(FIXTURES, "buyers.golden.json"),
            "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"),
            "--as-of", AS_OF, "--pretty"])
        if code != 0:
            report.fail("calibration: the UAE buyer run is scoreable", err.strip()[:400])
            return
        code, match_out, err = run_script("score_match.py", [
            "--input", os.path.join(FIXTURES, "match-134.input.json"),
            "--as-of", AS_OF, "--pretty"])
        if code != 0:
            report.fail("calibration: the match-134 run is scoreable", err.strip()[:400])
            return
        buyers_path = _write_temp_text(buyers_out, "kbtm-cal-buyers-", ".json")
        match_path = _write_temp_text(match_out, "kbtm-cal-match-", ".json")
        temp.extend([buyers_path, match_path])

        # --- make_review_sheet.py golden output ------------------------------------
        code, blind, err = run_script("make_review_sheet.py", [
            "--input", buyers_path, "--as-of", AS_OF, "--include-excluded"])
        report.check("calibration: make_review_sheet.py (buyer run, blind) exits 0", code == 0,
                     "exit %d\n%s" % (code, err.strip()[:400]))
        golden_blind = _read_text(os.path.join(
            EXPECTED, "review-sheet.buyers.uae.blind.csv"))
        report.check("calibration: the blind buyer sheet matches its golden byte for byte",
                     blind == golden_blind, "generated sheet differs from the expected fixture")

        code, ranked, err = run_script("make_review_sheet.py", [
            "--input", match_path, "--as-of", AS_OF, "--no-blind"])
        report.check("calibration: make_review_sheet.py (match run, --no-blind) exits 0",
                     code == 0, "exit %d\n%s" % (code, err.strip()[:400]))
        golden_ranked = _read_text(os.path.join(
            EXPECTED, "review-sheet.match-134.ranked.csv"))
        report.check("calibration: the ranked match sheet matches its golden byte for byte",
                     ranked == golden_ranked, "generated sheet differs from the expected fixture")

        # --- the blind sheet leaks neither the score nor the ranking ---------------
        header = blind.splitlines()[0].split(",")
        leaked = [c for c in ("rank", "score", "qualified") if c in header]
        report.check("calibration: a blind sheet carries no rank/score/qualified column",
                     not leaked, "leaked columns: %s" % leaked)
        scored_doc = json.loads(buyers_out)
        scores = set(str(r["qualification_score"]) for r in scored_doc["records"])
        cells = set()
        for row in _csv_rows(blind):
            cells.update(v for v in row.values() if v)
        report.check("calibration: no blind sheet cell carries a qualification score",
                     not (scores & cells), "score values present: %s" % sorted(scores & cells))

        rank_order = [r["buyer_id"] for r in scored_doc["records"]]
        blind_order = [r["record_id"] for r in _csv_rows(blind)]
        report.check("calibration: blind row order is independent of rank",
                     blind_order[:len(rank_order)] != rank_order,
                     "the blind sheet reproduced the ranked order, so it anchors the reviewer")
        expected_order = sorted(
            blind_order,
            key=lambda rid: (hashlib.sha256(rid.encode("utf-8")).hexdigest(), rid))
        report.check("calibration: blind row order is the sha256 order of the record ids",
                     blind_order == expected_order,
                     "expected %s\n     got %s" % (expected_order[:4], blind_order[:4]))

        # --- --include-excluded ----------------------------------------------------
        excluded_ids = set(e["id"] for e in scored_doc["excluded"])
        code, without, _err = run_script("make_review_sheet.py", [
            "--input", buyers_path, "--as-of", AS_OF])
        ids_without = set(r["record_id"] for r in _csv_rows(without))
        ids_with = set(blind_order)
        report.check("calibration: --include-excluded lists excluded[] and the default does not",
                     bool(excluded_ids) and not (excluded_ids & ids_without)
                     and excluded_ids <= ids_with,
                     "excluded=%s with=%s without=%s"
                     % (sorted(excluded_ids), len(ids_with), len(ids_without)))

        # --- INV-13 determinism ------------------------------------------------------
        code2, blind2, _err = run_script("make_review_sheet.py", [
            "--input", buyers_path, "--as-of", AS_OF, "--include-excluded"])
        report.check("calibration: INV-13 make_review_sheet.py is byte-identical on a re-run",
                     code2 == 0 and blind2 == blind, "second run differed")

        # --- acceptance_report.py golden output --------------------------------------
        for label, scored_path, reviews_path, expected_name in (
                ("buyer", buyers_path, REVIEWS_BUYERS,
                 "acceptance.buyers.uae.expected.json"),
                ("match", match_path, REVIEWS_MATCH,
                 "acceptance.match-134.expected.json")):
            code, out, err = run_script("acceptance_report.py", [
                "--scored", scored_path, "--reviews", reviews_path,
                "--as-of", AS_OF, "--pretty"])
            report.check("calibration: acceptance_report.py (%s) exits 0" % label, code == 0,
                         "exit %d\n%s" % (code, err.strip()[:600]))
            expected_text = _read_text(os.path.join(EXPECTED, expected_name))
            report.check("calibration: the %s acceptance report matches its golden byte for byte"
                         % label, out == expected_text,
                         "generated report differs from %s" % expected_name)
            code2, out2, _err = run_script("acceptance_report.py", [
                "--scored", scored_path, "--reviews", reviews_path,
                "--as-of", AS_OF, "--pretty"])
            report.check("calibration: INV-13 acceptance_report.py (%s) is byte-identical on a "
                         "re-run" % label, code2 == code and out2 == out, "second run differed")
            vcode, _vout, verr = run_script("validate_output.py", [
                "--input", os.path.join(EXPECTED, expected_name),
                "--schema", "acceptance-report", "--invariants", "--strict"])
            report.check("calibration: the %s acceptance report validates --strict" % label,
                         vcode == 0, verr.strip()[:400])

        # --- the report states plainly what it does and does not measure --------------
        report_doc = json.loads(_read_text(os.path.join(
            EXPECTED, "acceptance.buyers.uae.expected.json")))
        notes_blob = " ".join(report_doc["notes"])
        report.check("calibration: the report says it measures Human Acceptance Rate only",
                     "Human Acceptance Rate" in notes_blob and "RFQ Conversion" in notes_blob,
                     "notes[] does not state the limit of the measurement")

        # --- insufficient sample -------------------------------------------------------
        summary = report_doc["summary"]
        problems = []
        if summary["insufficient_sample"] is not True:
            problems.append("insufficient_sample is not true at min_sample %s"
                            % summary["min_sample"])
        if not any("insufficient sample" in n for n in report_doc["notes"]):
            problems.append("notes[] carries no insufficient-sample line")
        if not any("cannot" in n.lower() and "threshold" in n.lower()
                   for n in report_doc["notes"]):
            problems.append("the insufficient-sample note does not say a threshold change is "
                            "unjustified")
        report.check("calibration: a small sample is flagged and refuses to be evidence",
                     not problems, "\n".join(problems))
        code, out, _err = run_script("acceptance_report.py", [
            "--scored", buyers_path, "--reviews", REVIEWS_BUYERS,
            "--as-of", AS_OF, "--min-sample", "5"])
        relaxed = json.loads(out) if code == 0 else {}
        report.check("calibration: --min-sample lowers the bar and clears the flag",
                     code == 0 and relaxed.get("summary", {}).get(
                         "insufficient_sample") is False,
                     "exit %d, insufficient_sample=%s"
                     % (code, relaxed.get("summary", {}).get("insufficient_sample")))

        # --- refusal paths ---------------------------------------------------------------
        base = _read_text(REVIEWS_BUYERS)
        cases = [
            ("an unknown verdict", {"verdict": "maybe"}, "is not one of"),
            ("an unknown reason_code", {"verdict": "reject", "reason_code": "vibes"},
             "reason_code 'vibes' is not one of"),
            ("a reject with no reason_code", {"verdict": "reject", "reason_code": ""},
             "requires a reason_code"),
            ("a malformed reviewed_on", {"reviewed_on": "12/09/2026"},
             "is not a YYYY-MM-DD date"),
            ("an impossible reviewed_on", {"reviewed_on": "2026-02-31"},
             "not a valid calendar date"),
            ("a reviewed_on later than as_of", {"reviewed_on": "2026-12-01"},
             "later than the report's as_of"),
            ("an email address in a note",
             {"note": "ask their buyer at sourcing.lead@gulfglow.example"},
             "contains an email address"),
            ("a phone number in reviewer_role",
             {"reviewer_role": "trade operator +82 10 1234 5678"},
             "contains a phone-number-like string"),
        ]
        for label, changes, expect in cases:
            path = _write_temp_text(
                _rewrite_review(base, "BUY-gulfglow-example", changes), "kbtm-cal-bad-")
            temp.append(path)
            _refusal(report, "acceptance_report.py",
                     ["--scored", buyers_path, "--reviews", path, "--as-of", AS_OF],
                     expect, label)

        # a second row for the same id with a different verdict
        rows = _csv_rows(base)
        columns = base.splitlines()[0].split(",")
        clash = io.StringIO()
        writer = csv.DictWriter(clash, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        extra = dict((c, "") for c in columns)
        extra.update({"record_id": "BUY-gulfglow-example", "entity_type": "buyer",
                      "verdict": "reject", "reason_code": "other",
                      "reviewer_role": "trade operator", "reviewed_on": AS_OF})
        writer.writerow(extra)
        path = _write_temp_text(clash.getvalue(), "kbtm-cal-dup-")
        temp.append(path)
        _refusal(report, "acceptance_report.py",
                 ["--scored", buyers_path, "--reviews", path, "--as-of", AS_OF],
                 "conflicting verdicts", "a duplicate record_id with conflicting verdicts")

        # a review row that joins to nothing
        orphan = _rewrite_review(base, "BUY-gulfglow-example",
                                 {"record_id": "BUY-ghost-example"})
        path = _write_temp_text(orphan, "kbtm-cal-orphan-")
        temp.append(path)
        _refusal(report, "acceptance_report.py",
                 ["--scored", buyers_path, "--reviews", path, "--as-of", AS_OF],
                 "matches no record in the scored document",
                 "a review row that matches no scored record")

        # two rubric versions in one report (the INV-23 principle)
        other = json.loads(buyers_out)
        other["score_version"] = "kbtm-score-0.9.9"
        other_path = _write_temp_json(other, "kbtm-cal-otherver-")
        temp.append(other_path)
        _refusal(report, "acceptance_report.py",
                 ["--scored", buyers_path, "--scored", other_path,
                  "--reviews", REVIEWS_BUYERS, "--as-of", AS_OF],
                 "refusing to mix score_version", "two score_versions in one report")

        # discovery and match populations in one report
        _refusal(report, "acceptance_report.py",
                 ["--scored", buyers_path, "--scored", match_path,
                  "--reviews", REVIEWS_BUYERS, "--as-of", AS_OF],
                 "refusing to mix", "a discovery and a match document in one report")

        # the same record reaching the report twice
        _refusal(report, "acceptance_report.py",
                 ["--scored", buyers_path, "--scored", buyers_path,
                  "--reviews", REVIEWS_BUYERS, "--as-of", AS_OF],
                 "appears in two --scored documents",
                 "one record_id carried by two --scored documents")

        # a duplicate row whose verdict agrees but whose reason_code does not
        rows = _csv_rows(base)
        columns = base.splitlines()[0].split(",")
        clash = io.StringIO()
        writer = csv.DictWriter(clash, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        extra = dict((c, "") for c in columns)
        extra.update({"record_id": "BUY-marinaretail-example", "entity_type": "buyer",
                      "verdict": "reject", "reason_code": "duplicate",
                      "reviewer_role": "trade operator", "reviewed_on": AS_OF})
        writer.writerow(extra)
        path = _write_temp_text(clash.getvalue(), "kbtm-cal-dupreason-")
        temp.append(path)
        _refusal(report, "acceptance_report.py",
                 ["--scored", buyers_path, "--reviews", path, "--as-of", AS_OF],
                 "conflicting reason_codes",
                 "a duplicate record_id with conflicting reason_codes")

        _calibration_blind_partition(report, buyers_out, match_path, match_out, temp)
        _calibration_formula_injection(report, buyers_out, temp)
        _calibration_detector_edges(report, buyers_path, base, temp)
        _calibration_report_edges(report, buyers_path, buyers_out, match_path, base, temp)
        _calibration_no_match(report, temp)
    finally:
        for path in temp:
            try:
                os.unlink(path)
            except OSError:
                pass


def phase_safety(report):
    hits = []
    for path in package_files():
        rel = os.path.relpath(path, PKG_ROOT)
        if rel == os.path.join("tests", "run_tests.py"):
            continue  # this file names the patterns in order to forbid them
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                low = line.lower()
                for pattern in SEND_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        if any(marker in low for marker in NEGATION_MARKERS):
                            continue
                        hits.append("%s:%d: %s" % (rel, lineno, line.strip()[:120]))
                        break
    report.check("safety: INV-10 the package contains no send capability", not hits,
                 "\n".join(hits[:15]))

    auto_send_bad, approval_bad = [], []
    for path in package_files():
        rel = os.path.relpath(path, PKG_ROOT)
        if rel == os.path.join("tests", "run_tests.py"):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                low = line.lower()
                if re.search(r"auto[_-]send[\"']?\s*[:=]\s*[\"']?(true|1|yes|on)\b", low):
                    auto_send_bad.append("%s:%d: %s" % (rel, lineno, line.strip()[:120]))
                if "APPROVED_FOR_OUTREACH" in line:
                    if not any(m in low for m in NEGATION_MARKERS) \
                            and "entity_status" not in low and "enum" not in low \
                            and "approved_for_outreach" not in low.split('"')[0]:
                        if re.search(r"=\s*[\"']APPROVED_FOR_OUTREACH", line) \
                                or re.search(r"status[\"']?\s*:\s*[\"']APPROVED_FOR_OUTREACH",
                                             line):
                            approval_bad.append("%s:%d: %s" % (rel, lineno, line.strip()[:120]))
    report.check("safety: no auto-send path exists (auto_send is always false)",
                 not auto_send_bad, "\n".join(auto_send_bad[:10]))
    report.check("safety: INV-09 nothing sets APPROVED_FOR_OUTREACH or a later state",
                 not approval_bad, "\n".join(approval_bad[:10]))

    harvest = []
    for path in package_files((".py",)):
        rel = os.path.relpath(path, PKG_ROOT)
        if rel == os.path.join("tests", "run_tests.py"):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for pattern in (r"\{first\}.?\{last\}@", r"first\.last@", r"%s\.%s@", r"\.format\([^)]*\)@"):
            if re.search(pattern, text):
                harvest.append("%s: email-pattern generation (%s)" % (rel, pattern))
    report.check("safety: INV-11 no email-pattern generator", not harvest, "\n".join(harvest))

    placeholders = []
    # The two harness files name the forbidden tokens in order to forbid them, so they
    # are the only files exempt from this scan.
    self_documenting = (os.path.join("tests", "run_tests.py"),
                        os.path.join("tests", "cases.md"))
    for path in package_files():
        rel = os.path.relpath(path, PKG_ROOT)
        if rel in self_documenting:
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                for pattern in PLACEHOLDER_PATTERNS:
                    if re.search(pattern, line):
                        placeholders.append("%s:%d: %s" % (rel, lineno, line.strip()[:100]))
                        break
    report.check("safety: INV-28 no TODO/FIXME/stub placeholder ships", not placeholders,
                 "\n".join(placeholders[:15]))

    wall_clock, third_party, network = [], [], []
    for path in glob.glob(os.path.join(SCRIPT_DIR, "*.py")):
        rel = os.path.relpath(path, PKG_ROOT)
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                for pattern in WALL_CLOCK_PATTERNS:
                    if re.search(pattern, line):
                        wall_clock.append("%s:%d: %s" % (rel, lineno, line.strip()[:100]))
                match = re.match(r"\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
                if match:
                    module = match.group(1)
                    local = os.path.isfile(os.path.join(SCRIPT_DIR, module + ".py"))
                    if module not in STDLIB_OK and module != "__future__" and not local:
                        third_party.append("%s:%d: imports %s" % (rel, lineno, module))
                    if module in ("socket", "ssl", "http") or "urllib.request" in line:
                        network.append("%s:%d: %s" % (rel, lineno, line.strip()[:100]))
    report.check("safety: INV-14 no wall-clock read on the scoring path", not wall_clock,
                 "\n".join(wall_clock[:10]))
    report.check("safety: INV-26 scripts import stdlib modules only", not third_party,
                 "\n".join(third_party[:10]))
    report.check("safety: R7.11.1 scripts open no network client", not network,
                 "\n".join(network[:10]))

    if os.path.isdir(SCRIPT_DIR) and glob.glob(os.path.join(SCRIPT_DIR, "*.py")):
        bad = []
        for path in sorted(glob.glob(os.path.join(SCRIPT_DIR, "*.py"))):
            # compile() in-process: it reports the same syntax errors as py_compile
            # without writing a .pyc into a directory tests/ does not own.
            try:
                with open(path, encoding="utf-8") as fh:
                    compile(fh.read(), path, "exec")
            except SyntaxError as exc:
                bad.append("%s: %s" % (os.path.relpath(path, PKG_ROOT), exc))
        report.check("safety: every scripts/*.py compiles", not bad, "\n".join(bad))

    forbidden_files = []
    for path in package_files((".py", ".sh")):
        rel = os.path.relpath(path, PKG_ROOT)
        name = os.path.basename(path).lower()
        if any(token in name for token in ("mailer", "smtp", "send_mail", "sendmail",
                                           "dispatch")):
            forbidden_files.append(rel)
    report.check("safety: no transport module ships (BUILD-CONTRACT 2.3)", not forbidden_files,
                 "\n".join(forbidden_files))


# ---------------------------------------------------------------------------
# 6. package-level checks: manifest, SKILL.md portability, CLI resilience
# ---------------------------------------------------------------------------
# BUILD-CONTRACT 2.2, package-relative. README.md and docs/ are repository files by
# design (2.2 note) and are checked separately.
MANIFEST = [
    "SKILL.md",
    "install.sh",
    "references/buyer-discovery.md",
    "references/seller-discovery.md",
    "references/qualification-rubric.md",
    "references/matching-rules.md",
    "references/evidence-policy.md",
    "references/outreach-guidelines.md",
    "references/compliance-notes.md",
    "references/data-contract.md",
    "references/output-format.md",
    "references/runtime-adapters.md",
    "schemas/buyer.schema.json",
    "schemas/seller.schema.json",
    "schemas/rfq.schema.json",
    "schemas/evidence.schema.json",
    "schemas/match-result.schema.json",
    "schemas/scoring.config.json",
    "schemas/discovery-result.schema.json",
    "schemas/acceptance-report.schema.json",
    "scripts/_common.py",
    "scripts/normalize_company.py",
    "scripts/dedupe_companies.py",
    "scripts/score_buyer.py",
    "scripts/score_seller.py",
    "scripts/score_match.py",
    "scripts/validate_output.py",
    "scripts/make_review_sheet.py",
    "scripts/acceptance_report.py",
    "templates/buyer_outreach.md",
    "templates/seller_outreach.md",
    "templates/legal_notices.md",
    "tests/cases.md",
    "tests/run_tests.py",
    "tests/fixtures",
    "adapters/tradewith_adapter.py",
    "adapters/tradewith_adapter.md",
]

# INV-36: the case-insensitive grep list, verbatim from BUILD-CONTRACT section 11.
PORTABILITY_PATTERNS = [r"WebSearch", r"WebFetch", r"web\.run", r"browser\.", r"mcp__",
                        r"claude", r"anthropic", r"openai", r"codex", r"gpt-", r"chatgpt"]

# The six CLI scripts of BUILD-CONTRACT 7.1; R7.3.2 / R7.3.3 / INV-35 apply to each.
# make_review_sheet.py joins them because it takes the same --input: the same 0xff byte
# must reach the same clean exit 2. acceptance_report.py does not, because it takes no
# --input at all (--scored / --reviews are repeatable), so an --input there is an unknown
# flag and argparse's own exit 2 is the correct answer (R7.1.4).
CLI_SCRIPTS = [n for n in PIPELINE_SCRIPTS if n != "_common.py"] + ["make_review_sheet.py"]


def _read_frontmatter(text):
    """The YAML frontmatter as an ordered list of (key, value); no yaml dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    pairs = []
    for line in lines[1:]:
        if line.strip() == "---":
            return pairs
        if not line.strip() or line.startswith((" ", "\t")):
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        pairs.append((key.strip(), value.strip()))
    return None


def phase_package(report):
    # ---- BUILD-CONTRACT 2.2 file manifest ----------------------------------------
    absent = [rel for rel in MANIFEST if not os.path.exists(os.path.join(PKG_ROOT, rel))]
    report.check("package: every BUILD-CONTRACT 2.2 shipped file exists", not absent,
                 "absent: %s" % ", ".join(absent))
    repo_readme = os.path.join(os.path.dirname(PKG_ROOT), "README.md")
    report.check("package: README.md exists at the repository root (BUILD-CONTRACT 2.2 note)",
                 os.path.isfile(repo_readme), "not found: %s" % repo_readme)

    skill_path = os.path.join(PKG_ROOT, "SKILL.md")
    if not os.path.isfile(skill_path):
        report.fail("package: SKILL.md is readable", "missing %s" % skill_path)
        return
    with open(skill_path, encoding="utf-8") as fh:
        skill_text = fh.read()

    # ---- INV-36 portability grep ---------------------------------------------------
    hits = []
    for lineno, line in enumerate(skill_text.splitlines(), 1):
        for pattern in PORTABILITY_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                # The one allowed context: a pointer at the runtime-adapters page.
                if "references/runtime-adapters.md" in line:
                    continue
                hits.append("SKILL.md:%d: /%s/ in %s" % (lineno, pattern, line.strip()[:90]))
                break
    report.check("package: INV-36 SKILL.md names no provider-specific tool, model or API",
                 not hits, "\n".join(hits[:10]))

    # ---- BUILD-CONTRACT 12.1: one skill_version, stated in three places -------------
    # It lives in the BODY of SKILL.md (never the frontmatter - 2.2 row 1), in
    # _common.SKILL_VERSION and in the adapter. A bump that reaches two of the three
    # ships a package that reports a version it is not.
    versions = {}
    match = re.search(r"`skill_version ([0-9]+\.[0-9]+\.[0-9]+)`", skill_text)
    versions["SKILL.md body"] = match.group(1) if match else None
    for rel in ("scripts/_common.py", "adapters/tradewith_adapter.py"):
        path = os.path.join(PKG_ROOT, rel)
        text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
        found = re.search(r'^SKILL_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"', text, re.MULTILINE)
        versions[rel] = found.group(1) if found else None
    report.check("package: BUILD-CONTRACT 12.1 skill_version agrees everywhere it is stated",
                 len(set(versions.values())) == 1 and None not in versions.values(),
                 "; ".join("%s=%s" % (k, v) for k, v in sorted(versions.items())))

    # ---- BUILD-CONTRACT 2.2 row 1 frontmatter shape --------------------------------
    pairs = _read_frontmatter(skill_text)
    problems = []
    if pairs is None:
        problems.append("SKILL.md has no parseable `---` YAML frontmatter block")
    else:
        keys = [k for k, _v in pairs]
        if keys != ["name", "description"]:
            problems.append("frontmatter keys are %s; BUILD-CONTRACT 2.2 row 1 allows exactly "
                            "['name', 'description'] so every runtime parses it unchanged" % (keys,))
        mapping = dict(pairs)
        name = mapping.get("name", "")
        description = mapping.get("description", "")
        if name != "kbeauty-trade-matchmaker":
            problems.append("frontmatter name is %r, expected 'kbeauty-trade-matchmaker'" % name)
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name or ""):
            problems.append("frontmatter name %r is not a lowercase hyphen slug" % name)
        if len(name) > 64:
            problems.append("frontmatter name is %d chars, max 64" % len(name))
        if not description:
            problems.append("frontmatter description is empty")
        if len(description) > 1024:
            problems.append("frontmatter description is %d chars, max 1024" % len(description))
        if "\n" in description:
            problems.append("frontmatter description must be a single line")
    report.check("package: BUILD-CONTRACT 2.2 row 1 SKILL.md frontmatter shape",
                 not problems, "\n".join(problems))

    # ---- R7.3.2 / R7.3.3 / INV-35: a malformed input never tracebacks --------------
    if missing_scripts():
        report.skip("package: R7.3.3 a non-UTF-8 input is a clean usage error, not a traceback",
                    "scripts/ incomplete")
    else:
        handle, bad_path = tempfile.mkstemp(suffix=".json", prefix="kbtm-badbytes-")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(b'{"records": [{"company_name": "A\xff\xfeB"}]}')
            broken = []
            for name in CLI_SCRIPTS:
                code, out, err = run_script(name, ["--input", bad_path])
                error_lines = [ln for ln in err.splitlines() if ln.startswith("ERROR:")]
                if "Traceback (most recent call last)" in err:
                    broken.append("%s: raw traceback escaped main()" % name)
                if code != 2:
                    broken.append("%s: exit %d, expected 2 (a read failure is a usage error)"
                                  % (name, code))
                if len(error_lines) != 1:
                    broken.append("%s: %d 'ERROR:' lines on stderr, R7.3.3 requires exactly 1"
                                  % (name, len(error_lines)))
                if out.strip():
                    broken.append("%s: stdout is not empty on exit 2 (R7.3.2)" % name)
            report.check("package: R7.3.3 a non-UTF-8 input is a clean usage error, "
                         "not a traceback", not broken, "\n".join(broken[:12]))
        finally:
            os.unlink(bad_path)

    # ---- INV-35: a malformed field degrades instead of aborting ---------------------
    if missing_scripts():
        report.skip("package: INV-35 a non-string canonical_domain degrades, never tracebacks",
                    "scripts/ incomplete")
    else:
        poisoned = {"records": [
            {"company_id": "X-a", "company_name": "A Co", "canonical_domain": "a.example",
             "website": "https://a.example", "country": "AE", "company_type": "distributor",
             "evidence": []},
            {"company_id": "X-b", "company_name": "B Co", "canonical_domain": ["b.example"],
             "website": "https://b.example", "country": "AE", "company_type": "distributor",
             "evidence": []},
        ]}
        handle, poison_path = tempfile.mkstemp(suffix=".json", prefix="kbtm-poison-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(poisoned, fh)
            broken = []
            for name in ("score_buyer.py", "score_seller.py"):
                code, out, err = run_script(name, ["--input", poison_path])
                if "Traceback (most recent call last)" in err:
                    broken.append("%s: raw traceback escaped main() (sorting excluded[])" % name)
                if not [ln for ln in err.splitlines() if ln.startswith("ERROR:")]:
                    broken.append("%s: exited %d with no 'ERROR:' line (R7.3.3)" % (name, code))
                if not out.strip():
                    broken.append("%s: wrote no document at all (R7.3.2)" % name)
            report.check("package: INV-35 a non-string canonical_domain degrades, "
                         "never tracebacks", not broken, "\n".join(broken[:8]))
        finally:
            os.unlink(poison_path)


# ---------------------------------------------------------------------------
# 7. adapter guards: INV-10 / INV-25 dispatch keys, INV-34 live demand
# ---------------------------------------------------------------------------
def phase_adapter_guards(report):
    adapter_path = os.path.join(PKG_ROOT, "adapters", "tradewith_adapter.py")
    if not os.path.isfile(adapter_path):
        report.skip("adapter: dispatch/credential guard", "adapters/ not present")
        return
    sys.path.insert(0, os.path.join(PKG_ROOT, "adapters"))
    sys.dont_write_bytecode = True
    try:
        import tradewith_adapter as tw
    except Exception as exc:  # pragma: no cover - defensive
        report.fail("adapter: module imports", str(exc))
        return

    def base_draft():
        return {"entity_id": "BUY-example-com", "side": "Buyer",
                "channel_type": "corporate_email", "channel_value": "info@example.example",
                "language": "English", "subject": "Sourcing enquiry",
                "body": "We work with GCC distributors sourcing Korean sun care."}

    def refused(draft):
        try:
            tw._normalize_draft(draft, 0)
        except tw.DataError:
            return True
        return False

    # A clean draft must still go through, or the guards are just breaking the adapter.
    ok = True
    try:
        stored = tw._normalize_draft(base_draft(), 0)
        ok = (stored["status"] == "READY_FOR_REVIEW" and stored["auto_send"] is False
              and stored["manual_approval_required"] is True)
    except tw.DataError as exc:
        ok = False
        report.fail("adapter: a clean draft is still accepted", str(exc))
    if ok:
        report.ok("adapter: a clean draft is still accepted")

    nested = base_draft()
    nested["delivery"] = {"transport": "smtp", "recipients": ["ops@example.example"],
                          "schedule_at": "2026-10-01"}
    nested["auth_block"] = {"token": "placeholder-value", "password": "placeholder-value"}
    report.check("adapter: INV-10/INV-25 a NESTED dispatch or credential block is refused",
                 refused(nested),
                 "a draft carrying delivery.{transport,recipients,schedule_at} and "
                 "auth_block.{token,password} was accepted and stored verbatim")

    leaks = []
    for key in ("to", "reply_to", "envelope_from", "mail_from", "smtp_host", "smtp_port",
                "send", "send_at", "queue_for_send", "webhook_url", "sendgrid_key",
                "api_token", "access_key", "bearer", "cookie", "recipients", "transport",
                "token", "password"):
        draft = base_draft()
        draft[key] = "value"
        if not refused(draft):
            leaks.append(key)
    report.check("adapter: INV-25 every dispatch/credential key name is refused at top level",
                 not leaks, "accepted: %s" % ", ".join(leaks))

    # INV-34 / R10.4.3 first clause, both languages.
    demand_en = base_draft()
    demand_en["subject"] = "Buyer waiting for your sunscreen"
    demand_en["body"] = ("We have a buyer currently looking for your sunscreen. "
                         "Limited slots, closing soon.")
    demand_ko = base_draft()
    demand_ko["language"] = "Korean"
    demand_ko["body"] = "\ud604\uc7ac \uadc0\uc0ac \uc81c\ud488\uc744 \ucc3e\ub294 \ubc14\uc774\uc5b4\uac00 \uc788\uc2b5\ub2c8\ub2e4."
    report.check("adapter: T05/INV-34 an uncited live-demand claim is refused (EN)",
                 refused(demand_en), "the draft was accepted with no rfq_id")
    report.check("adapter: T05/INV-34 an uncited live-demand claim is refused (KO)",
                 refused(demand_ko), "the draft was accepted with no rfq_id")

    cited = base_draft()
    cited["rfq_id"] = "RFQ-134"
    cited["rfq_status"] = "matching"
    cited["rfq_as_of"] = AS_OF
    cited["body"] = ("RFQ #134 - status: matching, as of %s - a buyer is currently looking for "
                     "private-label sunscreen into the UAE." % AS_OF)
    report.check("adapter: INV-34 a demand claim citing an open RFQ with its status and date "
                 "is allowed", not refused(cited),
                 "a correctly cited demand claim was refused; the check is over-tight")

    half_cited = dict(cited)
    half_cited["body"] = "A buyer is currently looking for private-label sunscreen into the UAE."
    report.check("adapter: R10.4.3 a cited RFQ whose status and date are not stated in the "
                 "draft is refused", refused(half_cited),
                 "the draft never prints the RFQ status or as_of date but was accepted")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="kbeauty-trade-matchmaker golden test suite")
    parser.add_argument("--allow-missing-scripts", action="store_true",
                        help="report script-dependent cases as SKIP instead of FAIL")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print a line for every case, not only failures")
    args = parser.parse_args(argv)

    report = Report(verbose=args.verbose)
    print("kbeauty-trade-matchmaker test suite  (package root: %s, as_of: %s)"
          % (PKG_ROOT, AS_OF))
    print("-" * 78)

    phase_schema_selfcheck(report)
    validator = pick_validator(report)
    phase_fixture_validation(report, validator)
    pipeline = Pipeline()
    phase_pipeline(report, pipeline, args.allow_missing_scripts)
    phase_cases(report, pipeline, args.allow_missing_scripts)
    phase_field_regressions(report, args.allow_missing_scripts)
    phase_calibration(report, args.allow_missing_scripts)
    phase_safety(report)
    phase_package(report)
    phase_adapter_guards(report)

    passed, failed, skipped = report.counts()
    print("-" * 78)
    print("PASS %d / FAIL %d / SKIP %d" % (passed, failed, skipped))
    if failed:
        print("FAILED CASES:")
        for status, name, _ in report.rows:
            if status == "FAIL":
                print("  - %s" % name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
