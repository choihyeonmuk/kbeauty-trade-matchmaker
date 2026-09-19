#!/usr/bin/env python3
"""Build the two release archives of kbeauty-trade-matchmaker, deterministically.

  kbeauty-trade-matchmaker.zip         the skill folder for claude.ai upload: one top
                                       directory `kbeauty-trade-matchmaker/`, with an
                                       explicit entry for every directory.
  kbeauty-trade-matchmaker-plugin.zip  the skills-only portable plugin for ChatGPT and
                                       Codex: `plugin.json` (packaging/openai/plugin.json
                                       verbatim) plus `skills/kbeauty-trade-matchmaker/`.

Korean gloss: 릴리스 ZIP 두 개(claude.ai 스킬 ZIP, ChatGPT/Codex 플러그인 ZIP)를 결정적으로 만든다.

Every archived byte is read from the HEAD commit (`git ls-tree` + `git cat-file`), not
the working tree, and the build refuses while the package or either manifest has
uncommitted changes or untracked files. So gitignored local state (operator review
sheets, adapter data, agent caches) and edited-but-uncommitted content can never reach a
public asset. Both archives carry exactly the same package files. Every entry gets a
fixed timestamp (1980-01-01 00:00:00, or midnight of --as-of), a fixed permission table
(install.sh 0755, other files 0644, directories 0755) and a sorted position, so the same
commit gives the same bytes with the same zlib. No clock is read and no network is used.

Usage (from the repository root):

    python3 tools/build_release.py --list            # entry lists, writes nothing
    python3 tools/build_release.py --out dist/       # writes both ZIPs into dist/

Exit codes (BUILD-CONTRACT 7.3): 0 ok; 1 the checkout cannot be released as is
(uncommitted or untracked package files, a symlink, a version disagreement, a plugin
manifest inside the package, ...); 2 usage error, or a manifest that is missing from
HEAD, unreadable or not valid JSON. Every non-zero exit prints exactly one `ERROR:` line
and nothing on stdout; both archives are checked before either target is replaced.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

SCRIPT_NAME = "build_release.py"
PACKAGE = "kbeauty-trade-matchmaker"
SKILL_ZIP = "kbeauty-trade-matchmaker.zip"
PLUGIN_ZIP = "kbeauty-trade-matchmaker-plugin.zip"

# The only package file shipped executable. Modes never come from the filesystem, so a
# checkout that lost the execute bit (core.fileMode=false, a Windows clone) still gives
# the same archive bytes.
EXECUTABLE_FILES = frozenset(["install.sh"])
FILE_MODE = 0o100644
EXEC_MODE = 0o100755
DIR_MODE = 0o40755
MSDOS_DIRECTORY_FLAG = 0x10
UNIX_SYSTEM = 3
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# A plugin manifest or component directory at the package root would change how a
# plugin host discovers the skill and would leak into the claude.ai skill ZIP.
FORBIDDEN_AT_PACKAGE_ROOT = (".claude-plugin", ".codex-plugin", ".agent-plugin", "plugin.json",
                             ".mcp.json", "mcp.json", ".app.json", "skills", "agents",
                             "commands", "hooks", "bin", "workflows", "output-styles",
                             "monitors")

_VERSION_RE = re.compile(r'^SKILL_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"', re.MULTILINE)
_DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")


class UsageError(Exception):
    """Bad invocation: exit 2."""


class ReleaseError(Exception):
    """The checkout cannot be released as is: exit 1."""


class InputError(Exception):
    """A required input file is missing, unreadable or not valid JSON: exit 2 (BC 7.3)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def _paths():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return {
        "repo": repo,
        "package": os.path.join(repo, PACKAGE),
        "common": os.path.join(repo, PACKAGE, "scripts", "_common.py"),
    }


# Repository paths (POSIX, as git names them) of the inputs read from HEAD.
MARKETPLACE_PATH = ".claude-plugin/marketplace.json"
OPENAI_PATH = "packaging/openai/plugin.json"
COMMON_PATH = PACKAGE + "/scripts/_common.py"
# Paths whose uncommitted edits would make an archive differ from what the maintainer sees.
CLEAN_PATHS = (PACKAGE, ".claude-plugin", "packaging")


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _version_in(text, where):
    found = _VERSION_RE.search(text)
    if not found:
        raise ReleaseError("no SKILL_VERSION line in %s" % where)
    return found.group(1)


def _skill_version_on_disk(paths):
    """--version only: the working-tree value, readable without git."""
    try:
        text = _read_bytes(paths["common"]).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError("cannot read %s: %s" % (paths["common"], exc))
    return _version_in(text, paths["common"])


def _decode_json(name, data):
    if data is None:
        raise InputError("%s is not in the HEAD commit" % name)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputError("%s is not valid UTF-8 JSON: %s" % (name, exc))


def _check_versions(skill_version, marketplace, openai):
    plugins = marketplace.get("plugins") if isinstance(marketplace, dict) else None
    entry = plugins[0] if isinstance(plugins, list) and plugins and isinstance(plugins[0], dict) \
        else {}
    stated = {
        "scripts/_common.py SKILL_VERSION": skill_version,
        MARKETPLACE_PATH + " plugins[0].version": entry.get("version"),
        OPENAI_PATH + " version": openai.get("version") if isinstance(openai, dict) else None,
    }
    for label, value in sorted(stated.items()):
        if not isinstance(value, str):
            raise ReleaseError("%s must be a version string, got %s"
                               % (label, json.dumps(value)))
    if len(set(stated.values())) != 1:
        raise ReleaseError("skill_version disagrees: %s" % "; ".join(
            "%s=%s" % (k, v) for k, v in sorted(stated.items())))
    if not isinstance(openai, dict) or openai.get("name") != PACKAGE:
        raise ReleaseError("%s name must be %r" % (OPENAI_PATH, PACKAGE))


def _git(repo, args, stdin=None):
    try:
        proc = subprocess.run(["git", "-C", repo] + list(args), input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ReleaseError("git is not available: %s" % exc)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ReleaseError("git %s failed: %s" % (args[0], detail[0] if detail else
                                                  "exit %d" % proc.returncode))
    return proc.stdout


def _cat_blobs(repo, names):
    """Contents of each git object name (a blob sha or HEAD:<path>); None when absent."""
    if not names:
        return []
    if any("\n" in name for name in names):
        raise ReleaseError("object name with a newline")
    raw = _git(repo, ["cat-file", "--batch"],
               stdin=("\n".join(names) + "\n").encode("utf-8"))
    out, pos = [], 0
    for name in names:
        end = raw.index(b"\n", pos)
        header = raw[pos:end].decode("utf-8", "replace").split(" ")
        pos = end + 1
        if header[-1] == "missing" or len(header) != 3:
            out.append(None)
            continue
        size = int(header[2])
        if header[1] != "blob":
            raise ReleaseError("%s is a %s, not a file" % (name, header[1]))
        out.append(raw[pos:pos + size])
        pos += size + 1
    return out


def _check_checkout(paths):
    """Refuse a checkout whose working tree differs from HEAD where the archives look."""
    repo = paths["repo"]
    toplevel = _git(repo, ["rev-parse", "--show-toplevel"]).decode("utf-8").strip()
    if os.path.realpath(toplevel) != os.path.realpath(repo):
        raise ReleaseError("%s is not the root of a git checkout" % repo)
    untracked = [p for p in _git(repo, ["ls-files", "-z", "--others", "--exclude-standard",
                                        "--"] + list(CLEAN_PATHS)).decode("utf-8").split("\0")
                 if p]
    if untracked:
        raise ReleaseError("untracked, unignored files (commit or remove them first): %s"
                           % ", ".join(sorted(untracked)[:10]))
    records = _git(repo, ["status", "--porcelain", "-z", "--untracked-files=no", "--"]
                   + list(CLEAN_PATHS)).decode("utf-8").split("\0")
    dirty, index = [], 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) > 3:
            dirty.append(record[3:])
            if record[0] in "RC":  # a rename or copy is followed by its source path
                index += 1
    if dirty:
        raise ReleaseError("uncommitted changes (commit or discard them first): %s"
                           % ", ".join(sorted(dirty)[:10]))


def _package_tree(paths):
    """Sorted (package-relative POSIX path, blob sha) of every package file in HEAD."""
    prefix = PACKAGE + "/"
    files = []
    for record in _git(paths["repo"], ["ls-tree", "-r", "-z", "--full-tree", "HEAD", "--",
                                       PACKAGE]).decode("utf-8").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, kind, sha = meta.split(" ")
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if mode == "120000":
            raise ReleaseError("symlink tracked in the package: %s" % path)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ReleaseError("unsupported git entry (mode %s) in the package: %s"
                               % (mode, path))
        parts = rel.split("/")
        if not rel or "\\" in rel or any(p in ("", ".", "..") for p in parts):
            raise ReleaseError("unsafe path in the package: %r" % path)
        files.append((rel, sha))
    names = set(rel for rel, _sha in files)
    if "SKILL.md" not in names:
        raise ReleaseError("SKILL.md is not committed at the package root")
    roots = set(rel.split("/", 1)[0] for rel in names)
    on_disk = set(os.listdir(paths["package"])) if os.path.isdir(paths["package"]) else set()
    clash = sorted(n for n in FORBIDDEN_AT_PACKAGE_ROOT if n in roots or n in on_disk)
    if clash:
        raise ReleaseError("plugin manifest or component path at the package root: %s"
                           % ", ".join(clash))
    return sorted(files, key=lambda item: item[0].encode("utf-8"))


def _entries(prefix, files):
    """(archive name, package-relative path or None for a directory), sorted, dirs first."""
    out, seen = [], set()
    for rel in files:
        parts = rel.split("/")
        for depth in range(len(parts) - 1):
            directory = prefix + "/".join(parts[:depth + 1]) + "/"
            if directory not in seen:
                seen.add(directory)
                out.append((directory, None))
        out.append((prefix + rel, rel))
    return out


def _plan(files):
    skill = [(PACKAGE + "/", None)] + _entries(PACKAGE + "/", files)
    plugin_prefix = "skills/%s/" % PACKAGE
    plugin = [("plugin.json", "\0openai"), ("skills/", None), (plugin_prefix, None)] \
        + _entries(plugin_prefix, files)
    return [(SKILL_ZIP, skill), (PLUGIN_ZIP, plugin)]


def _zip_bytes(contents, entries, date_time):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, rel in entries:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.create_system = UNIX_SYSTEM
            if rel is None:
                info.external_attr = (DIR_MODE << 16) | MSDOS_DIRECTORY_FLAG
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, b"")
                continue
            mode = EXEC_MODE if rel in EXECUTABLE_FILES else FILE_MODE
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents[rel], compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)
    return buffer.getvalue()


def _check_targets(directory, names):
    for name in names:
        target = os.path.join(directory, name)
        if os.path.lexists(target) and not os.path.islink(target) \
                and not os.path.isfile(target):
            raise ReleaseError("%s exists and is not a regular file" % target)


def _write_all(directory, blobs):
    """Write every archive to a fresh temp file, then rename each over its target.

    Nothing is renamed until every temp file is complete, so a failure leaves the old
    pair in place. os.replace swaps the directory entry itself, so a pre-existing
    symlink at a target is replaced and whatever it pointed at is never written.
    """
    temps = []
    try:
        for name, data in blobs:
            handle, temp = tempfile.mkstemp(prefix=".kbtm-build-", suffix=".zip",
                                            dir=directory)
            temps.append(temp)
            with os.fdopen(handle, "wb") as fh:
                fh.write(data)
            os.chmod(temp, 0o644)
        for (name, _data), temp in zip(blobs, temps):
            os.replace(temp, os.path.join(directory, name))
    finally:
        for temp in temps:
            if os.path.lexists(temp):
                os.unlink(temp)


def _date_time(as_of):
    if as_of is None:
        return FIXED_DATE_TIME
    found = _DATE_RE.match(as_of)
    if not found:
        raise UsageError("--as-of must be YYYY-MM-DD, got %r" % as_of)
    year, month, day = (int(g) for g in found.groups())
    try:
        datetime.date(year, month, day)
    except ValueError:
        raise UsageError("--as-of %r is not a calendar date" % as_of)
    if not 1980 <= year <= 2107:
        raise UsageError("--as-of %r is outside the ZIP date range 1980-2107" % as_of)
    return (year, month, day, 0, 0, 0)


def _emit(document, pretty):
    if pretty:
        text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    sys.stdout.write(text + "\n")


def _run(args):
    paths = _paths()
    date_time = _date_time(args.as_of)
    out_dir = None
    if args.out is not None:
        out_dir = os.path.realpath(args.out)
        if not os.path.isdir(out_dir):
            raise UsageError("--out %s is not an existing directory" % args.out)
        package = os.path.realpath(paths["package"])
        if out_dir == package or out_dir.startswith(package + os.sep):
            raise UsageError("--out must be outside the package folder %s" % package)

    _check_checkout(paths)
    tree = _package_tree(paths)
    common, market_raw, openai_raw = _cat_blobs(
        paths["repo"], ["HEAD:" + COMMON_PATH, "HEAD:" + MARKETPLACE_PATH, "HEAD:" + OPENAI_PATH])
    if common is None:
        raise InputError("%s is not in the HEAD commit" % COMMON_PATH)
    try:
        skill_version = _version_in(common.decode("utf-8"), COMMON_PATH)
    except UnicodeDecodeError as exc:
        raise InputError("%s is not UTF-8: %s" % (COMMON_PATH, exc))
    _check_versions(skill_version, _decode_json(MARKETPLACE_PATH, market_raw),
                    _decode_json(OPENAI_PATH, openai_raw))
    files = [rel for rel, _sha in tree]
    plan = _plan(files)
    stamp = "%04d-%02d-%02dT%02d:%02d:%02d" % date_time

    if out_dir is None:
        _emit({"skill_version": skill_version, "timestamp": stamp,
               "archives": [{"file": name, "entries": [n for n, _r in entries]}
                            for name, entries in plan]}, args.pretty)
        return 0

    _check_targets(out_dir, [name for name, _entries_ in plan])
    contents = dict(zip(files, _cat_blobs(paths["repo"], [sha for _rel, sha in tree])))
    contents["\0openai"] = openai_raw
    built = [(name, len(entries), _zip_bytes(contents, entries, date_time))
             for name, entries in plan]
    _write_all(out_dir, [(name, data) for name, _count, data in built])
    archives = []
    for name, count, data in built:
        archives.append({"file": name, "entries": count, "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
        if not args.quiet:
            sys.stderr.write("%s: wrote %s (%d entries, %d bytes)\n"
                             % (SCRIPT_NAME, os.path.join(out_dir, name), count, len(data)))
    _emit({"skill_version": skill_version, "timestamp": stamp, "archives": archives},
          args.pretty)
    return 0


def main(argv):
    parser = _Parser(prog=SCRIPT_NAME, description="Build the skill ZIP and the plugin ZIP.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--out", metavar="DIR", help="existing directory to write both ZIPs into")
    mode.add_argument("--list", action="store_true", help="print the entry lists; write nothing")
    parser.add_argument("--as-of", dest="as_of", metavar="YYYY-MM-DD",
                        help="entry timestamp date (default 1980-01-01)")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON on stdout")
    parser.add_argument("--quiet", action="store_true", help="no diagnostics on stderr")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    try:
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:  # --help
            return int(exc.code or 0)
        if args.version:
            sys.stdout.write("%s skill_version=%s\n"
                             % (SCRIPT_NAME, _skill_version_on_disk(_paths())))
            return 0
        if args.out is None and not args.list:
            raise UsageError("one of --out DIR or --list is required")
        return _run(args)
    except (UsageError, InputError) as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 2
    except ReleaseError as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 1
    except Exception as exc:  # any other failure is still one clean ERROR line
        sys.stderr.write("ERROR: %s: %s\n" % (type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
