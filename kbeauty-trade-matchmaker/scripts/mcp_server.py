#!/usr/bin/env python3
"""Expose the package's deterministic scripts as tools over a stdio JSON-RPC tool server.

The server reads newline-delimited JSON-RPC 2.0 messages on stdin and writes one
response per request on stdout; every diagnostic goes to stderr. Each tool runs one of
this package's own scripts as a child process with exactly the CLI flags that script
already has, so a tool result is byte-for-byte what the command line would print.

Safety rules, all enforced here:
  - It never contacts anyone, fetches a web page or opens a network connection, and it
    runs nothing but the scripts listed in TOOLS.
  - --root is required. Every input and output path must resolve, after realpath, inside
    that one directory; a symlink cannot lead outside it.
  - The server writes nothing itself. A child writes only to an output_path that does
    not exist yet (no overwrite), inside the root and outside the package.
  - Every tool requires as_of; the server never supplies or guesses a date.
  - A child never inherits the server's stdin, so a script cannot consume the
    JSON-RPC stream.
  - output_path must end in .json or .csv and may not name a hidden file or folder.
  - No single input frame can end the process: parse depth, lone surrogates and a
    closed stderr are all answered or absorbed.

Korean gloss: 패키지의 결정론적 스크립트를 stdio 도구 서버로 노출한다. 아무것도 전송하거나
가져오지 않으며, 지정한 --root 안에서만 읽고 쓰고, 기존 파일을 덮어쓰지 않는다.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

SCRIPT_NAME = "mcp_server.py"
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
PACKAGE_ROOT = os.path.dirname(SCRIPT_DIR)

SERVER_NAME = "kbeauty-trade-matchmaker"
SERVER_TITLE = "K-Beauty Trade Matchmaker"

# Legacy revisions open a session with `initialize`; newest first. A client asking for
# one of them gets it echoed back; any other request is answered with the first entry,
# the newest revision this server can speak through the handshake.
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
# Modern revisions carry the version in every request's params._meta and have no
# handshake; server/discover advertises them.
MODERN_VERSIONS = ("2026-07-28",)
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
TOOLS_TTL_MS = 300000
DISCOVER_TTL_MS = 3600000

DEFAULT_TOOL_TIMEOUT = 120
DEFAULT_MAX_INLINE_BYTES = 32768
MIN_INLINE_BYTES = 1024
MAX_FRAME_BYTES = 16 * 1024 * 1024
# An inline document that travels on the child's command line (score_buyer/score_seller
# query) is capped well below Linux's per-argument limit (MAX_ARG_STRLEN, 128 KiB).
MAX_ARGV_INLINE_BYTES = 65536
OUTPUT_SUFFIXES = (".json", ".csv")
MAX_DIAGNOSTIC_LINES = 200
LOG_PREFIX = "kbtm-mcp:"

# JSON-RPC 2.0 and MCP error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_VERSION = -32022

INSTRUCTIONS = (
    "Deterministic normalize, dedupe, scoring, validation, calibration, run-diff, re-check "
    "and export tools for K-Beauty trade records. Every call requires as_of (YYYY-MM-DD). "
    "Paths resolve inside the server's --root. No tool contacts anyone, fetches a web page "
    "or opens a network connection; outreach ends at READY_FOR_REVIEW. A result above the "
    "inline size cap is refused: pass output_path (a file that does not exist yet) and the "
    "result reports its path, size and sha256 instead."
)

_STRICT_DATE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TOOL_NAME = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")

# validate_output.py --schema choices, pinned here so building tools/list imports no
# other script; the test suite asserts it equals validate_output.KINDS + ("auto",).
VALIDATE_SCHEMAS = ("buyer", "seller", "rfq", "evidence", "match-result", "discovery-result",
                    "acceptance-report", "run-diff", "recheck-queue", "tradewith-bulk-buyers",
                    "auto")

_PATH = {"type": "string", "minLength": 1, "maxLength": 4096}
_ENTITY = {"type": "string", "enum": ["buyer", "seller", "auto"],
           "description": "Record kind (script default: auto)."}
_THRESHOLD = {"type": "integer", "minimum": 0, "maximum": 100,
              "description": "Override the configured qualification threshold."}
_THRESHOLD_MODE = {"type": "string", "enum": ["fixed", "percentile"],
                   "description": "Override the configured threshold mode."}
_TOP = {"type": "integer", "minimum": 1, "maximum": 1000,
        "description": "Return at most this many ranked results."}


def _prop(base, description):
    merged = dict(base)
    merged["description"] = description
    return merged


# The tool registry, in pipeline order; tools/list returns it in exactly this order.
# Keys:
#   script      the package script the tool runs (nothing else is ever executed)
#   output      "json" or "csv" (export_leads switches on its format argument)
#   writes      True when output_path is offered (then the tool is not read-only)
#   primary     the inline/path pair of the main document: ("input", "input_path"),
#               None for a tool that takes only paths
#   primary_required  whether exactly one of the primary pair must be given
#   primary_alternative  argument names that, all given together, stand in for an absent
#               primary document (score_match: rfq_path plus sellers_path)
#   extra       tool-specific properties
#   argv        ordered (argument, kind, flag, false_flag) rows mapping arguments to flags;
#               kind is "path", "paths", "value" or "bool"
#   pairs       inline/path pairs other than the primary one, each (inline, path, flag)
TOOLS = [
    {
        "name": "normalize_company",
        "script": "normalize_company.py",
        "title": "Normalize company records",
        "description": ("Derive canonical_domain, normalized_name and alias_domains on buyer "
                        "or seller records (pipeline stage Normalize)."),
        "output": "json", "writes": True,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {
            "entity": _ENTITY,
            "envelope": {"type": "boolean",
                         "description": "Wrap the output array as {\"records\": [...]}."},
        },
        "argv": [("entity", "value", "--entity", None),
                 ("envelope", "bool", "--envelope", None)],
    },
    {
        "name": "dedupe_companies",
        "script": "dedupe_companies.py",
        "title": "Merge duplicate companies",
        "description": ("Merge duplicate buyer or seller records by canonical_domain, then "
                        "normalized_name plus country; conflicts are recorded, never resolved "
                        "silently (pipeline stage Dedupe)."),
        "output": "json", "writes": True,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {
            "entity": _ENTITY,
            "strict_country": {"type": "boolean", "description": (
                "false allows a name-based merge when one country is unknown (single-country "
                "runs only); script default true.")},
            "report_only": {"type": "boolean", "description": "Emit the merge plan only."},
        },
        "argv": [("entity", "value", "--entity", None),
                 ("strict_country", "bool", "--strict-country", "--no-strict-country"),
                 ("report_only", "bool", "--report-only", None)],
    },
    {
        "name": "score_buyer",
        "script": "score_buyer.py",
        "title": "Score buyer candidates",
        "description": ("Score deduplicated buyer records against a query surface and return "
                        "a discovery-result envelope (pipeline stage Score, Mode 1)."),
        "output": "json", "writes": True,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {
            "query": {"type": "object", "description": "Inline query surface."},
            "query_path": _prop(_PATH, "Query surface JSON file; exclusive with query."),
            "threshold": _THRESHOLD, "threshold_mode": _THRESHOLD_MODE, "top": _TOP,
        },
        "argv": [("threshold", "value", "--threshold", None),
                 ("threshold_mode", "value", "--threshold-mode", None),
                 ("top", "value", "--top", None)],
        "pairs": [("query", "query_path", "--query")],
    },
    {
        "name": "score_seller",
        "script": "score_seller.py",
        "title": "Score seller candidates",
        "description": ("Score deduplicated seller records against a query surface and "
                        "return a discovery-result envelope (pipeline stage Score, Mode 2)."),
        "output": "json", "writes": True,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {
            "query": {"type": "object", "description": "Inline query surface."},
            "query_path": _prop(_PATH, "Query surface JSON file; exclusive with query."),
            "threshold": _THRESHOLD, "threshold_mode": _THRESHOLD_MODE, "top": _TOP,
        },
        "argv": [("threshold", "value", "--threshold", None),
                 ("threshold_mode", "value", "--threshold-mode", None),
                 ("top", "value", "--top", None)],
        "pairs": [("query", "query_path", "--query")],
    },
    {
        "name": "score_match",
        "script": "score_match.py",
        "title": "Match an RFQ to sellers",
        "description": ("Run hard filters, the weighted score and the bounded rerank for one "
                        "RFQ against seller records and return a match-result (Mode 3). Pass "
                        "one envelope carrying rfq, records and query as input or input_path; "
                        "rfq_path plus sellers_path is the fallback without a query surface."),
        "output": "json", "writes": True,
        "primary": ("input", "input_path"), "primary_required": False,
        "primary_alternative": ("rfq_path", "sellers_path"),
        "extra": {
            "rfq_path": _prop(_PATH, "RFQ document file."),
            "sellers_path": _prop(_PATH, "Seller records file."),
            "rerank_input_path": _prop(_PATH, (
                "Model-authored rerank map file: seller_id -> {delta, rationale, "
                "evidence_ids}.")),
            "rationale_input_path": _prop(_PATH, (
                "Narrative map file: seller_id -> {rationale: [...], risks: [...]}.")),
            "threshold": _THRESHOLD, "threshold_mode": _THRESHOLD_MODE, "top": _TOP,
            "include_excluded": {"type": "boolean", "description": (
                "false drops excluded[] from the result; script default true.")},
        },
        "argv": [("rfq_path", "path", "--rfq", None),
                 ("sellers_path", "path", "--sellers", None),
                 ("rerank_input_path", "path", "--rerank-input", None),
                 ("rationale_input_path", "path", "--rationale-input", None),
                 ("threshold", "value", "--threshold", None),
                 ("threshold_mode", "value", "--threshold-mode", None),
                 ("top", "value", "--top", None),
                 ("include_excluded", "bool", "--include-excluded", "--no-include-excluded")],
    },
    {
        "name": "validate_output",
        "script": "validate_output.py",
        "title": "Validate a document",
        "description": ("Check a document against its schema and the contract invariants "
                        "(pipeline stage Verify). The report is the answer: valid false with "
                        "exit_code 1 is a successful call that found problems. Writes nothing."),
        "output": "json", "writes": False,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {
            "schema": {"type": "string", "enum": list(VALIDATE_SCHEMAS),
                       "description": "Document kind (script default: auto)."},
            "profile": {"type": "string", "enum": ["raw", "scored", "auto"],
                        "description": "Validation profile (script default: auto)."},
            "invariants": {"type": "boolean", "description": (
                "false skips the contract invariants; script default true.")},
            "strict": {"type": "boolean", "description": "Treat warnings as failures."},
            "max_errors": {"type": "integer", "minimum": 1, "maximum": 10000,
                           "description": "Cap each error list (script default 100)."},
        },
        "argv": [("schema", "value", "--schema", None),
                 ("profile", "value", "--profile", None),
                 ("invariants", "bool", "--invariants", "--no-invariants"),
                 ("strict", "bool", "--strict", None),
                 ("max_errors", "value", "--max-errors", None)],
        "fixed": ["--json"],
    },
    {
        "name": "make_review_sheet",
        "script": "make_review_sheet.py",
        "title": "Cut a blind review sheet",
        "description": ("Turn a scored discovery-result or match-result into a CSV review "
                        "sheet for an operator (calibration)."),
        "output": "csv", "writes": True,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {
            "blind": {"type": "boolean", "description": (
                "false keeps rank order and appends rank, score and qualified; script "
                "default true.")},
            "include_excluded": {"type": "boolean",
                                 "description": "Also list excluded[] records."},
        },
        "argv": [("blind", "bool", "--blind", "--no-blind"),
                 ("include_excluded", "bool", "--include-excluded", None)],
    },
    {
        "name": "acceptance_report",
        "script": "acceptance_report.py",
        "title": "Measure human acceptance",
        "description": ("Join filled review sheets to the scored runs they were cut from and "
                        "report the Human Acceptance Rate (calibration)."),
        "output": "json", "writes": True,
        "primary": None, "primary_required": False,
        "extra": {
            "scored_paths": {"type": "array", "minItems": 1, "maxItems": 50, "items": _PATH,
                             "description": "Scored discovery-result or match-result files."},
            "reviews_paths": {"type": "array", "minItems": 1, "maxItems": 50, "items": _PATH,
                              "description": "Filled review sheet CSV files."},
            "min_sample": {"type": "integer", "minimum": 0, "maximum": 100000,
                           "description": "Override calibration.min_sample."},
        },
        "required": ["scored_paths", "reviews_paths"],
        "argv": [("scored_paths", "paths", "--scored", None),
                 ("reviews_paths", "paths", "--reviews", None),
                 ("min_sample", "value", "--min-sample", None)],
    },
    {
        "name": "diff_runs",
        "script": "diff_runs.py",
        "title": "Compare two scored runs",
        "description": ("Report what changed between two scored runs of the same search or "
                        "RFQ: new and gone records, exclusions, score, rank, qualified and "
                        "Missing changes. At most one of before / after may be inline."),
        "output": "json", "writes": True,
        "primary": None, "primary_required": False,
        "extra": {
            "before": {"type": "object", "description": "The older run, inline."},
            "before_path": _prop(_PATH, "The older run file; exclusive with before."),
            "after": {"type": "object", "description": "The newer run, inline."},
            "after_path": _prop(_PATH, "The newer run file; exclusive with after."),
        },
        "argv": [],
        "pairs": [("before", "before_path", "--before"), ("after", "after_path", "--after")],
        "stdin_pairs": True,
    },
    {
        "name": "stale_evidence",
        "script": "stale_evidence.py",
        "title": "List evidence to re-check",
        "description": ("List the evidence that should be re-read, most urgent first. It "
                        "fetches nothing and changes no record or score."),
        "output": "json", "writes": True,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {"top": _prop(_TOP, "List only the first N queued records.")},
        "argv": [("top", "value", "--top", None)],
    },
    {
        "name": "export_leads",
        "script": "export_leads.py",
        "title": "Export scored leads",
        "description": ("Export the leads of one scored discovery-result as a CSV or as a "
                        "TradeWith admin bulk-import file (buyers only). It writes a file or "
                        "returns the text; it never posts, uploads or sends."),
        "output": "json_if_format", "writes": True,
        "primary": ("input", "input_path"), "primary_required": True,
        "extra": {
            "format": {"type": "string", "enum": ["csv", "tradewith-json", "tradewith-csv"],
                       "description": "Output format (script default: csv)."},
            "include_unqualified": {"type": "boolean", "description": (
                "Also export scored records with qualified != true.")},
            "min_score": {"type": "integer", "minimum": 0, "maximum": 100,
                          "description": "Also require qualification_score >= N."},
        },
        "argv": [("format", "value", "--format", None),
                 ("include_unqualified", "bool", "--include-unqualified", None),
                 ("min_score", "value", "--min-score", None)],
    },
]


class Refusal(Exception):
    """A tool call the server declines before or after running a script (isError)."""


class RpcError(Exception):
    """A JSON-RPC protocol error answered in the error member."""

    def __init__(self, code, message, data=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.data = data


# --------------------------------------------------------------------------
# tool schema
# --------------------------------------------------------------------------


def _input_schema(tool):
    properties = {"as_of": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
                            "description": ("Run date YYYY-MM-DD. Required: it is the only "
                                            "time source.")}}
    if tool["primary"]:
        inline, path = tool["primary"]
        properties[inline] = {"type": "object", "description": (
            "The input document inline (an array of records goes in {\"records\": [...]}); "
            "exclusive with %s." % path)}
        properties[path] = _prop(_PATH, "The input document as a file inside the root; "
                                        "exclusive with %s." % inline)
    for key, value in tool["extra"].items():
        properties[key] = value
    if tool["writes"]:
        properties["output_path"] = _prop(_PATH, (
            "Write the result to this new .json or .csv file inside the root instead of "
            "returning it; the file must not exist yet, its directory must, and no part of "
            "the path may start with a dot."))
    return {"type": "object", "additionalProperties": False,
            "required": ["as_of"] + list(tool.get("required", [])),
            "properties": properties}


def _annotations(tool):
    writes = bool(tool["writes"])
    return {"title": tool["title"], "readOnlyHint": not writes, "destructiveHint": False,
            "idempotentHint": not writes, "openWorldHint": False}


def tool_list():
    tools = []
    for tool in TOOLS:
        tools.append({"name": tool["name"], "title": tool["title"],
                      "description": tool["description"], "inputSchema": _input_schema(tool),
                      "annotations": _annotations(tool)})
    return tools


def _tool_by_name(name):
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None


# --------------------------------------------------------------------------
# path policy
# --------------------------------------------------------------------------


def _inside(path, directory):
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def _require_utf8(text, label):
    """Refuse a string holding a lone surrogate: it has no UTF-8 encoding."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise Refusal("%s is not valid UTF-8 text (it holds a lone surrogate)" % label)


def _within_package(directory):
    """True when directory is the package folder or lies below it.

    The check compares (st_dev, st_ino) of every ancestor with the package folder, so a
    case-changed or differently normalised spelling of the same folder on a
    case-insensitive or normalising file system is still recognised.
    """
    if _inside(directory, PACKAGE_ROOT):
        return True
    try:
        package = os.stat(PACKAGE_ROOT)
    except OSError:
        return False
    current = directory
    while True:
        try:
            info = os.stat(current)
        except OSError:
            info = None
        if info is not None and (info.st_dev, info.st_ino) == (package.st_dev,
                                                                package.st_ino):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _resolve(root, raw, label):
    if not isinstance(raw, str) or not raw.strip():
        raise Refusal("%s is empty" % label)
    if "\0" in raw:
        raise Refusal("%s contains a NUL byte" % label)
    _require_utf8(raw, label)
    joined = raw if os.path.isabs(raw) else os.path.join(root, raw)
    return os.path.normpath(joined)


def resolve_read_path(root, raw, label):
    """A file to read: its realpath must be an existing regular file inside the root."""
    real = os.path.realpath(_resolve(root, raw, label))
    if not _inside(real, root):
        raise Refusal("%s %r resolves outside the server root %s (path rule: every path "
                      "must stay inside --root)" % (label, raw, root))
    if not os.path.isfile(real):
        raise Refusal("%s %r is not an existing file" % (label, raw))
    return real


def resolve_output_path(root, raw, read_paths):
    """A file to create: new, inside the root, outside the package, parent existing."""
    label = "output_path"
    lexical = _resolve(root, raw, label)
    name = os.path.basename(lexical)
    if name in ("", ".", ".."):
        raise Refusal("output_path %r does not name a file" % raw)
    parent = os.path.realpath(os.path.dirname(lexical))
    real = os.path.join(parent, name)
    if not _inside(real, root):
        raise Refusal("output_path %r resolves outside the server root %s (path rule: every "
                      "path must stay inside --root)" % (raw, root))
    if _within_package(parent):
        raise Refusal("output_path %r is inside the skill package; write results elsewhere "
                      "in the root" % raw)
    if not name.lower().endswith(OUTPUT_SUFFIXES):
        raise Refusal("output_path %r must end in .json or .csv" % raw)
    hidden = [part for part in os.path.relpath(real, root).split(os.sep)
              if part.startswith(".")]
    if hidden:
        raise Refusal("output_path %r names a hidden file or folder (%s); pick a visible "
                      "path" % (raw, hidden[0]))
    if not os.path.isdir(parent):
        raise Refusal("the directory of output_path %r does not exist; the server never "
                      "creates directories" % raw)
    if os.path.lexists(real):
        raise Refusal("output_path %r already exists; the server never overwrites a file, "
                      "so pick a new name" % raw)
    if real in read_paths:
        raise Refusal("output_path %r is also an input of this call" % raw)
    return real


# --------------------------------------------------------------------------
# tool call
# --------------------------------------------------------------------------


def _check_as_of(value):
    text = value if isinstance(value, str) else ""
    if not _STRICT_DATE.match(text):
        raise Refusal("as_of %r is not a YYYY-MM-DD date" % (value,))
    try:
        datetime.date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError as exc:
        raise Refusal("as_of %r is not a valid calendar date: %s" % (value, exc))
    return text


def _exclusive(arguments, inline, path, required):
    given = [key for key in (inline, path) if key in arguments]
    if len(given) == 2:
        raise Refusal("pass either %s or %s, not both" % (inline, path))
    if required and not given:
        raise Refusal("pass one of %s or %s" % (inline, path))
    return given[0] if given else None


def _inline_text(value, label):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    _require_utf8(text, label)
    return text


def _inline_bytes(value, label):
    return _inline_text(value, label).encode("utf-8")


def build_command(tool, arguments, root):
    """Validate the arguments and return (argv, stdin_bytes, output_real, output_kind)."""
    errors = _common.validate(arguments, _input_schema(tool))
    if errors:
        raise Refusal("invalid arguments: %s" % "; ".join(errors[:5]))
    as_of = _check_as_of(arguments.get("as_of"))

    argv = []
    stdin_bytes = None
    read_paths = []

    def read(raw, label):
        real = resolve_read_path(root, raw, label)
        read_paths.append(real)
        return real

    if tool["primary"]:
        inline, path = tool["primary"]
        chosen = _exclusive(arguments, inline, path, tool["primary_required"])
        if chosen == inline:
            stdin_bytes = _inline_bytes(arguments[inline], inline)
            argv += ["--input", "-"]
        elif chosen == path:
            argv += ["--input", read(arguments[path], path)]
        alternative = tool.get("primary_alternative")
        if chosen is None and alternative and not all(k in arguments for k in alternative):
            # Without this the script would read an empty stdin and blame "<stdin>",
            # which the caller never used.
            raise Refusal("pass %s or %s, or %s" % (
                inline, path, " together with ".join(alternative)))
    for inline, path, flag in tool.get("pairs", []):
        chosen = _exclusive(arguments, inline, path, bool(tool.get("stdin_pairs")))
        if chosen == path:
            argv += [flag, read(arguments[path], path)]
        elif chosen == inline and tool.get("stdin_pairs"):
            if stdin_bytes is not None:
                raise Refusal("at most one document may be inline per call; pass the other "
                              "as a path")
            stdin_bytes = _inline_bytes(arguments[inline], inline)
            argv += [flag, "-"]
        elif chosen == inline:
            text = _inline_text(arguments[inline], inline)
            if len(text.encode("utf-8")) > MAX_ARGV_INLINE_BYTES:
                raise Refusal("inline %s is above %d bytes; save it to a file inside the root "
                              "and pass %s instead" % (inline, MAX_ARGV_INLINE_BYTES, path))
            argv += [flag, text]
    for key, kind, flag, false_flag in tool["argv"]:
        if key not in arguments:
            continue
        value = arguments[key]
        if kind == "path":
            argv += [flag, read(value, key)]
        elif kind == "paths":
            for index, item in enumerate(value):
                argv += [flag, read(item, "%s[%d]" % (key, index))]
        elif kind == "bool":
            chosen_flag = flag if value else false_flag
            if chosen_flag:
                argv.append(chosen_flag)
        else:
            text = str(value)
            _require_utf8(text, key)
            argv += [flag, text]
    argv += list(tool.get("fixed", []))
    argv += ["--as-of", as_of]

    output_real = None
    if tool["writes"] and "output_path" in arguments:
        output_real = resolve_output_path(root, arguments["output_path"], read_paths)
        argv += ["--output", output_real]

    output_kind = tool["output"]
    if output_kind == "json_if_format":
        output_kind = "json" if arguments.get("format") == "tradewith-json" else "csv"
    return argv, stdin_bytes, output_real, output_kind


def _child_env():
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("TRADEWITH_"))
    return env


def _sha256_file(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _result(tool_name, exit_code, body, diagnostics, error, is_error):
    structured = {"tool": tool_name, "exit_code": exit_code}
    structured.update(body)
    structured["diagnostics"] = diagnostics
    if error is not None:
        structured["error"] = error
    text = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured,
            "isError": bool(is_error)}


def call_tool(tool, arguments, config):
    try:
        argv, stdin_bytes, output_real, output_kind = build_command(
            tool, arguments, config["root"])
    except Refusal as exc:
        _log(config, "%s %s refused" % (LOG_PREFIX, tool["name"]))
        return _result(tool["name"], None, {}, [], str(exc), True)

    command = [config["python"], "-I", "-B", "-X", "utf8",
               os.path.join(SCRIPT_DIR, tool["script"])] + argv
    try:
        proc = subprocess.run(
            command, input=stdin_bytes,
            stdin=subprocess.DEVNULL if stdin_bytes is None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PACKAGE_ROOT,
            env=_child_env(), timeout=config["timeout"])
    except OSError as exc:
        _log(config, "%s %s could not start: %s" % (LOG_PREFIX, tool["name"], exc))
        return _result(tool["name"], None, {}, [], (
            "%s could not be started (%s); pass large documents as files" % (
                tool["script"], exc.strerror or exc)), True)
    except subprocess.TimeoutExpired:
        _log(config, "%s %s timeout" % (LOG_PREFIX, tool["name"]))
        body = {}
        if output_real is not None and os.path.isfile(output_real):
            size, sha = _sha256_file(output_real)
            body = {"output": {"path": output_real, "bytes": size, "sha256": sha}}
        return _result(tool["name"], None, body, [], (
            "%s timed out after %d s and was stopped" % (tool["script"], config["timeout"])),
            True)
    code = proc.returncode
    _log(config, "%s %s exit=%d" % (LOG_PREFIX, tool["name"], code))
    stdout = proc.stdout.decode("utf-8", errors="replace")
    lines = [ln for ln in proc.stderr.decode("utf-8", errors="replace").splitlines()
             if ln.strip()]
    diagnostics = lines[:MAX_DIAGNOSTIC_LINES]
    if len(lines) > MAX_DIAGNOSTIC_LINES:
        diagnostics.append("(%d more lines omitted)" % (len(lines) - MAX_DIAGNOSTIC_LINES))
    error = None
    for line in lines:
        if line.startswith("ERROR:"):
            error = line[len("ERROR:"):].strip()
            break
    if error is None and code != 0 and tool["name"] != "validate_output":
        error = "%s exited %d" % (tool["script"], code)

    body = {}
    ok = code == 0
    if output_real is not None:
        if os.path.isfile(output_real) and not os.path.islink(output_real):
            size, sha = _sha256_file(output_real)
            body = {"output": {"path": output_real, "bytes": size, "sha256": sha}}
            if code != 0:
                error = "%s; the file was still written, so a retry needs a new output_path" % (
                    error or ("%s exited %d" % (tool["script"], code)))
        elif code == 0:
            error = "%s exited 0 but wrote no file at output_path" % tool["script"]
            ok = False
    elif stdout.strip():
        size = len(proc.stdout)
        if size > config["max_inline"]:
            hint = ("lower max_errors" if not tool["writes"]
                    else "call again with output_path")
            error = ("the result is %d bytes, above the inline cap of %d bytes; %s" % (
                size, config["max_inline"], hint))
            ok = False
        elif output_kind == "csv":
            body = {"csv": stdout}
        else:
            try:
                body = {"document": json.loads(stdout)}
            except ValueError as exc:
                error = "%s printed output that is not valid JSON: %s" % (tool["script"], exc)
                ok = False
    elif code == 0:
        error = "%s exited 0 but printed nothing" % tool["script"]
        ok = False

    if tool["name"] == "validate_output" and code == 1 and "document" in body \
            and isinstance(body["document"], dict) and "valid" in body["document"]:
        # The validator's verdict is the answer, not a failed call.
        ok = True
    return _result(tool["name"], code, body, diagnostics, error, not ok)


# --------------------------------------------------------------------------
# JSON-RPC dispatch
# --------------------------------------------------------------------------


def _server_info():
    return {"name": SERVER_NAME, "title": SERVER_TITLE, "version": _common.SKILL_VERSION}


def _modern(params):
    """Return True for a modern request (versioned _meta), raising on a malformed one."""
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict) or META_VERSION not in meta:
        return False
    requested = meta.get(META_VERSION)
    if not isinstance(meta.get(META_CLIENT_CAPS), dict):
        raise RpcError(INVALID_PARAMS, "params._meta lacks %s" % META_CLIENT_CAPS)
    if requested not in MODERN_VERSIONS:
        raise RpcError(UNSUPPORTED_VERSION, "Unsupported protocol version",
                       {"supported": list(MODERN_VERSIONS), "requested": requested})
    return True


def _finish(result, modern):
    if modern:
        result["resultType"] = "complete"
        result["_meta"] = {META_SERVER_INFO: _server_info()}
    return result


def handle_request(method, params, config):
    if params is not None and not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params must be an object")
    params = params or {}
    modern = _modern(params)
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return {"protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": _server_info(), "instructions": INSTRUCTIONS}
    if method == "ping":
        return _finish({}, modern)
    if method == "server/discover":
        return {"resultType": "complete", "supportedVersions": list(MODERN_VERSIONS),
                "capabilities": {"tools": {}}, "instructions": INSTRUCTIONS,
                "ttlMs": DISCOVER_TTL_MS, "cacheScope": "public",
                "_meta": {META_SERVER_INFO: _server_info()}}
    if method == "tools/list":
        result = {"tools": tool_list()}
        if modern:
            result["ttlMs"] = TOOLS_TTL_MS
            result["cacheScope"] = "public"
        return _finish(result, modern)
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            raise RpcError(INVALID_PARAMS, "tools/call needs a string name")
        tool = _tool_by_name(name)
        if tool is None:
            raise RpcError(INVALID_PARAMS, "Unknown tool: %s" % name[:128])
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "tools/call arguments must be an object")
        return _finish(call_tool(tool, arguments, config), modern)
    raise RpcError(METHOD_NOT_FOUND, "Method not found: %s" % method[:128])


def _valid_id(value):
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _error(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _reject_constant(name):
    raise ValueError("non-standard JSON constant %s" % name)


def handle_line(raw, config):
    """Return the response object for one input line, or None when none is owed."""
    try:
        message = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        # RecursionError: nesting deeper than the interpreter's limit.
        return _error(None, PARSE_ERROR, "Parse error: %s" % exc)
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "Invalid Request: a message must be one JSON "
                                             "object (batches are not supported)")
    if "method" not in message and ("result" in message or "error" in message):
        return None  # a stray response (any id, even null); this server sends no requests
    has_id = "id" in message
    request_id = message.get("id")
    if has_id and not _valid_id(request_id):
        return _error(None, INVALID_REQUEST, "Invalid Request: id must be a string or an "
                                             "integer")
    reply_id = request_id if has_id else None
    if message.get("jsonrpc") != "2.0":
        return _error(reply_id, INVALID_REQUEST, "Invalid Request: jsonrpc must be \"2.0\"")
    method = message.get("method")
    if method is None:
        return _error(reply_id, INVALID_REQUEST, "Invalid Request: no method")
    if not isinstance(method, str):
        return _error(reply_id, INVALID_REQUEST, "Invalid Request: method must be a string")
    if not has_id:
        return None  # a notification is never answered
    try:
        result = handle_request(method, message.get("params"), config)
    except RpcError as exc:
        return _error(request_id, exc.code, exc.message, exc.data)
    except Exception as exc:  # the server must answer, never die mid-stream
        _log(config, "%s internal error: %s: %s" % (LOG_PREFIX, type(exc).__name__, exc),
             force=True)
        return _error(request_id, INTERNAL_ERROR, "Internal error")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _encode_frame(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        # An echoed string holds a lone surrogate; \u escapes keep the frame valid JSON.
        return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _write_frame(obj):
    sys.stdout.buffer.write(_encode_frame(obj) + b"\n")
    sys.stdout.buffer.flush()


def _log(config, line, force=False):
    """Best-effort stderr line: a closed or broken stderr never stops the server."""
    if force or not config.get("quiet"):
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except (AttributeError, OSError, ValueError):
            pass


def _read_frame(stream):
    """Return (bytes, oversized) for the next line, or (None, False) at end of file."""
    line = stream.readline(MAX_FRAME_BYTES + 1)
    if not line:
        return None, False
    if len(line) > MAX_FRAME_BYTES and not line.endswith(b"\n"):
        while True:
            rest = stream.readline(65536)
            if not rest or rest.endswith(b"\n"):
                break
        return b"", True
    return line, False


def serve(config):
    stream = sys.stdin.buffer
    while True:
        raw, oversized = _read_frame(stream)
        if raw is None:
            return 0
        if oversized:
            _write_frame(_error(None, INVALID_REQUEST, "Invalid Request: a message is larger "
                                                       "than %d bytes" % MAX_FRAME_BYTES))
            continue
        if not raw.strip():
            continue
        try:
            response = handle_line(raw.strip(), config)
        except Exception as exc:  # no single frame may end the process
            _log(config, "%s internal error: %s" % (LOG_PREFIX, type(exc).__name__),
                 force=True)
            response = _error(None, INTERNAL_ERROR, "Internal error")
        if response is not None:
            _write_frame(response)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Serve this package's deterministic scripts as tools over stdio JSON-RPC. It "
            "sends nothing, fetches nothing and writes only new files inside --root. "
            "Korean gloss: 패키지 스크립트를 stdio 도구로 제공한다."
        ),
    )
    parser.add_argument("--root", default=None, metavar="DIR",
                        help="Required. The one directory tools may read from and write to")
    parser.add_argument("--tool-timeout", dest="tool_timeout", type=int,
                        default=DEFAULT_TOOL_TIMEOUT, metavar="SECONDS",
                        help="Stop a script after this many seconds (1-3600, default %d)"
                        % DEFAULT_TOOL_TIMEOUT)
    parser.add_argument("--max-inline-bytes", dest="max_inline", type=int,
                        default=DEFAULT_MAX_INLINE_BYTES, metavar="N",
                        help="Largest result returned inline; above it the call must use "
                        "output_path (%d-%d, default %d)"
                        % (MIN_INLINE_BYTES, MAX_FRAME_BYTES, DEFAULT_MAX_INLINE_BYTES))
    parser.add_argument("--quiet", action="store_true", help="No per-call stderr log line")
    parser.add_argument("--version", action="store_true", help="Print versions and exit")
    return parser


def _check_root(raw):
    if raw is None or not raw.strip():
        raise _common.UsageError("--root DIR is required: the one directory tools may read "
                                 "and write")
    if "\0" in raw:
        raise _common.UsageError("--root contains a NUL byte")
    root = os.path.realpath(os.path.abspath(raw))
    if not os.path.isdir(root):
        raise _common.UsageError("--root %s is not an existing directory" % raw)
    if root == os.path.dirname(root):
        raise _common.UsageError("--root %s is a filesystem root; pass a project directory"
                                 % raw)
    home = os.path.expanduser("~")
    home = os.path.realpath(home) if os.path.isabs(home) else None
    if home is not None and _inside(home, root):
        raise _common.UsageError("--root %s is the home directory or one of its parents; pass "
                                 "a project directory" % raw)
    return root


def main(argv):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        config_doc = _common.load_config(None)
    except Exception as exc:
        return _common.die(str(exc), 2)
    if args.version:
        sys.stdout.write("%s skill_version=%s schema_version=%s score_version=%s\n" % (
            SCRIPT_NAME, _common.SKILL_VERSION, _common.SCHEMA_VERSION,
            config_doc.get("score_version", "unknown")))
        return 0
    try:
        root = _check_root(args.root)
        if not 1 <= args.tool_timeout <= 3600:
            raise _common.UsageError("--tool-timeout must be 1-3600 seconds")
        if not MIN_INLINE_BYTES <= args.max_inline <= MAX_FRAME_BYTES:
            raise _common.UsageError("--max-inline-bytes must be %d-%d"
                                     % (MIN_INLINE_BYTES, MAX_FRAME_BYTES))
        if not sys.executable:
            raise _common.UsageError("cannot locate the Python interpreter to run the scripts")
    except _common.UsageError as exc:
        return _common.die(str(exc), 2)
    for tool in TOOLS:
        if not _TOOL_NAME.match(tool["name"]):
            return _common.die("tool name %r is not a valid tool name" % tool["name"], 2)
    config = {"root": root, "timeout": args.tool_timeout, "max_inline": args.max_inline,
              "quiet": args.quiet, "python": sys.executable}
    try:
        return serve(config)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # The client went away; point stdout at devnull so the interpreter's final flush
        # does not raise a second time.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
