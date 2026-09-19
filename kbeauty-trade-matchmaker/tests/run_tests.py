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
import copy
import csv
import glob
import hashlib
import io
import json
import os
import re
import shutil
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


#: Modules that open a network connection (or send mail) on their own.
NETWORK_MODULES = ("socket", "ssl", "http", "ftplib", "smtplib", "poplib", "imaplib",
                   "telnetlib", "xmlrpc")
#: The one package script that starts child processes (the MCP server runs the other
#: scripts); a subprocess anywhere else could shell out to curl past every other scan.
SUBPROCESS_OK_SCRIPTS = ("mcp_server.py",)


def _network_problem(line, allow_subprocess):
    """A reason string when one source line could open a network path, else None.

    Catches the spellings a plain "import socket" scan misses: "from urllib import
    request", "__import__(...)", importlib, and subprocess (a shell-out to curl) outside
    the files allowed to start children. Comment lines are ignored.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = re.match(r"(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
    module = match.group(1) if match else None
    if module in NETWORK_MODULES or "urllib.request" in stripped:
        return "network client"
    if re.match(r"from\s+urllib\s+import\s+.*\brequest\b", stripped):
        return "network client"
    if "__import__(" in stripped or re.search(r"\bimportlib\b", stripped):
        return "dynamic import"
    if module == "subprocess" and not allow_subprocess:
        return "subprocess outside its allow-list"
    return None


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


def _refusal(report, name, args, expect, label, prefix="calibration", empty_stdout=False):
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
    if empty_stdout and out.strip():
        problems.append("stdout is not empty")
    report.check("%s: refuses %s" % (prefix, label), not problems, "\n".join(problems))


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


# ---------------------------------------------------------------------------
# 5b. run diff - scripts/diff_runs.py compares two scored runs
#
# The inputs are scored in-phase, as phase_calibration does. The three goldens are
# byte-compared; everything else is a property of fresh output, so a mutation case
# never depends on the exact numbers of a golden.
# ---------------------------------------------------------------------------
DIFF_SCRIPTS = ["diff_runs.py"]
DIFF_FORBIDDEN_KEYS = ("contact_channels", "evidence", "website", "observed_value",
                       "required_value", "failed_rules")


def _diff_keys(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            _diff_keys(value, out)
    elif isinstance(node, list):
        for value in node:
            _diff_keys(value, out)
    return out


def _diff_run(args, stdin_data=None):
    """(exit, parsed JSON or None, stderr) for one diff_runs.py call."""
    code, out, err = run_script("diff_runs.py", args, stdin_data)
    try:
        parsed = json.loads(out) if code == 0 else None
    except ValueError:
        parsed = None
    return code, parsed, err


def _diff_usage(report, args, label, stdin_data=None):
    """A usage error: exit 2, exactly one ERROR: line, no traceback, empty stdout."""
    code, out, err = run_script("diff_runs.py", args, stdin_data)
    error_lines = [ln for ln in err.splitlines() if ln.startswith("ERROR:")]
    problems = []
    if code != 2:
        problems.append("exit %d, expected 2" % code)
    if len(error_lines) != 1:
        problems.append("%d 'ERROR:' lines, R7.3.3 requires exactly 1" % len(error_lines))
    if "Traceback (most recent call last)" in err:
        problems.append("raw traceback escaped main()")
    if out.strip():
        problems.append("stdout is not empty")
    report.check("run diff: usage error %s" % label, not problems, "\n".join(problems))


def _diff_refusal(report, args, expect, label):
    _refusal(report, "diff_runs.py", args, expect, label, prefix="run diff",
             empty_stdout=True)


def _diff_goldens(report, paths):
    """The three byte goldens, INV-13 re-runs and the --strict validator on each."""
    cases = (
        # The as_of pair takes no --as-of: the diff defaults to the later input date,
        # and an explicit AS_OF would be earlier than the 2028 run (refused by design).
        ("the UAE buyer run re-scored two years later",
         ["--before", paths["B1"], "--after", paths["B2"], "--pretty"],
         "diff.buyers.uae.asof.expected.json"),
        ("match-134 without and with the bounded rerank",
         ["--before", paths["M1"], "--after", paths["M2"], "--as-of", AS_OF, "--pretty"],
         "diff.match-134.rerank.expected.json"),
        ("the UAE buyer run against the UK sunscreen query",
         ["--before", paths["B1"], "--after", paths["B3"], "--as-of", AS_OF, "--pretty"],
         "diff.buyers.uae-uk.expected.json"),
    )
    parsed = {}
    for label, args, expected_name in cases:
        code, out, err = run_script("diff_runs.py", args)
        report.check("run diff: %s exits 0" % label, code == 0,
                     "exit %d\n%s" % (code, err.strip()[:400]))
        golden = _read_text(os.path.join(EXPECTED, expected_name))
        report.check("run diff: %s matches %s byte for byte" % (label, expected_name),
                     out == golden, "generated diff differs from the expected fixture")
        code2, out2, _err = run_script("diff_runs.py", args)
        report.check("run diff: INV-13 %s is byte-identical on a re-run" % label,
                     code2 == 0 and out2 == out, "second run differed")
        path = _write_temp_text(out, "kbtm-diff-out-", ".json")
        try:
            vcode, _vout, verr = run_script("validate_output.py", [
                "--input", path, "--schema", "run-diff", "--invariants", "--strict"])
        finally:
            os.unlink(path)
        report.check("run diff: %s passes validate_output --schema run-diff --strict" % label,
                     vcode == 0, verr.strip()[:400])
        parsed[expected_name] = json.loads(out) if code == 0 else {}
    return parsed


def _diff_properties(report, paths, goldens):
    asof = goldens["diff.buyers.uae.asof.expected.json"]
    rerank = goldens["diff.match-134.rerank.expected.json"]
    uk = goldens["diff.buyers.uae-uk.expected.json"]

    # Identity: a run against itself changes nothing.
    code, same, err = _diff_run(["--before", paths["B1"], "--after", paths["B1"]])
    lists = ("new", "gone", "newly_excluded", "newly_returned", "exclusion_changes", "changed")
    counts = dict((k, v) for k, v in (same or {}).get("summary", {}).items()
                  if k not in ("paired", "unchanged", "rank_changed"))
    report.check("run diff: a run diffed against itself reports no change",
                 code == 0 and all(same[k] == [] for k in lists)
                 and all(v == 0 for v in counts.values())
                 and same["summary"]["paired"] == same["summary"]["unchanged"] == 20
                 and same["score_version"] == read_json(paths["B1"])["score_version"]
                 and same["notes"] == [],
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # Antisymmetry: swapping the runs negates every delta and swaps gained / lost.
    code, swapped, err = _diff_run(["--before", paths["B2"], "--after", paths["B1"]])
    forward = dict((i["record_id"], i["score"]["delta"]) for i in asof["changed"] if "score" in i)
    backward = dict((i["record_id"], i["score"]["delta"])
                    for i in (swapped or {}).get("changed", []) if "score" in i)
    report.check("run diff: swapping --before and --after negates every score delta and "
                 "swaps qualified gained / lost",
                 code == 0 and forward and backward == dict((k, -v) for k, v in forward.items())
                 and swapped["summary"]["qualified_gained"] == asof["summary"]["qualified_lost"]
                 and swapped["summary"]["qualified_lost"] == asof["summary"]["qualified_gained"]
                 and any("dated later than --after" in n for n in swapped["notes"]),
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # Ordering: changed[] by |score delta| descending, then record_id.
    order = [((-abs(i["score"]["delta"]) if "score" in i else 0), i["record_id"])
             for i in asof["changed"]]
    report.check("run diff: changed[] is ordered by |score delta| desc, then record_id",
                 order == sorted(order) and len(order) > 1, str(order[:4]))

    lost = sorted(i["record_id"] for i in asof["changed"]
                  if i.get("qualified", {}).get("flip") == "lost")
    report.check("run diff: re-scoring at 2028-03-01 loses qualified on exactly "
                 "luminaglow and lioncitybeauty",
                 asof["summary"]["qualified_lost"] == 2
                 and lost == ["BUY-lioncitybeauty-example", "BUY-luminaglow-example"],
                 "lost=%s" % lost)
    report.check("run diff: a confidence delta is computed in Decimal (kantoimport "
                 "0.41 -> 0.15 = -0.26)",
                 any(i["record_id"] == "BUY-kantoimport-example"
                     and i.get("confidence") == {"before": 0.41, "after": 0.15, "delta": -0.26}
                     for i in asof["changed"]),
                 "kantoimport confidence change missing or wrong")
    report.check("run diff: the diff is dated at the later input run and notes the as_of "
                 "change",
                 asof["as_of"] == "2028-03-01" and asof["context"]["as_of_changed"] is True
                 and any("different as_of dates" in n for n in asof["notes"]),
                 "as_of=%s" % asof.get("as_of"))

    by_id = dict((i["record_id"], i) for i in rerank["changed"])
    hanbit = by_id.get("SEL-hanbitcos-example", {})
    report.check("run diff: the rerank moves two ranks, and base_score is absent where "
                 "the base did not change",
                 rerank["summary"]["rank_changed"] == 2
                 and hanbit.get("rank") == {"before": 2, "after": 1}
                 and "score" in hanbit and "base_score" not in hanbit
                 and by_id.get("SEL-hansolodm-example", {}).get("rank") == {"before": 1,
                                                                            "after": 2},
                 "changed=%s" % sorted(by_id))
    report.check("run diff: the listed / not-listed note is on a match diff only",
                 any("listed / not listed" in n for n in rerank["notes"])
                 and not any("listed / not listed" in n for n in asof["notes"] + uk["notes"]),
                 "notes differ")
    report.check("run diff: a discovery diff has rank_changed null and weights_changed null; "
                 "a match diff has query_changed null",
                 asof["summary"]["rank_changed"] is None
                 and asof["context"]["weights_changed"] is None
                 and rerank["context"]["query_changed"] is None
                 and rerank["context"]["weights_changed"] is False,
                 "context=%s / %s" % (asof["context"], rerank["context"]))
    report.check("run diff: a changed query is flagged and Missing-line changes are reported",
                 uk["context"]["query_changed"] is True
                 and uk["summary"]["missing_changed"] == 9
                 and uk["summary"]["qualified_gained"] > 0
                 and uk["summary"]["qualified_lost"] > 0,
                 "context=%s summary=%s" % (uk["context"], uk["summary"]))

    leaked = set()
    for document in (asof, rerank, uk):
        leaked |= _diff_keys(document, set()) & set(DIFF_FORBIDDEN_KEYS)
    report.check("run diff: no contact channel, evidence, website or observed value is "
                 "copied into a diff", not leaked, "leaked keys: %s" % sorted(leaked))

    # stdin is accepted for one side and gives the same bytes as the file.
    code, via_file, _err = run_script("diff_runs.py", [
        "--before", paths["M1"], "--after", paths["M2"], "--as-of", AS_OF])
    code2, via_stdin, _err = run_script("diff_runs.py", [
        "--before", "-", "--after", paths["M2"], "--as-of", AS_OF],
        _read_text(paths["M1"]))
    report.check("run diff: --before - reads stdin and yields the same diff",
                 code == 0 and code2 == 0 and via_file == via_stdin,
                 "exit %d / %d" % (code, code2))


def _diff_mutations(report, paths, temp):
    b1 = read_json(paths["B1"])
    m1 = read_json(paths["M1"])
    first = b1["records"][0]
    xid = first["buyer_id"]

    def write(document, prefix):
        path = _write_temp_json(document, prefix)
        temp.append(path)
        return path

    def run(before, after):
        return _diff_run(["--before", before, "--after", after, "--as-of", AS_OF])

    # (a) a record deleted from one side is gone / new, with its returned state.
    dropped = copy.deepcopy(b1)
    dropped["records"] = dropped["records"][1:]
    dropped_path = write(dropped, "kbtm-diff-drop-")
    code, gone, err = run(paths["B1"], dropped_path)
    report.check("run diff: a record missing from --after is gone, state returned, with "
                 "its score and qualified flag",
                 code == 0 and [(i["record_id"], i["state"], i.get("score"), i.get("qualified"))
                                for i in gone["gone"]]
                 == [(xid, "returned", first["qualification_score"], first["qualified"])]
                 and gone["new"] == [] and "merged_into" not in gone["gone"][0],
                 "exit %d\n%s" % (code, err.strip()[:300]))
    code, new, err = run(dropped_path, paths["B1"])
    report.check("run diff: a record missing from --before is new",
                 code == 0 and [i["record_id"] for i in new["new"]] == [xid]
                 and new["gone"] == [], "exit %d\n%s" % (code, err.strip()[:300]))

    # (b) returned -> excluded and back.
    excluded = copy.deepcopy(b1)
    excluded["records"] = excluded["records"][1:]
    excluded["excluded"].append({
        "id": xid, "company_name": first["company_name"],
        "canonical_domain": first["canonical_domain"],
        "reason_summary": "HF-01 Product category not covered",
        "failed_rules": [{"rule_id": "HF-01", "rule_name": "Product category",
                          "reason": "Product category not covered",
                          "observed_value": "skincare", "required_value": "sunscreen"}]})
    excluded_path = write(excluded, "kbtm-diff-excl-")
    code, moved, err = run(paths["B1"], excluded_path)
    item = (moved or {}).get("newly_excluded", [{}])[0] if moved else {}
    report.check("run diff: returned -> excluded is newly_excluded with the failed rule ids "
                 "only",
                 code == 0 and len(moved["newly_excluded"]) == 1
                 and item.get("record_id") == xid and item.get("failed_rule_ids") == ["HF-01"]
                 and item.get("before_score") == first["qualification_score"]
                 and moved["gone"] == [] and moved["new"] == [],
                 "exit %d\n%s" % (code, err.strip()[:300]))
    code, back, err = run(excluded_path, paths["B1"])
    item = back["newly_returned"][0] if code == 0 and back["newly_returned"] else {}
    report.check("run diff: excluded -> returned is newly_returned with the old rule ids",
                 code == 0 and item.get("record_id") == xid
                 and item.get("before_failed_rule_ids") == ["HF-01"]
                 and item.get("after_score") == first["qualification_score"],
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (c) excluded in both runs with a different rule set.
    rules = copy.deepcopy(m1)
    target = None
    for entry in rules["excluded"]:
        if [r["rule_id"] for r in entry["failed_rules"]] == ["HF-02"]:
            entry["failed_rules"][0]["rule_id"] = "HF-03"
            target = entry["seller_id"]
            break
    code, changed, err = run(paths["M1"], write(rules, "kbtm-diff-rules-"))
    report.check("run diff: an exclusion whose rule changes is an exclusion_change with "
                 "rules_added / rules_removed",
                 code == 0 and target is not None
                 and [(i["record_id"], i["rules_added"], i["rules_removed"])
                      for i in changed["exclusion_changes"]]
                 == [(target, ["HF-03"], ["HF-02"])]
                 and changed["summary"]["exclusion_changed"] == 1,
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (d) a dedupe merge changes the surviving id: pair through merged_from, and a
    # record absorbed into another reads as merged_into, not as a lost lead.
    yid = "BUY-kantoimport-example"
    merged = copy.deepcopy(b1)
    merged["records"][0]["buyer_id"] = "BUY-gulfglow2-example"
    merged["records"][0]["merged_from"] = [xid, yid]
    merged["records"] = [r for r in merged["records"] if r["buyer_id"] != yid]
    code, doc, err = run(paths["B1"], write(merged, "kbtm-diff-merge-"))
    pair = [i for i in (doc or {}).get("changed", []) if i.get("before_id") == xid]
    report.check("run diff: a re-keyed record pairs through merged_from and an absorbed "
                 "one is gone with merged_into",
                 code == 0 and doc["new"] == [] and doc["summary"]["paired"] == 19
                 and [(i["record_id"], i.get("merged_into")) for i in doc["gone"]]
                 == [(yid, "BUY-gulfglow2-example")]
                 and doc["summary"]["unchanged"] == 18 and len(pair) == 1
                 and pair[0]["matched_by"] == "merged_from"
                 and pair[0]["record_id"] == "BUY-gulfglow2-example",
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (e) a renamed id without merged_from is never guessed: gone plus new.
    renamed = copy.deepcopy(b1)
    renamed["records"][0]["buyer_id"] = "BUY-gulfglow2-example"
    code, doc, err = run(paths["B1"], write(renamed, "kbtm-diff-rename-"))
    report.check("run diff: an id renamed without merged_from is gone + new, not paired",
                 code == 0 and [i["record_id"] for i in doc["gone"]] == [xid]
                 and [i["record_id"] for i in doc["new"]] == ["BUY-gulfglow2-example"],
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (f) the literal id "unknown" is never paired and says so.
    anonymous = copy.deepcopy(b1)
    anonymous["records"][0]["buyer_id"] = "unknown"
    code, doc, err = run(paths["B1"], write(anonymous, "kbtm-diff-unknown-"))
    report.check("run diff: a record with id \"unknown\" is listed as new, never paired, "
                 "with a note",
                 code == 0 and [i["record_id"] for i in doc["new"]] == ["unknown"]
                 and [i["record_id"] for i in doc["gone"]] == [xid]
                 and any("id \"unknown\"" in n for n in doc["notes"]),
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (g) a Missing-line change keeps list order.
    missing = copy.deepcopy(b1)
    labels = list(missing["records"][0].get("missing") or [])
    missing["records"][0]["missing"] = labels[1:] + ["Registered importer licence"]
    code, doc, err = run(paths["B1"], write(missing, "kbtm-diff-missing-"))
    item = [i for i in (doc or {}).get("changed", []) if i["record_id"] == xid]
    report.check("run diff: a Missing-line change lists added / removed labels in order",
                 code == 0 and len(item) == 1
                 and item[0].get("missing") == {"added": ["Registered importer licence"],
                                                "removed": labels[:1]}
                 and doc["summary"]["missing_changed"] == 1,
                 "exit %d labels=%s\n%s" % (code, labels, err.strip()[:300]))

    # (h) a partial run is noted.
    partial = copy.deepcopy(b1)
    partial["partial"] = True
    code, doc, err = run(paths["B1"], write(partial, "kbtm-diff-partial-"))
    report.check("run diff: a partial run is echoed and noted",
                 code == 0 and doc["after"]["partial"] is True
                 and any("Run --after is partial" in n for n in doc["notes"]),
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (f2) "unknown" on the before side is listed as gone, and the note names --before.
    code, doc, err = run(write(anonymous, "kbtm-diff-unknown-b-"), paths["B1"])
    report.check("run diff: a --before record with id \"unknown\" is listed as gone, never "
                 "paired, with a note naming --before",
                 code == 0 and [i["record_id"] for i in doc["gone"]] == ["unknown"]
                 and [i["record_id"] for i in doc["new"]] == [xid]
                 and any(n.startswith("--before carries") and "id \"unknown\"" in n
                         and "gone" in n for n in doc["notes"]),
                 "exit %d notes=%s\n%s" % (code, (doc or {}).get("notes"), err.strip()[:300]))

    # (i) the reverse merged_from pass: a before record that lists the after id.
    split_before = copy.deepcopy(b1)
    split_before["records"][0]["merged_from"] = ["BUY-gulfglow2-example"]
    split_after = copy.deepcopy(b1)
    split_after["records"][0]["buyer_id"] = "BUY-gulfglow2-example"
    split_after["records"][0]["merged_from"] = []
    code, doc, err = run(write(split_before, "kbtm-diff-split-b-"),
                         write(split_after, "kbtm-diff-split-a-"))
    pair = [i for i in (doc or {}).get("changed", []) if i.get("before_id") == xid]
    report.check("run diff: a before record whose merged_from names the after id pairs "
                 "through merged_from",
                 code == 0 and doc["new"] == [] and doc["gone"] == [] and len(pair) == 1
                 and pair[0]["matched_by"] == "merged_from"
                 and pair[0]["record_id"] == "BUY-gulfglow2-example",
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (j) a base_score change is reported on its own, with no final-score key.
    base = copy.deepcopy(m1)
    base["results"][0]["base_score"] -= 1
    top = base["results"][0]
    code, doc, err = run(paths["M1"], write(base, "kbtm-diff-base-"))
    item = [i for i in (doc or {}).get("changed", []) if i["record_id"] == top["seller_id"]]
    report.check("run diff: a base_score change is reported with its delta",
                 code == 0 and len(item) == 1
                 and item[0].get("base_score") == {"before": top["base_score"] + 1,
                                                   "after": top["base_score"], "delta": -1}
                 and "score" not in item[0],
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (k) a changed weights_used and a changed skill_version are flagged.
    weights = copy.deepcopy(m1)
    weights["weights_used"]["product_fit"] = 0.25
    weights["weights_used"]["market_fit"] = 0.15
    code, doc, err = run(paths["M1"], write(weights, "kbtm-diff-weights-"))
    report.check("run diff: a changed weights_used sets context.weights_changed",
                 code == 0 and doc["context"]["weights_changed"] is True,
                 "exit %d\n%s" % (code, err.strip()[:300]))
    skill = copy.deepcopy(b1)
    skill["skill_version"] = "0.1.0"
    code, doc, err = run(write(skill, "kbtm-diff-skill-"), paths["B1"])
    report.check("run diff: a different skill_version is echoed and noted",
                 code == 0 and doc["before"]["skill_version"] == "0.1.0"
                 and any("different skill versions (0.1.0, " in n for n in doc["notes"]),
                 "exit %d\n%s" % (code, err.strip()[:300]))
    blank = copy.deepcopy(b1)
    blank["skill_version"] = ""
    code, doc, err = run(paths["B1"], write(blank, "kbtm-diff-skill-blank-"))
    report.check("run diff: an empty skill_version on an input reads as unknown, not a "
                 "failed diff",
                 code == 0 and doc["after"]["skill_version"] == "unknown",
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # (l) a match run cut by --threshold 90 --top 3 flags the threshold and lists the
    # cut sellers as gone with their before rank.
    code, doc, err = run(paths["M1"], paths["MT"])
    report.check("run diff: a changed threshold is flagged and cut sellers are gone with "
                 "their rank",
                 code == 0 and doc["context"]["threshold_changed"] is True
                 and doc["gone"] != []
                 and all(i["state"] != "returned" or isinstance(i.get("rank"), int)
                         for i in doc["gone"]),
                 "exit %d\n%s" % (code, err.strip()[:300]))

    # A real dedupe: the raw run against a deduped-then-scored run.
    code, deduped_out, err = run_script("dedupe_companies.py", [
        "--input", os.path.join(FIXTURES, "buyers.golden.json"), "--as-of", AS_OF])
    deduped_path = _write_temp_text(deduped_out, "kbtm-diff-dedupe-", ".json")
    temp.append(deduped_path)
    code, scored_out, err = run_script("score_buyer.py", [
        "--input", deduped_path,
        "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"), "--as-of", AS_OF])
    scored_path = _write_temp_text(scored_out, "kbtm-diff-dedupe-scored-", ".json")
    temp.append(scored_path)
    code, doc, err = run(paths["B1"], scored_path)
    report.check("run diff: a real dedupe merge reads as merged_into, not as a lost lead",
                 code == 0 and [(i["record_id"], i.get("merged_into")) for i in doc["gone"]]
                 == [("BUY-www-luminaglow-example", "BUY-luminaglow-example")]
                 and doc["new"] == [],
                 "exit %d\n%s" % (code, err.strip()[:300]))
    return b1, m1


def _diff_refusals(report, paths, b1, temp):
    def write(document, prefix):
        path = _write_temp_json(document, prefix)
        temp.append(path)
        return path

    version = copy.deepcopy(b1)
    version["score_version"] = "kbtm-score-0.2.0"
    for record in version["records"]:
        record["score_version"] = "kbtm-score-0.2.0"
    _diff_refusal(report, ["--before", paths["B1"], "--after", write(version, "kbtm-diff-v2-")],
                  "refusing to compare score_version",
                  "two runs scored with different score_versions")
    mixed = copy.deepcopy(b1)
    mixed["records"][0]["score_version"] = "kbtm-score-0.2.0"
    _diff_refusal(report, ["--before", paths["B1"], "--after", write(mixed, "kbtm-diff-mix-")],
                  "mixes score_version", "a run whose records disagree with its envelope")
    unscored = copy.deepcopy(b1)
    unscored["score_version"] = "unscored"
    _diff_refusal(report, ["--before", write(unscored, "kbtm-diff-unscored-"),
                           "--after", paths["B1"]],
                  "score_version", "an unscored run")
    typed = copy.deepcopy(b1)
    typed["records"][0]["qualification_score"] = "96"
    _diff_refusal(report, ["--before", paths["B1"], "--after", write(typed, "kbtm-diff-type-")],
                  "is not a valid discovery-result document",
                  "an input that fails its own schema")
    _diff_refusal(report, ["--before", paths["B1"], "--after", paths["M1"]],
                  "discovery-result against a match-result",
                  "a discovery run against a match run")
    _diff_refusal(report, ["--before", paths["B1"], "--after", paths["S1"]],
                  "buyer and seller", "a buyer run against a seller run")
    _diff_refusal(report, ["--before", paths["M1"], "--after", paths["MN"]],
                  "different RFQs", "match runs for two different RFQs")
    _diff_refusal(report, ["--before", paths["B1"], "--after", paths["BR"]],
                  "--records-only", "a bare --records-only array")
    duplicate = copy.deepcopy(b1)
    duplicate["excluded"].append(dict(duplicate["excluded"][0]))
    _diff_refusal(report, ["--before", paths["B1"], "--after", write(duplicate, "kbtm-diff-dup-")],
                  "appears twice", "a record id that appears twice in one run")
    _diff_refusal(report, ["--before", paths["B1"], "--after", write({}, "kbtm-diff-empty-")],
                  "neither a discovery-result nor a match-result", "an empty object")
    _diff_refusal(report, ["--before", paths["B1"], "--after", paths["B2"], "--as-of", AS_OF],
                  "earlier than input run date", "an --as-of earlier than an input run")

    ranked = copy.deepcopy(read_json(paths["M1"]))
    ranked["results"][0]["rank"] = 5
    _diff_refusal(report, ["--before", paths["M1"], "--after", write(ranked, "kbtm-diff-rank-")],
                  "rank must equal index + 1", "a match record whose rank is not its position")

    _diff_usage(report, ["--before", paths["B1"]], "(missing --after)")
    _diff_usage(report, ["--before", paths["B1"], "--after", paths["B1"], "--as-of", ""],
                "(empty --as-of)")
    _diff_usage(report, ["--before", paths["B1"], "--after", paths["B1"],
                         "--as-of", "2026-13-40"], "(malformed --as-of)")
    handle, bad = tempfile.mkstemp(suffix=".json", prefix="kbtm-diff-bytes-")
    with os.fdopen(handle, "wb") as fh:
        fh.write(b"\xff\xfe")
    temp.append(bad)
    _diff_usage(report, ["--before", bad, "--after", paths["B1"]], "(non-UTF-8 input)")
    _diff_usage(report, ["--before", "-", "--after", "-"], "(both sides on stdin)", "{}")

    # A diff that fails its own schema is written nowhere. --schema-dir also serves
    # the input schemas, so the bundled ones are copied beside the broken run-diff.
    bad_dir = tempfile.mkdtemp(prefix="kbtm-diff-badschema-")
    try:
        for name in os.listdir(SCHEMA_DIR):
            if name.endswith(".schema.json"):
                with open(os.path.join(bad_dir, name), "w", encoding="utf-8") as fh:
                    fh.write(_read_text(os.path.join(SCHEMA_DIR, name)))
        with open(os.path.join(bad_dir, "run-diff.schema.json"), "w", encoding="utf-8") as fh:
            json.dump({"$schema": "https://json-schema.org/draft/2020-12/schema",
                       "type": "object", "required": ["a_field_no_diff_has"]}, fh)
        out_path = os.path.join(bad_dir, "diff.json")
        code, out, err = run_script("diff_runs.py", [
            "--before", paths["M1"], "--after", paths["M2"], "--as-of", AS_OF,
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
        report.check("run diff: a diff that fails its schema is not written at all",
                     not problems, "\n".join(problems))
    finally:
        for name in os.listdir(bad_dir):
            os.unlink(os.path.join(bad_dir, name))
        os.rmdir(bad_dir)

    code, out, err = run_script("diff_runs.py", ["--version"])
    report.check("run diff: --version exits 0 and names the three versions",
                 code == 0 and out.startswith("diff_runs.py skill_version=")
                 and "score_version=" in out, "exit %d %r" % (code, out))


def phase_run_diff(report, allow_missing):
    absent = [n for n in DIFF_SCRIPTS if not os.path.isfile(os.path.join(SCRIPT_DIR, n))]
    if absent or missing_scripts():
        note = "scripts absent: %s" % ", ".join(absent or missing_scripts())
        if allow_missing:
            report.skip("run diff: cases", note)
            return
        report.fail("run diff: cases", note)
        return

    runs = (
        ("B1", "score_buyer.py", ["--input", "buyers.golden.json",
                                  "--query", "query-buyer-uae-kbeauty.json", "--as-of", AS_OF]),
        ("B2", "score_buyer.py", ["--input", "buyers.golden.json",
                                  "--query", "query-buyer-uae-kbeauty.json",
                                  "--as-of", "2028-03-01"]),
        ("B3", "score_buyer.py", ["--input", "buyers.golden.json",
                                  "--query", "query-buyer-uk-sunscreen.json", "--as-of", AS_OF]),
        ("BR", "score_buyer.py", ["--input", "buyers.golden.json",
                                  "--query", "query-buyer-uae-kbeauty.json", "--as-of", AS_OF,
                                  "--records-only"]),
        ("S1", "score_seller.py", ["--input", "sellers.golden.json",
                                   "--query", "query-seller-sunscreen-oem.json", "--as-of", AS_OF]),
        ("M1", "score_match.py", ["--input", "match-134.input.json", "--as-of", AS_OF]),
        ("M2", "score_match.py", ["--input", "match-134.input.json", "--as-of", AS_OF,
                                  "--rerank-input", "rerank-134.json"]),
        ("MN", "score_match.py", ["--input", "match-no-match.input.json", "--as-of", AS_OF]),
        ("MT", "score_match.py", ["--input", "match-134.input.json", "--as-of", AS_OF,
                                  "--threshold", "90", "--top", "3"]),
    )
    temp = []
    paths = {}
    try:
        for key, script, args in runs:
            args = [os.path.join(FIXTURES, a) if a.endswith(".json") else a for a in args]
            code, out, err = run_script(script, args)
            if code != 0:
                report.fail("run diff: input run %s is scoreable" % key, err.strip()[:400])
                return
            paths[key] = _write_temp_text(out, "kbtm-diff-%s-" % key.lower(), ".json")
            temp.append(paths[key])

        goldens = _diff_goldens(report, paths)
        if not all(goldens.values()):
            report.fail("run diff: property cases", "a golden diff did not run")
            return
        _diff_properties(report, paths, goldens)
        b1, _m1 = _diff_mutations(report, paths, temp)
        _diff_refusals(report, paths, b1, temp)
    finally:
        for path in temp:
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 4d. re-check queue - which stored evidence to re-read
#
# stale_evidence.py reads records that are already on disk and lists the evidence an
# agent should re-read, most urgent first. It fetches nothing and changes no record or
# score, so nothing here touches score_version (INV-NEW-stale). The reason codes are
# references/evidence-policy.md section 5.6.
# ---------------------------------------------------------------------------
RECHECK_SCRIPTS = ["stale_evidence.py"]
RECHECK_KIND = "recheck-queue"


def _recheck_refusal(report, args, expect, label, code_expected=1):
    """One refusal path: the expected exit, exactly one ERROR: line, empty stdout."""
    code, out, err = run_script("stale_evidence.py", args)
    error_lines = [ln for ln in err.splitlines() if ln.startswith("ERROR:")]
    problems = []
    if code != code_expected:
        problems.append("exit %d, expected %d" % (code, code_expected))
    if len(error_lines) != 1:
        problems.append("%d 'ERROR:' lines, R7.3.3 requires exactly 1" % len(error_lines))
    if "Traceback (most recent call last)" in err:
        problems.append("raw traceback escaped main()")
    if out.strip():
        problems.append("stdout is not empty")
    if not any(expect in ln for ln in error_lines):
        problems.append("no ERROR line carries %r; got %s" % (expect, error_lines[:1]))
    report.check("recheck: %s" % label, not problems, "\n".join(problems))


def _recheck_entry(doc, record_id):
    for entry in doc.get("queue", []):
        if entry.get("record_id") == record_id:
            return entry
    return None


def _recheck_items(entry):
    return dict((i["evidence_id"], i) for i in (entry or {}).get("items", []))


def _recheck_keys(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            _recheck_keys(value, out)
    elif isinstance(node, list):
        for value in node:
            _recheck_keys(value, out)
    return out


def _recheck_run(args):
    code, out, err = run_script("stale_evidence.py", list(args))
    doc = None
    if code == 0:
        try:
            doc = json.loads(out)
        except ValueError:
            doc = None
    return code, out, err, doc


def _recheck_bundle(entity, records, temp, extra=None):
    body = {"schema_version": "0.1.0", "as_of": AS_OF, "entity": entity, "records": records}
    body.update(extra or {})
    path = _write_temp_text(json.dumps(body, ensure_ascii=False), "kbtm-recheck-", ".json")
    temp.append(path)
    return path


def _recheck_goldens(report):
    """Golden bytes, INV-13, validation and the fixed semantics of both golden queues."""
    docs = {}
    for label, fixture, expected_name in (
            ("buyer", "buyers.golden.json", "recheck.buyers.golden.expected.json"),
            ("seller", "sellers.golden.json", "recheck.sellers.golden.expected.json")):
        args = ["--input", os.path.join(FIXTURES, fixture), "--as-of", AS_OF, "--pretty"]
        code, out, err = run_script("stale_evidence.py", args)
        report.check("recheck: stale_evidence.py (%s bundle) exits 0" % label, code == 0,
                     "exit %d\n%s" % (code, err.strip()[:400]))
        golden = _read_text(os.path.join(EXPECTED, expected_name))
        report.check("recheck: the %s queue matches its golden byte for byte" % label,
                     out == golden, "generated queue differs from %s" % expected_name)
        code2, out2, _err = run_script("stale_evidence.py", args)
        report.check("recheck: INV-13 the %s queue is byte-identical on a re-run" % label,
                     code2 == 0 and out2 == out, "second run differed")
        code, vout, verr = run_script("validate_output.py", [
            "--input", os.path.join(EXPECTED, expected_name), "--schema", RECHECK_KIND,
            "--invariants", "--strict"])
        report.check("recheck: the %s queue passes validate_output.py --strict" % label,
                     code == 0, "exit %d\n%s%s" % (code, vout[:300], verr[:300]))
        try:
            docs[label] = json.loads(out)
        except ValueError:
            docs[label] = {"queue": [], "summary": {}, "notes": []}
    code, vout, _verr = run_script("validate_output.py", [
        "--input", os.path.join(EXPECTED, "recheck.buyers.golden.expected.json"),
        "--schema", "auto", "--invariants", "--strict", "--json"])
    report.check("recheck: validate_output.py --schema auto routes a queue to recheck-queue",
                 code == 0 and '"schema":"%s"' % RECHECK_KIND in vout.replace(" ", ""),
                 "exit %d\n%s" % (code, vout[:300]))

    buyers, sellers = docs["buyer"], docs["seller"]
    north = _recheck_entry(buyers, "BUY-northgateimport-example")
    report.check("recheck: an unreachable buyer is queued first with site_unreachable",
                 bool(north) and north["position"] == 1
                 and north["record_reasons"] == ["site_unreachable"]
                 and north["priority_reason"] == "site_unreachable",
                 "got %s" % str(north and (north["position"], north["record_reasons"])))
    north_items = _recheck_items(north)
    report.check("recheck: evidence older than the stale threshold is past_stale_threshold, "
                 "and a non-material item ranks after every material one",
                 north_items.get("EV-001", {}).get("reasons") == ["past_stale_threshold"]
                 and [i["material"] for i in (north or {}).get("items", [])][-1] is False
                 and all(i["material"] for i in (north or {}).get("items", [])[:-1]),
                 "items: %s" % [(i["evidence_id"], i["material"], i["reasons"])
                                for i in (north or {}).get("items", [])])
    pale = _recheck_entry(buyers, "BUY-palefade-example")
    pale_items = _recheck_items(pale)
    report.check("recheck: a record flagged stale is record_flagged_stale and its flagged "
                 "items are source_flagged_stale",
                 bool(pale) and pale["record_reasons"] == ["record_flagged_stale"]
                 and "source_flagged_stale" in pale_items.get("EV-001", {}).get("reasons", []),
                 "got %s" % str(pale and pale["record_reasons"]))
    kanto = _recheck_entry(buyers, "BUY-kantoimport-example")
    kanto_items = _recheck_items(kanto)
    report.check("recheck: an unresolved conflict queues both sides, and a claim with a "
                 "current item gets no age reason",
                 bool(kanto) and kanto["priority_reason"] == "unresolved_conflict"
                 and kanto_items.get("EV-003", {}).get("reasons") == ["unresolved_conflict"]
                 and kanto_items.get("EV-003", {}).get("conflicts_with") == ["EV-002"]
                 and kanto_items.get("EV-002", {}).get("reasons") == ["unresolved_conflict"]
                 and kanto["claims"] == [],
                 "got %s" % str(kanto and [(i["evidence_id"], i["reasons"]) for i in kanto["items"]]))
    absent = [rid for rid in ("BUY-pacificglowdist-example", "BUY-dunesourcing-example",
                              "BUY-marinaretail-example", "BUY-gulfglow-example")
              if _recheck_entry(buyers, rid)]
    report.check("recheck: a resolved conflict, a superseded old item, fresh evidence and an "
                 "undated item read recently queue nothing", not absent,
                 "queued anyway: %s" % absent)
    report.check("recheck: undated items read recently are counted and named in notes[]",
                 buyers.get("summary", {}).get("evidence_items_undated") == 1
                 and any("undated evidence item(s) were not queued" in n
                         for n in buyers.get("notes", [])),
                 "summary %s" % buyers.get("summary"))

    sopoong = _recheck_entry(sellers, "SEL-sopoongworks-example")
    report.check("recheck: an unreachable seller is queued first",
                 bool(sopoong) and sopoong["position"] == 1
                 and sopoong["record_reasons"] == ["site_unreachable"],
                 "got %s" % str(sopoong and (sopoong["position"], sopoong["record_reasons"])))
    areum = _recheck_entry(sellers, "SEL-areumfactory-example")
    report.check("recheck: a seller flagged stale is record_flagged_stale",
                 bool(areum) and areum["record_reasons"] == ["record_flagged_stale"],
                 "got %s" % str(areum and areum["record_reasons"]))
    yeonhwa = _recheck_entry(sellers, "SEL-yeonhwalab-example")
    claims = dict((c["claim"], c) for c in (yeonhwa or {}).get("claims", []))
    cert = claims.get("certifications", {})
    report.check("recheck: a material claim resting only on aging evidence is "
                 "no_current_evidence",
                 bool(yeonhwa) and yeonhwa["priority_reason"] == "aging"
                 and cert.get("reason") == "no_current_evidence"
                 and cert.get("newest_source_date") == "2024-11-20"
                 and cert.get("evidence_ids") == ["EV-305"]
                 and cert.get("counts") == {"old": 1, "undated": 0, "flagged": 0},
                 "got %s" % claims)
    report.check("recheck: a seller whose undated items were read recently is not queued",
                 _recheck_entry(sellers, "SEL-hanbitcos-example") is None,
                 "SEL-hanbitcos-example was queued")

    leaked = set()
    for doc in (buyers, sellers):
        leaked |= _recheck_keys(doc, set()) & {"value", "quote_or_summary", "contact_channels",
                                               "observed_at", "notes_internal"}
    report.check("recheck: the queue copies no value, quote_or_summary or contact channel",
                 not leaked, "leaked keys: %s" % sorted(leaked))
    return buyers


def _recheck_inputs(report, buyers, temp):
    """Every accepted input shape, and the date-driven behaviour."""
    buyers_path = os.path.join(FIXTURES, "buyers.golden.json")
    code, _out, err, later = _recheck_run(["--input", buyers_path, "--as-of", "2027-09-12"])
    gulf = _recheck_items(_recheck_entry(later or {}, "BUY-gulfglow-example"))
    report.check("recheck: --as-of alone drives age (a year later EV-004 is aging and the "
                 "undated EV-008 is due by its last reading)",
                 code == 0 and gulf.get("EV-004", {}).get("reasons") == ["aging"]
                 and gulf.get("EV-008", {}).get("reasons") == ["undated"]
                 and gulf.get("EV-008", {}).get("days_since_read") == 367,
                 "exit %d %s\n%s" % (code, err.strip()[:200],
                                     dict((k, v.get("reasons")) for k, v in gulf.items())))

    bundle = read_json(buyers_path)
    gulf_record = [r for r in bundle["records"] if r["buyer_id"] == "BUY-gulfglow-example"][0]
    for item in gulf_record["evidence"]:
        if item["evidence_id"] == "EV-008":
            item["observed_at"] = "2025-01-10T09:00:00Z"
    path = _recheck_bundle("buyer", [gulf_record], temp)
    code, _out, err, doc = _recheck_run(["--input", path, "--as-of", AS_OF])
    entry = _recheck_entry(doc or {}, "BUY-gulfglow-example")
    claims = dict((c["claim"], c) for c in (entry or {}).get("claims", []))
    report.check("recheck: an undated item last read long ago is queued as undated",
                 code == 0 and bool(entry) and entry["priority_reason"] == "undated"
                 and claims.get("contact_channels", {}).get("newest_source_date") == "unknown"
                 and claims.get("contact_channels", {}).get("counts", {}).get("undated") == 1,
                 "exit %d %s\n%s" % (code, err.strip()[:200], entry))

    code, scored, err = run_script("score_buyer.py", [
        "--input", buyers_path,
        "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"), "--as-of", AS_OF])
    scored_path = _write_temp_text(scored, "kbtm-recheck-scored-", ".json")
    temp.append(scored_path)
    code, _out, err, doc = _recheck_run(["--input", scored_path, "--as-of", AS_OF])
    golden_ids = set(e["record_id"] for e in buyers.get("queue", []))
    got_ids = set(e["record_id"] for e in (doc or {}).get("queue", []))
    report.check("recheck: a scored discovery-result is accepted and notes its excluded[]",
                 code == 0 and bool(got_ids) and got_ids <= golden_ids
                 and any("excluded candidate(s) carry no evidence" in n
                         for n in (doc or {}).get("notes", [])),
                 "exit %d %s\n%s" % (code, err.strip()[:200], sorted(got_ids)))

    match_input = os.path.join(FIXTURES, "match-134.input.json")
    code, _out, err, doc = _recheck_run(["--input", match_input, "--as-of", AS_OF])
    report.check("recheck: a match input is accepted and its RFQ is scanned too",
                 code == 0 and (doc or {}).get("summary", {}).get("records_scanned")
                 == len(read_json(match_input)["records"]) + 1,
                 "exit %d %s" % (code, err.strip()[:200]))

    rfq = read_json(os.path.join(FIXTURES, "rfq.134.json"))
    rfq["stale"] = True
    rfq["operational_status"] = "unreachable"
    for item in rfq.get("evidence", [])[:1]:
        item["source_date"] = "2023-01-05"
    path = _write_temp_text(json.dumps(rfq, ensure_ascii=False), "kbtm-recheck-rfq-", ".json")
    temp.append(path)
    code, _out, err, doc = _recheck_run(["--input", path, "--as-of", AS_OF])
    queue = (doc or {}).get("queue", [])
    report.check("recheck: an RFQ is classified as an RFQ (its rfq_id, not its buyer_id) and "
                 "takes no record-level reason",
                 code == 0 and len(queue) == 1 and queue[0]["entity"] == "rfq"
                 and queue[0]["record_id"] == rfq["rfq_id"] and queue[0]["record_reasons"] == [],
                 "exit %d %s\n%s" % (code, err.strip()[:200], queue))

    code, _out, err, doc = _recheck_run([
        "--input", os.path.join(FIXTURES, "sellers.golden.json"), "--as-of", AS_OF, "--top", "1"])
    report.check("recheck: --top lists the first N and the summary still counts all",
                 code == 0 and len((doc or {}).get("queue", [])) == 1
                 and doc["summary"]["records_listed"] == 1
                 and doc["summary"]["records_queued"] == 3
                 and doc["queue"][0]["record_id"] == "SEL-sopoongworks-example",
                 "exit %d %s" % (code, err.strip()[:200]))

    old = read_json(buyers_path)
    for record in old["records"]:
        record["score_version"] = "kbtm-score-0.0.9"
    path = _recheck_bundle("buyer", old["records"], temp)
    code, _out, err, doc = _recheck_run(["--input", path, "--as-of", AS_OF])
    report.check("recheck: records scored under an older rubric are queued, not refused",
                 code == 0 and len(doc["queue"]) == len(buyers.get("queue", []))
                 and any("kbtm-score-0.0.9" in n for n in doc["notes"]),
                 "exit %d %s" % (code, err.strip()[:200]))

    path = _recheck_bundle("buyer", [], temp)
    code, _out, err, doc = _recheck_run(["--input", path, "--as-of", AS_OF])
    report.check("recheck: an empty records[] is an empty queue, exit 0",
                 code == 0 and doc["queue"] == []
                 and not any(doc["summary"]["by_reason"].values()),
                 "exit %d %s" % (code, err.strip()[:200]))

    out_path = _write_temp_text("", "kbtm-recheck-out-", ".json")
    temp.append(out_path)
    code, out, err = run_script("stale_evidence.py", [
        "--input", buyers_path, "--as-of", AS_OF, "--pretty", "--output", out_path])
    report.check("recheck: --output writes the same bytes and leaves stdout empty",
                 code == 0 and not out.strip() and _read_text(out_path) == _read_text(
                     os.path.join(EXPECTED, "recheck.buyers.golden.expected.json")),
                 "exit %d %s" % (code, err.strip()[:200]))


def _recheck_refusals(report, temp):
    buyers_path = os.path.join(FIXTURES, "buyers.golden.json")
    code, match_out, _err = run_script("score_match.py", [
        "--input", os.path.join(FIXTURES, "match-134.input.json"), "--as-of", AS_OF])
    match_path = _write_temp_text(match_out, "kbtm-recheck-match-", ".json")
    temp.append(match_path)
    _recheck_refusal(report, ["--input", match_path, "--as-of", AS_OF],
                     "match-result cannot be aged", "refuses a match-result")
    _recheck_refusal(report, ["--input", os.path.join(
        EXPECTED, "acceptance.buyers.uae.expected.json"), "--as-of", AS_OF],
        "is a report, not a record document", "refuses an acceptance report")

    bundle = read_json(buyers_path)
    twice = [bundle["records"][0], bundle["records"][0]]
    _recheck_refusal(report, ["--input", _recheck_bundle("buyer", twice, temp),
                              "--as-of", AS_OF],
                     "appears twice", "refuses a duplicate record id")
    anonymous = dict(bundle["records"][0])
    anonymous.pop("buyer_id")
    path = _write_temp_text(json.dumps([anonymous]), "kbtm-recheck-anon-", ".json")
    temp.append(path)
    _recheck_refusal(report, ["--input", path, "--as-of", AS_OF],
                     "has no buyer_id/seller_id/rfq_id", "refuses a record without an id")
    _recheck_refusal(report, ["--input", buyers_path, "--as-of", "2026-09-01"],
                     "INV-24", "refuses an --as-of earlier than an observed_at (INV-24)")

    _recheck_refusal(report, ["--input", buyers_path], "needs --as-of",
                     "a missing --as-of is a usage error (exit 2)", 2)
    _recheck_refusal(report, ["--input", buyers_path, "--as-of", "2026-13-40"],
                     "2026-13-40", "a malformed --as-of is a usage error (exit 2)", 2)
    _recheck_refusal(report, ["--input", buyers_path, "--as-of", AS_OF, "--top", "0"],
                     "--top", "--top 0 is a usage error (exit 2)", 2)
    config = read_json(os.path.join(SCHEMA_DIR, "scoring.config.json"))
    for bucket in config["evidence"]["recency_buckets"]:
        if bucket["label"] == "aging":
            bucket["label"] = "older"
    config_path = _write_temp_text(json.dumps(config), "kbtm-recheck-config-", ".json")
    temp.append(config_path)
    _recheck_refusal(report, ["--input", buyers_path, "--as-of", AS_OF,
                              "--config", config_path],
                     "no 'aging' bucket", "a config without the due bucket is a config "
                     "error (exit 2)", 2)

    handle, bad_path = tempfile.mkstemp(suffix=".json", prefix="kbtm-recheck-bad-")
    with os.fdopen(handle, "wb") as fh:
        fh.write(b'{"records": [{"company_name": "A\xff\xfeB"}]}')
    temp.append(bad_path)
    _recheck_refusal(report, ["--input", bad_path], "not valid UTF-8",
                     "a non-UTF-8 input is reported as such even without --as-of", 2)


def _recheck_ev(evidence_id, claim, age=None, **extra):
    """A minimal evidence item `age` days before AS_OF (None: undated), read 2 days ago."""
    import datetime  # local: only this phase does date arithmetic, and only from AS_OF
    as_of = datetime.date(*[int(p) for p in AS_OF.split("-")])
    item = {"evidence_id": evidence_id, "claim": claim,
            "source_url": "https://edge.example/%s" % evidence_id.lower(),
            "source_date": "unknown" if age is None
            else (as_of - datetime.timedelta(days=age)).isoformat(),
            "observed_at": "2026-09-10T09:00:00Z"}
    item.update(extra)
    return dict((k, v) for k, v in item.items() if v is not None)


def _recheck_edges(report, temp):
    """Bucket and threshold edges, tie-breaks, bounded notes and odd input shapes."""
    def run(records, *extra_args):
        path = _recheck_bundle("buyer", records, temp)
        return _recheck_run(["--input", path, "--as-of", AS_OF] + list(extra_args))

    edge = {"buyer_id": "BUY-edge-example", "evidence": [
        _recheck_ev("EV-730", "company_type", 730),
        _recheck_ev("EV-731", "product_categories", 731),
        _recheck_ev("EV-365", "korean_products_signal", 365),
        _recheck_ev("EV-366", "wholesale_signal", 366),
        _recheck_ev("EV-UNREAD", "partnership_signal", None, observed_at=None),
        _recheck_ev("EV-UFLAG", "sourcing_intent", None, stale=True),
        _recheck_ev("EV-CFLAG", "country", 10, stale=True),
        _recheck_ev("EV-COLD", "country", 800),
    ]}
    code, _out, err, doc = run([edge])
    entry = _recheck_entry(doc or {}, "BUY-edge-example")
    items = dict((k, v["reasons"]) for k, v in _recheck_items(entry).items())
    claims = dict((c["claim"], c) for c in (entry or {}).get("claims", []))
    report.check("recheck: age 730 (= stale_threshold_days) is aging, 731 is "
                 "past_stale_threshold; 365 is current and 366 is aging",
                 code == 0 and items.get("EV-730") == ["aging"]
                 and items.get("EV-731") == ["past_stale_threshold"]
                 and "EV-365" not in items and "korean_products_signal" not in claims
                 and items.get("EV-366") == ["aging"],
                 "exit %d %s\n%s" % (code, err.strip()[:200], items))
    report.check("recheck: an undated item with no observed_at is queued as undated, and a "
                 "flagged undated item is source_flagged_stale only",
                 items.get("EV-UNREAD") == ["undated"]
                 and items.get("EV-UFLAG") == ["source_flagged_stale"],
                 "items %s" % items)
    report.check("recheck: claim counts.old counts only items in a due bucket",
                 claims.get("country", {}).get("counts") == {"old": 1, "undated": 0,
                                                              "flagged": 1},
                 "country %s" % claims.get("country"))

    def rec(record_id, *ages):
        keys = ["company_type", "product_categories", "country"]
        return {"buyer_id": record_id, "evidence": [
            _recheck_ev("EV-%d" % n, keys[n], age) for n, age in enumerate(ages)]}
    code, _out, err, doc = run([rec("BUY-tie-a", 400), rec("BUY-tie-b", 400, 400),
                                rec("BUY-tie-c", 500), rec("BUY-tie-d", 600)])
    order = [e["record_id"] for e in (doc or {}).get("queue", [])]
    report.check("recheck: records tied on priority rank by more claims without current "
                 "evidence, then by the oldest queued item",
                 order == ["BUY-tie-b", "BUY-tie-d", "BUY-tie-c", "BUY-tie-a"],
                 "exit %d %s\norder %s" % (code, err.strip()[:200], order))

    long_claim = "c" * 200
    code, _out, err, doc = run([{"buyer_id": "BUY-long-example", "company_name": "N" * 301,
                                 "evidence": [_recheck_ev("EV-1", long_claim, 800)]}])
    entry = _recheck_entry(doc or {}, "BUY-long-example") or {}
    report.check("recheck: a 200-character claim (the evidence schema's limit) is queued, and "
                 "an over-long company_name is left out instead of failing the run",
                 code == 0 and [i["claim"] for i in entry.get("items", [])] == [long_claim]
                 and "company_name" not in entry,
                 "exit %d %s" % (code, err.strip()[:300]))
    path = _recheck_bundle("buyer", [{"buyer_id": "BUY-long-example", "evidence": [
        _recheck_ev("EV-1", "c" * 201, 800)]}], temp)
    _recheck_refusal(report, ["--input", path, "--as-of", AS_OF], "longer than the 200",
                     "refuses a claim longer than the input schema allows")

    # Worst case for note length: 128-character ids (the input schemas' limit), more than
    # ten offenders of each kind, and many distinct over-long score_versions.
    many = [{"buyer_id": "BUY-" + "r" * 121 + "%03d" % r,
             "score_version": "kbtm-score-%03d" % r + "9" * 1200,
             "evidence": [_recheck_ev("E" * 125 + "%03d" % n, "company_type", None,
                                      source_date="March 2024" if n else "x" * 1200)
                          for n in range(8)]} for r in range(40)]
    many.extend({"buyer_id": "BUY-" + "s" * 121 + "%03d" % r, "evidence": "not-a-list"}
                for r in range(12))
    code, _out, err, doc = run(many)
    notes = (doc or {}).get("notes", [])
    report.check("recheck: 320 unreadable source_dates, 12 non-list evidence values and "
                 "over-long echoed values make one note of at most 1000 characters per kind",
                 code == 0 and len(notes) <= 10 and all(len(n) <= 1000 for n in notes)
                 and sum("320 evidence item(s) carry a source_date that is not a date" in n
                         for n in notes) == 1
                 and sum("12 record(s) carry an evidence value that is not a list" in n
                         for n in notes) == 1,
                 "exit %d %s\nnote lengths %s" % (code, err.strip()[:300],
                                                  [len(n) for n in notes]))

    base = {"evidence": [_recheck_ev("EV-1", "company_type", 800)]}
    for label, value in (("missing", None), ("integer", 7), ("blank", " ")):
        record = dict(base, buyer_id=value) if value is not None else dict(base)
        path = _recheck_bundle("buyer", [record, dict(record)], temp)
        _recheck_refusal(report, ["--input", path, "--as-of", AS_OF], "no usable buyer_id",
                         "refuses a %s buyer_id even when the bundle names the entity" % label)

    odd = {"buyer_id": "BUY-odd-example", "conflicts": [{"field": ["company_type"]}],
           "evidence": [_recheck_ev("EV-1", "company_type", 10, conflicts_with="EV-9")]}
    code, _out, err, doc = run([odd])
    odd_items = _recheck_items(_recheck_entry(doc or {}, "BUY-odd-example"))
    report.check("recheck: a non-string conflicts[].field is ignored, and a string "
                 "conflicts_with is named on the item it flags",
                 code == 0 and odd_items.get("EV-1", {}).get("reasons") == ["unresolved_conflict"]
                 and odd_items.get("EV-1", {}).get("conflicts_with") == ["EV-9"],
                 "exit %d %s\n%s" % (code, err.strip()[:200], odd_items))
    _recheck_refusal(report, ["--input", os.path.join(FIXTURES, "buyers.golden.json"),
                              "--as-of", AS_OF, "--top", "²"],
                     "--top", "--top with a non-ASCII digit is a usage error (exit 2)", 2)


def _recheck_isolation(report):
    """INV-NEW-stale: no scoring path reads the re-check tool or its document."""
    names = ["score_buyer.py", "score_seller.py", "score_match.py", "normalize_company.py",
             "dedupe_companies.py", "_common.py"]
    hits = []
    for name in names:
        text = _read_text(os.path.join(SCRIPT_DIR, name))
        for token in ("stale_evidence", "DUE_FROM_BUCKET", "REASON_ORDER"):
            if token in text:
                hits.append("%s mentions %s" % (name, token))
        if name != "_common.py" and RECHECK_KIND in text:
            hits.append("%s mentions %s" % (name, RECHECK_KIND))
    report.check("recheck: INV-NEW-stale no scoring path reads the re-check tool", not hits,
                 "\n".join(hits))


def phase_recheck(report, allow_missing):
    absent = [n for n in RECHECK_SCRIPTS if not os.path.isfile(os.path.join(SCRIPT_DIR, n))]
    if absent or missing_scripts():
        note = "scripts absent: %s" % ", ".join(absent or missing_scripts())
        if allow_missing:
            report.skip("recheck: re-check queue cases", note)
            return
        report.fail("recheck: re-check queue cases", note)
        return
    temp = []
    try:
        buyers = _recheck_goldens(report)
        _recheck_inputs(report, buyers, temp)
        _recheck_refusals(report, temp)
        _recheck_edges(report, temp)
        _recheck_isolation(report)
    finally:
        for path in temp:
            try:
                os.unlink(path)
            except OSError:
                pass


EXPORT_SCRIPTS = ["export_leads.py"]
#: The 25 keys of tradewith-api BulkBuyerRowDto; an export row may use a subset only.
TRADEWITH_DTO_KEYS = frozenset([
    "companyName", "country", "city", "website", "logoUrl", "imageUrl", "altWebsites",
    "contactEmail", "contactName", "contactPhone", "industry", "category", "productsSummary",
    "originalSource", "sourceUrl", "postedDate", "notes", "extraNotes", "social",
    "scaleRevenue", "displayFlag", "qualityTierLabel", "hsCodes", "annualVolumeUsd",
    "sourceId"])
#: Keys the exporter must never fill (INV-31, the tier-C decision, the notes decision,
#: and the no-contact-field decision: even a role mailbox never fills contactEmail).
TRADEWITH_NEVER_KEYS = ("contactName", "contactEmail", "contactPhone", "notes", "extraNotes",
                        "qualityTierLabel", "hsCodes")


def _expected_source_id(record):
    """The design's sourceId rule, restated independently of export_leads.py."""
    domain = record.get("canonical_domain")
    if isinstance(domain, str) and domain.strip() and domain != "unknown":
        host = domain.strip().lower()
        while host.startswith("www."):
            host = host[4:]
        return "kbtm:" + host
    return "kbtm:id:" + record["buyer_id"]


def _admin_ui_parse(text):
    """tradewith-admin buyers/import/page.tsx parseCSV + mapRowToPayload, transcribed.

    The page splits on newlines, strips quotes from the header, toggles on every double
    quote inside a line, trims every cell and keeps only non-empty known keys.
    """
    lines = [ln for ln in re.split(r"\r?\n", text) if ln.strip()]
    if not lines:
        return []
    headers = [re.sub(r'^"|"$', "", h.strip()) for h in lines[0].split(",")]
    known = ("companyName", "country", "city", "website", "contactEmail", "contactName",
             "contactPhone", "industry", "sourceId")
    rows = []
    for line in lines[1:]:
        cells, current, in_quote = [], "", False
        for ch in line:
            if ch == '"':
                in_quote = not in_quote
            elif ch == "," and not in_quote:
                cells.append(current.strip())
                current = ""
            else:
                current += ch
        cells.append(current.strip())
        obj = dict((h, (cells[i] if i < len(cells) else "").strip())
                   for i, h in enumerate(headers))
        rows.append(dict((k, obj[k]) for k in known if obj.get(k)))
    return rows


def _export_refusal(report, args, code_expected, expect, label, out_path=None):
    """One export refusal: the exit code, one ERROR: line, empty stdout, no file."""
    full = list(args) + (["--output", out_path] if out_path else [])
    code, out, err = run_script("export_leads.py", full)
    error_lines = [ln for ln in err.splitlines() if ln.startswith("ERROR:")]
    problems = []
    if code != code_expected:
        problems.append("exit %d, expected %d" % (code, code_expected))
    if len(error_lines) != 1:
        problems.append("%d 'ERROR:' lines, R7.3.3 requires exactly 1" % len(error_lines))
    if "Traceback (most recent call last)" in err:
        problems.append("raw traceback escaped main()")
    if out.strip():
        problems.append("stdout is not empty")
    if out_path and os.path.exists(out_path):
        problems.append("--output file was created")
    if not any(expect in ln for ln in error_lines):
        problems.append("no ERROR line carries %r; got %s" % (expect, error_lines[:1]))
    report.check("export: refuses %s (exit %d)" % (label, code_expected), not problems,
                 "\n".join(problems))


def _export_variant(base, temp, mutate, prefix="kbtm-export-"):
    document = json.loads(base)
    mutate(document)
    path = _write_temp_json(document, prefix)
    temp.append(path)
    return path


def _record(document, record_id):
    for record in document["records"]:
        if record.get("buyer_id") == record_id or record.get("seller_id") == record_id:
            return record
    raise KeyError(record_id)


def phase_export(report, allow_missing):
    absent = [n for n in EXPORT_SCRIPTS if not os.path.isfile(os.path.join(SCRIPT_DIR, n))]
    if absent or missing_scripts():
        note = "scripts absent: %s" % ", ".join(absent or missing_scripts())
        if allow_missing:
            report.skip("export: lead export cases", note)
            return
        report.fail("export: lead export cases", note)
        return

    temp = []
    try:
        code, buyers_out, err = run_script("score_buyer.py", [
            "--input", os.path.join(FIXTURES, "buyers.golden.json"),
            "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"),
            "--as-of", AS_OF, "--pretty"])
        if code != 0:
            report.fail("export: the UAE buyer run is scoreable", err.strip()[:400])
            return
        code, sellers_out, err = run_script("score_seller.py", [
            "--input", os.path.join(FIXTURES, "sellers.golden.json"),
            "--query", os.path.join(FIXTURES, "query-seller-sunscreen-oem.json"),
            "--as-of", AS_OF, "--pretty"])
        if code != 0:
            report.fail("export: the sunscreen seller run is scoreable", err.strip()[:400])
            return
        buyers_path = _write_temp_text(buyers_out, "kbtm-export-buyers-", ".json")
        sellers_path = _write_temp_text(sellers_out, "kbtm-export-sellers-", ".json")
        temp.extend([buyers_path, sellers_path])
        buyers_doc = json.loads(buyers_out)
        by_id = dict((r["buyer_id"], r) for r in buyers_doc["records"])
        by_source = dict((_expected_source_id(r), r) for r in buyers_doc["records"]
                         if r.get("qualified") is True)

        # --- X1-X3, X4: goldens, byte for byte, and a rerun is identical (INV-13) --------
        goldens = [
            ("X1 buyer csv", [buyers_path], "export.buyers.uae.csv"),
            ("X2 buyer tradewith-json", [buyers_path, "--format", "tradewith-json",
                                         "--pretty"], "export.buyers.uae.tradewith.json"),
            ("X3 seller csv", [sellers_path], "export.sellers.csv"),
            ("X4 buyer tradewith-csv", [buyers_path, "--format", "tradewith-csv"],
             "export.buyers.uae.tradewith.csv"),
        ]
        outputs = {}
        for label, args, golden in goldens:
            code, out, err = run_script("export_leads.py",
                                        ["--input"] + args + ["--as-of", AS_OF])
            report.check("export: %s exits 0" % label, code == 0,
                         "exit %d\n%s" % (code, err.strip()[:400]))
            report.check("export: %s matches %s byte for byte" % (label, golden),
                         out == _read_text(os.path.join(EXPECTED, golden)),
                         "generated export differs from the expected fixture")
            code2, out2, _err2 = run_script("export_leads.py",
                                            ["--input"] + args + ["--as-of", AS_OF])
            report.check("export: %s is byte-stable across reruns (INV-13)" % label,
                         code2 == 0 and out2 == out, "the second run differs")
            outputs[label] = (out, err)

        buyer_csv, buyer_err = outputs["X1 buyer csv"]
        csv_rows = _csv_rows(buyer_csv)
        qualified = [r["buyer_id"] for r in buyers_doc["records"] if r.get("qualified") is True]
        report.check("export: X1 the default buyer csv holds exactly the qualified records, "
                     "in document order",
                     [r["record_id"] for r in csv_rows] == qualified and len(csv_rows) == 7,
                     "rows: %s" % [r["record_id"] for r in csv_rows])
        report.check("export: X1 csv is LF-only with a header row first",
                     "\r" not in buyer_csv and buyer_csv.startswith("record_id,entity_type,"),
                     "CR found or header missing")
        seller_csv, seller_err = outputs["X3 seller csv"]
        seller_rows = _csv_rows(seller_csv)
        seller_ids = [r["record_id"] for r in seller_rows]
        hwadam = [r for r in seller_rows if r["record_id"] == "SEL-hwadamglobal-example"]
        report.check("export: X3 seller csv skips the unreachable maker and keeps the "
                     "country-unknown one as the literal unknown",
                     len(seller_rows) == 12 and "SEL-sopoongworks-example" not in seller_ids
                     and hwadam and hwadam[0]["country"] == "unknown"
                     and "operational_status=1" in seller_err,
                     "rows=%d stderr=%s" % (len(seller_rows), seller_err.strip()[:200]))

        # --- X5: the TradeWith body is the DTO, minus the never-filled fields ---------
        body_text = outputs["X2 buyer tradewith-json"][0]
        try:
            body = json.loads(body_text)
        except ValueError:
            body = {}  # X2 already failed; report the rest instead of crashing
        sys.path.insert(0, SCRIPT_DIR)
        import _common  # noqa: E402 - the harness already imported it for validation
        schema = load_schema("tradewith-bulk-buyers")
        errors = _common.validate(body, schema)
        report.check("export: X5 the tradewith-json body passes its own schema", not errors,
                     "; ".join(str(e) for e in errors[:3]))
        rows = body.get("buyers", [])
        problems = []
        if set(body) != {"buyers"}:
            problems.append("top-level keys %s" % sorted(body))
        for row in rows:
            extra = set(row) - TRADEWITH_DTO_KEYS
            if extra:
                problems.append("%s: non-DTO keys %s" % (row.get("sourceId"), sorted(extra)))
            for key in TRADEWITH_NEVER_KEYS:
                if key in row:
                    problems.append("%s: carries %s" % (row.get("sourceId"), key))
            for key, value in row.items():
                if value is None or value == "" or value == "unknown" or value == []:
                    problems.append("%s: %s is an empty/unknown value" % (row.get("sourceId"), key))
                if isinstance(value, str) and _common.personal_data_hits(value):
                    problems.append("%s: %s trips the personal-data scan"
                                    % (row.get("sourceId"), key))
        report.check("export: X5 every TradeWith row is a DTO subset with no contact name, "
                     "email or phone, notes, tier label or HS codes, and no empty value",
                     rows and not problems, "\n".join(problems[:8]))
        report.check("export: X5 no '@' appears anywhere in the body (not even a role mailbox)",
                     "@" not in body_text, "an address reached the TradeWith body")
        report.check("export: X5 provenance and staleness ride in originalSource and sourceUrl",
                     all(row.get("originalSource", "").startswith("kbeauty-trade-matchmaker | ")
                         and ("record_id=%s | stale=false"
                              % by_source.get(row["sourceId"], {}).get("buyer_id"))
                         in row["originalSource"]
                         and row.get("sourceUrl", "").startswith("https://") for row in rows),
                     "originalSource/sourceUrl missing")

        # --- X6: sourceId is kbtm:<domain>, unique, and stable under a wider filter ----
        ids = [row["sourceId"] for row in rows]
        report.check("export: X6 sourceId is kbtm:<www-stripped canonical_domain> and unique",
                     ids == [_expected_source_id(by_id[i]) for i in qualified]
                     and len(set(ids)) == len(ids), "sourceIds: %s" % ids)
        # luminaglow.example and www.luminaglow.example are one sourceId: refused.
        _export_refusal(report, ["--input", buyers_path, "--format", "tradewith-json",
                                 "--include-unqualified"], 1, "share the TradeWith sourceId",
                        "two records on one www-stripped domain (duplicate sourceId)")
        deduped = _export_variant(buyers_out, temp, lambda d: d["records"].remove(
            _record(d, "BUY-www-luminaglow-example")))
        wide_code, wide_body, wide_err = run_script("export_leads.py", [
            "--input", deduped, "--format", "tradewith-json", "--include-unqualified"])
        wide_rows = json.loads(wide_body)["buyers"] if wide_code == 0 else []
        wide_same = dict((r["sourceId"], r) for r in wide_rows)
        report.check("export: X6 a wider export carries the same row for the same record",
                     wide_code == 0 and all(wide_same.get(row["sourceId"]) == row
                                            for row in rows),
                     "a row changed under --include-unqualified")

        # --- X4 continued: the admin import page reads the CSV as the JSON says ---------
        ui_rows = _admin_ui_parse(outputs["X4 buyer tradewith-csv"][0])
        projected = [dict((k, row[k]) for k in ("sourceId", "companyName", "country",
                                                "website", "industry")
                          if k in row) for row in rows]
        report.check("export: X4 the admin import page's parser reads the tradewith-csv "
                     "into exactly the tradewith-json rows", ui_rows == projected,
                     "parsed %s" % ui_rows[:1])
        tw_csv_err = outputs["X4 buyer tradewith-csv"][1]
        report.check("export: X4 tradewith-csv says on stderr that it drops provenance and "
                     "points at tradewith-json",
                     "note: tradewith-csv carries only" in tw_csv_err
                     and "7 row(s) lost" in tw_csv_err and "--format tradewith-json" in tw_csv_err,
                     tw_csv_err.strip()[:300])
        report.check("export: X4 tradewith-json prints no such note",
                     "note: tradewith-csv" not in outputs["X2 buyer tradewith-json"][1],
                     "note printed for tradewith-json")
        seller_tw = _export_variant(sellers_out, temp, lambda d: None)
        comma_path = _export_variant(buyers_out, temp, lambda d: _record(
            d, "BUY-gulfglow-example").update({"company_name": "Gulf Glow Trading, FZ-LLC"}))
        code, out, err = run_script("export_leads.py", ["--input", comma_path,
                                                        "--format", "tradewith-csv"])
        report.check("export: X4 a comma in a company name survives the admin page parser",
                     code == 0 and _admin_ui_parse(out)[0].get("companyName")
                     == "Gulf Glow Trading, FZ-LLC", "exit %d: %s" % (code, err.strip()[:200]))

        # --- X7: filters -----------------------------------------------------------
        code, wide_csv, wide_csv_err = run_script("export_leads.py", [
            "--input", buyers_path, "--include-unqualified"])
        wide_csv_rows = dict((r["record_id"], r) for r in _csv_rows(wide_csv))
        report.check("export: X7 --include-unqualified keeps every scored record except the "
                     "unreachable one", len(wide_csv_rows) == 18
                     and "BUY-northgateimport-example" not in wide_csv_rows,
                     "rows=%d" % len(wide_csv_rows))
        code, floor_csv, _e = run_script("export_leads.py", [
            "--input", buyers_path, "--min-score", "80"])
        report.check("export: X7 --min-score 80 keeps the five records at or above 80",
                     code == 0 and len(_csv_rows(floor_csv)) == 5,
                     "rows=%d" % len(_csv_rows(floor_csv)))
        report.check("export: X7 an absent list is 'unknown' and a verified-empty one 'none'",
                     wide_csv_rows.get("BUY-kantoimport-example", {}).get("product_categories")
                     == "unknown"
                     and wide_csv_rows.get("BUY-straitswholesale-example", {})
                     .get("contact_channels") == "none",
                     "kantoimport/straitswholesale literals wrong")
        excluded_ids = [e.get("id") for e in buyers_doc.get("excluded", [])]
        report.check("export: X7 excluded[] never appears in an export",
                     excluded_ids and not any(i in wide_csv for i in excluded_ids)
                     and not any(i in wide_body for i in excluded_ids),
                     "excluded ids: %s" % excluded_ids)

        # --- X8: a dropped dimension is not_applicable, not unknown -----------------
        dropped = _export_variant(buyers_out, temp, lambda d: _record(
            d, "BUY-gulfglow-example")["dimension_scores"].pop("market_relevance"))
        code, out, err = run_script("export_leads.py", ["--input", dropped, "--no-validate"])
        cell = [r for r in _csv_rows(out) if r["record_id"] == "BUY-gulfglow-example"]
        report.check("export: X8 a dropped market_relevance is the literal not_applicable",
                     code == 0 and cell and cell[0]["dim_market_relevance"] == "not_applicable",
                     "exit %d %s" % (code, err.strip()[:200]))

        # --- X9: the spreadsheet formula guard --------------------------------------
        formula = _export_variant(buyers_out, temp, lambda d: _record(
            d, "BUY-gulfglow-example").update(
                {"company_name": '=HYPERLINK("http://x.example")'}))
        code, out, err = run_script("export_leads.py", ["--input", formula])
        cell = [r for r in _csv_rows(out) if r["record_id"] == "BUY-gulfglow-example"]
        report.check("export: X9 a formula-lead company name is apostrophe-guarded in csv",
                     code == 0 and cell and cell[0]["company_name"].startswith("'="),
                     "exit %d" % code)
        code, out, err = run_script("export_leads.py", ["--input", formula,
                                                        "--format", "tradewith-json"])
        report.check("export: X9 the JSON body keeps the raw name (it is not a spreadsheet)",
                     code == 0 and json.loads(out)["buyers"][0]["companyName"]
                     == '=HYPERLINK("http://x.example")', "exit %d" % code)
        _export_refusal(report, ["--input", formula, "--format", "tradewith-csv"], 1,
                        "formula character", "a formula-lead name in tradewith-csv")

        # --- X10: only a company ROLE mailbox ever fills contactEmail ----------------
        def personal(d):
            _record(d, "BUY-gulfglow-example")["contact_channels"][1]["value"] = \
                "jane.doe@gulfglow.example"
            _record(d, "BUY-dunesourcing-example")["contact_channels"][1]["value"] = \
                "info@other-company.example"
            _record(d, "BUY-britsun-example")["contact_channels"][1]["value"] = \
                "sales@gmail.com"
        role_path = _export_variant(buyers_out, temp, personal)
        code, out, err = run_script("export_leads.py", ["--input", role_path,
                                                        "--format", "tradewith-json"])
        role_rows = dict((r["sourceId"], r) for r in json.loads(out)["buyers"]) \
            if code == 0 else {}
        report.check("export: X10 no address of any kind, role mailbox included, reaches a "
                     "TradeWith row",
                     code == 0 and len(role_rows) == 7 and "@" not in out
                     and all("contactEmail" not in row for row in role_rows.values()),
                     "exit %d %s" % (code, err.strip()[:200]))
        code, out, err = run_script("export_leads.py", ["--input", role_path])
        report.check("export: X10 the csv withholds those addresses and says so on stderr",
                     code == 0 and "jane.doe" not in out and "gmail.com" not in out
                     and "other-company" not in out and "withheld 3 corporate_email" in err,
                     "exit %d %s" % (code, err.strip()[:300]))
        report.check("export: X10 the csv keeps a real role mailbox on the company's domain",
                     code == 0 and "corporate_email=partners@luminaglow.example" in out,
                     "luminaglow's partners@ was lost")
        freemail = _export_variant(buyers_out, temp, lambda d: _record(
            d, "BUY-gulfglow-example").update({
                "canonical_domain": "gmail.com",
                "contact_channels": [{"type": "corporate_email", "value": "sales@gmail.com"}]}))
        code, out, err = run_script("export_leads.py", ["--input", freemail, "--no-validate"])
        report.check("export: X10 a role local part on a free-mail domain is withheld even "
                     "when that domain is the record's own canonical_domain",
                     code == 0 and "sales@gmail.com" not in out
                     and "withheld 1 corporate_email" in err,
                     "exit %d %s" % (code, err.strip()[:300]))

        # --- X11: usage refusals, exit 2 ----------------------------------------------
        match_code, match_out, _e = run_script("score_match.py", [
            "--input", os.path.join(FIXTURES, "match-134.input.json"), "--as-of", AS_OF])
        match_path = _write_temp_text(match_out, "kbtm-export-match-", ".json")
        temp.append(match_path)
        for args, expect, label in [
            (["--input", seller_tw, "--format", "tradewith-json"], "seller bulk-import",
             "a seller run as tradewith-json"),
            (["--input", seller_tw, "--format", "tradewith-csv"], "seller bulk-import",
             "a seller run as tradewith-csv"),
            (["--input", buyers_path, "--pretty"], "--pretty", "--pretty with csv"),
            (["--input", buyers_path, "--min-score", "101"], "--min-score", "--min-score 101"),
            (["--input", buyers_path, "--min-score", "abc"], "--min-score", "--min-score abc"),
            (["--input", buyers_path, "--as-of", "2026-13-01"], "", "a malformed --as-of"),
        ]:
            _export_refusal(report, args, 2, expect, label)

        # --- X12: data refusals, exit 1, nothing written ---------------------------------
        out_dir = tempfile.mkdtemp(prefix="kbtm-export-out-")
        try:
            target = os.path.join(out_dir, "leads.out")
            unscored = _export_variant(buyers_out, temp,
                                       lambda d: d.update({"score_version": "unscored"}))
            mixed = _export_variant(buyers_out, temp, lambda d: d["records"][1].update(
                {"score_version": "kbtm-score-0.0.9"}))
            dup = _export_variant(buyers_out, temp,
                                  lambda d: d["records"].append(dict(d["records"][0])))
            pii = _export_variant(buyers_out, temp, lambda d: _record(
                d, "BUY-gulfglow-example").update(
                    {"company_name": "Gulf Glow (jane.doe@x.example)"}))
            phone_url = _export_variant(buyers_out, temp, lambda d: _record(
                d, "BUY-gulfglow-example").update(
                    {"website": "https://gulfglow.example/tel=010-1234-5678"}))
            poisoned_domain = _export_variant(buyers_out, temp, lambda d: _record(
                d, "BUY-gulfglow-example").update({"canonical_domain": 42}))
            poisoned_list = _export_variant(buyers_out, temp, lambda d: _record(
                d, "BUY-gulfglow-example").update({"product_categories": {"a": 1}}))
            poisoned_channels = _export_variant(buyers_out, temp, lambda d: _record(
                d, "BUY-gulfglow-example").update({"contact_channels": [7]}))
            poisoned_record = _export_variant(buyers_out, temp,
                                              lambda d: d["records"].insert(0, 5))
            for args, expect, label in [
                (["--input", unscored, "--no-validate"], "not scored", "an unscored run"),
                (["--input", mixed, "--no-validate"], "rubrics", "a mixed score_version"),
                (["--input", dup], "appears twice", "a duplicated record id"),
                (["--input", pii, "--format", "tradewith-json"], "email",
                 "an address in a company name (tradewith-json)"),
                (["--input", pii], "email", "an address in a company name (csv)"),
                (["--input", phone_url, "--format", "tradewith-json"], "phone",
                 "a phone number inside a URL"),
                (["--input", poisoned_domain, "--no-validate", "--format", "tradewith-json"],
                 "canonical_domain", "a non-string canonical_domain under --no-validate"),
                (["--input", poisoned_list, "--no-validate"], "product_categories",
                 "a non-list product_categories under --no-validate"),
                (["--input", poisoned_channels, "--no-validate", "--format", "tradewith-json"],
                 "contact channel", "a non-object contact channel under --no-validate"),
                (["--input", poisoned_record, "--no-validate"], "JSON object",
                 "a non-object record under --no-validate"),
                (["--input", poisoned_domain], "fails its schema",
                 "a schema-invalid input"),
                # BUILD-CONTRACT 7.3: a document that parses but is the wrong kind is a
                # contract failure (exit 1), as in diff_runs.py and stale_evidence.py.
                (["--input", match_path], "match-result", "a match-result input"),
                (["--input", os.path.join(FIXTURES, "rfq.134.json")],
                 "not a discovery-result", "an RFQ document, which is not a scored run"),
            ]:
                _export_refusal(report, args, 1, expect, label, out_path=target)

            # --- X13: an empty export still exits 0 -------------------------------------
            code, out, err = run_script("export_leads.py", ["--input", buyers_path,
                                                            "--min-score", "100"])
            report.check("export: X13 an empty csv export is header-only with a WARNING",
                         code == 0 and out.count("\n") == 1
                         and "WARNING: no record passed the filters" in err, "exit %d" % code)
            code, out, err = run_script("export_leads.py", ["--input", buyers_path,
                                                            "--min-score", "100",
                                                            "--format", "tradewith-json"])
            report.check("export: X13 an empty tradewith-json export is {\"buyers\":[]}",
                         code == 0 and out == '{"buyers":[]}\n', "got %r" % out[:80])

            # --- X16: --output writes the file and leaves stdout empty ------------------
            code, out, err = run_script("export_leads.py", ["--input", buyers_path,
                                                            "--output", target])
            written = _read_text(target) if os.path.exists(target) else None
            report.check("export: X16 --output writes the file and stdout stays empty",
                         code == 0 and not out and written == buyer_csv, "exit %d" % code)
        finally:
            for name in os.listdir(out_dir):
                os.unlink(os.path.join(out_dir, name))
            os.rmdir(out_dir)

        # --- X14: the body-size warning -------------------------------------------------
        def bulk(d):
            template = _record(d, "BUY-gulfglow-example")
            d["records"] = []
            for n in range(260):
                copy = json.loads(json.dumps(template))
                copy["buyer_id"] = "BUY-bulk%03d-example" % n
                copy["company_name"] = "Bulk Account %03d Trading" % n
                copy["canonical_domain"] = "bulk%03d.example" % n
                d["records"].append(copy)
        bulk_path = _export_variant(buyers_out, temp, bulk)
        code, out, err = run_script("export_leads.py", ["--input", bulk_path, "--no-validate",
                                                        "--format", "tradewith-json"])
        report.check("export: X14 a body over TradeWith's JSON limit exits 0 with a WARNING",
                     code == 0 and "WARNING: the import body is" in err,
                     "exit %d %s" % (code, err.strip()[:200]))

        # --- X15: likely duplicates are named, not silently exported twice -----------
        report.check("export: X15 an undeduplicated pair is named in a WARNING",
                     "possible duplicate" in wide_csv_err
                     and "BUY-luminaglow-example" in wide_csv_err
                     and "BUY-www-luminaglow-example" in wide_csv_err,
                     wide_csv_err.strip()[:300])
        report.check("export: X15 the default (qualified-only) export warns of no duplicate",
                     "possible duplicate" not in buyer_err, buyer_err.strip()[:300])

        # --- X18: review follow-ups - each case fails if its guard is removed -------------
        def gulf(d):
            return _record(d, "BUY-gulfglow-example")

        def tw_rows(path, extra=()):
            code, out, err = run_script("export_leads.py", ["--input", path, "--format",
                                                            "tradewith-json"] + list(extra))
            rows = dict((r["originalSource"].split("record_id=")[1].split(" |")[0], r)
                        for r in json.loads(out)["buyers"]) if code == 0 else {}
            return code, rows, err

        no_country = _export_variant(buyers_out, temp, lambda d: gulf(d).update(
            {"country": "unknown"}))
        code, found, err = tw_rows(no_country)
        report.check("export: X18 a qualified buyer with country unknown is skipped and "
                     "counted in a TradeWith format",
                     code == 0 and len(found) == 6 and "BUY-gulfglow-example" not in found
                     and "country_unknown=1" in err, "exit %d %s" % (code, err.strip()[:200]))
        code, out, err = run_script("export_leads.py", ["--input", no_country])
        report.check("export: X18 the generic csv keeps that row with country=unknown",
                     code == 0 and any(r["record_id"] == "BUY-gulfglow-example"
                                       and r["country"] == "unknown" for r in _csv_rows(out)),
                     "exit %d" % code)

        quoted = _export_variant(buyers_out, temp, lambda d: gulf(d).update(
            {"company_name": 'Gulf "Glow" Trading'}))
        _export_refusal(report, ["--input", quoted, "--format", "tradewith-csv"], 1,
                        "double quote", "a double quote in a name (tradewith-csv)")

        partial = _export_variant(buyers_out, temp, lambda d: d.update({"partial": True}))
        code, out, err = run_script("export_leads.py", ["--input", partial])
        report.check("export: X18 a partial input run exports with a WARNING",
                     code == 0 and "WARNING: the input run is partial" in err,
                     "exit %d %s" % (code, err.strip()[:200]))

        member = _export_variant(buyers_out, temp, lambda d: gulf(d)["contact_channels"].append(
            {"type": "linkedin", "value": "https://www.linkedin.com/in/jane-kim-5b1a2"}))
        code, found, err = tw_rows(member)
        report.check("export: X18 a LinkedIn member profile never becomes social and is "
                     "counted on stderr",
                     code == 0 and "social" not in found.get("BUY-gulfglow-example", {"social": 1})
                     and "withheld 1 linkedin" in err,
                     "exit %d %s" % (code, err.strip()[:200]))
        code, out, err = run_script("export_leads.py", ["--input", member])
        report.check("export: X18 the generic csv withholds the member profile too",
                     code == 0 and "/in/jane-kim" not in out and "withheld 1 linkedin" in err,
                     "exit %d %s" % (code, err.strip()[:200]))

        messenger = _export_variant(buyers_out, temp, lambda d: gulf(d)["contact_channels"]
                                    .append({"type": "messenger", "value": "+971 4 555 0111"}))
        code, out, err = run_script("export_leads.py", ["--input", messenger])
        report.check("export: X18 an official messenger number is company-level like a "
                     "switchboard and exports",
                     code == 0 and "messenger=+971 4 555 0111" in out,
                     "exit %d %s" % (code, err.strip()[:200]))
        messenger_mail = _export_variant(buyers_out, temp, lambda d: gulf(d)[
            "contact_channels"].append({"type": "messenger", "value": "jane.kim@mail.example"}))
        _export_refusal(report, ["--input", messenger_mail], 1, "email",
                        "an address in a messenger channel (csv)")
        phone_mail = _export_variant(buyers_out, temp, lambda d: _record(
            d, "BUY-britsun-example")["contact_channels"][2].update(
                {"value": "jane.kim@mail.example"}))
        _export_refusal(report, ["--input", phone_mail], 1, "email",
                        "an address in a phone channel (csv)")

        nan_score = _export_variant(buyers_out, temp, lambda d: gulf(d).update(
            {"qualification_score": float("nan")}))
        _export_refusal(report, ["--input", nan_score, "--no-validate", "--min-score", "50"], 1,
                        "finite", "a NaN qualification_score with --min-score")
        _export_refusal(report, ["--input", nan_score, "--no-validate"], 1, "finite",
                        "a NaN qualification_score in the csv")

        bad_category = _export_variant(buyers_out, temp, lambda d: gulf(d).update(
            {"company_type": "reseller"}))
        _export_refusal(report, ["--input", bad_category, "--no-validate", "--format",
                                 "tradewith-json"], 1, "tradewith-bulk-buyers",
                        "a row outside the TradeWith schema, even under --no-validate")

        no_domain = _export_variant(buyers_out, temp, lambda d: gulf(d).update(
            {"canonical_domain": "unknown", "stale": True}))
        code, found, err = tw_rows(no_domain)
        row = found.get("BUY-gulfglow-example", {})
        report.check("export: X18 an unknown domain falls back to kbtm:id:<record id>, and a "
                     "stale record says stale=true in originalSource",
                     code == 0 and row.get("sourceId") == "kbtm:id:BUY-gulfglow-example"
                     and row.get("originalSource", "").endswith("| stale=true"),
                     "exit %d %s %s" % (code, row.get("sourceId"), err.strip()[:200]))

        # --- X17: --version --------------------------------------------------------------
        code, out, _e = run_script("export_leads.py", ["--version"])
        report.check("export: X17 --version prints the standard version line",
                     code == 0 and re.match(
                         r"^export_leads\.py skill_version=\S+ schema_version=0\.1\.0 "
                         r"score_version=kbtm-score-0\.1\.0\n$", out), "got %r" % out)
    finally:
        for path in temp:
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 5e. MCP tool server (scripts/mcp_server.py) - tests/cases.md section 16
#
# The server is driven offline with scripted stdin JSON-RPC transcripts. Fixture paths
# are absolute and machine-specific, so the transcripts are built here in code, inside
# a throwaway --root that holds copies of the fixtures; every comparison is structural,
# against the direct CLI run of the same script, or against an existing golden.
# ---------------------------------------------------------------------------
MCP_SCRIPT = "mcp_server.py"
MCP_TOOL_NAMES = ["normalize_company", "dedupe_companies", "score_buyer", "score_seller",
                  "score_match", "validate_output", "make_review_sheet", "acceptance_report",
                  "diff_runs", "stale_evidence", "export_leads"]
MCP_LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
MCP_MODERN_VERSION = "2026-07-28"
MCP_META_VERSION = "io.modelcontextprotocol/protocolVersion"
MCP_META_CAPS = "io.modelcontextprotocol/clientCapabilities"
MCP_BANNED_NAME_TOKENS = ("send", "mail", "fetch", "http", "post", "dispatch", "upload")
MCP_TOOLS_GOLDEN = "mcp.tools-list.expected.json"
MCP_BIG_INLINE = "16777216"
MCP_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def _mcp_run(lines, command, env=None):
    """Feed raw lines to a server command; returns (exit, stdout text, stderr text)."""
    run_env = dict(os.environ) if env is None else env
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"
    data = "".join(line + "\n" for line in lines).encode("utf-8")
    proc = subprocess.run(command, input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, cwd=PKG_ROOT, env=run_env, timeout=900)
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def _mcp_command(root, extra=()):
    command = [sys.executable, os.path.join(SCRIPT_DIR, MCP_SCRIPT)]
    if root is not None:
        command += ["--root", root]
    return command + list(extra)


def _mcp_session(messages, root, extra=("--quiet",), env=None):
    """Run one transcript; returns (exit, stdout, stderr, responses by id, frames)."""
    lines = [m if isinstance(m, str) else json.dumps(m, ensure_ascii=False) for m in messages]
    code, out, err = _mcp_run(lines, _mcp_command(root, extra), env)
    by_id, frames = {}, []
    for line in out.splitlines():
        try:
            frame = json.loads(line)
        except ValueError:
            frame = None
        frames.append(frame)
        if isinstance(frame, dict) and frame.get("id") is not None:
            by_id.setdefault(frame["id"], frame)
    return code, out, err, by_id, frames


def _mcp_req(request_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _mcp_call(request_id, name, arguments, meta=None):
    params = {"name": name, "arguments": arguments}
    if meta is not None:
        params["_meta"] = meta
    return _mcp_req(request_id, "tools/call", params)


def _mcp_meta(version=MCP_MODERN_VERSION, caps=True):
    meta = {MCP_META_VERSION: version}
    if caps:
        meta[MCP_META_CAPS] = {}
    return meta


def _mcp_sc(response):
    result = (response or {}).get("result") or {}
    return result.get("structuredContent") or {}


def _mcp_is_error(response):
    return ((response or {}).get("result") or {}).get("isError")


def _mcp_err_code(response):
    return ((response or {}).get("error") or {}).get("code")


def _mcp_fixture_root(root):
    for name in sorted(os.listdir(FIXTURES)):
        source = os.path.join(FIXTURES, name)
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(root, name))
    os.mkdir(os.path.join(root, "out"))
    scored = (
        ("buyers.uae.scored.json", "score_buyer.py",
         ["--input", os.path.join(root, "buyers.golden.json"),
          "--query", os.path.join(root, "query-buyer-uae-kbeauty.json")]),
        ("buyers.uk.scored.json", "score_buyer.py",
         ["--input", os.path.join(root, "buyers.golden.json"),
          "--query", os.path.join(root, "query-buyer-uk-sunscreen.json")]),
        ("match.scored.json", "score_match.py",
         ["--input", os.path.join(root, "match-134.input.json"),
          "--rerank-input", os.path.join(root, "rerank-134.json")]),
    )
    for name, script, args in scored:
        code, out, err = run_script(script, args + ["--as-of", AS_OF])
        if code != 0:
            raise RuntimeError("%s for %s: exit %d %s" % (script, name, code, err.strip()[:300]))
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(out)
    poison = {"records": [
        {"company_id": "X-a", "company_name": "A Co", "canonical_domain": "a.example",
         "website": "https://a.example", "country": "AE", "company_type": "distributor",
         "evidence": []},
        {"company_id": "X-b", "company_name": "B Co", "canonical_domain": ["b.example"],
         "website": "https://b.example", "country": "AE", "company_type": "distributor",
         "evidence": []}]}
    with open(os.path.join(root, "poison.json"), "w", encoding="utf-8") as fh:
        json.dump(poison, fh)
    return poison


def _mcp_parity_rows(root):
    """(label, tool, arguments, script, cli args, kind, golden) for every exposed tool."""
    def p(name):
        return os.path.join(root, name)
    return [
        ("normalize_company", "normalize_company",
         {"input_path": "buyers.golden.json", "entity": "buyer", "envelope": True},
         "normalize_company.py",
         ["--input", p("buyers.golden.json"), "--entity", "buyer", "--envelope"], "json", None),
        ("dedupe_companies", "dedupe_companies",
         {"input_path": "buyers.golden.json", "entity": "buyer", "strict_country": False},
         "dedupe_companies.py",
         ["--input", p("buyers.golden.json"), "--entity", "buyer", "--no-strict-country"],
         "json", None),
        ("score_buyer", "score_buyer",
         {"input_path": "buyers.golden.json", "query_path": "query-buyer-uae-kbeauty.json",
          "top": 5},
         "score_buyer.py",
         ["--input", p("buyers.golden.json"), "--query", p("query-buyer-uae-kbeauty.json"),
          "--top", "5"], "json", None),
        ("score_seller", "score_seller",
         {"input_path": "sellers.golden.json", "query_path": "query-seller-sunscreen-oem.json",
          "threshold": 60, "threshold_mode": "fixed"},
         "score_seller.py",
         ["--input", p("sellers.golden.json"), "--query", p("query-seller-sunscreen-oem.json"),
          "--threshold", "60", "--threshold-mode", "fixed"], "json", None),
        ("score_match", "score_match",
         {"input_path": "match-134.input.json", "rerank_input_path": "rerank-134.json",
          "include_excluded": False},
         "score_match.py",
         ["--input", p("match-134.input.json"), "--rerank-input", p("rerank-134.json"),
          "--no-include-excluded"], "json", None),
        ("validate_output", "validate_output",
         {"input_path": "match.scored.json", "schema": "match-result", "strict": True},
         "validate_output.py",
         ["--input", p("match.scored.json"), "--schema", "match-result", "--strict", "--json"],
         "json", None),
        ("make_review_sheet", "make_review_sheet",
         {"input_path": "buyers.uae.scored.json", "include_excluded": True},
         "make_review_sheet.py",
         ["--input", p("buyers.uae.scored.json"), "--include-excluded"], "csv",
         "review-sheet.buyers.uae.blind.csv"),
        ("acceptance_report", "acceptance_report",
         {"scored_paths": ["buyers.uae.scored.json"],
          "reviews_paths": ["reviews.buyers.uae.csv"]},
         "acceptance_report.py",
         ["--scored", p("buyers.uae.scored.json"), "--reviews", p("reviews.buyers.uae.csv")],
         "json", "acceptance.buyers.uae.expected.json"),
        ("diff_runs", "diff_runs",
         {"before_path": "buyers.uae.scored.json", "after_path": "buyers.uk.scored.json"},
         "diff_runs.py",
         ["--before", p("buyers.uae.scored.json"), "--after", p("buyers.uk.scored.json")],
         "json", "diff.buyers.uae-uk.expected.json"),
        ("stale_evidence", "stale_evidence", {"input_path": "buyers.golden.json"},
         "stale_evidence.py", ["--input", p("buyers.golden.json")], "json",
         "recheck.buyers.golden.expected.json"),
        ("export_leads csv", "export_leads", {"input_path": "buyers.uae.scored.json"},
         "export_leads.py", ["--input", p("buyers.uae.scored.json")], "csv",
         "export.buyers.uae.csv"),
        ("export_leads tradewith-json", "export_leads",
         {"input_path": "buyers.uae.scored.json", "format": "tradewith-json"},
         "export_leads.py",
         ["--input", p("buyers.uae.scored.json"), "--format", "tradewith-json"], "json",
         "export.buyers.uae.tradewith.json"),
    ]


def _mcp_readonly_transcript(root, poison):
    """Messages that write nothing, so two runs must print identical bytes (INV-13)."""
    with open(os.path.join(root, "buyers.golden.json"), encoding="utf-8") as fh:
        buyers = json.load(fh)
    with open(os.path.join(root, "query-buyer-uae-kbeauty.json"), encoding="utf-8") as fh:
        query = json.load(fh)
    with open(os.path.join(root, "buyers.uae.scored.json"), encoding="utf-8") as fh:
        uae = json.load(fh)
    outside = os.path.join(FIXTURES, "buyers.golden.json")
    messages = [
        _mcp_req(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                   "clientInfo": {"name": "kbtm-tests", "version": "0"}}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/unknown-thing", "params": {}},
        {"jsonrpc": "2.0", "method": "foo/bar"},
        _mcp_req(2, "ping"),
        _mcp_req(3, "tools/list"),
    ]
    for offset, version in enumerate(MCP_LEGACY_VERSIONS + ["1999-01-01", MCP_MODERN_VERSION]):
        messages.append(_mcp_req(10 + offset, "initialize", {
            "protocolVersion": version, "capabilities": {},
            "clientInfo": {"name": "kbtm-tests", "version": "0"}}))
    for offset, row in enumerate(_mcp_parity_rows(root)):
        arguments = dict(row[2])
        arguments["as_of"] = AS_OF
        messages.append(_mcp_call(100 + offset, row[1], arguments))
    messages += [
        _mcp_call(200, "score_buyer", {"as_of": AS_OF, "input": buyers, "query": query,
                                       "top": 5}),
        _mcp_call(201, "diff_runs", {"as_of": AS_OF, "before": uae,
                                     "after_path": "buyers.uk.scored.json"}),
        _mcp_call(300, "score_buyer", {"input_path": "buyers.golden.json"}),
        _mcp_call(301, "score_buyer", {"as_of": "2026-02-30", "input_path": "buyers.golden.json"}),
        _mcp_call(302, "score_buyer", {"as_of": AS_OF + "\n", "input_path": "buyers.golden.json"}),
        _mcp_call(303, "score_buyer", {"as_of": "20260912", "input_path": "buyers.golden.json"}),
        _mcp_call(304, "score_buyer", {"as_of": AS_OF, "input": buyers,
                                       "input_path": "buyers.golden.json"}),
        _mcp_call(305, "score_buyer", {"as_of": AS_OF}),
        _mcp_call(306, "diff_runs", {"as_of": AS_OF, "before": uae, "after": uae}),
        _mcp_call(307, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                       "pretty": True}),
        _mcp_call(308, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                       "top": "5"}),
        _mcp_call(309, "score_buyer", {"as_of": AS_OF, "input_path": outside}),
        _mcp_call(310, "score_buyer", {"as_of": AS_OF,
                                       "input_path": os.path.relpath(outside, root)}),
        _mcp_call(311, "score_buyer", {"as_of": AS_OF, "input_path": "escape-link.json"}),
        _mcp_call(312, "score_buyer", {"as_of": AS_OF, "input_path": "out"}),
        _mcp_call(313, "score_buyer", {"as_of": AS_OF, "input_path": "a\u0000b.json"}),
        _mcp_call(314, "acceptance_report", {"as_of": AS_OF, "scored_paths": [],
                                             "reviews_paths": ["reviews.buyers.uae.csv"]}),
        _mcp_call(315, "validate_output", {"as_of": AS_OF, "input_path": "match.scored.json",
                                           "output_path": "out/v.json"}),
        _mcp_call(500, "score_buyer", {"as_of": AS_OF, "input_path": "poison.json"}),
        _mcp_call(501, "validate_output", {"as_of": AS_OF, "input": poison,
                                           "schema": "discovery-result"}),
        _mcp_call(502, "score_match", {"as_of": AS_OF}),
        # Padding larger than any stdin read-ahead buffer: a child that inherited the
        # server's stdin would swallow it, and request 503 would go unanswered.
        _mcp_req(503, "ping", {"pad": "x" * 262144}),
        _mcp_call(505, "score_match", {"as_of": AS_OF, "rfq_path": "rfq.134.json"}),
        "garbage{",
        json.dumps([_mcp_req(600, "ping")]),
        json.dumps({"jsonrpc": "1.0", "id": 601, "method": "ping"}),
        '{"jsonrpc":"2.0","id":true,"method":"ping"}',
        '{"jsonrpc":"2.0","id":1.5,"method":"ping"}',
        _mcp_req(602, "foo/bar"),
        _mcp_call(603, "send_everything", {}),
        _mcp_req(604, "tools/call", ["score_buyer"]),
        _mcp_req(605, "tools/call", {"name": "score_buyer", "arguments": "x"}),
        '{"jsonrpc":"2.0","id":606,"method":"tools/call","params":{"name":"score_buyer",'
        '"arguments":{"as_of":"2026-09-12","top":NaN}}}',
        {"jsonrpc": "2.0", "id": 607, "result": {}},
        _mcp_req(700, "server/discover", {"_meta": _mcp_meta()}),
        _mcp_req(701, "tools/list", {"_meta": _mcp_meta()}),
        _mcp_req(702, "tools/list", {"_meta": _mcp_meta("2099-01-01")}),
        _mcp_req(703, "tools/list", {"_meta": _mcp_meta(caps=False)}),
        _mcp_call(704, "validate_output", {"as_of": AS_OF, "input_path": "match.scored.json"},
                  meta=_mcp_meta()),
        _mcp_req(999, "ping"),
    ]
    return messages


def _mcp_protocol_cases(report, by_id, frames, out, err, code):
    init = by_id.get(1, {}).get("result") or {}
    report.check("mcp: M-01 initialize echoes 2025-11-25 with the tools capability and the "
                 "package skill_version",
                 init.get("protocolVersion") == "2025-11-25"
                 and init.get("capabilities") == {"tools": {}}
                 and (init.get("serverInfo") or {}).get("version") == _skill_version_from_common()
                 and (init.get("serverInfo") or {}).get("name") == "kbeauty-trade-matchmaker"
                 and "resultType" not in init and init.get("instructions"),
                 "got %s" % json.dumps(init)[:300])
    got = [((by_id.get(10 + i) or {}).get("result") or {}).get("protocolVersion")
           for i in range(len(MCP_LEGACY_VERSIONS) + 2)]
    want = MCP_LEGACY_VERSIONS + ["2025-11-25", "2025-11-25"]
    report.check("mcp: M-02 version negotiation echoes every supported legacy revision and "
                 "answers anything else with the newest legacy one", got == want,
                 "expected %s\n     got %s" % (want, got))
    report.check("mcp: M-03 ping answers {} and notifications get no response",
                 (by_id.get(2) or {}).get("result") == {}
                 and all(isinstance(f, dict) and "id" in f for f in frames),
                 "ping %s" % by_id.get(2))

    nulls = [f for f in frames if isinstance(f, dict) and f.get("id") is None]
    null_codes = sorted(_mcp_err_code(f) for f in nulls)
    report.check("mcp: M-04 a malformed line is -32700 and a batch or a bad id is -32600, "
                 "each with id null",
                 null_codes == [-32700, -32700, -32600, -32600, -32600],
                 "null-id error codes %s" % null_codes)
    report.check("mcp: M-05 a bad jsonrpc keeps the request id; an unknown method is -32601",
                 _mcp_err_code(by_id.get(601)) == -32600 and _mcp_err_code(by_id.get(602))
                 == -32601, "601 %s / 602 %s" % (by_id.get(601), by_id.get(602)))
    report.check("mcp: M-06 an unknown tool, non-object params and non-object arguments are "
                 "-32602", all(_mcp_err_code(by_id.get(i)) == -32602 for i in (603, 604, 605)),
                 "; ".join("%d %s" % (i, by_id.get(i)) for i in (603, 604, 605)))
    report.check("mcp: M-07 a stray response is ignored and requests after errors are still "
                 "answered", 607 not in by_id and (by_id.get(999) or {}).get("result") == {},
                 "607 %s / 999 %s" % (by_id.get(607), by_id.get(999)))

    discover = (by_id.get(700) or {}).get("result") or {}
    modern_list = (by_id.get(701) or {}).get("result") or {}
    legacy_list = (by_id.get(3) or {}).get("result") or {}
    bad_version = (by_id.get(702) or {}).get("error") or {}
    modern_call = (by_id.get(704) or {}).get("result") or {}
    problems = []
    if discover.get("resultType") != "complete" \
            or discover.get("supportedVersions") != [MCP_MODERN_VERSION] \
            or discover.get("capabilities") != {"tools": {}} \
            or "io.modelcontextprotocol/serverInfo" not in (discover.get("_meta") or {}):
        problems.append("server/discover %s" % json.dumps(discover)[:200])
    if modern_list.get("resultType") != "complete" or modern_list.get("ttlMs") != 300000 \
            or modern_list.get("cacheScope") != "public" \
            or modern_list.get("tools") != legacy_list.get("tools"):
        problems.append("modern tools/list lacks resultType/ttlMs/cacheScope or differs")
    if set(legacy_list) != {"tools"}:
        problems.append("legacy tools/list carries %s" % sorted(legacy_list))
    if bad_version.get("code") != -32022 or (bad_version.get("data") or {}).get(
            "supported") != [MCP_MODERN_VERSION]:
        problems.append("unsupported _meta version %s" % bad_version)
    if _mcp_err_code(by_id.get(703)) != -32602:
        problems.append("_meta without clientCapabilities %s" % by_id.get(703))
    if modern_call.get("resultType") != "complete" or modern_call.get("isError") is not False:
        problems.append("modern tools/call %s" % json.dumps(modern_call)[:200])
    report.check("mcp: M-08 the 2026-07-28 path: server/discover, _meta requests, -32022 for "
                 "an unsupported version, -32602 for a malformed _meta", not problems,
                 "\n".join(problems))

    bad_lines = [ln[:80] for ln, f in zip(out.splitlines(), frames)
                 if not (isinstance(f, dict) and f.get("jsonrpc") == "2.0")]
    report.check("mcp: M-09 stdout carries only JSON-RPC frames, stderr is silent under "
                 "--quiet, and end of input exits 0",
                 code == 0 and not bad_lines and err == "" and out.endswith("\n"),
                 "exit %d, bad lines %s, stderr %r" % (code, bad_lines[:3], err[:300]))


def _mcp_tool_list_cases(report, by_id, out):
    tools = ((by_id.get(3) or {}).get("result") or {}).get("tools") or []
    names = [t.get("name") for t in tools]
    bad = [n for n in names if not re.match(r"^[A-Za-z0-9_.-]{1,128}$", str(n))
           or any(tok in str(n).lower() for tok in MCP_BANNED_NAME_TOKENS)]
    report.check("mcp: M-10 tools/list names the package scripts in pipeline order and no "
                 "name suggests sending or fetching", names == MCP_TOOL_NAMES and not bad,
                 "got %s; bad %s" % (names, bad))

    problems = []
    for tool in tools:
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False \
                or "as_of" not in (schema.get("required") or []):
            problems.append("%s: not a closed object requiring as_of" % tool.get("name"))
        if set(schema) & {"oneOf", "anyOf", "allOf"}:
            problems.append("%s: top-level combinator" % tool.get("name"))

        def visit(key, path, name=tool.get("name")):
            if key not in SUPPORTED_KEYWORDS and key not in ANNOTATION_KEYWORDS:
                problems.append("%s: keyword %s at %s" % (name, key, path))
        walk_schema(schema, visit)
        for key, prop in props.items():
            if (key.endswith("_path") and prop.get("type") != "string") or \
                    (key.endswith("_paths") and (prop.get("items") or {}).get("type") != "string"):
                problems.append("%s: %s is not a path string" % (tool.get("name"), key))
        hints = tool.get("annotations") or {}
        writes = "output_path" in props
        if any(h not in hints for h in MCP_HINTS) or hints.get("openWorldHint") is not False \
                or hints.get("destructiveHint") is not False \
                or hints.get("readOnlyHint") is writes or hints.get("idempotentHint") is writes:
            problems.append("%s: annotations %s dishonest for writes=%s"
                            % (tool.get("name"), hints, writes))
    report.check("mcp: M-11 every inputSchema is a closed object in the _common.validate "
                 "subset and every tool carries honest annotations", bool(tools) and not problems,
                 "\n".join(problems[:10]))

    kinds = None
    try:
        sys.path.insert(0, SCRIPT_DIR)
        sys.dont_write_bytecode = True
        import validate_output as validator_module
        kinds = list(validator_module.KINDS) + ["auto"]
    except Exception as exc:  # pragma: no cover - defensive
        kinds = ["import failed: %s" % exc]
    enum = None
    for tool in tools:
        if tool.get("name") == "validate_output":
            enum = ((tool.get("inputSchema") or {}).get("properties") or {}).get(
                "schema", {}).get("enum")
    report.check("mcp: M-12 the validate_output schema enum equals validate_output.KINDS + auto",
                 enum == kinds, "enum %s\n     kinds %s" % (enum, kinds))

    line = [ln for ln in out.splitlines() if ln.startswith('{"jsonrpc":"2.0","id":3,"result":')]
    golden_path = os.path.join(EXPECTED, MCP_TOOLS_GOLDEN)
    golden = _read_text(golden_path) if os.path.isfile(golden_path) else None
    body = line[0][len('{"jsonrpc":"2.0","id":3,"result":'):-1] + "\n" if line else None
    report.check("mcp: M-13 tools/list is byte-identical to %s" % MCP_TOOLS_GOLDEN,
                 golden is not None and body == golden,
                 "golden missing" if golden is None else "tools/list bytes differ from golden")


def _mcp_parity_cases(report, root, by_id):
    problems, golden_checked, exercised = [], [], set()
    for offset, row in enumerate(_mcp_parity_rows(root)):
        label, tool, _args, script, cli_args, kind, golden = row
        response = by_id.get(100 + offset)
        sc = _mcp_sc(response)
        exercised.add(tool)
        code, cli_out, cli_err = run_script(script, cli_args + ["--as-of", AS_OF])
        if code != 0:
            problems.append("%s: the CLI itself exited %d %s" % (label, code, cli_err[:200]))
            continue
        if _mcp_is_error(response) is not False or sc.get("exit_code") != 0:
            problems.append("%s: isError %s exit %s error %s" % (
                label, _mcp_is_error(response), sc.get("exit_code"), sc.get("error")))
            continue
        if kind == "csv":
            got, want = sc.get("csv"), cli_out
        else:
            got, want = sc.get("document"), json.loads(cli_out)
        if got != want:
            problems.append("%s: the tool result differs from the CLI output" % label)
        text = ((response.get("result") or {}).get("content") or [{}])[0].get("text")
        if text != json.dumps(sc, ensure_ascii=False, separators=(",", ":")):
            problems.append("%s: the text block is not the serialized structuredContent" % label)
        if golden:
            expected = _read_text(os.path.join(EXPECTED, golden))
            if (kind == "csv" and got != expected) or \
                    (kind == "json" and got != json.loads(expected)):
                problems.append("%s: differs from golden %s" % (label, golden))
            golden_checked.append(golden)
    report.check("mcp: M-14 every tool returns exactly what its CLI prints (%d calls)"
                 % len(_mcp_parity_rows(root)),
                 not problems and exercised == set(MCP_TOOL_NAMES), "\n".join(problems[:12])
                 + ("\nnot exercised: %s" % sorted(set(MCP_TOOL_NAMES) - exercised)))
    report.check("mcp: M-15 tool results match the existing goldens (review sheet, acceptance, "
                 "diff, re-check queue, exports)", not problems and len(golden_checked) == 6,
                 "checked %s" % golden_checked)

    path_doc = _mcp_sc(by_id.get(102)).get("document")
    inline_doc = _mcp_sc(by_id.get(200)).get("document")
    diff_path = _mcp_sc(by_id.get(108)).get("document")
    diff_inline = _mcp_sc(by_id.get(201)).get("document")
    report.check("mcp: M-16 an inline document and a path give the same result (score_buyer "
                 "input+query, diff_runs before)",
                 path_doc is not None and path_doc == inline_doc and diff_path is not None
                 and diff_path == diff_inline,
                 "score_buyer equal %s, diff_runs equal %s" % (path_doc == inline_doc,
                                                            diff_path == diff_inline))


def _mcp_refusal_cases(report, by_id):
    def refused(request_id, needle=None):
        sc = _mcp_sc(by_id.get(request_id))
        return (_mcp_is_error(by_id.get(request_id)) is True and sc.get("exit_code") is None
                and bool(sc.get("error"))
                and (needle is None or needle in str(sc.get("error"))))

    def show(ids):
        return "\n".join("%d: %s" % (i, _mcp_sc(by_id.get(i)).get("error") or by_id.get(i))
                         for i in ids if not refused(i))

    ids = (300, 301, 302, 303)
    report.check("mcp: M-17 as_of is required and must be a real YYYY-MM-DD date; no script "
                 "runs without one", all(refused(i) for i in ids), show(ids))
    ids = (304, 305, 306, 307, 308, 314)
    report.check("mcp: M-18 argument errors are tool errors: both or neither of input / "
                 "input_path, two inline documents, an unknown key, a wrong type, an empty list",
                 all(refused(i) for i in ids), show(ids))
    ids = (309, 310, 311)
    report.check("mcp: M-19 an input path outside --root is refused: absolute, ../ and a "
                 "symlink that leads out", all(refused(i, "--root") for i in ids), show(ids))
    ids = (312, 313, 315)
    report.check("mcp: M-20 a directory, a NUL byte and output_path on the read-only "
                 "validate_output are refused", all(refused(i) for i in ids), show(ids))

    poisoned = _mcp_sc(by_id.get(500))
    report.check("mcp: M-21 a script data error (exit 1) is isError with its ERROR line and "
                 "the document it still wrote (R7.3.2)",
                 _mcp_is_error(by_id.get(500)) is True and poisoned.get("exit_code") == 1
                 and poisoned.get("error") and isinstance(poisoned.get("document"), dict)
                 and any(ln.startswith("ERROR:") for ln in poisoned.get("diagnostics", [])),
                 json.dumps(poisoned)[:400])
    verdict = _mcp_sc(by_id.get(501))
    report.check("mcp: M-22 validate_output exit 1 is a successful call whose report says "
                 "valid false", _mcp_is_error(by_id.get(501)) is False
                 and verdict.get("exit_code") == 1
                 and (verdict.get("document") or {}).get("valid") is False,
                 json.dumps(verdict)[:400])
    empty = _mcp_sc(by_id.get(502))
    half = _mcp_sc(by_id.get(505))
    report.check("mcp: M-23 score_match without input / input_path, or with only one of "
                 "rfq_path / sellers_path, is refused before any script runs (no empty-stdin "
                 "error blaming <stdin>) and the stream continues",
                 all(_mcp_is_error(by_id.get(i)) is True for i in (502, 505))
                 and empty.get("exit_code") is None and half.get("exit_code") is None
                 and "rfq_path together with sellers_path" in str(empty.get("error"))
                 and "rfq_path together with sellers_path" in str(half.get("error"))
                 and "stdin" not in str(empty.get("error"))
                 and (by_id.get(503) or {}).get("result") == {}
                 and (by_id.get(999) or {}).get("result") == {},
                 json.dumps(empty)[:300] + " / " + json.dumps(half)[:300])


def _mcp_write_cases(report, root, poison_doc):
    outside = os.path.realpath(tempfile.mkdtemp(prefix="kbtm-mcp-outside-"))
    try:
        os.symlink(outside, os.path.join(root, "outlink"))
        os.symlink(os.path.join(root, "out", "nowhere.json"),
                   os.path.join(root, "out", "dangle.json"))
        existing = os.path.join(root, "buyers.golden.json")
        before = _read_text(existing)
        os.mkdir(os.path.join(root, "existing-dir.json"))
        messages = [
            _mcp_call(400, "score_match", {"as_of": AS_OF, "input_path": "match-134.input.json",
                                           "rerank_input_path": "rerank-134.json",
                                           "output_path": "out/match.json"}),
            _mcp_call(401, "score_match", {"as_of": AS_OF, "input_path": "match-134.input.json",
                                           "output_path": "out/match.json"}),
            _mcp_call(402, "score_buyer", {"as_of": AS_OF, "input_path": "poison.json",
                                           "output_path": "out/poison.scored.json"}),
            _mcp_call(403, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                           "output_path": "buyers.golden.json"}),
            _mcp_call(404, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                           "output_path": "out/dangle.json"}),
            _mcp_call(405, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                           "output_path": "nodir/x.json"}),
            _mcp_call(406, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                           "output_path": "outlink/x.json"}),
            _mcp_call(407, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                           "output_path": "existing-dir.json"}),
            _mcp_call(408, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json"}),
            '{"jsonrpc":"2.0","id":409,"method":"ping","params":{"pad":"'
            + "x" * (16 * 1024 * 1024) + '"}}',
            _mcp_req(410, "ping"),
        ]
        code, out, err, by_id, frames = _mcp_session(messages, root, extra=())

        sc = _mcp_sc(by_id.get(400))
        written = os.path.join(root, "out", "match.json")
        problems = []
        if _mcp_is_error(by_id.get(400)) is not False or "document" in sc:
            problems.append("isError %s keys %s" % (_mcp_is_error(by_id.get(400)), sorted(sc)))
        if os.path.isfile(written):
            with open(written, "rb") as fh:
                raw = fh.read()
            want = {"path": written, "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest()}
            if sc.get("output") != want:
                problems.append("output %s, expected %s" % (sc.get("output"), want))
            cli_code, cli_out, _e = run_script("score_match.py", [
                "--input", os.path.join(root, "match-134.input.json"),
                "--rerank-input", os.path.join(root, "rerank-134.json"), "--as-of", AS_OF])
            if cli_code != 0 or json.loads(raw.decode("utf-8")) != json.loads(cli_out):
                problems.append("the written file differs from the CLI output")
        else:
            problems.append("no file at out/match.json")
        report.check("mcp: M-25 output_path writes a new file inside the root and returns its "
                     "path, bytes and sha256 instead of the document", not problems,
                     "\n".join(problems))

        again = _mcp_sc(by_id.get(401))
        report.check("mcp: M-26 an existing output_path is refused and the file is unchanged",
                     _mcp_is_error(by_id.get(401)) is True and again.get("exit_code") is None
                     and "already exists" in str(again.get("error"))
                     and _mcp_is_error(by_id.get(403)) is True
                     and _read_text(existing) == before
                     and os.path.isfile(written) and sc.get("output", {}).get("sha256")
                     == hashlib.sha256(open(written, "rb").read()).hexdigest(),
                     "401 %s / 403 %s" % (again.get("error"), _mcp_sc(by_id.get(403)).get("error")))

        failed = _mcp_sc(by_id.get(402))
        report.check("mcp: M-27 a script that exits 1 after writing reports the file and says "
                     "a retry needs a new output_path",
                     _mcp_is_error(by_id.get(402)) is True and failed.get("exit_code") == 1
                     and (failed.get("output") or {}).get("path")
                     == os.path.join(root, "out", "poison.scored.json")
                     and "new output_path" in str(failed.get("error")),
                     json.dumps(failed)[:400])

        refusals = [(404, "already exists"), (405, "does not exist"), (406, "--root"),
                    (407, "already exists")]
        bad = ["%d: %s" % (i, _mcp_sc(by_id.get(i)).get("error")) for i, needle in refusals
               if not (_mcp_is_error(by_id.get(i)) is True
                       and needle in str(_mcp_sc(by_id.get(i)).get("error")))]
        leaked = sorted(os.listdir(outside))
        if os.path.lexists(os.path.join(root, "out", "nowhere.json")):
            leaked.append("out/nowhere.json (through the dangling link)")
        if os.path.lexists(os.path.join(root, "nodir")):
            leaked.append("nodir/ was created")
        report.check("mcp: M-28 output_path refusals: a dangling symlink, a missing directory, "
                     "a symlinked directory leading out of the root, an existing directory",
                     not bad and not leaked, "\n".join(bad + leaked))
        report.check("mcp: M-29 nothing but the two requested files was written under the root",
                     sorted(os.listdir(os.path.join(root, "out")))
                     == ["dangle.json", "match.json", "poison.scored.json"],
                     "out/ holds %s" % sorted(os.listdir(os.path.join(root, "out"))))

        capped = _mcp_sc(by_id.get(408))
        report.check("mcp: M-30 a result above the default inline cap is refused with a pointer "
                     "to output_path and no document",
                     _mcp_is_error(by_id.get(408)) is True and capped.get("exit_code") == 0
                     and "output_path" in str(capped.get("error"))
                     and "document" not in capped, json.dumps(capped)[:400])
        report.check("mcp: M-31 a message above 16 MiB is -32600 and the next request is "
                     "answered", any(isinstance(f, dict) and f.get("id") is None
                                     and _mcp_err_code(f) == -32600 for f in frames)
                     and (by_id.get(410) or {}).get("result") == {} and 409 not in by_id,
                     "frames %d, 410 %s" % (len(frames), by_id.get(410)))
        logged = [ln for ln in err.splitlines() if ln]
        report.check("mcp: M-32 without --quiet each call logs one stderr line and never a "
                     "traceback", code == 0 and "Traceback" not in err
                     and "kbtm-mcp: score_match exit=0" in logged
                     and all(ln.startswith("kbtm-mcp: ") for ln in logged),
                     "exit %d stderr %r" % (code, err[:400]))
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def _mcp_package_rule(report):
    target = os.path.join(PKG_ROOT, "scripts", "kbtm-mcp-should-not-exist.json")
    code, _out, err, by_id, _f = _mcp_session([
        _mcp_call(1, "score_buyer", {"as_of": AS_OF,
                                     "input_path": "tests/fixtures/buyers.golden.json",
                                     "output_path": "scripts/kbtm-mcp-should-not-exist.json"})],
        PKG_ROOT)
    sc = _mcp_sc(by_id.get(1))
    report.check("mcp: M-33 output_path inside the skill package is refused even when the "
                 "root contains it", code == 0 and _mcp_is_error(by_id.get(1)) is True
                 and "skill package" in str(sc.get("error")) and not os.path.lexists(target),
                 "exit %d %s %s" % (code, sc.get("error"), err[:200]))


MCP_SMALL_INPUT = {"records": [{"company_name": "A Co", "website": "https://a.example",
                                 "country": "AE", "company_type": "distributor"}]}


def _mcp_frames_ok(out):
    """Every stdout line is one JSON value (a crash or stray print breaks this)."""
    for line in out.splitlines():
        try:
            json.loads(line)
        except ValueError:
            return False
    return True


def _mcp_import_server():
    sys.path.insert(0, SCRIPT_DIR)
    sys.dont_write_bytecode = True
    import mcp_server
    return mcp_server


def _mcp_robustness_cases(report, root):
    """Review findings: frames that used to end the process, and path/child hardening."""
    ascii_line = json.dumps  # ensure_ascii=True writes a lone surrogate as a \u escape
    code, out, err, by_id, _f = _mcp_session([
        ascii_line(_mcp_req("\ud800", "ping")),
        ascii_line(_mcp_req(501, "\ud800x")),
        ascii_line(_mcp_call(502, "\ud800", {})),
        ascii_line(_mcp_call(503, "validate_output", {
            "as_of": AS_OF, "input_path": "buyers.golden.json", "schema": "\udc80"})),
        ascii_line(_mcp_call(504, "normalize_company", {"as_of": "\ud800",
                                                        "input": MCP_SMALL_INPUT})),
        _mcp_req(505, "ping")], root)
    report.check("mcp: M-37 a lone surrogate echoed in an id, method, tool name or error is "
                 "answered and the server keeps serving",
                 code == 0 and _mcp_frames_ok(out) and "Traceback" not in err
                 and (by_id.get("\ud800") or {}).get("result") == {}
                 and _mcp_err_code(by_id.get(501)) == -32601
                 and _mcp_err_code(by_id.get(502)) == -32602
                 and _mcp_is_error(by_id.get(503)) is True
                 and _mcp_is_error(by_id.get(504)) is True
                 and (by_id.get(505) or {}).get("result") == {},
                 "exit %d ids %s stderr %r" % (code, sorted(map(repr, by_id)), err[-300:]))

    deep_args = ('{"jsonrpc":"2.0","id":603,"method":"tools/call","params":{"name":'
                 '"normalize_company","arguments":{"as_of":"%s","input":%s1%s}}}'
                 % (AS_OF, '{"x":' * 200000, "}" * 200000))
    code, out, err, by_id, frames = _mcp_session([
        "[" * 200000,
        '{"x":' * 200000 + "1" + "}" * 200000,
        deep_args,
        _mcp_req(604, "ping")], root)
    parse_errors = [f for f in frames if isinstance(f, dict) and f.get("id") is None
                    and _mcp_err_code(f) == -32700]
    report.check("mcp: M-38 JSON nested deeper than the interpreter allows is a parse error "
                 "and the next request is answered",
                 code == 0 and _mcp_frames_ok(out) and "Traceback" not in err
                 and len(parse_errors) >= 2 and (by_id.get(604) or {}).get("result") == {},
                 "exit %d frames %d stderr %r" % (code, len(frames), err[-300:]))

    closer = ("import os, sys; os.close(2); "
              "os.execv(sys.executable, [sys.executable] + sys.argv[1:])")
    code, out, _err = _mcp_run([
        json.dumps(_mcp_call(1, "normalize_company", {"as_of": AS_OF,
                                                      "input": MCP_SMALL_INPUT})),
        json.dumps(_mcp_req(2, "ping"))],
        [sys.executable, "-c", closer] + _mcp_command(root)[1:])
    frames = [json.loads(ln) for ln in out.splitlines()] if _mcp_frames_ok(out) else []
    answered = dict((f.get("id"), f) for f in frames if isinstance(f, dict))
    report.check("mcp: M-39 a closed stderr never stops the server (the per-call log line "
                 "is best effort)",
                 code == 0 and _mcp_is_error(answered.get(1)) is False
                 and (answered.get(2) or {}).get("result") == {},
                 "exit %d stdout %r" % (code, out[:300]))

    big = {"pad": "x" * (2 * 1024 * 1024)}
    over = {"pad": "x" * 70000}
    code, out, err, by_id, _f = _mcp_session([
        _mcp_call(801, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                       "query": big}),
        _mcp_call(802, "score_seller", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                        "query": over}),
        _mcp_req(803, "ping")], root)
    problems = []
    for rid in (801, 802):
        sc = _mcp_sc(by_id.get(rid))
        if not (_mcp_is_error(by_id.get(rid)) is True and sc.get("exit_code") is None
                and "query_path" in str(sc.get("error"))):
            problems.append("%d: %s" % (rid, json.dumps(by_id.get(rid))[:300]))
    try:
        server = _mcp_import_server()
        tool = [t for t in server.TOOLS if t["name"] == "normalize_company"][0]
        config = {"root": root, "timeout": 30, "max_inline": 32768, "quiet": True,
                  "python": os.path.join(root, "no-such-python")}
        result = server.call_tool(tool, {"as_of": AS_OF, "input": MCP_SMALL_INPUT}, config)
        if not (result.get("isError") is True
                and "could not be started" in str(result["structuredContent"].get("error"))):
            problems.append("unstartable child: %s" % json.dumps(result)[:300])
    except Exception as exc:
        problems.append("unstartable child raised %s: %s" % (type(exc).__name__, exc))
    report.check("mcp: M-40 an inline query too large for the command line, or a child that "
                 "cannot start, is a tool error pointing to query_path, not -32603",
                 code == 0 and (by_id.get(803) or {}).get("result") == {} and not problems,
                 "\n".join(problems))

    code, out, err, by_id, _f = _mcp_session([
        ascii_line(_mcp_call(901, "normalize_company", {
            "as_of": AS_OF, "input": {"records": [{"company_name": "\ud800 Co"}]}})),
        ascii_line(_mcp_call(902, "score_buyer", {"as_of": AS_OF,
                                                  "input_path": "buyers.golden.json",
                                                  "query": {"market": "\udc00"}})),
        ascii_line(_mcp_call(903, "normalize_company", {"as_of": AS_OF,
                                                        "input_path": "\ud800.json"})),
        _mcp_req(904, "ping")], root)
    bad = ["%d: %s" % (rid, _mcp_sc(by_id.get(rid)).get("error")) for rid in (901, 902, 903)
           if not (_mcp_is_error(by_id.get(rid)) is True
                   and "UTF-8" in str(_mcp_sc(by_id.get(rid)).get("error")))]
    report.check("mcp: M-41 an inline document or path holding a lone surrogate is a tool "
                 "error, not -32603", code == 0 and not bad
                 and (by_id.get(904) or {}).get("result") == {}, "\n".join(bad))

    code, out, err, by_id, frames = _mcp_session([
        '{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"client parse error"}}',
        '{"jsonrpc":"2.0","id":1.5,"result":{}}',
        '{"jsonrpc":"2.0","id":77,"result":{}}',
        _mcp_req(701, "ping")], root)
    report.check("mcp: M-42 a stray response is never answered, even with id null",
                 code == 0 and frames == [{"jsonrpc": "2.0", "id": 701, "result": {}}],
                 "frames %s" % frames)

    parent_dir = os.path.dirname(PKG_ROOT)
    variant_pkg = os.path.join(parent_dir, os.path.basename(PKG_ROOT).swapcase())
    probe_name = "kbtm-mcp-case-probe.json"
    real_probe = os.path.join(PKG_ROOT, "tests", probe_name)
    code, out, err, by_id, _f = _mcp_session([
        _mcp_call(1, "score_buyer", {
            "as_of": AS_OF,
            "input_path": os.path.join(PKG_ROOT, "tests", "fixtures", "buyers.golden.json"),
            "output_path": os.path.join(variant_pkg, "tests", probe_name)})], parent_dir)
    created = os.path.lexists(real_probe)
    if created:
        os.remove(real_probe)
    sc = _mcp_sc(by_id.get(1))
    folds = os.path.isdir(os.path.join(variant_pkg, "tests"))
    problems = []
    if code != 0 or _mcp_is_error(by_id.get(1)) is not True or created:
        problems.append("exit %d isError %s created %s error %s" % (
            code, _mcp_is_error(by_id.get(1)), created, sc.get("error")))
    elif folds and "skill package" not in str(sc.get("error")):
        problems.append("case-folded spelling not recognised: %s" % sc.get("error"))
    link_dir = tempfile.mkdtemp(prefix="kbtm-mcp-link-")
    try:
        server = _mcp_import_server()
        link = os.path.join(link_dir, "pkg")
        os.symlink(PKG_ROOT, link)
        # _within_package takes an already-resolved directory; an unresolved alias of the
        # package can only be recognised by (st_dev, st_ino), as a case-folded name is.
        if not server._within_package(os.path.join(link, "tests")):
            problems.append("an alias of the package folder is not recognised by identity")
        if server._within_package(link_dir):
            problems.append("an unrelated folder is reported inside the package")
    except (OSError, NotImplementedError, AttributeError) as exc:
        problems.append("identity check unavailable: %s" % exc)
    finally:
        shutil.rmtree(link_dir, ignore_errors=True)
    report.check("mcp: M-43 the package is recognised by file identity, so a case-changed "
                 "spelling of its path cannot write into it", not problems,
                 "\n".join(problems))

    base = os.path.join(root, "m44")
    os.makedirs(os.path.join(base, ".cfg"))
    code, out, err, by_id, _f = _mcp_session([
        _mcp_call(1, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                     "output_path": "m44/probe.py"}),
        _mcp_call(2, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                     "output_path": "m44/.probe.json"}),
        _mcp_call(3, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                     "output_path": "m44/.cfg/probe.json"}),
        _mcp_call(4, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                     "output_path": "m44/probe.JSON"})], root)
    bad = ["%d: %s" % (rid, _mcp_sc(by_id.get(rid)).get("error"))
           for rid, needle in ((1, ".json or .csv"), (2, "hidden"), (3, "hidden"))
           if not (_mcp_is_error(by_id.get(rid)) is True
                   and needle in str(_mcp_sc(by_id.get(rid)).get("error")))]
    left = sorted(os.listdir(base)) + sorted(os.listdir(os.path.join(base, ".cfg")))
    report.check("mcp: M-44 output_path must end in .json or .csv and may not name a hidden "
                 "file or folder", code == 0 and not bad and _mcp_is_error(by_id.get(4)) is False
                 and left == [".cfg", "probe.JSON"], "\n".join(bad) + " left %s" % left)

    site = tempfile.mkdtemp(prefix="kbtm-mcp-site-")
    saved = os.environ.get("TRADEWITH_BASE_URL")
    try:
        with open(os.path.join(site, "sitecustomize.py"), "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.stderr.write('KBTM-SITECUSTOMIZE-RAN\\n')\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = site
        env["TRADEWITH_BASE_URL"] = "https://tradewith.example"
        code, out, err, by_id, _f = _mcp_session([
            _mcp_call(1, "normalize_company", {"as_of": AS_OF, "input": MCP_SMALL_INPUT})],
            root, env=env)
        diagnostics = _mcp_sc(by_id.get(1)).get("diagnostics") or []
        problems = []
        if code != 0 or _mcp_is_error(by_id.get(1)) is not False:
            problems.append("exit %d result %s" % (code, json.dumps(by_id.get(1))[:300]))
        if any("KBTM-SITECUSTOMIZE-RAN" in line for line in diagnostics):
            problems.append("the child honoured PYTHONPATH (it did not run isolated)")
        server = _mcp_import_server()
        os.environ["TRADEWITH_BASE_URL"] = "https://tradewith.example"
        if any(key.startswith("TRADEWITH_") for key in server._child_env()):
            problems.append("TRADEWITH_* reaches the child environment")
    finally:
        if saved is None:
            os.environ.pop("TRADEWITH_BASE_URL", None)
        else:
            os.environ["TRADEWITH_BASE_URL"] = saved
        shutil.rmtree(site, ignore_errors=True)
    report.check("mcp: M-45 a child runs isolated (-I ignores PYTHONPATH) and never sees "
                 "TRADEWITH_* variables", not problems, "\n".join(problems))


def _mcp_cli_cases(report, root):
    code, out, err = _mcp_run([], _mcp_command(None, ["--version"]))
    report.check("mcp: M-34 --version prints the standard version line",
                 code == 0 and re.match(r"^mcp_server\.py skill_version=\S+ schema_version="
                                        r"0\.1\.0 score_version=kbtm-score-0\.1\.0\n$", out),
                 "exit %d %r" % (code, out))
    home = os.path.join(root, "home")
    os.mkdir(home)
    home_env = dict(os.environ)
    home_env["HOME"] = home
    problems = []
    for label, command, env, needs_error_line in (
            ("no --root", _mcp_command(None), None, True),
            ("missing dir", _mcp_command(os.path.join(root, "no-such-dir")), None, True),
            ("filesystem root", _mcp_command(os.path.abspath(os.sep)), None, True),
            ("home ancestor", _mcp_command(root), home_env, True),
            ("home itself", _mcp_command(home), home_env, True),
            ("tool timeout 0", _mcp_command(root, ["--tool-timeout", "0"]), None, True),
            ("inline cap 10", _mcp_command(root, ["--max-inline-bytes", "10"]), None, True),
            ("unknown flag", _mcp_command(root, ["--bogus"]), None, False)):
        code, out, err = _mcp_run([json.dumps(_mcp_req(1, "ping"))], command, env)
        errors = [ln for ln in err.splitlines() if ln.startswith("ERROR:")]
        if code != 2 or out or "Traceback" in err or (needs_error_line and len(errors) != 1):
            problems.append("%s: exit %d stdout %r stderr %r" % (label, code, out[:80],
                                                                 err[:200]))
    report.check("mcp: M-35 the server refuses to start without a safe --root or with a bad "
                 "flag (exit 2, one ERROR line, empty stdout)", not problems,
                 "\n".join(problems))


def _mcp_plugin_case(report, root):
    if not os.path.isfile(MARKETPLACE_PATH):
        report.skip("mcp: M-36 the marketplace entry's server command starts the server",
                    "not a repository checkout (installed copy or plugin cache)")
        return
    try:
        with open(MARKETPLACE_PATH, encoding="utf-8") as fh:
            entry = (json.load(fh).get("plugins") or [{}])[0]
        server = list((entry.get("mcpServers") or {}).values())[0]
    except (OSError, ValueError, IndexError, AttributeError) as exc:
        report.fail("mcp: M-36 the marketplace entry's server command starts the server",
                    "no readable mcpServers entry: %s" % exc)
        return
    args = [a.replace("${CLAUDE_PLUGIN_ROOT}", PKG_ROOT).replace("${CLAUDE_PROJECT_DIR}", root)
            for a in server.get("args", [])]
    script_ok = bool(args) and os.path.isfile(args[0])
    code, out, err = _mcp_run([
        json.dumps(_mcp_req(1, "initialize", {"protocolVersion": "2025-06-18",
                                              "capabilities": {},
                                              "clientInfo": {"name": "t", "version": "0"}})),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(_mcp_req(2, "tools/list"))],
        [sys.executable] + args) if script_ok else (None, "", "")
    frames = [json.loads(ln) for ln in out.splitlines()] if code == 0 else []
    names = [t.get("name") for t in (frames[1].get("result") or {}).get("tools", [])] \
        if len(frames) == 2 else []
    report.check("mcp: M-36 the marketplace entry's server command starts the server",
                 server.get("command") == "python3" and script_ok and code == 0
                 and names == MCP_TOOL_NAMES,
                 "command %r args %r exit %s stderr %r" % (server.get("command"), args, code,
                                                           err[:200]))


def phase_mcp_server(report, allow_missing):
    script = os.path.join(SCRIPT_DIR, MCP_SCRIPT)
    if not os.path.isfile(script) or missing_scripts():
        note = "scripts absent: %s" % ", ".join(
            missing_scripts() or ["scripts/%s" % MCP_SCRIPT])
        if allow_missing:
            report.skip("mcp: tool server cases", note)
        else:
            report.fail("mcp: tool server cases", note)
        return
    root = os.path.realpath(tempfile.mkdtemp(prefix="kbtm-mcp-"))
    try:
        try:
            poison = _mcp_fixture_root(root)
        except (OSError, RuntimeError) as exc:
            report.fail("mcp: fixture root", str(exc))
            return
        os.symlink(os.path.join(FIXTURES, "buyers.golden.json"),
                   os.path.join(root, "escape-link.json"))
        messages = _mcp_readonly_transcript(root, poison)
        code, out, err, by_id, frames = _mcp_session(
            messages, root, extra=("--quiet", "--max-inline-bytes", MCP_BIG_INLINE))
        _mcp_protocol_cases(report, by_id, frames, out, err, code)
        _mcp_tool_list_cases(report, by_id, out)
        _mcp_parity_cases(report, root, by_id)
        _mcp_refusal_cases(report, by_id)
        code2, out2, _err2, _b, _f = _mcp_session(
            messages, root, extra=("--quiet", "--max-inline-bytes", MCP_BIG_INLINE))
        report.check("mcp: M-24 INV-13 the same read-only transcript prints byte-identical "
                     "stdout twice", code2 == code and out2 == out, "second run differed")
        _mcp_write_cases(report, root, poison)
        _mcp_package_rule(report)
        _mcp_robustness_cases(report, root)
        _mcp_cli_cases(report, root)
        _mcp_plugin_case(report, root)
        _mcp_review_cases(report, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _mcp_review_cases(report, root):
    """v0.3.0 final-review follow-ups on the server: logging and the child's stdin."""
    code, _out, err, by_id, _f = _mcp_session([
        _mcp_call(801, "score_buyer", {"as_of": AS_OF, "input_path": "../x.json"}),
        _mcp_call(802, "score_buyer", {"as_of": AS_OF, "input_path": "buyers.golden.json",
                                       "output_path": "buyers.golden.json"}),
        _mcp_req(803, "ping")], root, extra=())
    refused = [ln for ln in err.splitlines() if ln == "kbtm-mcp: score_buyer refused"]
    report.check("mcp: M-47 without --quiet a refused call still logs one "
                 "'kbtm-mcp: <tool> refused' line on stderr",
                 code == 0 and len(refused) == 2
                 and all(_mcp_is_error(by_id.get(i)) is True for i in (801, 802)),
                 "exit %d stderr %r" % (code, err[-300:]))

    # A driver process whose OWN stdin holds a valid buyer document: a child that
    # inherited it would score that document and exit 0; with stdin closed it exits 2.
    driver = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "sys.dont_write_bytecode = True\n"
        "import mcp_server as m\n"
        "tool = [t for t in m.TOOLS if t['name'] == 'score_buyer'][0]\n"
        "m.build_command = lambda tool, arguments, root: (['--as-of', %r], None, None, "
        "'json')\n"
        "result = m.call_tool(tool, {}, {'root': %r, 'timeout': 120, 'max_inline': 1 << 24,"
        " 'quiet': True, 'python': sys.executable})\n"
        "print(json.dumps(result['structuredContent']))\n" % (SCRIPT_DIR, AS_OF, root))
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with open(os.path.join(FIXTURES, "buyers.golden.json"), "rb") as fh:
        feed = fh.read()
    proc = subprocess.run([sys.executable, "-c", driver], input=feed, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, cwd=PKG_ROOT, env=env, timeout=300)
    try:
        structured = json.loads(proc.stdout.decode("utf-8"))
    except ValueError:
        structured = {}
    report.check("mcp: M-48 a child started without a stdin document gets an empty stdin, "
                 "never the server's own (the driver's stdin holds a valid document)",
                 proc.returncode == 0 and structured.get("exit_code") == 2
                 and "empty" in str(structured.get("error")),
                 "exit %d %s %s" % (proc.returncode, proc.stdout[:300],
                                    proc.stderr.decode("utf-8", "replace")[-300:]))


REVIEW_BAD_AS_OF = "2026-13-01"


def _review_exit(report, label, script, args, code_expected, expect, stdin_data=None):
    code, out, err = run_script(script, args, stdin_data)
    problems = []
    if code != code_expected:
        problems.append("exit %d, expected %d" % (code, code_expected))
    if "Traceback (most recent call last)" in err:
        problems.append("raw traceback escaped main()")
    if expect and expect not in err:
        problems.append("stderr lacks %r: %s" % (expect, err.strip()[-300:]))
    report.check(label, not problems, "\n".join(problems))
    return code, out, err


def _review_as_of_cases(report, buyers_scored, sellers_scored, temp):
    # R-04: one exit code for an impossible calendar date in every script (7.3: exit 2).
    reviews = os.path.join(FIXTURES, "reviews.buyers.uae.csv")
    rows = [
        ("normalize_company.py", ["--input", os.path.join(FIXTURES, "buyers.golden.json")]),
        ("dedupe_companies.py", ["--input", os.path.join(FIXTURES, "buyers.golden.json")]),
        ("score_buyer.py", ["--input", os.path.join(FIXTURES, "buyers.golden.json")]),
        ("score_seller.py", ["--input", os.path.join(FIXTURES, "sellers.golden.json")]),
        ("score_match.py", ["--input", os.path.join(FIXTURES, "match-134.input.json")]),
        ("validate_output.py", ["--input", buyers_scored]),
        ("make_review_sheet.py", ["--input", buyers_scored]),
        ("acceptance_report.py", ["--scored", buyers_scored, "--reviews", reviews]),
        ("diff_runs.py", ["--before", buyers_scored, "--after", buyers_scored]),
        ("stale_evidence.py", ["--input", buyers_scored]),
        ("export_leads.py", ["--input", buyers_scored]),
    ]
    wrong = []
    for script, args in rows:
        for value in (REVIEW_BAD_AS_OF, "garbage"):
            code, _o, err = run_script(script, args + ["--as-of", value])
            if code != 2 or "--as-of" not in err or "Traceback" in err:
                wrong.append("%s --as-of %s: exit %d %s" % (script, value, code,
                                                            err.strip()[-160:]))
    report.check("review: R-04 an impossible or malformed --as-of is exit 2 (a usage "
                 "error, BUILD-CONTRACT 7.3) in all eleven scripts", not wrong,
                 "\n".join(wrong[:12]))
    for script, args in (("export_leads.py", ["--input", buyers_scored]),
                         ("acceptance_report.py", ["--scored", buyers_scored,
                                                   "--reviews", reviews])):
        _review_exit(report, "review: R-05 %s refuses an empty --as-of (exit 2) instead of "
                     "falling back or blaming another field" % script, script,
                     args + ["--as-of", ""], 2, "--as-of is empty")


def _review_validate_cases(report, temp):
    expected_body = os.path.join(FIXTURES, "expected", "export.buyers.uae.tradewith.json")
    code, out, _e = run_script("validate_output.py", ["--input", expected_body, "--strict",
                                                       "--json"])
    doc = json.loads(out) if out.strip().startswith("{") else {}
    report.check("review: R-01 validate_output detects a tradewith-json body as "
                 "tradewith-bulk-buyers and the golden passes --strict",
                 code == 0 and doc.get("schema") == "tradewith-bulk-buyers"
                 and doc.get("valid") is True, "exit %d %s" % (code, out[:300]))
    bad = _write_temp_json({"buyers": [{"foo": 1}]}, "kbtm-review-bulk-")
    temp.append(bad)
    for extra, label in ((["--schema", "tradewith-bulk-buyers"], "--schema tradewith-bulk-"
                          "buyers"), ([], "auto detection")):
        code, out, err = run_script("validate_output.py", ["--input", bad, "--json"] + extra)
        doc = json.loads(out) if out.strip().startswith("{") else {}
        report.check("review: R-01 an invalid export body fails validate_output under %s "
                     "(exit 1, schema errors)" % label,
                     code == 1 and doc.get("valid") is False
                     and any("sourceId" in e.get("message", "") for e in doc.get("errors", [])),
                     "exit %d %s %s" % (code, out[:300], err.strip()[-200:]))
    with open(expected_body, encoding="utf-8") as fh:
        body = json.load(fh)
    body["buyers"][0]["social"] = "https://www.linkedin.com/company/../in/ahmed-khan"
    traversal = _write_temp_json(body, "kbtm-review-social-")
    temp.append(traversal)
    code, out, _e = run_script("validate_output.py", ["--input", traversal, "--json"])
    doc = json.loads(out) if out.strip().startswith("{") else {}
    report.check("review: R-03 the export schema's social pattern refuses a /company/ URL "
                 "that resolves to a member profile (a hand-edited body)",
                 code == 1 and any("social" in e.get("path", "") for e in doc.get("errors", [])),
                 "exit %d %s" % (code, out[:300]))
    unknown = _write_temp_json({"foo": 1}, "kbtm-review-unknown-")
    temp.append(unknown)
    code, out, _e = run_script("validate_output.py", [
        "--input", unknown, "--json", "--schema-file",
        os.path.join(SCHEMA_DIR, "tradewith-bulk-buyers.schema.json")])
    doc = json.loads(out) if out.strip().startswith("{") else {}
    report.check("review: R-02 --schema-file validates a document whose kind is not "
                 "detectable instead of passing it with only a warning",
                 code == 1 and doc.get("valid") is False and doc.get("errors"),
                 "exit %d %s" % (code, out[:300]))
    digest = os.path.join(FIXTURES, "expected", "match-134.expected.json")
    for extra in ([], ["--no-validate"]):
        code, out, err = run_script("validate_output.py", ["--input", digest, "--json"] + extra)
        doc = json.loads(out) if out.strip().startswith("{") else {}
        report.check("review: R-06 a document that only looks like a match-result is "
                     "reported invalid, not a crash%s" % (" (--no-validate)" if extra else ""),
                     code == 1 and "Traceback" not in err and "AttributeError" not in err
                     and doc.get("valid") is False,
                     "exit %d %s %s" % (code, out[:200], err.strip()[-200:]))


def _review_export_cases(report, buyers_out, temp):
    def gulf(d):
        return _record(d, "BUY-gulfglow-example")

    def social(value):
        return lambda d: gulf(d)["contact_channels"].append({"type": "linkedin",
                                                              "value": value})

    for value in ("https://www.linkedin.com/company/../in/ahmed-khan",
                  "https://www.linkedin.com/company/%2e%2e/in/ahmed-khan",
                  "https://www.linkedin.com/company//in/ahmed-khan"):
        path = _export_variant(buyers_out, temp, social(value))
        code, out, err = run_script("export_leads.py", ["--input", path, "--format",
                                                        "tradewith-json"])
        rows = json.loads(out)["buyers"] if code == 0 else []
        leaked = [r for r in rows if "ahmed-khan" in r.get("social", "")]
        report.check("review: R-07 a /company/ URL that resolves to a member profile (%s) "
                     "never becomes TradeWith social" % value.split("/company/")[1],
                     code == 0 and not leaked and "withheld 1 linkedin" in err,
                     "exit %d %s" % (code, err.strip()[-200:]))
        code, out, err = run_script("export_leads.py", ["--input", path])
        report.check("review: R-07 the generic csv withholds it too (%s)"
                     % value.split("/company/")[1],
                     code == 0 and "ahmed-khan" not in out and "withheld 1 linkedin" in err,
                     "exit %d %s" % (code, err.strip()[-200:]))

    def evidence_url(url):
        def mutate(d):
            gulf(d)["evidence"][0]["source_url"] = url
        return mutate

    encoded = _export_variant(buyers_out, temp, evidence_url(
        "https://gulfglow.example/x?email=ahmed.khan%40gulfglow.example"))
    _export_refusal(report, ["--input", encoded, "--format", "tradewith-json"], 1, "email",
                    "a percent-encoded address in the tier-1 sourceUrl (R-08)")
    slug = _export_variant(buyers_out, temp, evidence_url(
        "https://gulfglow.example/team/ahmed-khan-mobile-0501234567"))
    _export_refusal(report, ["--input", slug, "--format", "tradewith-json"], 1, "phone",
                    "a mobile number in a URL slug in the sourceUrl (R-09)")
    form = _export_variant(buyers_out, temp, lambda d: gulf(d)["contact_channels"][0].update(
        {"value": "https://gulfglow.example/form?to=ahmed.khan%40gulfglow.example"}))
    _export_refusal(report, ["--input", form], 1, "email",
                    "a percent-encoded address in a partnership_form channel (R-10)")
    named = _export_variant(buyers_out, temp, lambda d: _record(
        d, "BUY-britsun-example")["contact_channels"][2].update(
            {"value": "+44 20 7946 0100 (Mr. Ahmed Khan, mobile)"}))
    _export_refusal(report, ["--input", named], 1, "phone",
                    "a phone channel that carries more than a bare number (R-11)")
    link = _export_variant(buyers_out, temp, lambda d: gulf(d)["contact_channels"].append(
        {"type": "messenger", "value": "https://api.whatsapp.com/send?phone=97145550111"}))
    code, out, err = run_script("export_leads.py", ["--input", link])
    report.check("review: R-12 an official WhatsApp business link still exports as a "
                 "messenger channel",
                 code == 0 and "messenger=https://api.whatsapp.com/send?phone=97145550111"
                 in out, "exit %d %s" % (code, err.strip()[-200:]))

    for name, label in ((" =1+1", "a leading space"), ("＝1+1", "a full-width '='")):
        path = _export_variant(buyers_out, temp, lambda d, n=name: gulf(d).update(
            {"company_name": n}))
        _export_refusal(report, ["--input", path, "--format", "tradewith-csv"], 1, "formula",
                        "a formula behind %s in tradewith-csv (R-13)" % label)
    spaced = _export_variant(buyers_out, temp, lambda d: gulf(d).update(
        {"company_name": " =1+1"}))
    code, out, err = run_script("export_leads.py", ["--input", spaced, "--format",
                                                    "tradewith-json"])
    report.check("review: R-14 tradewith-json keeps such a name verbatim and warns on stderr",
                 code == 0 and '"companyName":" =1+1"' in out
                 and "WARNING:" in err and "formula" in err,
                 "exit %d %s" % (code, err.strip()[-200:]))
    code, out, err = run_script("export_leads.py", ["--input", spaced])
    report.check("review: R-15 the generic csv guards a formula behind leading whitespace",
                 code == 0 and any(r["record_id"] == "BUY-gulfglow-example"
                                   and r["company_name"] == "' =1+1" for r in _csv_rows(out)),
                 "exit %d %s" % (code, err.strip()[-200:]))


def _review_scan_cases(report):
    sys.path.insert(0, SCRIPT_DIR)
    sys.dont_write_bytecode = True
    import _common
    clean = ["https://hotel-20240101.example/rooms",
             "https://registry.example/cert/0123456789",
             "see https://cdsco.example/q?no=BPOM%20NA18230100123 for the record",
             "https://telco.example/tel-plans/2024"]
    flagged = [text for text in clean if _common.personal_data_hits(text)]
    report.check("review: R-16 the widened URL scan still passes registry ids, dates and "
                 "words that merely contain 'tel'", not flagged, "\n".join(flagged))
    samples = [
        ("from urllib import request", True), ("import urllib.request", True),
        ("x = __import__('socket')", True), ("import importlib", True),
        ("    mod = importlib.import_module(name)", True), ("import smtplib", True),
        ("import subprocess", True), ("from urllib.parse import unquote, urlsplit", False),
        ("import json", False), ("# importlib is never used here", False)]
    wrong = [line for line, bad in samples if bool(_network_problem(line, False)) != bad]
    wrong += ["subprocess allowed: " + line for line in ("import subprocess",)
              if _network_problem(line, True)]
    report.check("review: R-17 the network scan flags from-imports, __import__, importlib "
                 "and a subprocess outside its allow-list", not wrong, "\n".join(wrong))


def phase_review_followups(report, allow_missing):
    """v0.3.0 final review: each case fails if its fix is reverted (tests/cases.md section 17)."""
    needed = ["export_leads.py", "validate_output.py", "diff_runs.py", "stale_evidence.py",
              "acceptance_report.py", "make_review_sheet.py"]
    absent = [n for n in needed if not os.path.isfile(os.path.join(SCRIPT_DIR, n))]
    if absent or missing_scripts():
        note = "scripts absent: %s" % ", ".join(absent or missing_scripts())
        if allow_missing:
            report.skip("review: v0.3.0 review follow-ups", note)
        else:
            report.fail("review: v0.3.0 review follow-ups", note)
        return
    temp = []
    try:
        code, buyers_out, err = run_script("score_buyer.py", [
            "--input", os.path.join(FIXTURES, "buyers.golden.json"),
            "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"),
            "--as-of", AS_OF, "--pretty"])
        if code != 0:
            report.fail("review: the UAE buyer run is scoreable", err.strip()[:400])
            return
        buyers_scored = _write_temp_text(buyers_out, "kbtm-review-buyers-", ".json")
        temp.append(buyers_scored)
        _review_as_of_cases(report, buyers_scored, None, temp)
        _review_validate_cases(report, temp)
        _review_export_cases(report, buyers_out, temp)
        _review_scan_cases(report)
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
        allow_subprocess = os.path.basename(path) in SUBPROCESS_OK_SCRIPTS
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
                problem = _network_problem(line, allow_subprocess)
                if problem:
                    network.append("%s:%d: %s: %s" % (rel, lineno, problem,
                                                      line.strip()[:100]))
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
    "references/calibration-notes.md",
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
    "schemas/run-diff.schema.json",
    "schemas/recheck-queue.schema.json",
    "schemas/tradewith-bulk-buyers.schema.json",
    "scripts/_common.py",
    "scripts/normalize_company.py",
    "scripts/dedupe_companies.py",
    "scripts/score_buyer.py",
    "scripts/score_seller.py",
    "scripts/score_match.py",
    "scripts/validate_output.py",
    "scripts/make_review_sheet.py",
    "scripts/acceptance_report.py",
    "scripts/diff_runs.py",
    "scripts/stale_evidence.py",
    "scripts/export_leads.py",
    "scripts/mcp_server.py",
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
# flag and argparse's own exit 2 is the correct answer (R7.1.4). stale_evidence.py joins
# too: it reads --input before it checks its required --as-of, so the same byte reaches
# the UTF-8 error rather than the missing-flag one.
CLI_SCRIPTS = [n for n in PIPELINE_SCRIPTS if n != "_common.py"] + ["make_review_sheet.py",
                                                                    "stale_evidence.py",
                                                                    "export_leads.py"]


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
# 6b. plugin packaging: repository manifests and the release builder (P-01..P-11)
#
# The manifests and tools/build_release.py live at the REPOSITORY root, outside the
# package, so an installed copy (a skills dir, the claude.ai sandbox, a plugin cache)
# has none of them and the whole phase reports one SKIP there.
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(PKG_ROOT)
MARKETPLACE_PATH = os.path.join(REPO_ROOT, ".claude-plugin", "marketplace.json")
OPENAI_MANIFEST_PATH = os.path.join(REPO_ROOT, "packaging", "openai", "plugin.json")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
BUILDER = os.path.join(TOOLS_DIR, "build_release.py")
SKILL_ZIP = "kbeauty-trade-matchmaker.zip"
PLUGIN_ZIP = "kbeauty-trade-matchmaker-plugin.zip"

# A plugin manifest or component dir at the package root changes how a plugin host
# discovers the skill and leaks into the claude.ai skill ZIP (mirrors the builder).
PLUGIN_COMPONENT_NAMES = (".claude-plugin", ".codex-plugin", ".agent-plugin", "plugin.json",
                          ".mcp.json", "mcp.json", ".app.json", "skills", "agents", "commands",
                          "hooks", "bin", "workflows", "output-styles", "monitors")
MARKETPLACE_TOP_KEYS = {"name", "owner", "description", "plugins"}
MARKETPLACE_ENTRY_KEYS = {"name", "source", "description", "version", "author", "homepage",
                          "repository", "keywords", "strict", "skills", "mcpServers"}
# Keys a skills-only portable plugin must never carry: component declarations would
# make it Desktop only or conflict with skills/ discovery, and the repository has no
# LICENSE file to point a license field at.
OPENAI_FORBIDDEN_KEYS = {"skills", "apps", "hooks", "mcpServers", "screenshots", "license",
                         "email"}
MCP_SERVER_SCRIPT_ARG = "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.py"
ARCHIVE_DROPPINGS = ("__pycache__", ".pyc", ".DS_Store", ".kbtm-install-manifest", ".omc/",
                     "tradewith-data")


MCP_SERVER_ARGS = [MCP_SERVER_SCRIPT_ARG, "--root", "${CLAUDE_PROJECT_DIR}"]
MCP_SERVER_REL = "scripts/mcp_server.py"


def _fixture_git_env():
    """Environment for git and the builder inside a throwaway fixture repository.

    User and system git config (hooks, autocrlf, signing) are shut out, and the commit
    identity and dates are fixed, so the fixture builds the same way on every machine.
    """
    env = dict(os.environ)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
                "GIT_AUTHOR_NAME": "kbtm fixture", "GIT_AUTHOR_EMAIL": "fixture@kbtm.example",
                "GIT_COMMITTER_NAME": "kbtm fixture",
                "GIT_COMMITTER_EMAIL": "fixture@kbtm.example",
                "GIT_AUTHOR_DATE": "2026-09-12T00:00:00+0000",
                "GIT_COMMITTER_DATE": "2026-09-12T00:00:00+0000"})
    return env


def run_tool(path, args, env=None):
    """run_script for a repository tool outside SCRIPT_DIR; cwd is the tool's repository."""
    if env is None:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, path] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True,
                          cwd=os.path.dirname(os.path.dirname(path)), env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _fixture_git(repo, args):
    proc = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=_fixture_git_env())
    if proc.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args[:2]),
                                           proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def _release_fixture(dest):
    """A throwaway git repository holding this checkout's releasable files, committed.

    The builder refuses a dirty or untracked package on purpose, so running it on the
    working checkout would make this suite depend on commit state (a stray out.json, a
    feature not yet committed). The fixture takes every git-tracked package file plus
    every 2.2 manifest file, as they are on disk now, and commits them in a fresh repo.
    """
    tracked = subprocess.run(["git", "-C", REPO_ROOT, "ls-files", "-z", "--",
                              os.path.basename(PKG_ROOT)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE).stdout.decode("utf-8").split("\0")
    prefix = os.path.basename(PKG_ROOT) + "/"
    rels = set(t[len(prefix):] for t in tracked if t.startswith(prefix))
    rels.update(rel for rel in MANIFEST if rel != "tests/fixtures")
    for rel in sorted(rels):
        source = os.path.join(PKG_ROOT, *rel.split("/"))
        if os.path.islink(source) or not os.path.isfile(source):
            continue
        target = os.path.join(dest, prefix, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
    for source in (BUILDER, MARKETPLACE_PATH, OPENAI_MANIFEST_PATH):
        target = os.path.join(dest, os.path.relpath(source, REPO_ROOT))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
    templates = os.path.join(dest, ".git-template")
    os.mkdir(templates)
    _fixture_git(dest, ["init", "-q", "--template=" + templates])
    shutil.rmtree(templates)
    _fixture_git(dest, ["add", "-A"])
    _fixture_git(dest, ["commit", "-q", "--no-gpg-sign", "-m", "fixture"])
    return os.path.join(dest, "tools", "build_release.py")


def _mcp_pairing_problems(entry, script_present, script_in_manifest):
    """P-04: the marketplace entry declares the MCP server exactly when the package ships it."""
    problems = []
    servers = entry.get("mcpServers")
    ships = script_present and script_in_manifest
    if servers is None:
        if script_present or script_in_manifest:
            problems.append("the package has %s (on disk: %s, in MANIFEST: %s) but the "
                            "marketplace entry declares no mcpServers"
                            % (MCP_SERVER_REL, script_present, script_in_manifest))
        return problems
    if not ships:
        problems.append("mcpServers is declared but %s is %s" % (
            MCP_SERVER_REL, "not in MANIFEST" if script_present else "not in the package"))
    if not (isinstance(servers, dict) and len(servers) == 1):
        problems.append("mcpServers must declare exactly one server")
        return problems
    server = list(servers.values())[0]
    if not isinstance(server, dict):
        problems.append("the server entry is not an object")
        return problems
    if server.get("command") != "python3":
        problems.append("command %r, expected python3" % server.get("command"))
    if server.get("args") != MCP_SERVER_ARGS:
        problems.append("args %r, expected %r" % (server.get("args"), MCP_SERVER_ARGS))
    banned = sorted(set(server) & {"url", "env", "headers", "headersHelper"})
    if banned or server.get("type", "stdio") != "stdio":
        problems.append("only a local stdio server is allowed (found %s)"
                        % (banned or server.get("type")))
    return problems


def _all_keys(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            _all_keys(value, out)
    elif isinstance(node, list):
        for value in node:
            _all_keys(value, out)
    return out


def _all_strings(node, out):
    if isinstance(node, dict):
        for value in node.values():
            _all_strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _all_strings(value, out)
    elif isinstance(node, str):
        out.append(node)
    return out


def _skill_version_from_common():
    path = os.path.join(SCRIPT_DIR, "_common.py")
    text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    found = re.search(r'^SKILL_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"', text, re.MULTILINE)
    return found.group(1) if found else None


def _allowlisted(rel):
    """True when a package-relative file is a BUILD-CONTRACT 2.2 manifest row."""
    return rel in MANIFEST or rel.startswith("tests/fixtures/")


def phase_plugins(report):
    if not os.path.isfile(MARKETPLACE_PATH):
        report.skip("plugins: repository manifests",
                    "not a repository checkout (installed copy or plugin cache)")
        return
    skill_version = _skill_version_from_common()
    skill_name = dict(_read_frontmatter(open(os.path.join(PKG_ROOT, "SKILL.md"),
                                             encoding="utf-8").read()) or []).get("name")
    manifests = {}
    for label, path in (("marketplace.json", MARKETPLACE_PATH),
                        ("packaging/openai/plugin.json", OPENAI_MANIFEST_PATH)):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            manifests[label] = (raw, json.loads(raw.decode("utf-8")))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            manifests[label] = (b"", None)
            report.fail("plugins: %s parses as UTF-8 JSON" % label, str(exc))
    market = manifests["marketplace.json"][1]
    openai = manifests["packaging/openai/plugin.json"][1]

    # ---- P-01 marketplace shape ------------------------------------------------------
    problems = []
    entry = {}
    if not isinstance(market, dict):
        problems.append("marketplace.json is not an object")
    else:
        extra = sorted(set(market) - MARKETPLACE_TOP_KEYS)
        if extra:
            problems.append("unexpected top-level keys %s" % extra)
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", str(market.get("name", ""))):
            problems.append("marketplace name %r is not kebab-case" % market.get("name"))
        if not (market.get("owner") or {}).get("name"):
            problems.append("owner.name is empty")
        plugins = market.get("plugins")
        if not (isinstance(plugins, list) and len(plugins) == 1
                and isinstance(plugins[0], dict)):
            problems.append("plugins must hold exactly one entry")
        else:
            entry = plugins[0]
            extra = sorted(set(entry) - MARKETPLACE_ENTRY_KEYS)
            if extra:
                problems.append("unexpected plugin-entry keys %s" % extra)
            if entry.get("name") != skill_name:
                problems.append("plugin name %r != SKILL.md name %r"
                                % (entry.get("name"), skill_name))
            if entry.get("source") != "./" + os.path.basename(PKG_ROOT) \
                    or entry.get("source") != "./kbeauty-trade-matchmaker":
                problems.append("source %r must be ./kbeauty-trade-matchmaker, the package "
                                "folder" % entry.get("source"))
            # The entry is the whole definition (no plugin.json inside the package), and
            # "./" names the root SKILL.md explicitly instead of relying on discovery.
            if entry.get("strict") is not False:
                problems.append("strict must be false: the entry is the whole definition")
            if entry.get("skills") != ["./"]:
                problems.append("skills must be [\"./\"] (the package root SKILL.md)")
    report.check("plugins: P-01 marketplace.json lists the package folder as one "
                 "single-skill plugin", not problems, "\n".join(problems))

    # ---- P-02 BUILD-CONTRACT 12.1 --------------------------------------------------
    stated = {"scripts/_common.py": skill_version,
              "marketplace.json plugins[0].version": entry.get("version"),
              "packaging/openai/plugin.json version":
                  openai.get("version") if isinstance(openai, dict) else None}
    report.check("plugins: P-02 BUILD-CONTRACT 12.1 every plugin manifest states "
                 "skill_version", len(set(stated.values())) == 1 and None not in
                 stated.values(), "; ".join("%s=%s" % kv for kv in sorted(stated.items())))

    # ---- P-03 no plugin component inside the package -------------------------------
    present = sorted(n for n in PLUGIN_COMPONENT_NAMES
                     if os.path.lexists(os.path.join(PKG_ROOT, n)))
    report.check("plugins: P-03 the package root holds no plugin manifest or component "
                 "dir", not present, "found: %s" % ", ".join(present))

    # ---- P-04 bundled MCP server declaration ---------------------------------------
    script_path = os.path.join(PKG_ROOT, *MCP_SERVER_REL.split("/"))
    problems = _mcp_pairing_problems(entry, os.path.isfile(script_path),
                                     MCP_SERVER_REL in MANIFEST)
    good = {"mcpServers": {"kbtm": {"command": "python3", "args": list(MCP_SERVER_ARGS)}}}
    no_root = {"mcpServers": {"kbtm": {"command": "python3", "args": [MCP_SERVER_SCRIPT_ARG]}}}
    for label, case, present, listed, want_ok in (
            ("declared and shipped", good, True, True, True),
            ("neither", {}, False, False, True),
            ("declared, script missing", good, False, False, False),
            ("declared, script not in MANIFEST", good, True, False, False),
            ("shipped, not declared", {}, True, True, False),
            ("declared without --root", no_root, True, True, False)):
        if (not _mcp_pairing_problems(case, present, listed)) != want_ok:
            problems.append("pairing rule self-check failed: %s" % label)
    report.check("plugins: P-04 the marketplace entry declares the stdio MCP server exactly "
                 "when the package ships scripts/mcp_server.py", not problems,
                 "\n".join(problems))

    # ---- P-05 OpenAI portable manifest, structural only -----------------------------
    problems = []
    if not isinstance(openai, dict):
        problems.append("packaging/openai/plugin.json is not an object")
    else:
        if not str(openai.get("$schema", "")).startswith("https://"):
            problems.append("$schema is missing")
        if openai.get("name") != skill_name:
            problems.append("name %r != SKILL.md name %r" % (openai.get("name"), skill_name))
        if not str(openai.get("description", "")).strip():
            problems.append("description is empty")
        if not str((openai.get("author") or {}).get("name", "")).strip():
            problems.append("author.name is empty")
        extensions = openai.get("extensions") or {}
        if set(extensions) - {"com.openai"}:
            problems.append("unexpected extensions %s" % sorted(set(extensions) - {"com.openai"}))
        interface = (extensions.get("com.openai") or {}).get("interface") or {}
        if not str(interface.get("displayName", "")).strip():
            problems.append("extensions.com.openai.interface.displayName is empty")
        banned = sorted(_all_keys(openai, set()) & OPENAI_FORBIDDEN_KEYS)
        if banned:
            problems.append("keys a skills-only plugin must not carry: %s" % banned)
    report.check("plugins: P-05 the OpenAI manifest is a skills-only portable plugin for "
                 "this skill", not problems, "\n".join(problems))

    # ---- P-06 portability and safety of both manifests ------------------------------
    problems = []
    common = None
    try:
        sys.path.insert(0, SCRIPT_DIR)
        sys.dont_write_bytecode = True
        import _common as common
    except Exception as exc:  # pragma: no cover - defensive
        problems.append("scripts/_common.py could not be imported: %s" % exc)
    for label, (raw, doc) in sorted(manifests.items()):
        text = raw.decode("utf-8", "replace")
        if "\r" in text or not text.endswith("\n") or text.endswith("\n\n"):
            problems.append("%s: not LF with exactly one final newline" % label)
        if any(line != line.rstrip() for line in text.splitlines()):
            problems.append("%s: trailing whitespace" % label)
        if re.search(r"auto[_-]send", text, re.IGNORECASE):
            problems.append("%s: names auto_send" % label)
        if "email" in _all_keys(doc, set()):
            problems.append("%s: carries an email field" % label)
        for value in _all_strings(doc, []):
            if common is not None and not value.startswith("https://") \
                    and common.personal_data_hits(value):
                problems.append("%s: personal-data shape in %r" % (label, value[:60]))
    report.check("plugins: P-06 manifests carry no personal contact and are UTF-8/LF",
                 not problems, "\n".join(problems))

    # ---- builder cases run on a throwaway fixture repository --------------------------
    if not os.path.isfile(BUILDER):
        report.fail("plugins: P-07 tools/build_release.py exists", "missing %s" % BUILDER)
        return
    if not os.path.lexists(os.path.join(REPO_ROOT, ".git")) or shutil.which("git") is None:
        report.skip("plugins: P-07..P-10 release builder",
                    "not a git checkout (the builder archives committed files only)")
        _phase_plugins_tool_scan(report)
        return
    with tempfile.TemporaryDirectory(prefix="kbtm-rel-fixture-") as scratch:
        base = os.path.join(scratch, "base")
        os.mkdir(base)
        try:
            builder = _release_fixture(base)
        except (OSError, RuntimeError) as exc:
            report.fail("plugins: P-07..P-10 fixture repository", str(exc))
            _phase_plugins_tool_scan(report)
            return
        _phase_plugins_builder(report, scratch, base, builder)
    _phase_plugins_tool_scan(report)


def _phase_plugins_builder(report, scratch, base, builder):
    env = _fixture_git_env()
    fixture_pkg = os.path.join(base, os.path.basename(PKG_ROOT))

    # ---- P-07 skill archive -----------------------------------------------------------
    code, out, err = run_tool(builder, ["--list"], env)
    listing = None
    try:
        listing = json.loads(out) if code == 0 else None
    except ValueError:
        listing = None
    archives = dict((a.get("file"), a.get("entries")) for a in (listing or {}).get(
        "archives", []) if isinstance(a, dict))
    problems = []
    if code != 0 or err.strip() or listing is None:
        problems.append("--list exit %d, stderr %r" % (code, err.strip()[:200]))
    skill_entries = archives.get(SKILL_ZIP) or []
    prefix = "kbeauty-trade-matchmaker/"
    skill_files = [n[len(prefix):] for n in skill_entries
                   if n.startswith(prefix) and not n.endswith("/")]
    if not skill_entries or any(not n.startswith(prefix) for n in skill_entries):
        problems.append("skill archive entries must all sit under %s" % prefix)
    if prefix + "SKILL.md" not in skill_entries:
        problems.append("skill archive lacks %sSKILL.md" % prefix)
    dropped = [n for n in skill_entries if any(d in n for d in ARCHIVE_DROPPINGS)]
    if dropped:
        problems.append("local droppings archived: %s" % dropped[:5])
    # Allowlist both ways (BUILD-CONTRACT 2.2): every manifest row ships, and no
    # git-tracked file outside the manifest rows ships.
    files_listed = [rel for rel in MANIFEST if rel != "tests/fixtures"]
    absent = [rel for rel in files_listed if rel not in skill_files]
    if absent:
        problems.append("manifest files missing from the archive: %s" % absent[:8])
    if not any(rel.startswith("tests/fixtures/") for rel in skill_files):
        problems.append("no tests/fixtures/ file is archived")
    stray = [rel for rel in skill_files if not _allowlisted(rel)]
    if stray:
        problems.append("git-tracked files outside the BUILD-CONTRACT 2.2 manifest: %s"
                        % stray[:8])
    report.check("plugins: P-07 the skill ZIP holds exactly the 2.2 manifest under one "
                 "top folder", not problems, "\n".join(problems))

    # ---- P-08 plugin archive -----------------------------------------------------------
    problems = []
    plugin_entries = archives.get(PLUGIN_ZIP) or []
    skill_prefix = "skills/kbeauty-trade-matchmaker/"
    roots = sorted(set(n.split("/", 1)[0] + ("/" if "/" in n else "") for n in plugin_entries))
    if roots != ["plugin.json", "skills/"]:
        problems.append("archive root is %s, expected plugin.json and skills/" % roots)
    outside = [n for n in plugin_entries
               if n not in ("plugin.json", "skills/") and not n.startswith(skill_prefix)]
    if outside:
        problems.append("entries outside %s: %s" % (skill_prefix, outside[:5]))
    for name in plugin_entries:
        leaf = name.rstrip("/").split("/")[-1]
        if leaf in ("mcp.json", ".mcp.json", ".app.json") and name.count("/") <= 2:
            problems.append("MCP/app declaration archived: %s" % name)
        if ".." in name.split("/") or name.startswith("/") or name.count("/") > 20:
            problems.append("unsafe path: %s" % name)
    plugin_files = [n[len(skill_prefix):] for n in plugin_entries
                    if n.startswith(skill_prefix) and not n.endswith("/")]
    if sorted(plugin_files) != sorted(skill_files):
        problems.append("the two archives carry different package files")
    report.check("plugins: P-08 the plugin ZIP is plugin.json plus the same files under "
                 "skills/<name>/", not problems, "\n".join(problems))

    # ---- P-09 INV-13 determinism and the --as-of timestamp -------------------------------
    import zipfile
    problems = []
    code2, out2, _err2 = run_tool(builder, ["--list"], env)
    if (code2, out2) != (code, out):
        problems.append("two --list runs differ")
    reports = []
    for label, extra in (("a", []), ("b", []), ("dated", ["--as-of", "2026-09-19"])):
        directory = os.path.join(scratch, "out-" + label)
        os.mkdir(directory)
        rc, stdout, stderr = run_tool(builder, ["--out", directory, "--quiet"] + extra, env)
        if rc != 0 or stderr.strip():
            problems.append("--out %s exit %d: %s" % (label, rc, stderr.strip()[:200]))
            continue
        reports.append(stdout)
        for item in json.loads(stdout).get("archives", []):
            with open(os.path.join(directory, item["file"]), "rb") as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != item.get("sha256"):
                problems.append("%s: reported sha256 does not match the file" % item["file"])
        want_date = (2026, 9, 19, 0, 0, 0) if extra else (1980, 1, 1, 0, 0, 0)
        for name in (SKILL_ZIP, PLUGIN_ZIP):
            with zipfile.ZipFile(os.path.join(directory, name)) as archive:
                for info in archive.infolist():
                    mode = info.external_attr >> 16
                    want = 0o40755 if info.filename.endswith("/") else (
                        0o100755 if info.filename.endswith("/install.sh") else 0o100644)
                    if info.date_time != want_date or mode != want:
                        problems.append("%s %s: date %s mode %o (want %s, %o)"
                                        % (label, info.filename, info.date_time, mode,
                                           want_date, want))
                        break
                if name == SKILL_ZIP and [i.filename for i in archive.infolist()] \
                        != skill_entries:
                    problems.append("--out archive order differs from --list")
    if len(reports) == 3:
        if reports[0] != reports[1]:
            problems.append("two --out builds produced different bytes")
        if reports[2] == reports[0] or '"timestamp":"2026-09-19T00:00:00"' not in reports[2]:
            problems.append("--as-of 2026-09-19 did not change the entry timestamps")
    report.check("plugins: P-09 INV-13 identical input builds byte-identical archives; "
                 "--as-of sets every entry date", not problems, "\n".join(problems))

    # ---- P-10 refusals ---------------------------------------------------------------------
    problems = []

    def refused(tool, args, want, label, needle=None):
        rc, stdout, stderr = run_tool(tool, args, env)
        lines = [ln for ln in stderr.splitlines() if ln.startswith("ERROR:")]
        if rc != want or len(lines) != 1 or stdout.strip() \
                or "Traceback (most recent call last)" in stderr \
                or (needle is not None and needle not in stderr):
            problems.append("%s: exit %d (want %d), stderr %r, stdout %r"
                            % (label, rc, want, stderr.strip()[:160], stdout[:80]))

    counter = [0]

    def variant(mutate, commit):
        """A clone of the fixture with one mutation; returns its builder path."""
        counter[0] += 1
        repo = os.path.join(scratch, "v%02d" % counter[0])
        subprocess.run(["git", "clone", "-q", base, repo], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=env, check=True)
        mutate(repo, os.path.join(repo, os.path.basename(PKG_ROOT)))
        if commit:
            _fixture_git(repo, ["add", "-A"])
            _fixture_git(repo, ["commit", "-q", "--no-gpg-sign", "-m", "mutation"])
        return os.path.join(repo, "tools", "build_release.py")

    def write(path, text):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def edit_json(path, change):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        change(doc)
        write(path, json.dumps(doc, indent=2) + "\n")

    # usage (exit 2); none may leave a file behind in the package
    before = sorted(os.listdir(os.path.join(fixture_pkg, "tests")))
    refused(builder, [], 2, "no --out and no --list")
    refused(builder, ["--list", "--out", base], 2, "--out with --list")
    refused(builder, ["--out", os.path.join(base, "no-such-dir")], 2, "missing --out dir")
    refused(builder, ["--out", os.path.join(fixture_pkg, "tests")], 2, "--out in the package")
    refused(builder, ["--list", "--as-of", "2026-9-12"], 2, "malformed --as-of")
    refused(builder, ["--list", "--as-of", "2026-02-31"], 2, "impossible --as-of",
            "not a calendar date")
    if sorted(os.listdir(os.path.join(fixture_pkg, "tests"))) != before:
        problems.append("a refused build left files in tests/")

    # the checkout differs from HEAD (exit 1)
    refused(variant(lambda r, p: write(os.path.join(p, "scratch.txt"), "x\n"), False),
            ["--list"], 1, "untracked package file", "untracked")
    refused(variant(lambda r, p: write(os.path.join(p, "references", "evidence-policy.md"),
                                       "uncommitted\n"), False),
            ["--list"], 1, "edited, uncommitted tracked file", "uncommitted changes")
    refused(variant(lambda r, p: os.unlink(os.path.join(p, "references",
                                                        "evidence-policy.md")), False),
            ["--list"], 1, "deleted, uncommitted tracked file", "uncommitted changes")
    refused(variant(lambda r, p: write(os.path.join(r, "packaging", "openai", "plugin.json"),
                                       "{}\n"), False),
            ["--list"], 1, "edited, uncommitted manifest", "uncommitted changes")
    refused(variant(lambda r, p: os.mkdir(os.path.join(p, "agents")), False),
            ["--list"], 1, "plugin component dir in the package", "agents")

    # a committed state that cannot ship
    refused(variant(lambda r, p: edit_json(
        os.path.join(r, ".claude-plugin", "marketplace.json"),
        lambda d: d["plugins"][0].update(version="9.9.9")), True),
        ["--list"], 1, "version drift", "skill_version disagrees")
    refused(variant(lambda r, p: edit_json(
        os.path.join(r, "packaging", "openai", "plugin.json"),
        lambda d: d.update(version=["0.2.0"])), True),
        ["--list"], 1, "list-valued version", "must be a version string")
    refused(variant(lambda r, p: write(os.path.join(r, "packaging", "openai", "plugin.json"),
                                       "{not json\n"), True),
            ["--list"], 2, "invalid manifest JSON", "not valid UTF-8 JSON")
    refused(variant(lambda r, p: os.unlink(os.path.join(r, "packaging", "openai",
                                                        "plugin.json")), True),
            ["--list"], 2, "manifest missing from HEAD", "not in the HEAD commit")
    try:
        link_builder = variant(lambda r, p: os.symlink("SKILL.md", os.path.join(p, "link.md")),
                               True)
    except (OSError, NotImplementedError):
        link_builder = None
    if link_builder is not None:
        refused(link_builder, ["--list"], 1, "tracked symlink", "symlink tracked")

    # bytes come from HEAD even when the index is told to ignore a working-tree edit
    policy = os.path.join("references", "evidence-policy.md")
    with open(os.path.join(fixture_pkg, policy), "rb") as fh:
        committed = fh.read()

    def hide_edit(repo, pkg):
        _fixture_git(repo, ["update-index", "--assume-unchanged",
                            os.path.basename(PKG_ROOT) + "/" + policy.replace(os.sep, "/")])
        write(os.path.join(pkg, policy), "HIDDEN EDIT\n")

    hidden = variant(hide_edit, False)
    hidden_out = os.path.join(os.path.dirname(os.path.dirname(hidden)), "dist")
    os.mkdir(hidden_out)
    rc, _stdout, stderr = run_tool(hidden, ["--out", hidden_out, "--quiet"], env)
    shipped = None
    if rc == 0:
        with zipfile.ZipFile(os.path.join(hidden_out, SKILL_ZIP)) as archive:
            shipped = archive.read(prefix + policy.replace(os.sep, "/"))
    if shipped != committed:
        problems.append("a hidden working-tree edit reached the archive (exit %d: %s)"
                        % (rc, stderr.strip()[:120]))

    # a failure on either target leaves both targets untouched
    blocked = os.path.join(scratch, "blocked")
    os.mkdir(blocked)
    os.mkdir(os.path.join(blocked, PLUGIN_ZIP))
    refused(builder, ["--out", blocked, "--quiet"], 1, "plugin target is a directory",
            "not a regular file")
    if sorted(os.listdir(blocked)) != [PLUGIN_ZIP]:
        problems.append("a refused build still wrote %s" % sorted(os.listdir(blocked)))

    # a symlink at a target is replaced, never written through
    out_dir = os.path.join(scratch, "linked")
    os.mkdir(out_dir)
    sentinel = os.path.join(scratch, "sentinel.txt")
    write(sentinel, "untouched\n")
    target = os.path.join(out_dir, SKILL_ZIP)
    try:
        os.symlink(sentinel, target)
        linked = True
    except (OSError, NotImplementedError):
        linked = False
    if linked:
        rc, _stdout, stderr = run_tool(builder, ["--out", out_dir, "--quiet"], env)
        with open(sentinel, encoding="utf-8") as fh:
            if fh.read() != "untouched\n":
                problems.append("a symlinked target was written through")
        if rc != 0 or os.path.islink(target) or not os.path.isfile(target):
            problems.append("a symlinked target was not replaced by the archive "
                            "(exit %d: %s)" % (rc, stderr.strip()[:120]))
        leftovers = [n for n in os.listdir(out_dir) if n.startswith(".kbtm-build-")]
        if leftovers:
            problems.append("temp files left behind: %s" % leftovers)
    report.check("plugins: P-10 every refusal exits cleanly, only committed bytes ship, "
                 "and --out never writes through a symlink or half a pair", not problems,
                 "\n".join(problems))


def _phase_plugins_tool_scan(report):
    # ---- P-11 the scripts-only scans, applied to tools/*.py ------------------------------
    problems = []
    for path in sorted(glob.glob(os.path.join(TOOLS_DIR, "*.py"))):
        rel = os.path.relpath(path, REPO_ROOT)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        try:
            compile(text, path, "exec")
        except SyntaxError as exc:
            problems.append("%s: %s" % (rel, exc))
        for lineno, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            where = "%s:%d: %s" % (rel, lineno, line.strip()[:100])
            if any(re.search(p, line, re.IGNORECASE) for p in SEND_PATTERNS) \
                    and not any(m in low for m in NEGATION_MARKERS):
                problems.append("send capability " + where)
            if any(re.search(p, line) for p in WALL_CLOCK_PATTERNS + PLACEHOLDER_PATTERNS):
                problems.append("clock read or placeholder " + where)
            match = re.match(r"\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if match:
                module = match.group(1)
                if module not in STDLIB_OK and module != "__future__":
                    problems.append("non-allowlisted import " + where)
            # tools/build_release.py runs git; nothing else under tools/ starts a child.
            problem = _network_problem(
                line, os.path.basename(rel) == "build_release.py")
            if problem:
                problems.append(problem + " " + where)
    report.check("plugins: P-11 tools/*.py pass the send, clock, placeholder, stdlib and "
                 "network scans", not problems, "\n".join(problems[:10]))


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
    phase_run_diff(report, args.allow_missing_scripts)
    phase_recheck(report, args.allow_missing_scripts)
    phase_export(report, args.allow_missing_scripts)
    phase_mcp_server(report, args.allow_missing_scripts)
    phase_review_followups(report, args.allow_missing_scripts)
    phase_safety(report)
    phase_package(report)
    phase_plugins(report)
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
