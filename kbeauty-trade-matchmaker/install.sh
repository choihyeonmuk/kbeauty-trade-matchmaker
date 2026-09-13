#!/bin/sh
# install.sh - install the kbeauty-trade-matchmaker Agent Skill into a runtime skills directory.
#
# Korean gloss: 이 스킬 폴더를 런타임의 skills 디렉터리에 설치한다.
#               기본은 symlink(원본을 고치면 즉시 반영), --copy 는 독립 사본.
#
# POSIX sh only. No network, no sudo, no package manager, no writes outside the chosen target.
# Every action is a function; nothing runs until main() is called at the very bottom, so this
# file can be read or sourced without side effects.
#
# Usage:
#   sh install.sh [--runtime claude|codex] [--copy] [--force] [--project DIR] [--verify] [--dry-run]
#
# Exit codes: 0 success, 1 install blocked or verification failed, 2 usage error.

set -eu

KBTM_SKILL_NAME='kbeauty-trade-matchmaker'
KBTM_MANIFEST='.kbtm-install-manifest'

# --- output helpers ----------------------------------------------------------

kbtm_info() {
    printf '%s\n' "$*"
}

kbtm_warn() {
    printf 'WARN: %s\n' "$*" >&2
}

kbtm_err() {
    printf 'ERROR: %s\n' "$*" >&2
}

kbtm_die() {
    # kbtm_die <exit-code> <message...>
    kbtm_die_code=$1
    shift
    kbtm_err "$*"
    exit "$kbtm_die_code"
}

kbtm_usage() {
    cat <<'USAGE'
install.sh - install the kbeauty-trade-matchmaker Agent Skill.

Usage:
  sh install.sh [options]

Options:
  --runtime NAME     Which runtime's skills directory to install into:
                       claude  (default)  ~/.claude/skills/  or  DIR/.claude/skills/
                       codex              ~/.agents/skills/  or  DIR/.agents/skills/
                     --codex is shorthand for --runtime codex.
  --copy             Install an independent copy instead of a symlink.
  --force            Replace an existing non-symlink target that this script did not create.
  --project DIR      Install into the chosen runtime's skills directory under DIR instead of
                     the one under $HOME.
  --verify           Run the bundled test suite against the installed skill afterwards.
  --dry-run          Print the plan and change nothing.
  -h, --help         Show this help and exit.

Default target:  $HOME/.claude/skills/kbeauty-trade-matchmaker
Codex target:    $HOME/.agents/skills/kbeauty-trade-matchmaker   (--runtime codex)
Default mode:    symlink (edits to the source tree are picked up immediately; both runtimes
                 follow a symlinked skill folder through to its target)

Locations the runtimes search but this installer does not write to are listed in
references/runtime-adapters.md sections 5.1 and 5.2 - enterprise/managed, nested, --add-dir and
plugin scopes for Claude Code, and /etc/codex/skills plus the per-directory .agents/skills roots
that Codex scans from the working directory up to the repository root.

Exit codes: 0 success, 1 install blocked or verification failed, 2 usage error.
USAGE
}

# --- filesystem helpers ------------------------------------------------------

# Absolute, symlink-resolved directory that holds this script (= the package root).
kbtm_dir_of() {
    kbtm_dir_of_path=$1
    kbtm_dir_of_parent=$(dirname -- "$kbtm_dir_of_path")
    (CDPATH='' cd -- "$kbtm_dir_of_parent" >/dev/null 2>&1 && pwd -P)
}

# Absolute, symlink-resolved path of an existing directory.
kbtm_abs_dir() {
    (CDPATH='' cd -- "$1" >/dev/null 2>&1 && pwd -P)
}

# Run a command, or print it when --dry-run is on.
kbtm_run() {
    if [ "$KBTM_DRY_RUN" -eq 1 ]; then
        printf '  would run: %s\n' "$*"
    else
        "$@"
    fi
}

# Read the source path recorded in a target we installed earlier; empty if absent.
kbtm_manifest_source() {
    kbtm_manifest_file="$1/$KBTM_MANIFEST"
    if [ -f "$kbtm_manifest_file" ]; then
        sed -n 's/^source=//p' "$kbtm_manifest_file" 2>/dev/null | head -n 1
    fi
}

kbtm_write_manifest() {
    # kbtm_write_manifest <target> <mode> <source>
    kbtm_wm_target=$1
    kbtm_wm_mode=$2
    kbtm_wm_source=$3
    if [ "$KBTM_DRY_RUN" -eq 1 ]; then
        printf '  would write: %s\n' "$kbtm_wm_target/$KBTM_MANIFEST"
        return 0
    fi
    {
        printf 'skill=%s\n' "$KBTM_SKILL_NAME"
        printf 'mode=%s\n' "$kbtm_wm_mode"
        printf 'source=%s\n' "$kbtm_wm_source"
        printf 'installed_by=install.sh\n'
    } >"$kbtm_wm_target/$KBTM_MANIFEST"
}

# Delete build/runtime droppings from a fresh copy. Only ever touches paths inside <target>.
kbtm_prune_copy() {
    kbtm_pc_target=$1
    kbtm_run rm -rf "$kbtm_pc_target/tradewith-data"
    kbtm_run rm -rf "$kbtm_pc_target/.omc"
    if [ "$KBTM_DRY_RUN" -eq 1 ]; then
        printf '  would run: rm -rf %s (every __pycache__ directory)\n' "$kbtm_pc_target/**/__pycache__"
        return 0
    fi
    find "$kbtm_pc_target" -type d -name '__pycache__' -prune -exec rm -rf -- {} + 2>/dev/null || true
}

# --- the install steps -------------------------------------------------------

kbtm_preflight() {
    # The four files that make this a usable skill package. SKILL.md is the runtime entry point;
    # without it neither Claude nor Codex can see the skill at all.
    kbtm_pf_missing=''
    for kbtm_pf_rel in \
        'SKILL.md' \
        'scripts/_common.py' \
        'schemas/scoring.config.json' \
        'references/runtime-adapters.md'
    do
        if [ ! -f "$KBTM_SOURCE_DIR/$kbtm_pf_rel" ]; then
            kbtm_pf_missing="$kbtm_pf_missing $kbtm_pf_rel"
        fi
    done

    if [ -n "$kbtm_pf_missing" ]; then
        kbtm_err "$KBTM_SOURCE_DIR does not look like a complete $KBTM_SKILL_NAME package."
        for kbtm_pf_rel in $kbtm_pf_missing; do
            printf '  missing: %s\n' "$kbtm_pf_rel" >&2
        done
        return 1
    fi
    return 0
}

# Relative skills directory for the chosen runtime. Both paths checked 2026-09-13:
#   claude -> .claude/skills   (https://code.claude.com/docs/en/skills)
#   codex  -> .agents/skills   (https://learn.chatgpt.com/docs/build-skills)
# Codex also still scans the deprecated $CODEX_HOME/skills; this script never writes there.
kbtm_skills_subdir() {
    case "$KBTM_RUNTIME" in
        claude) printf '.claude/skills\n' ;;
        codex) printf '.agents/skills\n' ;;
        *) kbtm_die 2 "unknown runtime: $KBTM_RUNTIME (expected claude or codex)" ;;
    esac
}

kbtm_plan_target() {
    kbtm_pt_subdir=$(kbtm_skills_subdir)
    if [ -n "$KBTM_PROJECT_DIR" ]; then
        if [ ! -d "$KBTM_PROJECT_DIR" ]; then
            kbtm_die 2 "--project directory does not exist: $KBTM_PROJECT_DIR"
        fi
        KBTM_SKILLS_DIR="$(kbtm_abs_dir "$KBTM_PROJECT_DIR")/$kbtm_pt_subdir"
        KBTM_SCOPE='project'
    else
        if [ -z "${HOME:-}" ]; then
            kbtm_die 2 'HOME is not set; pass --project DIR to choose an explicit target.'
        fi
        KBTM_SKILLS_DIR="$HOME/$kbtm_pt_subdir"
        KBTM_SCOPE='global'
    fi
    KBTM_TARGET="$KBTM_SKILLS_DIR/$KBTM_SKILL_NAME"
}

# Decide what to do about anything already sitting at the target path.
# Sets KBTM_EXISTING_ACTION to: none | already-installed | replace-symlink | replace-ours | blocked
kbtm_inspect_target() {
    KBTM_EXISTING_ACTION='none'

    if [ -L "$KBTM_TARGET" ]; then
        kbtm_it_link=$(readlink -- "$KBTM_TARGET" 2>/dev/null || printf '')
        kbtm_it_resolved=''
        if [ -d "$KBTM_TARGET" ]; then
            kbtm_it_resolved=$(kbtm_abs_dir "$KBTM_TARGET" 2>/dev/null || printf '')
        fi
        if [ "$KBTM_MODE" = 'symlink' ] && [ "$kbtm_it_resolved" = "$KBTM_SOURCE_DIR" ]; then
            KBTM_EXISTING_ACTION='already-installed'
        else
            KBTM_EXISTING_ACTION='replace-symlink'
        fi
        KBTM_EXISTING_NOTE="symlink -> ${kbtm_it_link:-<broken>}"
        return 0
    fi

    if [ ! -e "$KBTM_TARGET" ]; then
        KBTM_EXISTING_NOTE='nothing there'
        return 0
    fi

    if [ -d "$KBTM_TARGET" ]; then
        kbtm_it_owner=$(kbtm_manifest_source "$KBTM_TARGET")
        if [ -n "$kbtm_it_owner" ]; then
            KBTM_EXISTING_ACTION='replace-ours'
            KBTM_EXISTING_NOTE="copy installed by this script from $kbtm_it_owner"
        elif [ "$KBTM_FORCE" -eq 1 ]; then
            KBTM_EXISTING_ACTION='replace-ours'
            KBTM_EXISTING_NOTE='directory not created by this script (--force given)'
        else
            KBTM_EXISTING_ACTION='blocked'
            KBTM_EXISTING_NOTE='directory not created by this script'
        fi
        return 0
    fi

    # A regular file, a device, anything else.
    if [ "$KBTM_FORCE" -eq 1 ]; then
        KBTM_EXISTING_ACTION='replace-ours'
        KBTM_EXISTING_NOTE='non-directory file (--force given)'
    else
        KBTM_EXISTING_ACTION='blocked'
        KBTM_EXISTING_NOTE='non-directory file'
    fi
}

kbtm_install() {
    case "$KBTM_EXISTING_ACTION" in
        blocked)
            kbtm_err "refusing to replace $KBTM_TARGET ($KBTM_EXISTING_NOTE)."
            printf '  Re-run with --force to replace it, or move it aside first.\n' >&2
            return 1
            ;;
        already-installed)
            kbtm_info "Already installed: $KBTM_TARGET -> $KBTM_SOURCE_DIR"
            kbtm_info 'Nothing to do.'
            return 0
            ;;
        replace-symlink)
            kbtm_info "Replacing existing symlink ($KBTM_EXISTING_NOTE)."
            kbtm_run rm -f "$KBTM_TARGET"
            ;;
        replace-ours)
            kbtm_info "Replacing existing install ($KBTM_EXISTING_NOTE)."
            kbtm_run rm -rf "$KBTM_TARGET"
            ;;
        none)
            ;;
    esac

    kbtm_run mkdir -p "$KBTM_SKILLS_DIR"

    if [ "$KBTM_MODE" = 'symlink' ]; then
        kbtm_run ln -s "$KBTM_SOURCE_DIR" "$KBTM_TARGET"
        [ "$KBTM_DRY_RUN" -eq 1 ] || kbtm_info "Linked: $KBTM_TARGET -> $KBTM_SOURCE_DIR"
    else
        kbtm_run cp -R "$KBTM_SOURCE_DIR" "$KBTM_TARGET"
        kbtm_prune_copy "$KBTM_TARGET"
        kbtm_write_manifest "$KBTM_TARGET" 'copy' "$KBTM_SOURCE_DIR"
        [ "$KBTM_DRY_RUN" -eq 1 ] || kbtm_info "Copied: $KBTM_SOURCE_DIR -> $KBTM_TARGET"
    fi
    return 0
}

kbtm_verify() {
    kbtm_vf_runner="$KBTM_SOURCE_DIR/tests/run_tests.py"
    if [ ! -f "$kbtm_vf_runner" ]; then
        kbtm_err "--verify asked for, but the test runner is missing: $kbtm_vf_runner"
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        kbtm_err '--verify needs python3 on PATH (3.9 or newer, stdlib only).'
        return 1
    fi
    if [ "$KBTM_DRY_RUN" -eq 1 ]; then
        printf '  would run: python3 %s\n' "$kbtm_vf_runner"
        return 0
    fi
    kbtm_info ''
    kbtm_info "Verifying: python3 $kbtm_vf_runner"
    if python3 "$kbtm_vf_runner"; then
        kbtm_info 'Verification passed.'
        return 0
    fi
    kbtm_err 'the bundled test suite failed; the install is on disk but is not trustworthy.'
    return 1
}

# --- entry point -------------------------------------------------------------

kbtm_main() {
    KBTM_MODE='symlink'
    KBTM_FORCE=0
    KBTM_DRY_RUN=0
    KBTM_DO_VERIFY=0
    KBTM_PROJECT_DIR=''
    KBTM_RUNTIME='claude'

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --runtime)
                [ "$#" -ge 2 ] || kbtm_die 2 '--runtime needs a value (claude or codex).'
                KBTM_RUNTIME=$2
                shift
                ;;
            --runtime=*)
                KBTM_RUNTIME=${1#--runtime=}
                [ -n "$KBTM_RUNTIME" ] || kbtm_die 2 '--runtime needs a value (claude or codex).'
                ;;
            --codex) KBTM_RUNTIME='codex' ;;
            --claude) KBTM_RUNTIME='claude' ;;
            --copy) KBTM_MODE='copy' ;;
            --symlink) KBTM_MODE='symlink' ;;
            --force) KBTM_FORCE=1 ;;
            --verify) KBTM_DO_VERIFY=1 ;;
            --dry-run) KBTM_DRY_RUN=1 ;;
            --project)
                [ "$#" -ge 2 ] || kbtm_die 2 '--project needs a directory argument.'
                KBTM_PROJECT_DIR=$2
                shift
                ;;
            --project=*)
                KBTM_PROJECT_DIR=${1#--project=}
                [ -n "$KBTM_PROJECT_DIR" ] || kbtm_die 2 '--project needs a non-empty directory argument.'
                ;;
            -h | --help)
                kbtm_usage
                return 0
                ;;
            --)
                shift
                break
                ;;
            *) kbtm_die 2 "unknown option: $1 (try --help)" ;;
        esac
        shift
    done

    [ "$#" -eq 0 ] || kbtm_die 2 "unexpected argument: $1 (try --help)"

    KBTM_SOURCE_DIR=$(kbtm_dir_of "$KBTM_SCRIPT_PATH")
    [ -n "$KBTM_SOURCE_DIR" ] || kbtm_die 1 'could not resolve the package directory from $0.'

    kbtm_plan_target

    if [ "$KBTM_TARGET" = "$KBTM_SOURCE_DIR" ]; then
        kbtm_die 1 "the source and the target are the same directory: $KBTM_TARGET"
    fi

    kbtm_inspect_target

    kbtm_info "kbeauty-trade-matchmaker installer"
    kbtm_info "  source:  $KBTM_SOURCE_DIR"
    kbtm_info "  runtime: $KBTM_RUNTIME"
    kbtm_info "  target:  $KBTM_TARGET  ($KBTM_SCOPE scope)"
    kbtm_info "  mode:    $KBTM_MODE"
    kbtm_info "  target now: $KBTM_EXISTING_NOTE"
    if [ "$KBTM_DRY_RUN" -eq 1 ]; then
        kbtm_info '  dry run: nothing will be changed'
    fi
    kbtm_info ''

    kbtm_pf_status=0
    kbtm_preflight || kbtm_pf_status=$?
    if [ "$kbtm_pf_status" -ne 0 ]; then
        if [ "$KBTM_DRY_RUN" -eq 1 ]; then
            kbtm_warn 'dry run continues so you can see the whole plan, but a real install would stop here.'
        else
            return 1
        fi
    fi

    kbtm_install || return 1

    if [ "$KBTM_DO_VERIFY" -eq 1 ]; then
        kbtm_verify || return 1
    fi

    if [ "$KBTM_DRY_RUN" -eq 1 ]; then
        kbtm_info ''
        kbtm_info 'Dry run complete. Nothing was changed.'
        [ "$kbtm_pf_status" -eq 0 ] || return 1
    fi
    return 0
}

# Only run when executed as a script. Sourcing this file defines the functions and stops there.
KBTM_SCRIPT_PATH=${0:-install.sh}
case "${KBTM_SCRIPT_PATH##*/}" in
    install.sh)
        if [ "${KBTM_NO_MAIN:-0}" != '1' ]; then
            kbtm_main "$@"
            exit $?
        fi
        ;;
esac
