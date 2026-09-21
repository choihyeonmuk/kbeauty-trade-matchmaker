#!/usr/bin/env python3
"""Derive canonical_domain / normalized_name / alias_domains on buyer or seller records.

CLI contract: docs/BUILD-CONTRACT.md 7.6. Normalisation rules: 8.1 (canonical domain),
8.3 (company name), 8.7 (country), 3.3 R3.3.1 (unit precedence).

The script is pure and idempotent: running it twice produces byte-identical output.
It performs no network access, no filesystem write outside --output, no scoring and
no field invention. A record whose website yields no registrable domain keeps every
other field and gets `canonical_domain = "unknown"` plus a note; it is never dropped.

Two deliberate local decisions, both noted here as the BUILD-CONTRACT 1.4 SHOULD rule
requires:
  * `--entity auto` resolves in the order seller_id -> buyer_id -> country == "KR"
    (seller) -> buyer, which is BUILD-CONTRACT 7.6's "inferred from buyer_id/seller_id/
    country" made deterministic.
  * R3.3.1's unit copy is applied to `moq` -> `moq_unit` and `buyer_moq` ->
    `buyer_moq_unit` only. `lead_time_days` and `monthly_capacity_units` have no
    sibling unit field in seller.schema.json (which sets additionalProperties:false),
    so there is nothing to copy into; their range `unit` is left untouched and is
    never read by a scoring predicate.

All code comments are English by contract (BUILD-CONTRACT 1.5).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common  # noqa: E402

#: Fields whose range `unit` is copied into a sibling scalar unit field (R3.3.1).
#: buyer_moq / buyer_moq_unit mirrors seller moq / moq_unit exactly, as
#: buyer.schema.json buyer_moq states; a record of the other entity simply has
#: neither field and the pass is a no-op for it.
_UNIT_SIBLINGS = (("moq", "moq_unit"), ("buyer_moq", "buyer_moq_unit"))

_WEBSITE_RE = re.compile(r"^https?://[^\s]+$")
_DEFAULT_PORTS = {"http": "80", "https": "443"}
_HOST_RE = re.compile(r"^[^\s/:@?#]+$")

#: Schema cap on notes[] (buyer.schema.json / seller.schema.json).
_MAX_NOTES = 50
#: Schema cap on alias_domains[].
_MAX_ALIAS_DOMAINS = 50


def resolve_entity(record, requested="auto"):
    """Resolve the entity kind for one record (BUILD-CONTRACT 7.6)."""
    if requested in ("buyer", "seller"):
        return requested
    if isinstance(record, dict):
        if "seller_id" in record:
            return "seller"
        if "buyer_id" in record:
            return "buyer"
        if record.get("country") == "KR":
            return "seller"
    return "buyer"


def clean_website(value):
    """Return (canonical absolute URL, [host forms seen]).

    The scheme is kept, credentials and a default port are stripped, the host is
    lower-cased and punycoded, and a trailing root dot is removed. Path, query and
    fragment survive untouched. Returns (None, []) when the value cannot be turned
    into an http(s) URL, in which case the caller leaves the original in place.
    """
    if not isinstance(value, str) or not value.strip():
        return None, []
    text = unicodedata.normalize("NFKC", value.strip())
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        scheme, netloc, path, query, fragment = _common.split_url(text)
    except Exception:
        return None, []
    if "@" in netloc:  # strip credentials
        netloc = netloc.rsplit("@", 1)[1]
    if netloc.startswith("["):  # bracketed IPv6 literal: not a company website
        return None, []
    port = ""
    if ":" in netloc:
        netloc, port = netloc.split(":", 1)
    host = netloc.strip().lower()
    while host.endswith("."):
        host = host[:-1]
    if not host:
        return None, []
    ascii_host = _common._punycode(host)
    hosts_seen = [h for h in (host, ascii_host) if _is_plausible_host(h)]
    hosts_seen = list(dict.fromkeys(hosts_seen))
    if scheme not in ("http", "https"):
        # Not a website URL, but the host form is still worth recording as an alias.
        return None, hosts_seen
    authority = ascii_host
    if port and port.isdigit() and port != _DEFAULT_PORTS.get(scheme):
        authority = "%s:%s" % (ascii_host, port)
    url = _common.join_url(scheme, authority, path, query, fragment)
    if not _WEBSITE_RE.match(url):
        return None, hosts_seen
    return url, hosts_seen


def site_root(url):
    """Return (site-root URL, True) when `url` carries more than a bare "/" path.

    `website` is "Primary official website URL" (buyer.schema.json / seller.schema.json)
    and is rendered verbatim as the candidate's `Website:` line (references/
    output-format.md 10.5, PRD 15.1 criterion 2). A candidate first surfaced on a deep
    page would otherwise publish that product or collection page as the company's
    website. The HOST is kept exactly as it is - only the path, query and fragment are
    dropped - because folding `shop.acme.example` to `acme.example` would publish a URL
    that may not resolve at all, which is a worse failure than a deep path. The deep URL
    is not lost: it stays on the evidence item that carried it, and the note this
    trimming leaves records it verbatim.

    A URL whose host yields no registrable domain is left exactly as it is. That is
    the shape of a platform that identifies its tenants by PATH rather than by
    subdomain (`smartstore.naver.com/<tenant>`): there the path is the only thing that
    names the company, and trimming it would publish the platform's own front page.
    """
    try:
        if _common.canonical_domain(url) is None:
            return url, False
        scheme, netloc, path, query, fragment = _common.split_url(url)
    except Exception:
        return url, False
    if path in ("", "/") and not query and not fragment:
        return url, False
    return _common.join_url(scheme, netloc, "/", "", ""), True


def _is_plausible_host(host):
    """True for a dotted, whitespace-free host that is not an IPv4 literal."""
    if not isinstance(host, str) or not host or len(host) > 253:
        return False
    if "." not in host or _HOST_RE.match(host) is None:
        return False
    return not all(label.isdigit() for label in host.split("."))


def _normalize_country(value):
    """Return the alpha-2 code, the literal "unknown", or None when unconvertible.

    Country names and aliases ("United Kingdom", "UK", "영국") resolve through the static
    ISO-3166 table in _common (BUILD-CONTRACT 8.7); only a value no row matches is None.
    """
    return _common.normalize_country(value)


def _add_note(notes, text):
    """Append a note once; repeated runs must not grow the list (idempotency)."""
    if text not in notes:
        notes.append(text)


def normalize_record(record, entity="auto"):
    """Normalize one buyer or seller record. Returns a NEW dict; never mutates input."""
    if not isinstance(record, dict):
        raise _common.DataError("record is not a JSON object: %r" % (record,))

    out = dict(record)  # preserves the input key order for byte-stable output
    notes = [note for note in (out.get("notes") or []) if isinstance(note, str)]
    kind = resolve_entity(out, entity)

    # --- company name -------------------------------------------------------
    company_name = out.get("company_name")
    if isinstance(company_name, str) and company_name.strip():
        out["normalized_name"] = _common.normalize_company_name(company_name)
    else:
        _add_note(notes, "company_name is absent or empty; normalized_name not derived")

    # --- website ------------------------------------------------------------
    hosts_seen = []
    raw_website = out.get("website")
    if isinstance(raw_website, str) and raw_website.strip():
        cleaned, hosts_seen = clean_website(raw_website)
        if cleaned:
            rooted, trimmed = site_root(cleaned)
            out["website"] = rooted
            if trimmed:
                _add_note(
                    notes,
                    'website "%s" trimmed to the site root "%s" so the rendered '
                    "Website: line is the company site, not the page it was found on"
                    % (cleaned, rooted),
                )
        else:
            _add_note(
                notes,
                'website "%s" is not a usable http(s) URL; left unchanged' % raw_website.strip(),
            )

    # --- canonical domain ---------------------------------------------------
    domain = _common.canonical_domain(out.get("website"))
    if domain is None:
        existing = record.get("canonical_domain")
        if isinstance(existing, str) and not _common.is_unknown(existing):
            domain = _common.canonical_domain(existing)
            if domain:
                hosts_seen.append(existing.strip().lower())
    if domain is None:
        out["canonical_domain"] = _common.UNKNOWN
        website = out.get("website")
        if isinstance(website, str) and website.strip():
            _add_note(
                notes,
                'website "%s" yields no registrable domain; canonical_domain set to unknown'
                % website.strip(),
            )
        else:
            _add_note(notes, "no website URL on the record; canonical_domain set to unknown")
    else:
        out["canonical_domain"] = domain

    # --- alias domains ------------------------------------------------------
    aliases = []
    for alias in out.get("alias_domains") or []:
        if isinstance(alias, str) and alias.strip():
            aliases.append(alias.strip())
    for host in hosts_seen:
        if host and host not in aliases:
            aliases.append(host)
    aliases = sorted({alias for alias in aliases if alias != out.get("canonical_domain")})
    if aliases:
        if len(aliases) > _MAX_ALIAS_DOMAINS:
            _add_note(
                notes,
                "alias_domains truncated to the schema maximum of %d entries" % _MAX_ALIAS_DOMAINS,
            )
            aliases = aliases[:_MAX_ALIAS_DOMAINS]
        out["alias_domains"] = aliases
    elif "alias_domains" in out:
        out["alias_domains"] = []

    # --- country (BUILD-CONTRACT 8.7: ISO-3166-1 alpha-2, upper case) --------
    if "country" in out:
        country = _normalize_country(out.get("country"))
        if country is None:
            _add_note(
                notes,
                'country "%s" is not an ISO-3166-1 alpha-2 code; set to unknown '
                "(operator confirmation required)" % out.get("country"),
            )
            out["country"] = _common.UNKNOWN
        else:
            raw_country = out.get("country")
            if (
                country != _common.UNKNOWN
                and isinstance(raw_country, str)
                and raw_country.strip().upper() != country
            ):
                _add_note(
                    notes,
                    'country "%s" normalised to the ISO-3166-1 alpha-2 code %s (BUILD-CONTRACT 8.7)'
                    % (raw_country.strip(), country),
                )
                # buyer.schema.json carries country_name for display; keep the name that
                # was given rather than discarding it (seller.schema.json has no such field).
                if kind == "buyer" and "country_name" not in out and len(raw_country.strip()) > 2:
                    out["country_name"] = raw_country.strip()[:100]
            out["country"] = country

    # --- unit precedence (R3.3.1) ------------------------------------------
    for field, unit_field in _UNIT_SIBLINGS:
        value = out.get(field)
        if not isinstance(value, dict):
            continue
        range_unit = value.get("unit")
        if not isinstance(range_unit, str) or not range_unit.strip():
            continue
        range_unit = range_unit.strip()
        sibling = out.get(unit_field)
        if _common.is_unknown(sibling):
            out[unit_field] = range_unit
        elif isinstance(sibling, str) and sibling.strip() != range_unit:
            _add_note(
                notes,
                '%s "%s" wins over %s.unit "%s" (BUILD-CONTRACT 3.3 R3.3.1)'
                % (unit_field, sibling.strip(), field, range_unit),
            )

    # --- notes --------------------------------------------------------------
    out["notes"] = notes[:_MAX_NOTES]
    return out, kind


def build_parser():
    parser = argparse.ArgumentParser(
        prog="normalize_company.py",
        description="Derive canonical_domain, normalized_name and alias_domains on "
        "buyer or seller records (BUILD-CONTRACT 7.6).",
    )
    parser.add_argument("-i", "--input", default=None, help='input JSON path ("-" = stdin)')
    parser.add_argument("-o", "--output", default=None, help="output JSON path (default stdout)")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    parser.add_argument("--as-of", dest="as_of", default=None, help="run date, YYYY-MM-DD")
    parser.add_argument("--config", default=None, help="alternative scoring.config.json")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None, help="alternative schemas/")
    parser.add_argument(
        "--no-validate", dest="validate", action="store_false", help="skip output self-validation"
    )
    parser.add_argument("--quiet", action="store_true", help="suppress informational stderr output")
    parser.add_argument("--version", action="store_true", help="print versions and exit")
    parser.add_argument(
        "--entity", choices=("buyer", "seller", "auto"), default="auto", help="record kind"
    )
    parser.add_argument("--name", default=None, help="inline mode: company name")
    parser.add_argument("--url", default=None, help="inline mode: company website")
    parser.add_argument(
        "--envelope", action="store_true", help='wrap the output array in {"records": [...]}'
    )
    parser.set_defaults(validate=True)
    return parser


def _print_version(args):
    config = _common.load_config(args.config)
    sys.stdout.write(
        "normalize_company.py skill_version=%s schema_version=%s score_version=%s\n"
        % (_common.SKILL_VERSION, _common.SCHEMA_VERSION, config.get("score_version", "unknown"))
    )
    return 0


def _inline_record(args):
    """--name / --url single-record mode (BUILD-CONTRACT 7.6)."""
    name = args.name or ""
    website, _hosts = clean_website(args.url) if args.url else (None, [])
    if args.url and website is None:
        website = args.url.strip()
    domain = _common.canonical_domain(website)
    return {
        "company_name": name,
        "normalized_name": _common.normalize_company_name(name),
        "website": website if website else _common.UNKNOWN,
        "canonical_domain": domain if domain else _common.UNKNOWN,
    }


def _self_validate(records, kinds, args):
    """Warn (never fail) when a complete record does not satisfy its schema."""
    if not args.validate:
        return
    for index, record in enumerate(records):
        if not isinstance(record, dict) or "schema_version" not in record:
            continue  # a thin raw candidate is not yet a schema document
        try:
            schema = _common.load_schema(kinds[index], args.schema_dir)
        except _common.UsageError as exc:
            _common.eprint("WARN: %s" % exc, quiet=args.quiet)
            return
        for message in _common.validate(record, schema):
            _common.eprint(
                "WARN: record %d (%s): %s"
                % (index, record.get("buyer_id") or record.get("seller_id") or "?", message),
                quiet=args.quiet,
            )


def _emit(payload, args):
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            _common.write_output(payload, args.pretty, handle)
    else:
        _common.write_output(payload, args.pretty)


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.version:
            return _print_version(args)
        if args.as_of:
            # 7.3: an --as-of that is not a real YYYY-MM-DD date is a usage error; it
            # would otherwise ride into the envelope unchecked.
            _common.resolve_as_of([], args.as_of)

        if args.name is not None or args.url is not None:
            _emit(_inline_record(args), args)
            return 0

        data = _common.read_input(args)
        envelope = {}
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            envelope = data
            raw_records, shape = data["records"], "array"
        elif isinstance(data, list):
            raw_records, shape = data, "array"
        elif isinstance(data, dict):
            raw_records, shape = [data], "object"
        else:
            raise _common.DataError("input must be a record object, an array or an envelope")

        records = []
        kinds = []
        for record in raw_records:
            normalized, kind = normalize_record(record, args.entity)
            records.append(normalized)
            kinds.append(kind)

        _self_validate(records, kinds, args)

        if args.envelope:
            # BUILD-CONTRACT 7.6 forces the {"records": [...]} shape; an input
            # envelope's as_of / entity ride along so that the 7.4 as-of resolution
            # chain survives a `normalize | dedupe` pipe.
            payload = {"records": records}
            as_of = args.as_of or envelope.get("as_of")
            if as_of:
                payload["as_of"] = as_of
            if envelope.get("entity") in ("buyer", "seller"):
                payload["entity"] = envelope["entity"]
            elif args.entity in ("buyer", "seller"):
                payload["entity"] = args.entity
            _emit(payload, args)
        elif shape == "object":
            _emit(records[0], args)
        else:
            _emit(records, args)
        _common.eprint("normalized %d record(s)" % len(records), quiet=args.quiet)
        return 0
    except _common.UsageError as exc:
        return _common.die(str(exc), 2)
    except _common.ConfigError as exc:
        return _common.die(str(exc), 2)
    except _common.DataError as exc:
        return _common.die(str(exc), 1)
    except (IOError, OSError) as exc:
        return _common.die(str(exc), 2)
    except Exception as exc:  # never let a raw traceback escape main (BUILD-CONTRACT 7.5)
        return _common.die("%s: %s" % (type(exc).__name__, exc), 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
