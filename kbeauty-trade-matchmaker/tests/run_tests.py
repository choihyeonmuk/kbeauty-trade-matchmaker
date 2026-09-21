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
import glob
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
        # The formula above is a copy; score_match.py's own _rfq_readiness and the block it
        # writes to extensions.rfq_readiness are the real output, so both are checked and a
        # missing block is a failure (it used to be looked up under rfq_constraints, where
        # score_match never writes it, so this cross-check never ran).
        sys.path.insert(0, SCRIPT_DIR)
        sys.dont_write_bytecode = True
        import score_match  # noqa: E402
        real_score, real_detail = score_match._rfq_readiness(rfq)
        report.check("readiness: %s score_match._rfq_readiness agrees with expected" % name,
                     real_score == want["qualification_score"]
                     and real_detail["filled"] == want["readiness_detail"]["filled"]
                     and real_detail["missing_fields"] == want["readiness_detail"]["missing_fields"],
                     "got %r %r" % (real_score, real_detail))
        result = pipeline.match_134 if name == "rfq.134.json" else pipeline.match_nomatch
        emitted = ((result or {}).get("extensions") or {}).get("rfq_readiness")
        report.check("readiness: %s extensions.rfq_readiness written by score_match.py agrees"
                     % name,
                     isinstance(emitted, dict)
                     and emitted.get("qualification_score") == want["qualification_score"]
                     and emitted.get("readiness_detail") == real_detail,
                     "extensions.rfq_readiness = %r" % (emitted,))

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
            # EV-002 and EV-003 are cross-linked to each other: ONE unresolved conflict,
            # counted per unordered id pair (-10), not once per item (-20).
            if (rec.get("dimension_scores") or {}).get("evidence_quality") != 74:
                problems.append("evidence_quality = %r, expected 74 (one reciprocal conflict, "
                                "conflict_penalty -10)"
                                % (rec.get("dimension_scores") or {}).get("evidence_quality"))
            if rec.get("confidence") != 0.47:
                problems.append("confidence = %r, expected 0.47 (0.74 x 0.75 x 0.85)"
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
# 4c. audit regressions - verified findings of the scoring-track audit
#
# Each case reproduces an audit finding against the pre-fix code: --config not
# reaching confidence / evidence quality, MOQ unit synonyms switching HF-03 off,
# unmapped or marker-only categories zeroing product fit, country names falling to
# unknown, an invalid document written to --output, the uncited-certification
# incentive, reciprocal conflicts priced twice and glued Korean legal forms.
# ---------------------------------------------------------------------------
AUDIT_CASES = ("AR-01 --config reaches confidence and evidence quality",
               "AR-02 MOQ unit synonyms keep HF-03 on",
               "AR-03 unmapped or marker-only categories never reject",
               "AR-04 country names normalise to alpha-2",
               "AR-05 an invalid document never lands on --output",
               "AR-06 an uncited certification costs at least a tier-4 one",
               "AR-07 a reciprocal conflict is one conflict",
               "AR-08 glued Korean legal forms and spacing dedupe")


def _json_copy(document):
    return json.loads(json.dumps(document, ensure_ascii=False))


def _run_json(script, args):
    code, out, err = run_script(script, args)
    try:
        return code, json.loads(out), err
    except ValueError:
        return code, None, err


def _match_seller_outcome(doc, seller_id):
    """("result" | "excluded" | None, entry) for one seller of a match-result document."""
    for item in (doc or {}).get("results") or []:
        if item.get("seller_id") == seller_id:
            return "result", item
    for item in (doc or {}).get("excluded") or []:
        if item.get("seller_id") == seller_id:
            return "excluded", item
    return None, None


def _score_match_envelope(envelope, extra=None):
    path = _write_temp_json(envelope, "kbtm-ar-match-")
    try:
        return _run_json("score_match.py", ["--input", path, "--as-of", AS_OF] + list(extra or []))
    finally:
        os.unlink(path)


def phase_audit_regressions(report, allow_missing):
    scripts_present = all(
        os.path.isfile(os.path.join(SCRIPT_DIR, name)) for name in PIPELINE_SCRIPTS
    )
    if not scripts_present:
        for name in AUDIT_CASES:
            (report.skip if allow_missing else report.fail)("audit regression: %s" % name,
                                                            "scripts/ not present")
        return

    sys.path.insert(0, SCRIPT_DIR)
    sys.dont_write_bytecode = True
    import _common  # noqa: F811
    import dedupe_companies  # noqa: E402

    config = read_json(os.path.join(SCHEMA_DIR, "scoring.config.json"))
    match_input = read_json(os.path.join(FIXTURES, "match-134.input.json"))
    seller0 = match_input["records"][0]
    seller0_id = seller0["seller_id"]
    seller_query = os.path.join(FIXTURES, "query-seller-sunscreen-oem.json")

    # -- AR-01 (scoring-06, scoring-07) --------------------------------------
    # score_match.py typed the confidence constants in, and no scorer passed the config
    # to _common.evidence_quality, so --config changed neither value.
    problems = []
    ev_config = _json_copy(config)
    ev_config["evidence"]["official_bonus"] = 0
    ev_config["evidence"]["components"]["coverage"]["weight"] = 0
    conf_config = _json_copy(config)
    conf_config["confidence"]["coverage_factor"]["base"] = 0.5
    conf_config["confidence"]["min"] = 0.01
    ev_path = _write_temp_json(ev_config, "kbtm-ar-evcfg-")
    conf_path = _write_temp_json(conf_config, "kbtm-ar-confcfg-")
    try:
        runs = (
            ("score_buyer.py", ["--input", os.path.join(FIXTURES, "buyers.golden.json"),
                                "--query", os.path.join(FIXTURES, "query-buyer-uk-sunscreen.json"),
                                "--as-of", AS_OF], "records", "buyer_id", "dimension_scores"),
            ("score_seller.py", ["--input", os.path.join(FIXTURES, "sellers.golden.json"),
                                 "--query", seller_query, "--as-of", AS_OF],
             "records", "seller_id", "dimension_scores"),
            ("score_match.py", ["--input", os.path.join(FIXTURES, "match-134.input.json"),
                                "--as-of", AS_OF], "results", "seller_id", "component_scores"),
        )
        for script, base_args, list_key, id_key, score_key in runs:
            observed = {}
            for label, extra in (("default", []), ("evidence", ["--config", ev_path]),
                                 ("confidence", ["--config", conf_path])):
                code, doc, err = _run_json(script, base_args + extra)
                if doc is None:
                    problems.append("%s --config %s: no JSON (exit %d) %s"
                                    % (script, label, code, err.strip()[:200]))
                    break
                observed[label] = dict(
                    (item.get(id_key), ((item.get(score_key) or {}).get("evidence_quality"),
                                        item.get("confidence")))
                    for item in doc.get(list_key) or [])
            if len(observed) != 3:
                continue
            shared = set(observed["default"]) & set(observed["evidence"]) & set(observed["confidence"])
            if not shared:
                problems.append("%s: no record is returned under all three configs" % script)
                continue
            if all(observed["default"][i][0] == observed["evidence"][i][0] for i in shared):
                problems.append("%s: evidence.official_bonus / coverage weight in --config left "
                                "every evidence_quality unchanged" % script)
            if all(observed["default"][i][1] == observed["confidence"][i][1] for i in shared):
                problems.append("%s: confidence.coverage_factor.base in --config left every "
                                "confidence unchanged" % script)
            if any(observed["default"][i][0] != observed["confidence"][i][0] for i in shared):
                problems.append("%s: a confidence-only config change moved evidence_quality" % script)
            if script == "score_match.py":
                by_id = dict((s.get("seller_id"), s) for s in match_input["records"])
                for ident in sorted(shared):
                    eq, conf = observed["default"][ident]
                    want = _common.record_confidence(by_id[ident], "seller", eq, _common.load_config())
                    if conf != want:
                        problems.append("%s: match confidence %r differs from the shared "
                                        "_common.record_confidence %r" % (ident, conf, want))
    finally:
        os.unlink(ev_path)
        os.unlink(conf_path)
    report.check("audit regression: %s" % AUDIT_CASES[0], not problems, "\n".join(problems[:10]))

    # -- AR-02 (scoring-01, scoring-12) --------------------------------------
    # 'pcs' / 'EA' / '개' / 'unit' against a query in 'units' was a unit mismatch, so
    # HF-03 was skipped and a confirmed MOQ of 50,000 stayed qualified under a 3,000 max.
    problems = []
    for unit in ("pcs", "PCS", "pieces", "piece", "EA", "ea", "E.A.", "개", "unit", "units"):
        if _common.normalize_unit(unit) != "units":
            problems.append("normalize_unit(%r) = %r, expected 'units'"
                            % (unit, _common.normalize_unit(unit)))
    if _common.normalize_unit(None) != "units":
        problems.append("an absent unit must mean 'units'")
    for other in ("kg", "sets"):
        if _common.normalize_unit(other) == "units":
            problems.append("normalize_unit(%r) folded a different basis into 'units'" % other)
    for unit in ("pcs", "EA", "개", "unit", "pieces"):
        envelope = _json_copy(match_input)
        envelope["records"][0]["moq"] = 50000
        envelope["records"][0]["moq_unit"] = unit
        code, doc, err = _score_match_envelope(envelope)
        where, entry = _match_seller_outcome(doc, seller0_id)
        rules = [f.get("rule_id") for f in (entry or {}).get("failed_rules") or []]
        if where != "excluded" or "HF-03" not in rules:
            problems.append("score_match moq 50000 %r vs max 3000 units: %s %s, expected "
                            "excluded by HF-03" % (unit, where, rules))
        path = _write_temp_json({"records": [envelope["records"][0]]}, "kbtm-ar-seller-")
        try:
            code, doc, err = _run_json("score_seller.py", ["--input", path, "--query", seller_query,
                                                           "--as-of", AS_OF])
        finally:
            os.unlink(path)
        excluded = (doc or {}).get("excluded") or []
        rules = [f.get("rule_id") for e in excluded for f in e.get("failed_rules") or []]
        if "HF-03" not in rules:
            problems.append("score_seller moq 50000 %r vs max 3000 units: not excluded by HF-03 "
                            "(excluded rules %s)" % (unit, rules))
    envelope = _json_copy(match_input)
    envelope["records"][0]["moq"] = 50000
    envelope["records"][0]["moq_unit"] = "kg"
    code, doc, err = _score_match_envelope(envelope)
    where, entry = _match_seller_outcome(doc, seller0_id)
    rules = [f.get("rule_id") for f in (entry or {}).get("failed_rules") or []]
    if "HF-03" in rules:
        problems.append("a kg MOQ against a units ceiling was compared as a number")
    if where == "result" and "HF-03" not in entry["hard_filter"]["rules_skipped_unknown"]:
        problems.append("a kg MOQ against units did not skip HF-03 as unknown")
    report.check("audit regression: %s" % AUDIT_CASES[1], not problems, "\n".join(problems[:10]))

    # -- AR-03 (scoring-03, scoring-04) --------------------------------------
    # score_match had no S-PF1 inapplicable guard, so an RFQ category of 'k_beauty' gave
    # every seller product_fit 0; an unmapped seller slug was a verified HF-01 reject.
    problems = []
    cases = read_json(os.path.join(FIXTURES, "normalize.cases.json"))
    for case in cases["normalize_category"]:
        got = _common.normalize_category(case["input"])
        if got != case["expected"]:
            problems.append("normalize_category(%r) = %r, expected %r"
                            % (case["input"], got, case["expected"]))
        if _common.is_known_category(got) != case["in_vocabulary"]:
            problems.append("is_known_category(%r) = %r, expected %r"
                            % (got, _common.is_known_category(got), case["in_vocabulary"]))
    envelope = _json_copy(match_input)
    envelope["rfq"]["product_category"] = "k_beauty"
    envelope["rfq"]["product_categories_extra"] = []
    code, doc, err = _score_match_envelope(envelope)
    if doc is None:
        problems.append("score_match with rfq category k_beauty: no JSON (exit %d)" % code)
    else:
        if not doc.get("results") or (doc.get("summary") or {}).get("qualified_count", 0) == 0:
            problems.append("rfq category k_beauty: nobody qualified (summary %r)" % doc.get("summary"))
        for item in doc.get("results") or []:
            states = [d.get("state") for d in item.get("component_details") or []
                      if d.get("criterion_id") == "S-PF1"]
            if states != ["inapplicable"]:
                problems.append("%s: S-PF1 state %r, expected inapplicable" % (item["seller_id"], states))
            if any("category_no_match_verified" in note for note in item.get("notes") or []):
                problems.append("%s: category_no_match_verified fired with no requested category"
                                % item["seller_id"])
        for item in doc.get("excluded") or []:
            if any(f.get("rule_id") == "HF-01" for f in item.get("failed_rules") or []):
                problems.append("%s: HF-01 rejected with no requested category" % item["seller_id"])
    for categories, want_state in ((["선크림"], "scored"), (["sunscreen_spf50"], "scored"),
                                   (["totally_unmapped_term"], "unknown")):
        envelope = _json_copy(match_input)
        envelope["records"][0]["product_categories"] = categories
        code, doc, err = _score_match_envelope(envelope)
        where, entry = _match_seller_outcome(doc, seller0_id)
        rules = [f.get("rule_id") for f in (entry or {}).get("failed_rules") or []]
        if "HF-01" in rules:
            problems.append("seller categories %r: rejected by HF-01" % (categories,))
        if where == "result":
            states = [d.get("state") for d in entry.get("component_details") or []
                      if d.get("criterion_id") == "S-PF1"]
            if states != [want_state]:
                problems.append("seller categories %r: S-PF1 state %r, expected %r"
                                % (categories, states, want_state))
            if want_state == "unknown" and "HF-01" not in entry["hard_filter"]["rules_skipped_unknown"]:
                problems.append("an unmapped-only category list did not skip HF-01 as unknown")
        elif where is None:
            problems.append("seller categories %r: seller absent from the document" % (categories,))
    with open(seller_query, encoding="utf-8") as fh:
        query = json.load(fh)
    query["product_categories"] = ["k_beauty"]
    query_path = _write_temp_json(query, "kbtm-ar-query-")
    try:
        code, doc, err = _run_json("score_seller.py", [
            "--input", os.path.join(FIXTURES, "sellers.golden.json"), "--query", query_path,
            "--as-of", AS_OF])
    finally:
        os.unlink(query_path)
    for record in (doc or {}).get("records") or []:
        states = [d.get("state") for d in record.get("dimension_details") or []
                  if d.get("criterion_id") == "S-PF1"]
        if states != ["inapplicable"]:
            problems.append("score_seller query k_beauty: %s S-PF1 state %r"
                            % (record.get("seller_id"), states))
            break
    if doc is None or not doc.get("records"):
        problems.append("score_seller query k_beauty returned no records (exit %d)" % code)
    report.check("audit regression: %s" % AUDIT_CASES[2], not problems, "\n".join(problems[:10]))

    # -- AR-04 (dry-run-08) --------------------------------------------------
    # "United Kingdom" fell to unknown in normalize_company.py and B-MR1 lost its points.
    problems = []
    for case in cases["normalize_country"]:
        got = _common.normalize_country(case["input"])
        if got != case["expected"]:
            problems.append("normalize_country(%r) = %r, expected %r"
                            % (case["input"], got, case["expected"]))
    named = {}
    for key, code_value in _common.COUNTRY_ALIASES.items():
        if key != code_value.lower():
            named.setdefault(code_value, key)
    referenced = set()
    for members in _common.EXPORT_REGION_COUNTRIES.values():
        referenced.update(members)

    def walk(node):
        if isinstance(node, dict):
            code_value, name_value = node.get("country"), node.get("country_name")
            if isinstance(code_value, str) and re.match(r"^[A-Z]{2}$", code_value):
                referenced.add(code_value)
                if isinstance(name_value, str) and _common.normalize_country(name_value) != code_value:
                    problems.append("country_name %r does not normalise to its own country %s"
                                    % (name_value, code_value))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted(glob.glob(os.path.join(FIXTURES, "*.json"))):
        walk(read_json(path))
    for code_value in sorted(referenced):
        if code_value not in named:
            problems.append("country %s is referenced by the package but has no name row" % code_value)
    if len(_common.ISO_3166_ALPHA2) != 249:
        problems.append("ISO_3166_ALPHA2 holds %d codes, expected the 249 assigned ones"
                        % len(_common.ISO_3166_ALPHA2))
    strays = sorted(set(_common.COUNTRY_ALIASES.values()) - _common.ISO_3166_ALPHA2)
    if strays:
        problems.append("country name rows map to non-ISO codes: %s" % ", ".join(strays))
    for raw, want in (("United Kingdom", "GB"), ("영국", "GB"), ("Atlantis", "unknown"),
                      ("XX", "unknown"), ("Georgia", "unknown")):
        record = {"buyer_id": "BUY-country-example", "company_name": "Country Example",
                  "website": "https://country.example/", "country": raw}
        path = _write_temp_json(record, "kbtm-ar-country-")
        try:
            code, doc, err = _run_json("normalize_company.py", ["--input", path, "--entity", "buyer"])
        finally:
            os.unlink(path)
        if not isinstance(doc, dict) or doc.get("country") != want:
            problems.append("normalize_company.py country %r -> %r, expected %s"
                            % (raw, (doc or {}).get("country"), want))
        elif want != "unknown" and len(raw) > 2 and doc.get("country_name") != raw:
            problems.append("normalize_company.py dropped the given country name %r" % raw)
    report.check("audit regression: %s" % AUDIT_CASES[3], not problems, "\n".join(problems[:10]))

    # -- AR-05 (dry-run-09) --------------------------------------------------
    # score_buyer / score_seller wrote --output before the validation result was used.
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        bad_schemas = os.path.join(tmp, "schemas")
        os.mkdir(bad_schemas)
        for name in os.listdir(SCHEMA_DIR):
            if not name.endswith(".json"):
                continue
            document = read_json(os.path.join(SCHEMA_DIR, name))
            if name in ("discovery-result.schema.json", "match-result.schema.json"):
                document["required"] = list(document.get("required") or []) + ["__never_present__"]
            with open(os.path.join(bad_schemas, name), "w", encoding="utf-8") as fh:
                json.dump(document, fh, ensure_ascii=False)
        runs = (
            ("score_buyer.py", ["--input", os.path.join(FIXTURES, "buyers.golden.json"),
                                "--query", os.path.join(FIXTURES, "query-buyer-uk-sunscreen.json")]),
            ("score_seller.py", ["--input", os.path.join(FIXTURES, "sellers.golden.json"),
                                 "--query", seller_query]),
            ("score_match.py", ["--input", os.path.join(FIXTURES, "match-134.input.json")]),
        )
        for script, base_args in runs:
            stem = script.replace(".py", "")
            target = os.path.join(tmp, stem + ".json")
            invalid = os.path.join(tmp, stem + ".invalid.json")
            code, _out, _err = run_script(script, base_args + [
                "--as-of", AS_OF, "--schema-dir", bad_schemas, "--output", target])
            if code == 0:
                problems.append("%s: exit 0 although self-validation failed" % script)
            if os.path.exists(target):
                problems.append("%s: the invalid document was written to --output" % script)
            if not os.path.exists(invalid):
                problems.append("%s: no %s.invalid.json kept for inspection" % (script, stem))
            good = os.path.join(tmp, stem + ".ok.json")
            code, _out, err = run_script(script, base_args + ["--as-of", AS_OF, "--output", good])
            if code != 0 or not os.path.exists(good):
                problems.append("%s: a valid run did not write --output (exit %d) %s"
                                % (script, code, err.strip()[:200]))
            if os.path.exists(os.path.join(tmp, stem + ".ok.invalid.json")):
                problems.append("%s: a valid run wrote an .invalid.json file" % script)
    report.check("audit regression: %s" % AUDIT_CASES[4], not problems, "\n".join(problems[:10]))

    # -- AR-06 (scoring-05) --------------------------------------------------
    # A held certification with no evidence cost nothing, while the same token cited on a
    # tier-4 directory cost -10, so leaving the claim uncited scored higher.
    problems = []
    variants = {}
    for label in ("uncited", "tier4"):
        envelope = _json_copy(match_input)
        seller = envelope["records"][0]
        seller["certifications_verified"] = "unknown"
        seller["evidence"] = [e for e in seller["evidence"] if e.get("claim") != "certifications"]
        if label == "tier4":
            seller["evidence"].append({
                "schema_version": "0.1.0", "evidence_id": "EV-190", "claim": "certifications",
                "value": list(seller["certifications"]),
                "source_url": "https://directory.example/hanbitcos",
                "source_domain": "directory.example", "source_type": "third_party_directory",
                "source_tier": 4, "is_official": False, "observed_at": "2026-09-10T09:00:00Z",
                "source_date": "2026-08-01", "confidence": 0.6,
                "quote_or_summary": "Directory listing: ISO22716, CGMP, ISO9001.",
                "retrieval_method": "page_fetch",
            })
        code, doc, err = _score_match_envelope(envelope)
        where, entry = _match_seller_outcome(doc, seller0_id)
        if where != "result":
            problems.append("%s: seller not scored (%s)" % (label, where))
            continue
        variants[label] = entry
        path = _write_temp_json({"records": [seller]}, "kbtm-ar-cert-")
        try:
            code, sdoc, err = _run_json("score_seller.py", ["--input", path, "--query", seller_query,
                                                            "--as-of", AS_OF])
        finally:
            os.unlink(path)
        records = (sdoc or {}).get("records") or []
        adjustments = []
        if records:
            dims = ((records[0].get("extensions") or {}).get("score_breakdown") or {}).get("dimensions") or {}
            adjustments = [a.get("key") for a in (dims.get("compliance_readiness") or {}).get("adjustments") or []]
        if "certification_claim_unverified" not in adjustments:
            problems.append("score_seller %s: certification_claim_unverified did not fire (%s)"
                            % (label, adjustments))
    if len(variants) == 2:
        uncited = variants["uncited"]["component_scores"]["compliance_fit"]
        tier4 = variants["tier4"]["component_scores"]["compliance_fit"]
        if uncited > tier4:
            problems.append("compliance_fit uncited %d > tier-4 cited %d" % (uncited, tier4))
        if not any("certification_claim_unverified" in note for note in variants["uncited"].get("notes") or []):
            problems.append("score_match: certification_claim_unverified did not fire on an uncited token")
    report.check("audit regression: %s" % AUDIT_CASES[5], not problems, "\n".join(problems[:10]))

    # -- AR-07 (scoring-08) --------------------------------------------------
    # evidence_quality counted items, so one cross-linked pair cost -20 instead of -10.
    problems = []
    base = _json_copy(seller0)
    items = [e for e in base["evidence"] if e.get("claim") == "product_categories"]
    if len(items) < 2:
        problems.append("fixture seller no longer carries two product_categories items")
    else:
        first, second = items[0]["evidence_id"], items[1]["evidence_id"]
        claims = config["evidence"]["material_claims"]["seller"]
        cfg = _common.load_config()

        def variant(links):
            record = _json_copy(base)
            for item in record["evidence"]:
                if item["evidence_id"] in links:
                    item["conflicts_with"] = links[item["evidence_id"]]
            return record

        none = variant({})
        one_sided = variant({first: [second]})
        reciprocal = variant({first: [second], second: [first]})
        counts = [_common.unresolved_conflict_count(r) for r in (none, one_sided, reciprocal)]
        if counts != [0, 1, 1]:
            problems.append("unresolved_conflict_count none/one-sided/reciprocal = %r, expected "
                            "[0, 1, 1]" % counts)
        eq_one = _common.evidence_quality(one_sided, claims, AS_OF, cfg)
        eq_rec = _common.evidence_quality(reciprocal, claims, AS_OF, cfg)
        if eq_one != eq_rec:
            problems.append("evidence_quality one-sided %d != reciprocal %d" % (eq_one, eq_rec))
        if (_common.record_confidence(one_sided, "seller", eq_one, cfg)
                != _common.record_confidence(reciprocal, "seller", eq_rec, cfg)):
            problems.append("confidence differs between a one-sided and a reciprocal link")
        two_pairs = variant({first: [second, "EV-101"], second: [first]})
        if _common.unresolved_conflict_count(two_pairs) != 2:
            problems.append("two distinct conflicting pairs were not counted as two")
    report.check("audit regression: %s" % AUDIT_CASES[6], not problems, "\n".join(problems[:10]))

    # -- AR-08 (scoring-10) --------------------------------------------------
    # '주식회사한빛코스메틱' kept its glued legal form, and '한빛 코스메틱' / '한빛코스메틱'
    # were two different dedupe keys.
    problems = []
    spellings = ("주식회사한빛코스메틱", "(주)한빛코스메틱", "한빛코스메틱 주식회사", "㈜한빛 코스메틱")
    keys = set(_common.name_match_key(_common.normalize_company_name(n)) for n in spellings)
    if keys != {"한빛코스메틱"}:
        problems.append("Korean spellings gave dedupe keys %r, expected one" % sorted(keys))
    if _common.name_match_key("abc beauty") == _common.name_match_key("abcbeauty"):
        problems.append("a Latin-script name lost its word boundary in the dedupe key")
    records = [
        {"normalized_name": _common.normalize_company_name("주식회사 한빛 코스메틱"),
         "country": "KR", "canonical_domain": "hanbitcos.example"},
        {"normalized_name": _common.normalize_company_name("한빛코스메틱"),
         "country": "KR", "canonical_domain": "unknown"},
    ]
    groups, _keys, _blocked = dedupe_companies.build_components(records, ["SEL-a", "SEL-b"], True)
    if not any(sorted(members) == [0, 1] for members in groups.values()):
        problems.append("dedupe key 2 did not merge '주식회사 한빛 코스메틱' with '한빛코스메틱' "
                        "(groups %r)" % groups)
    report.check("audit regression: %s" % AUDIT_CASES[7], not problems, "\n".join(problems[:10]))

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
    "schemas/outreach-draft.schema.json",
    "schemas/compliance.config.json",
    "scripts/_common.py",
    "scripts/normalize_company.py",
    "scripts/dedupe_companies.py",
    "scripts/score_buyer.py",
    "scripts/score_seller.py",
    "scripts/score_match.py",
    "scripts/validate_output.py",
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
CLI_SCRIPTS = [n for n in PIPELINE_SCRIPTS if n != "_common.py"]


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
    repo_root = os.path.dirname(PKG_ROOT)
    repo_readme = os.path.join(repo_root, "README.md")
    readme_case = "package: README.md exists at the repository root (BUILD-CONTRACT 2.2 note)"
    # install.sh --copy ships the package folder only, so an installed copy has no repository
    # root around it. Report that as a SKIP, never as a silent pass.
    if os.path.exists(os.path.join(PKG_ROOT, ".kbtm-install-manifest")) \
            or not os.path.exists(os.path.join(repo_root, ".git")):
        report.skip(readme_case, "not running inside the git repository (installed copy)")
    else:
        report.check(readme_case, os.path.isfile(repo_readme), "not found: %s" % repo_readme)

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
def _adapter_write(path, document):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(document, fh, ensure_ascii=False)


# BUILD-CONTRACT.md 9.1 skill-performed edges, written out here rather than read from the
# adapter, so an edit to the adapter's table is a test failure and not a new expectation.
ADAPTER_EXPECTED_EDGES = {
    "DISCOVERED": {"VERIFIED", "CLOSED"},
    "VERIFIED": {"QUALIFIED", "DISCOVERED", "CLOSED"},
    "QUALIFIED": {"MATCH_CANDIDATE", "READY_FOR_REVIEW", "VERIFIED", "CLOSED"},
    "MATCH_CANDIDATE": {"READY_FOR_REVIEW", "QUALIFIED", "CLOSED"},
    "READY_FOR_REVIEW": {"QUALIFIED", "MATCH_CANDIDATE", "CLOSED"},
    "CLOSED": set(),
}


def phase_adapter_guards(report, pipeline, allow_missing):
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
    if missing_scripts() or not (pipeline.buyers_uae and pipeline.match_134):
        (report.skip if allow_missing else report.fail)("adapter: guards",
                                                        "pipeline output unavailable")
        return
    vo = tw._import_validator()
    if not report.check("adapter: loads scripts/validate_output.py by path for the draft gate",
                        vo is not None, "_import_validator() returned None"):
        return

    buyers = pipeline.buyers_uae.get("records") or []
    qualified = index_by(buyers, "buyer_id").get("BUY-gulfglow-example")
    not_qualified = next((r for r in buyers if r.get("qualified") is False), None)
    unscored = index_by(read_json(os.path.join(FIXTURES, "buyers.golden.json"))["records"],
                        "buyer_id").get("BUY-gulfglow-example")
    rfq_134 = read_json(os.path.join(FIXTURES, "rfq.134.json"))
    hanbit = next((row for row in pipeline.match_134.get("results") or []
                   if "Hanbit" in str(row.get("seller_name"))), None)
    if not (qualified and qualified.get("qualified") is True and not_qualified and unscored
            and hanbit and (hanbit.get("hard_filter") or {}).get("passed") is True):
        report.fail("adapter: guard fixtures", "buyers_uae lacks a qualified / non-qualified pair "
                    "or match_134 lacks a passing Hanbit candidate")
        return

    def json_draft(text, **fields):
        parsed, _ = vo.parse_outreach_draft(text)
        draft = dict((key, parsed.get(key)) for key in ("side", "channel_type", "channel_value",
                                                        "language", "subject"))
        draft.update(body="(rendered in draft_markdown)", draft_markdown=text)
        draft.update(fields)
        return draft

    def buyer_draft(**fields):
        return json_draft(_draft_text(_DRAFT_B), entity_id="BUY-gulfglow-example", **fields)

    def seller_draft(text=None, **fields):
        base = {"entity_id": hanbit["seller_id"], "rfq_id": "134", "rfq_status": "matching",
                "rfq_as_of": AS_OF}
        base.update(fields)
        return json_draft(text or _draft_text(_DRAFT_SE), **base)

    def refusal(draft, adapter=None, as_of=AS_OF):
        """The refusal text with every listed error, or None when the draft is accepted."""
        try:
            tw._normalize_draft(draft, 0, adapter=adapter, as_of=as_of)
        except tw.DataError as exc:
            return "\n".join([str(exc)] + [str(item) for item in getattr(exc, "errors", [])])
        return None

    def expect_refused(name, draft, marker, adapter=None, as_of=AS_OF):
        message = refusal(draft, adapter, as_of)
        report.check(name, message is not None and marker in message,
                     "accepted" if message is None
                     else "refused, but not by %r:\n%s" % (marker, message[:700]))

    def expect_accepted(name, draft, adapter=None, as_of=AS_OF):
        message = refusal(draft, adapter, as_of)
        report.check(name, message is None, str(message)[:700])

    # -- drafts: dispatch keys, fixed flags, the validate_outreach_draft gate ---------------
    try:
        stored = tw._normalize_draft(buyer_draft(), 0)
        report.check("adapter: a good fixture draft is accepted with the review flags filled in",
                     stored["status"] == "READY_FOR_REVIEW" and stored["auto_send"] is False
                     and stored["manual_approval_required"] is True, repr(stored)[:300])
    except tw.DataError as exc:
        report.fail("adapter: a good fixture draft is accepted with the review flags filled in",
                    "\n".join([str(exc)] + [str(e) for e in getattr(exc, "errors", [])]))

    nested = buyer_draft()
    nested["delivery"] = {"transport": "smtp", "recipients": ["ops@example.example"],
                          "schedule_at": "2026-10-01"}
    nested["auth_block"] = {"token": "placeholder-value", "password": "placeholder-value"}
    expect_refused("adapter: INV-10/INV-25 a NESTED dispatch or credential block is refused",
                   nested, "dispatch or credential")
    leaks = []
    for key in ("to", "reply_to", "envelope_from", "mail_from", "smtp_host", "smtp_port",
                "send", "send_at", "queue_for_send", "webhook_url", "sendgrid_key",
                "api_token", "access_key", "bearer", "cookie", "recipients", "transport",
                "token", "password"):
        message = refusal(buyer_draft(**{key: "value"}))
        if message is None or "dispatch or credential" not in message:
            leaks.append(key)
    report.check("adapter: INV-25 every dispatch/credential key name is refused at top level",
                 not leaks, "not refused as a dispatch key: %s" % ", ".join(leaks))
    expect_refused("adapter: INV-09 auto_send true is refused, never silently corrected",
                   buyer_draft(auto_send=True), "auto_send must be")

    bare = buyer_draft()
    del bare["draft_markdown"]
    expect_refused("adapter: a JSON draft without draft_markdown is not queued (DRAFT-01)", bare,
                   "DRAFT-01")
    cases = dict((case[0], case) for case in DRAFT_CASES)
    for label in ("zero personalization facts", "KR corporate email subject without (광고)",
                  "KR required notice block absent", "notice block not verbatim",
                  "unfilled {{token}}", "surviving [[ev:]] marker", "EN fake opt-out",
                  "free-mail sender address", "body over the 150-word buyer limit (strict)"):
        _, base, edits, _, rule = cases[label]
        text = _draft_text(base)
        for old, new in edits:
            text = text.replace(old, new, 1)
        entity = "BUY-gulfglow-example" if base.startswith("buyer") else "SEL-hanbitcos-example"
        if rule == "DRAFT-12":
            quiet = tw.get_adapter(backend="file", data_dir=os.path.join(FIXTURES, "no-adapter-data"),
                                   as_of=AS_OF, quiet=True)  # read-only lookups; nothing is written
            expect_accepted("adapter: a warning-only issue (DRAFT-12 body length) does not block "
                            "the queue", json_draft(text, entity_id=entity), quiet)
        else:
            expect_refused("adapter: READY_FOR_REVIEW refused for a draft with %s (%s)"
                           % (label, rule), json_draft(text, entity_id=entity), rule)

    # -- INV-34: rephrased demand, in the JSON body and in the rendered body -----------------
    for phrase in ("A distributor asked us specifically about your brand.",
                   "Our client is interested in your sun care line.",
                   "A distributor in Dubai is actively looking for your sunscreen.",
                   "We have buyers for your sunscreen.",
                   "Only 3 production slots left this quarter.",
                   "해당 바이어가 관심이 있습니다.",
                   "귀사 제품에 대한 문의가 왔습니다.",
                   "바이어가 대기 중입니다.",
                   "곧 마감됩니다."):
        expect_refused("adapter: T05/INV-34 JSON body %r is refused without an RFQ" % phrase,
                       buyer_draft(body=phrase), "live-demand claim")
    rendered = _draft_text(_DRAFT_B).replace(
        "Your brand-partnership page states",
        "A distributor asked us specifically about your brand. Your brand-partnership page states", 1)
    expect_refused("adapter: T05/INV-34 a rephrased claim in the rendered body is refused",
                   json_draft(rendered, entity_id="BUY-gulfglow-example"), "live-demand claim")
    for phrase in ("We are currently looking for UAE distributors.",
                   "Our customers have asked for SPF data sheets.",
                   "관심이 있는 카테고리를 알려 주시면 기록해 두겠습니다.",
                   "혹시 관심이 있다면 회신 부탁드립니다.",
                   "저희는 UAE 유통 파트너를 찾고 있습니다.",
                   "회신 대기 중인 문의는 없습니다."):
        expect_accepted("adapter: INV-34 honest wording %r is accepted (no third party wants the "
                        "recipient)" % phrase, buyer_draft(body=phrase))
    _, _, rule4_edits, _, _ = cases["buyer rule-4 dated sourcing sentence with a dated fact line"]
    rule4_text = _draft_text(_DRAFT_B)
    for old, new in rule4_edits:
        rule4_text = rule4_text.replace(old, new, 1)
    expect_accepted("adapter: INV-34 a dated 'you are actively looking' sentence backed by a dated "
                    "fact line is accepted without an RFQ",
                    json_draft(rule4_text, entity_id="BUY-gulfglow-example"))

    # _import_validator leaves sys.modules["_common"] and sys.path as it found them.
    probe = "\n".join([
        "import json, sys",
        "sys.dont_write_bytecode = True",
        "sys.path.insert(0, %r)" % os.path.join(PKG_ROOT, "adapters"),
        "import tradewith_adapter as tw",
        "path_before, common_before = list(sys.path), sys.modules.get('_common')",
        "result = {'loaded': tw._import_validator() is not None,",
        "          'nothing_held_common_and_nothing_does_now': common_before is None and '_common' not in sys.modules,",
        "          'sys_path_unchanged': sys.path == path_before}",
        "sentinel = type(sys)('_common')",
        "sys.modules['_common'] = sentinel",
        "tw._VALIDATOR_CACHE.clear()",
        "result['reloaded'] = tw._import_validator() is not None",
        "result['unrelated_common_restored'] = sys.modules.get('_common') is sentinel",
        "result['sys_path_unchanged_again'] = sys.path == path_before",
        "print(json.dumps(result))",
    ])
    proc = subprocess.run([sys.executable, "-c", probe], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        result = {"stdout": proc.stdout[:200], "stderr": proc.stderr[:400]}
    report.check("adapter: _import_validator restores sys.modules['_common'] (removed when nothing "
                 "held it, the unrelated module otherwise) and sys.path",
                 bool(result) and all(value is True for value in result.values()), repr(result))

    with tempfile.TemporaryDirectory() as tmp:
        def file_adapter(rfq=None, match=None, leads=()):
            root = tempfile.mkdtemp(dir=tmp)
            if rfq is not None:
                _adapter_write(os.path.join(root, "rfqs", "%s.json" % rfq["rfq_id"]), rfq)
            if match is not None:
                _adapter_write(os.path.join(root, "matches", "%s.json" % match["match_run_id"]),
                               match)
            for lead in leads:
                _adapter_write(os.path.join(root, "leads", "%s.json" % lead["buyer_id"]), lead)
            return tw.get_adapter(backend="file", data_dir=root, as_of=AS_OF, quiet=True), root

        # -- INV-34: the RFQ is read, not believed ------------------------------------------
        adapter, _ = file_adapter(rfq=rfq_134, match=pipeline.match_134)
        expect_accepted("adapter: INV-34 an RFQ draft is accepted when get_rfq finds it open and "
                        "current and the seller passed the stored match run", seller_draft(), adapter)
        expect_accepted("adapter: INV-34 the same RFQ draft passes with no adapter (form and age "
                        "checks only)", seller_draft())
        # The RFQ age is measured against an as_of somebody chose, never as_of_default.
        expect_refused("adapter: INV-34 an RFQ draft with no explicit as_of (and no adapter) is "
                       "refused", seller_draft(), "no explicit as_of", as_of=None)
        _, rfq_root = file_adapter(rfq=rfq_134, match=pipeline.match_134)
        implicit = tw.get_adapter(backend="file", data_dir=rfq_root, quiet=True)
        expect_refused("adapter: INV-34 an adapter built without as_of refuses an RFQ draft instead "
                       "of checking freshness against as_of_default",
                       seller_draft(), "no explicit as_of", implicit, as_of=None)
        expect_refused("adapter: INV-34 rfq_as_of alone (no RFQ text) still needs an explicit as_of",
                       buyer_draft(rfq_as_of=AS_OF), "no explicit as_of", as_of=None)
        expect_accepted("adapter: a draft that cites no RFQ needs no explicit as_of", buyer_draft(),
                        implicit, as_of=None)
        stale_text = _draft_text(_DRAFT_SE).replace("as of %s" % AS_OF, "as of 2019-01-01")
        expect_refused("adapter: INV-34 caller-supplied rfq_status matching with rfq_as_of "
                       "2019-01-01 is refused as stale", seller_draft(stale_text,
                                                                     rfq_as_of="2019-01-01"),
                       "stale demand")
        future_text = _draft_text(_DRAFT_SE).replace("as of %s" % AS_OF, "as of 2026-10-01")
        expect_refused("adapter: INV-34 an rfq_as_of after the run's as_of is refused",
                       seller_draft(future_text, rfq_as_of="2026-10-01"), "after the run's as_of")
        old = _json_copy(rfq_134)
        old["as_of"] = "2019-01-01"
        adapter, _ = file_adapter(rfq=old)
        expect_refused("adapter: INV-34 a stored RFQ as of 2019-01-01 is refused as stale",
                       seller_draft(stale_text, rfq_as_of="2019-01-01"), "stale demand", adapter)
        for status, marker in (("closed", "is 'closed' in TradeWith"),
                               ("qualified", "but RFQ 134 is 'qualified'")):
            doc = _json_copy(rfq_134)
            doc["status"] = status
            adapter, _ = file_adapter(rfq=doc)
            expect_refused("adapter: INV-34 draft says matching but get_rfq returns %s" % status,
                           seller_draft(), marker, adapter)
        adapter, _ = file_adapter()
        expect_refused("adapter: INV-34 a cited RFQ that get_rfq cannot read is refused",
                       seller_draft(), "could not be read", adapter)
        expect_refused("adapter: INV-34 text citing RFQ #134 with no rfq_id / status / as_of "
                       "fields is refused", seller_draft(rfq_id=None, rfq_status=None,
                                                         rfq_as_of=None), "no rfq_id is cited")
        stated = "status matching as of %s" % AS_OF
        for label, text, fields, marker in (
                ("never states the status", _draft_text(_DRAFT_SE).replace(stated, "as of " + AS_OF),
                 {}, "never states the RFQ status"),
                ("never states the as-of date", _draft_text(_DRAFT_SE).replace(stated, "status matching"),
                 {}, "never states the RFQ date"),
                ("also states a closed status", _draft_text(_DRAFT_SE).replace(
                    stated, stated + " (earlier status closed)"), {}, "which is not open"),
                ("cites RFQ #134 under rfq_id 135", None, {"rfq_id": "135"}, "but rfq_id is"),
                ("carries and states rfq_status closed", _draft_text(_DRAFT_SE).replace(
                    stated, "status closed as of " + AS_OF), {"rfq_status": "closed"},
                 "rfq_status 'closed' is not one of")):
            expect_refused("adapter: INV-34 an RFQ draft that %s is refused" % label,
                           seller_draft(text, **fields), marker)
        moved = _json_copy(rfq_134)
        moved["as_of"] = "2026-09-01"
        adapter, _ = file_adapter(rfq=moved)
        expect_refused("adapter: INV-34 rfq_as_of that differs from the stored RFQ's as_of is refused",
                       seller_draft(), "RFQ 134 is as of 2026-09-01", adapter)
        failed = _json_copy(pipeline.match_134)
        for row in failed["results"]:
            if row.get("seller_id") == hanbit["seller_id"]:
                row["hard_filter"]["passed"] = False
        adapter, _ = file_adapter(rfq=rfq_134, match=failed)
        expect_refused("adapter: INV-34 a seller that failed the stored match run's hard filter "
                       "is refused", seller_draft(), "hard filter of match run", adapter)
        excluded = _json_copy(pipeline.match_134)
        excluded["results"] = [row for row in excluded["results"]
                               if row.get("seller_id") != hanbit["seller_id"]]
        excluded["excluded"] = list(excluded.get("excluded") or []) + [
            {"seller_id": hanbit["seller_id"], "seller_name": hanbit.get("seller_name")}]
        adapter, _ = file_adapter(rfq=rfq_134, match=excluded)
        expect_refused("adapter: INV-34 a seller in the stored match run's excluded[] is refused",
                       seller_draft(), "is excluded from match run", adapter)

        # -- the stored lead is the validator's record -------------------------------------
        adapter, _ = file_adapter(leads=[qualified])
        expect_accepted("adapter: a buyer draft is accepted against its stored scored, qualified "
                        "lead", buyer_draft(), adapter)
        adapter, _ = file_adapter(leads=[unscored])
        expect_refused("adapter: DRAFT-09 a buyer draft whose stored lead is unscored is refused",
                       buyer_draft(), "DRAFT-09", adapter)
        rule4_draft = json_draft(rule4_text, entity_id="BUY-gulfglow-example")
        adapter, _ = file_adapter(leads=[qualified])
        expect_refused("adapter: INV-34 a rule-4 sentence is refused when the stored lead has no "
                       "sourcing_signals item on the fact's URL", rule4_draft, "live-demand claim",
                       adapter)
        signalled = _json_copy(qualified)
        signalled["evidence"].append(dict(
            [item for item in qualified["evidence"] if item.get("claim") == "sourcing_intent"][0],
            evidence_id="EV-010", claim="sourcing_signals", value="open_call_for_suppliers"))
        adapter, _ = file_adapter(leads=[signalled])
        expect_accepted("adapter: INV-34 a rule-4 sentence is accepted against a stored lead whose "
                        "dated sourcing_signals item backs the fact", rule4_draft, adapter)

        # -- PRD 11.1 bulk guardrail -------------------------------------------------------
        adapter, root = file_adapter()
        try:
            adapter.post_outreach_drafts([buyer_draft() for _ in range(21)])
            message = None
        except tw.DataError as exc:
            message = str(exc)
        report.check("adapter: PRD 11.1 21 drafts in one call are refused and nothing is queued",
                     message is not None and "bulk guardrail" in message
                     and not os.path.isdir(os.path.join(root, "outreach-drafts")), str(message))
        try:
            rows = adapter.post_outreach_drafts([buyer_draft(draft_id="OD-cap-%02d" % n)
                                                 for n in range(20)])
            ok, detail = len(rows) == 20, repr(rows[:2])
        except tw.DataError as exc:
            ok, detail = False, str(exc)
        report.check("adapter: PRD 11.1 exactly 20 drafts (the cap) are queued", ok, detail)

        # -- state machine -----------------------------------------------------------------
        def patch(lead, target):
            adapter, root = file_adapter(leads=[lead])
            path = os.path.join(root, "leads", "%s.json" % lead["buyer_id"])
            before = read_json(path)
            try:
                adapter.patch_lead_status(lead["buyer_id"], target)
                return None, read_json(path) == before
            except tw.DataError as exc:
                return ("\n".join([str(exc)] + [str(e) for e in getattr(exc, "errors", [])]),
                        read_json(path) == before)

        wrong_accepted, wrong_refused = [], []
        for current, edges in sorted(ADAPTER_EXPECTED_EDGES.items()):
            for target in sorted(ADAPTER_EXPECTED_EDGES):
                lead = _json_copy(qualified)
                lead["status"] = current
                message, unchanged = patch(lead, target)
                if current == target or target in edges:
                    if message is not None:
                        wrong_refused.append("%s -> %s: %s" % (current, target, message[:200]))
                elif message is None or not unchanged:
                    wrong_accepted.append("%s -> %s" % (current, target))
        report.check("adapter: 9.1 every non-edge transition is refused and the lead is untouched",
                     not wrong_accepted, "accepted: %s" % ", ".join(wrong_accepted))
        report.check("adapter: 9.1 every skill edge (and re-asserting a state) is accepted for a "
                     "scored, qualified lead", not wrong_refused, "\n".join(wrong_refused[:6]))
        lead = _json_copy(qualified)
        lead["status"] = "CONTACTED"
        message, unchanged = patch(lead, "VERIFIED")
        report.check("adapter: 9.2 a lead in an application-layer state is not moved",
                     message is not None and "application layer" in message and unchanged,
                     str(message))
        message, unchanged = patch(_json_copy(qualified), "APPROVED_FOR_OUTREACH")
        report.check("adapter: INV-09 APPROVED_FOR_OUTREACH is never written",
                     message is not None and "approval and every later state" in message
                     and unchanged, str(message))
        message, unchanged = patch(_json_copy(unscored), "QUALIFIED")
        report.check("adapter: INV-37 an unscored lead cannot move VERIFIED -> QUALIFIED",
                     message is not None and unchanged, "accepted")
        for current, target in (("VERIFIED", "QUALIFIED"), ("QUALIFIED", "MATCH_CANDIDATE"),
                                ("QUALIFIED", "READY_FOR_REVIEW")):
            lead = _json_copy(not_qualified)
            lead["status"] = current
            message, unchanged = patch(lead, target)
            report.check("adapter: INV-37 a scored lead with qualified false cannot move %s -> %s"
                         % (current, target),
                         message is not None and "qualified true" in message and unchanged,
                         str(message))
        adapter, _ = file_adapter()
        lead = _json_copy(not_qualified)
        lead["status"] = "QUALIFIED"
        try:
            adapter.post_research_leads([lead])
            message = None
        except tw.DataError as exc:
            message = str(exc)
        report.check("adapter: INV-37 save-leads refuses a qualified-false record at QUALIFIED",
                     message is not None and "qualified true" in message, str(message))

        # -- 1-10: save-leads never silently downgrades a stored lead ------------------------
        adapter, root = file_adapter()
        path = os.path.join(root, "leads", "BUY-gulfglow-example.json")

        def save(record, overwrite=False):
            return adapter.post_research_leads([_json_copy(record)], overwrite=overwrite)[0]

        first, second = save(qualified), save(qualified)
        report.check("adapter: save-leads reports created, then unchanged, for the same lead",
                     first.get("result") == "created" and second.get("result") == "unchanged",
                     "%r / %r" % (first, second))
        row = save(unscored)
        stored = read_json(path)
        report.check("adapter: save-leads refuses to replace a scored lead with an unscored one",
                     row.get("result") == "refused"
                     and stored.get("qualification_score") == qualified["qualification_score"]
                     and stored.get("score_version") == qualified["score_version"],
                     "%r; stored score %r" % (row, stored.get("qualification_score")))
        raw_path = os.path.join(tmp, "raw-leads.json")
        _adapter_write(raw_path, {"records": [unscored]})
        command = [sys.executable, adapter_path, "save-leads", "--data-dir", root, "--input",
                   raw_path, "--as-of", AS_OF, "--quiet"]
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
        try:
            rows = json.loads(proc.stdout)
        except ValueError:
            rows = []
        report.check("adapter: save-leads CLI exits 1 with a refused row and keeps the scored lead",
                     proc.returncode == 1 and rows and rows[0].get("result") == "refused"
                     and read_json(path).get("qualification_score")
                     == qualified["qualification_score"],
                     "exit %d stdout %s stderr %s" % (proc.returncode, proc.stdout[:200],
                                                      proc.stderr[:300]))
        proc = subprocess.run(command + ["--overwrite"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        try:
            rows = json.loads(proc.stdout)
        except ValueError:
            rows = []
        report.check("adapter: save-leads --overwrite replaces it and reports updated",
                     proc.returncode == 0 and rows and rows[0].get("result") == "updated"
                     and read_json(path).get("score_version") == "unscored",
                     "exit %d stdout %s stderr %s" % (proc.returncode, proc.stdout[:200],
                                                      proc.stderr[:300]))
        ahead = _json_copy(qualified)
        ahead["status"] = "QUALIFIED"
        _adapter_write(path, ahead)
        row = save(qualified)
        report.check("adapter: save-leads refuses a status regression (QUALIFIED -> VERIFIED)",
                     row.get("result") == "refused" and read_json(path).get("status") == "QUALIFIED",
                     repr(row))
        row = save(qualified, overwrite=True)
        report.check("adapter: save-leads overwrite=True applies the status regression",
                     row.get("result") == "updated" and read_json(path).get("status") == "VERIFIED",
                     repr(row))
        owned = _json_copy(qualified)
        owned["status"] = "CONTACTED"
        _adapter_write(path, owned)
        row = save(qualified, overwrite=True)
        report.check("adapter: save-leads never replaces an application-layer lead, even with "
                     "overwrite", row.get("result") == "refused"
                     and read_json(path).get("status") == "CONTACTED", repr(row))

    # -- INV-25 redaction --------------------------------------------------------------------
    token = "tw-test-token-7f3a91"
    out = tw._redact("request to /rfqs/134 failed with tw-test-token-7f3a91 in the echo", token)
    report.check("adapter: _redact removes the bearer token it is given",
                 token not in out and "***redacted***" in out, out)
    for text in ("Authorization: Bearer zz-unknown-credential-42",
                 "authorization=zz-unknown-credential-42"):
        out = tw._redact(text)
        report.check("adapter: _redact removes an Authorization value it was not told about (%s)"
                     % text.split("zz")[0].strip(), "zz-unknown-credential-42" not in out, out)
    saved = os.environ.get("TRADEWITH_TOKEN")
    os.environ["TRADEWITH_TOKEN"] = "env-token-5c2e88"
    try:
        out = tw._redact("upstream echoed env-token-5c2e88")
    finally:
        if saved is None:
            os.environ.pop("TRADEWITH_TOKEN", None)
        else:
            os.environ["TRADEWITH_TOKEN"] = saved
    report.check("adapter: _redact removes the TRADEWITH_TOKEN environment value",
                 "env-token-5c2e88" not in out, out)
    out = tw._redact("partners@tradewith.example", token)
    report.check("adapter: _redact targets credentials only; a company address is left intact",
                 out == "partners@tradewith.example", out)

    # -- HTTP backend against a local stdlib server (127.0.0.1 only) --------------------------
    import http.server
    import threading

    http_token = "tw-http-token-3b9d51"
    seen = {"auth": [], "paths": []}
    rfq_bytes = json.dumps(rfq_134, ensure_ascii=False).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, code, body):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            seen["auth"].append(self.headers.get("Authorization"))
            seen["paths"].append(self.path)
            if self.path == "/rfqs/134":
                if seen["paths"].count(self.path) == 1:
                    return self._reply(429, "{}")
                return self._reply(200, rfq_bytes)
            if self.path == "/rfqs/401":
                return self._reply(401, '{"error": "token %s is not valid"}' % http_token)
            return self._reply(429, "{}")

        def do_PATCH(self):
            seen["paths"].append("PATCH " + self.path)
            self._reply(200, "{}")

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    saved_env = dict((name, os.environ.get(name)) for name in ("no_proxy", "NO_PROXY"))
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    backoff = tw.RETRY_BACKOFF_SECONDS
    tw.RETRY_BACKOFF_SECONDS = 0.0
    try:
        client = tw.HttpAdapter(base_url="http://127.0.0.1:%d" % server.server_address[1],
                                token=http_token, max_retries=2, timeout=10, as_of=AS_OF,
                                quiet=True)
        try:
            document, error = client.get_rfq("134"), None
        except tw.AdapterError as exc:
            document, error = None, str(exc)
        report.check("adapter http: a 429 is retried and the RFQ arrives on the next attempt",
                     document is not None and document.get("rfq_id") == "134"
                     and seen["paths"].count("/rfqs/134") == 2,
                     "error %s; paths %s" % (error, seen["paths"]))
        report.check("adapter http: every request carries Authorization: Bearer <token>",
                     seen["auth"] and all(value == "Bearer " + http_token for value in seen["auth"]),
                     repr(seen["auth"]))
        try:
            client.get_rfq("401")
            error = None
        except tw.AdapterError as exc:
            error = str(exc)
        report.check("adapter http: a 401 is not retried and the echoed token never reaches the "
                     "error", error is not None and http_token not in error
                     and "***redacted***" in error and seen["paths"].count("/rfqs/401") == 1,
                     str(error))
        try:
            client.get_rfq("busy")
            error = None
        except tw.AdapterError as exc:
            error = str(exc)
        report.check("adapter http: the retry budget is bounded (max_retries 2 = 3 attempts)",
                     error is not None and seen["paths"].count("/rfqs/busy") == 3,
                     "error %s; paths %s" % (error, seen["paths"]))
        try:
            client.patch_lead_status("BUY-gulfglow-example", "APPROVED_FOR_OUTREACH")
            error = None
        except tw.DataError as exc:
            error = str(exc)
        report.check("adapter http: APPROVED_FOR_OUTREACH is refused before any request",
                     error is not None and not [p for p in seen["paths"] if p.startswith("PATCH")],
                     "error %s; paths %s" % (error, seen["paths"]))
    finally:
        tw.RETRY_BACKOFF_SECONDS = backoff
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        server.shutdown()
        server.server_close()
    try:
        tw.HttpAdapter._resolve_base_url("http://api.tradewith.example")
        error = None
    except tw.UsageError as exc:
        error = str(exc)
    report.check("adapter http: a bearer token is never sent over plain http to a remote host",
                 error is not None, "accepted")


# ---------------------------------------------------------------------------
# 8. validate_output.py negative fixtures, outreach drafts, Mode 1 -> Mode 4
# ---------------------------------------------------------------------------
INVALID = os.path.join(FIXTURES, "invalid")
DRAFTS = os.path.join(FIXTURES, "drafts")

# Every rule id these phases must hold a failing case for; a rule without one is untested.
TARGETED_DOCUMENT_RULES = {"INV-01", "INV-02", "INV-04", "INV-05", "INV-06", "INV-22", "INV-30",
                           "INV-37", "EVI-01", "EVI-02", "EVI-03", "EVI-04", "EVI-05", "EVI-06",
                           "VAL-01"}
TARGETED_DRAFT_RULES = {"DRAFT-%02d" % n for n in range(1, 13)} | {"INV-09", "INV-31", "INV-34",
                                                                    "R10.4.6"}


def _apply_mutations(document, mutations):
    """Apply a tests/fixtures/invalid/*.json mutation list (set / delete / swap / set_each)."""
    def parent_of(path):
        node = document
        for key in path[:-1]:
            node = node[key]
        return node

    for mutation in mutations:
        path = mutation["path"]
        parent = parent_of(path)
        if mutation["op"] == "set":
            parent[path[-1]] = mutation["value"]
        elif mutation["op"] == "delete":
            del parent[path[-1]]
        elif mutation["op"] == "swap":
            other = parent_of(mutation["with"])
            parent[path[-1]], other[mutation["with"][-1]] = (other[mutation["with"][-1]],
                                                            parent[path[-1]])
        elif mutation["op"] == "set_each":
            for item in parent[path[-1]]:
                item.update(mutation["value"])
        elif mutation["op"] == "append":
            parent[path[-1]].append(mutation["value"])
        else:
            raise ValueError("unknown mutation op %r" % (mutation["op"],))
    return document


def _validator_verdict(args):
    """Run validate_output.py; return (exit code, reported rule ids, short detail)."""
    code, out, err = run_script("validate_output.py", list(args) + ["--json"])
    try:
        doc = json.loads(out)
    except ValueError:
        return code, set(), (err.strip() or out.strip())[:400]
    failures = doc.get("invariant_failures") or []
    rules = set(item.get("invariant") for item in failures)
    rules |= set(item["invariant"] for item in doc.get("warnings") or [] if item.get("invariant"))
    if doc.get("errors"):
        rules.add("SCHEMA")
    detail = ["%s %s" % (item.get("invariant"), str(item.get("message"))[:160])
              for item in failures + (doc.get("warnings") or []) + (doc.get("errors") or [])]
    return code, rules, "\n".join(detail[:8])


def _invalid_base(base, pipeline, spec=None):
    if base == "literal":
        return _json_copy(spec["document"])
    source, _, record_id = base.partition(":")
    if source == "match_134":
        return _json_copy(pipeline.match_134) if pipeline.match_134 else None
    if source == "buyers_uae":
        records = (pipeline.buyers_uae or {}).get("records") or []
    elif source == "buyers_golden":
        records = read_json(os.path.join(FIXTURES, "buyers.golden.json"))["records"]
    else:
        return None
    for record in records:
        if record.get("buyer_id") == record_id:
            return _json_copy(record)
    return None


def phase_validator_negatives(report, pipeline, allow_missing):
    specs = sorted(glob.glob(os.path.join(INVALID, "*.json")))
    if missing_scripts() or not (pipeline.buyers_uae and pipeline.match_134):
        (report.skip if allow_missing else report.fail)(
            "validator negative: fixtures", "pipeline output unavailable")
        return
    covered = set()
    with tempfile.TemporaryDirectory() as tmp:
        # Controls: an unmutated base passes --strict, or a failure below proves nothing.
        for base in ("buyers_uae:BUY-gulfglow-example", "buyers_golden:BUY-gulfglow-example",
                     "match_134"):
            path = os.path.join(tmp, "control.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(_invalid_base(base, pipeline), fh, ensure_ascii=False)
            kind = "match-result" if base == "match_134" else "buyer"
            code, rules, detail = _validator_verdict(["--input", path, "--schema", kind,
                                                      "--invariants", "--strict",
                                                      "--as-of", AS_OF])
            report.check("validator negative: control %s passes --strict" % base,
                         code == 0 and not rules, "exit %d %s\n%s" % (code, sorted(rules), detail))
        for spec_path in specs:
            spec = read_json(spec_path)
            name = "validator negative: %s %s" % (spec["rule"],
                                                  os.path.basename(spec_path)[:-len(".json")])
            document = _invalid_base(spec["base"], pipeline, spec)
            if document is None:
                report.fail(name, "base %s not found" % spec["base"])
                continue
            document = _apply_mutations(document, spec.get("mutations") or [])
            path = os.path.join(tmp, os.path.basename(spec_path))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(document, fh, ensure_ascii=False)
            args = ["--input", path, "--schema", spec.get("schema", "auto"), "--invariants",
                    "--as-of", AS_OF]
            problems = []
            if spec.get("strict"):
                code, _, detail = _validator_verdict(args)
                if code != 0:
                    problems.append("without --strict a warning-only rule must exit 0, got %d\n%s"
                                    % (code, detail))
                args.append("--strict")
            code, rules, detail = _validator_verdict(args)
            if code != 1:
                problems.append("exit %d, expected 1" % code)
            if spec["rule"] not in rules:
                problems.append("%s not reported (reported: %s)" % (spec["rule"], sorted(rules)))
            covered.add(spec["rule"])
            report.check(name, not problems, "\n".join(problems + [detail]))
    report.check("validator negative: every targeted rule has a failing fixture",
                 TARGETED_DOCUMENT_RULES <= covered,
                 "no fixture for %s" % sorted(TARGETED_DOCUMENT_RULES - covered))


VALID = os.path.join(FIXTURES, "valid")


def phase_validator_positives(report, allow_missing):
    """tests/fixtures/valid/*.json: honest records the policy allows must pass --strict.

    Each spec mutates a raw golden record, scores it (so evidence_quality and confidence are
    recomputed by the scorer, never typed in) and validates it. Its control adds one mutation
    that removes the policy's reason and must fail the rule, so the pass is not a blind spot.
    """
    specs = sorted(glob.glob(os.path.join(VALID, "*.json")))
    if missing_scripts():
        (report.skip if allow_missing else report.fail)("validator positive: fixtures",
                                                        "scripts/ not present")
        return
    report.check("validator positive: tests/fixtures/valid holds fixtures", bool(specs), VALID)
    with tempfile.TemporaryDirectory() as tmp:
        for spec_path in specs:
            spec = read_json(spec_path)
            stem = os.path.basename(spec_path)[:-len(".json")]
            golden = read_json(os.path.join(FIXTURES, "buyers.golden.json"))

            def verdict(mutations, label):
                document = _apply_mutations(_invalid_base(spec["base"], None, spec), mutations)
                envelope = dict(golden, records=[document])
                source = os.path.join(tmp, "%s.%s.in.json" % (stem, label))
                with open(source, "w", encoding="utf-8") as fh:
                    json.dump(envelope, fh, ensure_ascii=False)
                scored = os.path.join(tmp, "%s.%s.scored.json" % (stem, label))
                code, _, err = run_script(spec["scorer"], [
                    "--input", source, "--query", os.path.join(FIXTURES, spec["query"]),
                    "--as-of", AS_OF, "--output", scored])
                if code != 0 or not os.path.isfile(scored):
                    return None, set(), "%s exit %d\n%s" % (spec["scorer"], code, err.strip()[:300])
                return _validator_verdict(["--input", scored, "--invariants", "--strict",
                                           "--as-of", AS_OF])

            code, rules, detail = verdict(spec["mutations"], "positive")
            report.check("validator positive: %s (%s) passes --strict after scoring"
                         % (stem, spec["policy"]), code == 0 and not rules,
                         "exit %s %s\n%s" % (code, sorted(rules), detail))
            control = spec["control"]
            code, rules, detail = verdict(spec["mutations"] + control["mutations"], "control")
            report.check("validator positive: %s control (%s) fails %s"
                         % (stem, control["description"], control["rule"]),
                         code == 1 and control["rule"] in rules,
                         "exit %s %s\n%s" % (code, sorted(rules), detail))


def _draft_text(name):
    with open(os.path.join(DRAFTS, name), encoding="utf-8") as fh:
        return fh.read()


_DRAFT_B = "buyer.en.gulfglow.md"
_DRAFT_BK = "buyer.ko.dunesourcing.md"
_DRAFT_SK = "seller.ko.hanbitcos.md"
_DRAFT_SE = "seller.en.hanbitcos.rfq134.md"
GOOD_DRAFTS = ((_DRAFT_B, ("buyers_uae",)), (_DRAFT_BK, ("buyers_uae",)),
               (_DRAFT_SK, ("sellers",)), (_DRAFT_SE, ("match_134", "rfq_134")))
_B_FOOTER = "partners@tradewith.example\n\nPersonalization facts"
_B_CHANNEL = "Recipient channel: partnership_form — https://gulfglow.example/brand-partnership"
_B_NOTICE = ("- Submitted through the distributor or partner application form published at\n"
             "  https://gulfglow.example/brand-partnership.\n"
             "- Sender: TradeWith — partners@tradewith.example\n"
             "- No telephone number is contacted from this workflow.")
_SK_NOTICE = ("Required legal notices\n- 제목 맨 앞에 (광고) 표시\n"
              "- 전송자: TradeWith (서울특별시 중구 세종대로 12)\n"
              "- 연락처: partners@tradewith.example\n"
              "- 수신거부: partners@tradewith.example 로 회신하시면 이후 광고성 정보를 보내지 않습니다.\n"
              "- 본 메일은 https://hanbitcos.example/contact 에 공개된 문의 채널로 발송되었습니다.\n\n")
_BK_PARTNER = "귀사 파트너 페이지는 브랜드 제휴 문의를 받는다고 안내하고 있습니다."
_BK_GREETING = "안녕하세요, Dune Sourcing Partners LLC 무역팀 여러분."
_SK_GREETING = "안녕하세요, Hanbit Cosmetics Co., Ltd. 수출팀님."
_B_WHO = "We are TradeWith, a Korea-based B2B sourcing desk for K-Beauty."
_B_OPENING = ("Your brand-partnership page states that partnership enquiries are reviewed by your "
              "sourcing team.")
_B_RULE4 = ("Your news page, dated 2026-07-15, says you are actively looking for new Korean beauty "
            "brands.")
_B_RULE4_FACT = ("(observed 2026-09-10)\n\nCompliance checks",
                 "(observed 2026-09-10)\n- News page dated 2026-07-15 announces an open call for new "
                 "Korean beauty brands — [https://gulfglow.example/news/open-call-2026] (observed "
                 "2026-09-10)\n\nCompliance checks")
_SE_DEMAND = ("Requirements: MOQ ceiling",
              "A buyer is currently looking for private-label sunscreen into the United Arab "
              "Emirates.\nRequirements: MOQ ceiling")

# (label, base draft, [(old, new), ...] applied once each in order, --record keys, rule or None)
# The personal-data and later-state strings live here, not in tests/fixtures/, because the
# fixture scans (INV-31, INV-09) must keep finding none there.
DRAFT_CASES = (
    ("envelope line 4 removed", _DRAFT_B, [("Approval: required (human)\n", "")], (), "DRAFT-01"),
    ("checklist item pre-ticked", _DRAFT_B,
     [("- [ ] Contact channel is an official company channel",
       "- [x] Contact channel is an official company channel")], (), "DRAFT-01"),
    ("line 3 says auto-send true", _DRAFT_B, [("Auto-send: false", "Auto-send: true")], (), "INV-09"),
    ("a state past review", _DRAFT_B,
     [("Status: READY_FOR_REVIEW", "Status: APPROVED_FOR_OUTREACH")], (), "INV-09"),
    ("subject over 60 characters", _DRAFT_B,
     [("Subject: Korean sunscreen suppliers for Gulf Glow",
       "Subject: Korean sunscreen, serum, toner and cleanser suppliers for Gulf Glow Trading")],
     (), "DRAFT-02"),
    ("greeting drops the company", _DRAFT_B,
     [("Hello Gulf Glow Trading FZ-LLC brand partnerships team,", "Hello brand partnerships team,")],
     (), "DRAFT-03"),
    ("zero personalization facts", _DRAFT_B,
     [("- Brand-partnership page states that partnership enquiries are reviewed by the sourcing "
       "team — [https://gulfglow.example/brand-partnership] (observed 2026-09-10)\n", ""),
      ("- Korean beauty page states direct import from Korean manufacturers and six Korean "
       "brands carried — [https://gulfglow.example/korean-beauty] (observed 2026-09-10)\n", "")],
     (), "DRAFT-04"),
    ("body URL missing from facts", _DRAFT_SK,
     [("- 한국 무역 기관 디렉터리: 수출 시장 SA, KW, SG, JP — "
       "[https://koreatradeagency.example/directory/hanbitcos] (observed 2026-09-10)\n", "")],
     (), "DRAFT-04"),
    ("fact URL is on no evidence item", _DRAFT_B,
     [("[https://gulfglow.example/korean-beauty] (observed",
       "[https://gulfglow.example/press-2026] (observed")], ("buyers_uae",), "DRAFT-05"),
    ("fact observed date differs from the evidence", _DRAFT_B,
     [("(observed 2026-09-10)", "(observed 2026-09-12)")], ("buyers_uae",), "DRAFT-05"),
    ("unfilled {{token}}", _DRAFT_B,
     [("Your Korean beauty page states", "Your {{buyer.category_display}} page states")],
     (), "DRAFT-06"),
    ("surviving [[ev:]] marker", _DRAFT_B,
     [("carry six Korean brands.", "carry six Korean brands. [[ev: EV-003]]")], (), "DRAFT-06"),
    ("EN fake opt-out", _DRAFT_B,
     [("If it is not, tell us which categories are and we will keep the note on file.",
       "If it is not, please ignore this email.")], (), "DRAFT-07"),
    ("KO fake opt-out", _DRAFT_SK,
     [('관심이 없으시면 "관심 없음"이라고 회신해 주십시오. 이후로는 연락드리지 않겠습니다.',
       "회신이 필요 없으시면 이 메일은 무시하셔도 됩니다.")], (), "DRAFT-07"),
    ("advertising flag set to no", _DRAFT_B,
     [("Advertising label / opt-out required: unknown",
       "Advertising label / opt-out required: no")], (), "DRAFT-08"),
    ("claims verified is blocked", _DRAFT_B,
     [("Claims verified against evidence: yes", "Claims verified against evidence: blocked")],
     (), "DRAFT-08"),
    ("jurisdiction differs from the record country", _DRAFT_B,
     [("Jurisdiction: United Arab Emirates", "Jurisdiction: Saudi Arabia")],
     ("buyers_uae",), "DRAFT-08"),
    ("drafted from an unscored record", _DRAFT_B, [], ("buyers_golden",), "DRAFT-09"),
    ("drafted for a seller the match run excluded", _DRAFT_SE,
     [("Hanbit Cosmetics Co., Ltd.", "Daehan Sun Care Co., Ltd."),
      ("Hanbit Cosmetics Co., Ltd.", "Daehan Sun Care Co., Ltd.")],
     ("match_134", "rfq_134"), "DRAFT-09"),
    ("channel is not one of the record's", _DRAFT_B,
     [(_B_CHANNEL, "Recipient channel: partnership_form — https://gulfglow.example/other-form")],
     ("buyers_uae",), "DRAFT-10"),
    ("channel type outside the vocabulary", _DRAFT_B,
     [("Recipient channel: partnership_form", "Recipient channel: personal_email")],
     (), "DRAFT-10"),
    ("KR corporate email subject without (광고)", _DRAFT_SK,
     [("Subject: (광고) 선케어", "Subject: 선케어")], (), "DRAFT-11"),
    ("body over the 150-word buyer limit (strict)", _DRAFT_B,
     [("We are TradeWith, a Korea-based B2B sourcing desk for K-Beauty.",
       "We are TradeWith, a Korea-based B2B sourcing desk for K-Beauty."
       + " We compare Korean skincare makers on published facts." * 20)], (), "DRAFT-12"),
    ("notice block not verbatim", _DRAFT_B,
     [("- No telephone number is contacted from this workflow.\n", "")], (), "R10.4.6"),
    ("KR required notice block absent", _DRAFT_SK, [(_SK_NOTICE, "")], (), "R10.4.6"),
    ("no block on file and no fallback line", _DRAFT_B,
     [(_B_CHANNEL, "Recipient channel: linkedin — https://www.linkedin.example/company/gulfglow")],
     (), "R10.4.6"),
    ("free-mail sender address", _DRAFT_B,
     [(_B_FOOTER, "tradewith.desk@gmail.com\n\nPersonalization facts")], (), "INV-31"),
    ("personal local part (name-shape heuristic: warning only, even under --strict)", _DRAFT_B,
     [(_B_FOOTER, "minji.kim@tradewith.example\n\nPersonalization facts")], (), ("WARN", "INV-31")),
    ("KR mobile number", _DRAFT_B,
     [(_B_FOOTER, "partners@tradewith.example · 010-2345-6789\n\nPersonalization facts")],
     (), "INV-31"),
    ("+82 mobile number", _DRAFT_B,
     [(_B_FOOTER, "partners@tradewith.example · +82 10 2345 6789\n\nPersonalization facts")],
     (), "INV-31"),
    ("EN honorific and name", _DRAFT_B,
     [("brand partnerships team,", "brand partnerships team, attention Mr. Haddad,")],
     (), "INV-31"),
    ("KO name and title (surname heuristic: warning only, even under --strict)", _DRAFT_SK,
     [("수출팀님.", "수출팀 김민지 과장님.")], (), ("WARN", "INV-31")),
    ("EN third-party action claim", _DRAFT_B,
     [("Your brand-partnership page states",
       "A distributor asked us specifically about your brand. Your brand-partnership page states")],
     (), "INV-34"),
    ("EN uncited buyer claim", _DRAFT_B,
     [("We are TradeWith, a Korea",
       "We have a buyer currently looking for your sunscreen. We are TradeWith, a Korea")],
     (), "INV-34"),
    ("EN third-party claim with a modifier before the verb", _DRAFT_B,
     [("We are TradeWith, a Korea",
       "A distributor in Dubai is actively looking for your sunscreen. We are TradeWith, a Korea")],
     (), "INV-34"),
    ("EN third-party claim that a client wants the recipient's line", _DRAFT_B,
     [("We are TradeWith, a Korea",
       "One of our clients in Riyadh needs your SPF range. We are TradeWith, a Korea")],
     (), "INV-34"),
    ("KO third-party interest claim", _DRAFT_SK,
     [("저희는 TradeWith, K-Beauty 해외", "해당 바이어가 관심이 있습니다.\n저희는 TradeWith, K-Beauty 해외")],
     (), "INV-34"),
    ("demand claim citing a closed RFQ", _DRAFT_SE, [_SE_DEMAND], ("match_134", "rfq_134_closed"),
     "INV-34"),
    ("demand claim without the RFQ status in the body", _DRAFT_SE,
     [_SE_DEMAND, ("status matching as of 2026-09-12", "as of 2026-09-12")], (), "INV-34"),
    ("demand claim citing an open RFQ with status and date", _DRAFT_SE, [_SE_DEMAND],
     ("match_134", "rfq_134"), None),
    ("no block on file, literal fallback line present", _DRAFT_B,
     [(_B_CHANNEL, "Recipient channel: linkedin — https://www.linkedin.example/company/gulfglow"),
      (_B_NOTICE, "- No notice block on file for United Arab Emirates / linkedin — obtain wording "
                  "before sending")], (), None),
    ("KO buyer channel is not one of the record's", _DRAFT_BK,
     [("corporate_email — trade@dunesourcing.example", "corporate_email — sales@dunesourcing.example")],
     ("buyers_uae",), "DRAFT-10"),
    # INV-34 honest wording: a sender's own search, an invitation, a negative and a generic
    # customer request name no third party wanting the recipient.
    ("KO invitation to name categories of interest", _DRAFT_BK,
     [("해당되지 않는다면 검토 중인 카테고리를", "관심이 있는 카테고리를")], ("buyers_uae",), None),
    ("KO conditional interest", _DRAFT_BK,
     [("해당되지 않는다면 검토 중인 카테고리를", "혹시 관심이 있다면 검토 중인 카테고리를")], (), None),
    ("KO sender's own search for partners", _DRAFT_BK,
     [(_BK_PARTNER, "저희는 UAE 유통 파트너를 찾고 있습니다.")], ("buyers_uae",), None),
    ("KO negative: no enquiry is pending", _DRAFT_BK, [(_BK_PARTNER, "회신 대기 중인 문의는 없습니다.")],
     (), None),
    ("EN sender's own search for distributors", _DRAFT_B,
     [(_B_WHO, "We are currently looking for UAE distributors.")], ("buyers_uae",), None),
    ("EN generic customer request", _DRAFT_B,
     [(_B_WHO, _B_WHO + " Our customers have asked for SPF data sheets.")], (), None),
    # templates/buyer_outreach.md section 3: "you are looking for" licensed by a dated
    # sourcing_signals fact instead of an RFQ.
    ("buyer rule-4 dated sourcing sentence with a dated fact line", _DRAFT_B,
     [(_B_OPENING, _B_RULE4), _B_RULE4_FACT], (), None),
    ("buyer rule-4 sentence backed by a sourcing_signals evidence item", _DRAFT_B,
     [(_B_OPENING, _B_RULE4), _B_RULE4_FACT], ("buyers_uae_signal",), None),
    ("buyer rule-4 sentence whose fact URL is not a sourcing_signals item", _DRAFT_B,
     [(_B_OPENING, _B_RULE4), _B_RULE4_FACT], ("buyers_uae",), "INV-34"),
    ("buyer rule-4 sentence without its date", _DRAFT_B,
     [(_B_OPENING, _B_RULE4.replace(", dated 2026-07-15,", "")), _B_RULE4_FACT], (), "INV-34"),
    # INV-31: role and department greetings, role addresses, the company's own names.
    ("KO greeting to the company's Korean short name and title (record names it)", _DRAFT_SK,
     [(_SK_GREETING, "안녕하세요, Hanbit Cosmetics Co., Ltd. 한빛 대표님.")], ("sellers_ko",), None),
    ("KO greeting to a short name and title without --record (warning only)", _DRAFT_SK,
     [(_SK_GREETING, "안녕하세요, Hanbit Cosmetics Co., Ltd. 한빛 대표님.")], (), ("WARN", "INV-31")),
    ("role address with lab and K-Beauty words", _DRAFT_B,
     [(_B_FOOTER, "kbeauty.lab@brand.example\n\nPersonalization facts")], (), None),
    ("role address built from the company's own name", _DRAFT_SK,
     [(_B_FOOTER, "hanbit.korea@hanbitcos.example\n\nPersonalization facts")], (), None),
    # DRAFT-03: a Korean greeting may address an English-named company by its Korean name.
    ("KO greeting by the record's company_name_ko", _DRAFT_SK,
     [(_SK_GREETING, "안녕하세요, 한빛화장품 수출팀님.")], ("sellers_ko",), None),
    ("KO greeting by a Korean name no --record supplies", _DRAFT_SK,
     [(_SK_GREETING, "안녕하세요, 한빛화장품 수출팀님.")], (), "DRAFT-03"),
) + tuple(
    ("KO role greeting %s" % role, _DRAFT_BK,
     [(_BK_GREETING, "안녕하세요, Dune Sourcing Partners LLC %s." % role)], (), None)
    for role in ("상품 기획 팀장님", "홍보 팀장님", "고객 서비스 팀장님", "성분 연구 실장", "전문가님",
                 "문의 주신 부장님")
)


def phase_outreach_drafts(report, pipeline, allow_missing):
    if missing_scripts() or not (pipeline.buyers_uae and pipeline.sellers and pipeline.match_134):
        (report.skip if allow_missing else report.fail)("outreach draft: validator cases",
                                                        "pipeline output unavailable")
        return
    sys.path.insert(0, SCRIPT_DIR)
    sys.dont_write_bytecode = True
    import validate_output  # noqa: E402

    with tempfile.TemporaryDirectory() as tmp:
        records = {"rfq_134": os.path.join(FIXTURES, "rfq.134.json"),
                   "buyers_golden": os.path.join(FIXTURES, "buyers.golden.json")}
        closed = read_json(records["rfq_134"])
        closed["status"] = "closed"
        # The UAE envelope with Gulf Glow's open-call page also recorded as a dated
        # sourcing_signals item (buyer template section 3), and the seller envelope with
        # Hanbit's Korean name (seller.schema.json company_name_ko).
        signal = _json_copy(pipeline.buyers_uae)
        sellers_ko = _json_copy(pipeline.sellers)
        for record in signal.get("records") or []:
            if record.get("buyer_id") == "BUY-gulfglow-example":
                record["evidence"].append({
                    "evidence_id": "EV-010", "claim": "sourcing_signals",
                    "value": "open_call_for_suppliers",
                    "source_url": "https://gulfglow.example/news/open-call-2026",
                    "source_domain": "gulfglow.example", "source_type": "official_site",
                    "source_tier": 1, "is_official": True, "observed_at": "2026-09-10T09:00:00Z",
                    "source_date": "2026-07-15", "confidence": 0.9,
                    "quote_or_summary": "Open call for suppliers: we are accepting new Korean beauty "
                                        "brands this season.", "retrieval_method": "page_fetch"})
        for record in sellers_ko.get("records") or []:
            if record.get("seller_id") == "SEL-hanbitcos-example":
                record["company_name_ko"] = "한빛화장품"
        for key, document in (("buyers_uae", pipeline.buyers_uae), ("sellers", pipeline.sellers),
                              ("match_134", pipeline.match_134), ("rfq_134_closed", closed),
                              ("buyers_uae_signal", signal), ("sellers_ko", sellers_ko)):
            records[key] = os.path.join(tmp, "%s.json" % key)
            with open(records[key], "w", encoding="utf-8") as fh:
                json.dump(document, fh, ensure_ascii=False)

        def verdict(text, keys):
            path = os.path.join(tmp, "draft.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            args = ["--input", path, "--schema", "outreach-draft", "--strict"]
            for key in keys:
                args.extend(["--record", records[key]])
            return _validator_verdict(args)

        for name, keys in GOOD_DRAFTS:
            for use in (keys, ()):
                code, rules, detail = verdict(_draft_text(name), use)
                report.check("outreach draft: good %s passes --strict %s"
                             % (name, "with --record" if use else "without --record"),
                             code == 0 and not rules,
                             "exit %d %s\n%s" % (code, sorted(rules), detail))

        covered = set()
        for label, base, edits, keys, expected in DRAFT_CASES:
            shown = "WARN %s" % expected[1] if isinstance(expected, tuple) else expected or "PASS"
            name = "outreach draft: %s %s" % (shown, label)
            text = _draft_text(base)
            anchor_missing = None
            for old, new in edits:
                if old not in text:
                    anchor_missing = old
                    break
                text = text.replace(old, new, 1)
            if anchor_missing is not None:
                report.fail(name, "mutation anchor not found: %r" % (anchor_missing[:80],))
                continue
            code, rules, detail = verdict(text, keys)
            if expected is None:
                report.check(name, code == 0 and not rules,
                             "exit %d %s\n%s" % (code, sorted(rules), detail))
            elif isinstance(expected, tuple):
                # A non-blocking warning: reported, yet the draft still passes --strict.
                report.check(name, code == 0 and rules == {expected[1]},
                             "exit %d, reported %s\n%s" % (code, sorted(rules), detail))
            else:
                covered.add(expected)
                report.check(name, code == 1 and expected in rules,
                             "exit %d, reported %s\n%s" % (code, sorted(rules), detail))
        report.check("outreach draft: every draft rule has a failing case",
                     TARGETED_DRAFT_RULES <= covered,
                     "no case for %s" % sorted(TARGETED_DRAFT_RULES - covered))

    # compliance.config.json is the lookup; templates/legal_notices.md is the wording. They
    # must name the same keys with the same status, or a draft is checked against a block that
    # the page does not carry.
    config = validate_output.load_compliance_config()
    blocks = validate_output.load_legal_notices()
    with open(os.path.join(PKG_ROOT, "templates", "legal_notices.md"), encoding="utf-8") as fh:
        index = dict(re.findall(r"^\| `([A-Z]{2}\.[a-z_]+)` \| `([a-z_]+)` \|", fh.read(), re.M))
    statuses = dict((key, value.get("status")) for key, value in config["notice_blocks"].items())
    report.check("outreach draft: compliance.config.json notice_blocks agree with "
                 "legal_notices.md (keys, status, block bodies)",
                 statuses == index and sorted(blocks) == sorted(index)
                 and all(blocks[key] for key in blocks),
                 "config %s\nindex %s\nblocks %s" % (statuses, index, sorted(blocks)))

    # The importable function the adapter can call on its JSON queue shape.
    json_draft = {"entity_id": "BUY-gulfglow-example", "side": "Buyer",
                  "channel_type": "partnership_form",
                  "channel_value": "https://gulfglow.example/brand-partnership",
                  "language": "English", "subject": "Korean sunscreen suppliers for Gulf Glow",
                  "body": "(rendered in draft_markdown)",
                  "personalization_facts": [{"fact": "Brand-partnership page",
                                             "source_url": "https://gulfglow.example/brand-partnership",
                                             "evidence_id": "EV-006"}],
                  "draft_markdown": _draft_text(_DRAFT_B)}
    issues = validate_output.validate_outreach_draft(json_draft, [pipeline.buyers_uae])
    report.check("outreach draft: validate_outreach_draft() accepts a clean JSON draft",
                 not issues, "%r" % (issues[:3],))
    wrong = _json_copy(json_draft)
    wrong["personalization_facts"][0]["evidence_id"] = "EV-001"
    rules = set(i["invariant"] for i in validate_output.validate_outreach_draft(
        wrong, [pipeline.buyers_uae]))
    report.check("outreach draft: a JSON fact citing an evidence_id on another URL is DRAFT-05",
                 "DRAFT-05" in rules, "reported %s" % sorted(rules))
    bare = _json_copy(json_draft)
    del bare["draft_markdown"]
    rules = set(i["invariant"] for i in validate_output.validate_outreach_draft(bare))
    report.check("outreach draft: a JSON draft without draft_markdown is not reviewable (DRAFT-01)",
                 "DRAFT-01" in rules, "reported %s" % sorted(rules))


def phase_mode1_to_mode4(report, allow_missing):
    cases = ("e2e: Mode 1 score_buyer -> Mode 4 draft passes against the qualified record",
             "e2e: the same draft aimed at a non-qualified record is refused (DRAFT-09)")
    if missing_scripts():
        for case in cases:
            (report.skip if allow_missing else report.fail)(case, "scripts/ not present")
        return
    with tempfile.TemporaryDirectory() as tmp:
        scored = os.path.join(tmp, "buyers.scored.json")
        code, _, err = run_script("score_buyer.py", [
            "--input", os.path.join(FIXTURES, "buyers.golden.json"),
            "--query", os.path.join(FIXTURES, "query-buyer-uae-kbeauty.json"),
            "--as-of", AS_OF, "--output", scored])
        problems = []
        if code != 0 or not os.path.isfile(scored):
            problems.append("score_buyer.py exit %d\n%s" % (code, err.strip()[:300]))
            report.fail(cases[0], "\n".join(problems))
            report.fail(cases[1], "no scored envelope")
            return
        envelope = read_json(scored)
        target = index_by(envelope.get("records") or [], "buyer_id").get("BUY-gulfglow-example")
        if not target or target.get("qualified") is not True:
            problems.append("BUY-gulfglow-example is not a qualified scored record: %r"
                            % ((target or {}).get("qualified"),))
        code, rules, detail = _validator_verdict([
            "--input", os.path.join(DRAFTS, _DRAFT_B), "--schema", "outreach-draft",
            "--record", scored, "--strict"])
        if code != 0 or rules:
            problems.append("exit %d %s\n%s" % (code, sorted(rules), detail))
        report.check(cases[0], not problems, "\n".join(problems))

        other = next((record for record in envelope.get("records") or []
                      if record.get("qualified") is False), None)
        if other is None:
            report.fail(cases[1], "the scored envelope holds no non-qualified record")
            return
        path = os.path.join(tmp, "draft.nonqualified.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_draft_text(_DRAFT_B).replace("Gulf Glow Trading FZ-LLC", other["company_name"]))
        code, rules, detail = _validator_verdict([
            "--input", path, "--schema", "outreach-draft", "--record", scored, "--strict"])
        report.check(cases[1], code == 1 and "DRAFT-09" in rules,
                     "%s: exit %d %s\n%s" % (other.get("buyer_id"), code, sorted(rules), detail))


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
    phase_audit_regressions(report, args.allow_missing_scripts)
    phase_validator_negatives(report, pipeline, args.allow_missing_scripts)
    phase_validator_positives(report, args.allow_missing_scripts)
    phase_outreach_drafts(report, pipeline, args.allow_missing_scripts)
    phase_mode1_to_mode4(report, args.allow_missing_scripts)
    phase_safety(report)
    phase_package(report)
    phase_adapter_guards(report, pipeline, args.allow_missing_scripts)

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
