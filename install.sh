#!/usr/bin/env bash
#
# Installs statusline.sh and wires it into ~/.claude/settings.json.
#
# By default, downloads the latest statusline.sh from github.com/bcap/cc-statusline.
# With --local, copies the statusline.sh sitting next to this script.

set -euo pipefail

STATUSLINE_RAW_URL="https://raw.githubusercontent.com/bcap/cc-statusline/main/statusline.sh"

usage() {
    cat <<EOF
Usage: install.sh [--local] [--path PATH]

Flags:
  --local        copy the statusline.sh sitting next to this script instead
                 of downloading the latest version from the canonical repo
  --path PATH    install destination [~/.claude/statusline.sh]
  -h, --help     show this help and exit

Behavior:
  - If the destination file exists and differs from the source, aborts with
    a diff (no overwrite). Identical content is a no-op.
  - If ~/.claude/settings.json already has a .statusLine block that would
    change, prints a diff and prompts before writing.
  - Nothing is written to disk until the prompt is accepted.
EOF
}

use_local=0
dest="$HOME/.claude/statusline.sh"
dest_display='~/.claude/statusline.sh'
dest_specified=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)   use_local=1; shift;;
        --path)    dest="$2"; dest_specified=1; shift 2;;
        -h|--help) usage; exit 0;;
        *) printf "install.sh: unknown flag: %s\n" "$1" >&2; usage >&2; exit 2;;
    esac
done

if [[ "$dest" == "~/"* ]]; then
    dest="$HOME/${dest#~/}"
elif [[ "$dest" == "~" ]]; then
    dest="$HOME"
fi

if [[ $dest_specified -eq 1 ]]; then
    if [[ "$dest" == "$HOME"/* ]]; then
        dest_display="~/${dest#$HOME/}"
    elif [[ "$dest" == "$HOME" ]]; then
        dest_display="~"
    else
        dest_display="$dest"
    fi
fi

command -v jq >/dev/null || { printf "install.sh: jq not found\n" >&2; exit 1; }
if [[ $use_local -eq 0 ]]; then
    command -v curl >/dev/null || { printf "install.sh: curl not found\n" >&2; exit 1; }
fi

cat >&2 <<EOF
Installing cc-statusline (https://github.com/bcap/cc-statusline)

This will:
  1. Place the statusline script at $dest_display
  2. Point Claude Code at it by setting .statusLine in ~/.claude/settings.json

Nothing is written until any conflicts are shown and confirmed.

EOF

src="$(mktemp)"
trap 'rm -f "$src"' EXIT

if [[ $use_local -eq 1 ]]; then
    self_dir="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
    local_src="$self_dir/statusline.sh"
    if [[ ! -e "$local_src" ]]; then
        printf "install.sh: --local: no statusline.sh next to %s\n" "${BASH_SOURCE[0]}" >&2
        exit 1
    fi
    cp "$local_src" "$src"
else
    printf "Fetching %s\n" "$STATUSLINE_RAW_URL" >&2
    if ! curl -fsSL "$STATUSLINE_RAW_URL" -o "$src"; then
        printf "install.sh: download failed\n" >&2
        exit 1
    fi
fi

first="$(head -n1 "$src")"
if [[ "$first" != "#!"*bash* ]]; then
    printf "install.sh: source does not look like a bash script\n" >&2
    exit 1
fi

CC_STATUSLINE_MARKER="# cc-statusline"

dest_action="write"
dest_needs_confirm=0
if [[ -e "$dest" ]]; then
    if cmp -s "$src" "$dest"; then
        dest_action="skip"
    elif grep -qF "$CC_STATUSLINE_MARKER" "$dest" 2>/dev/null; then
        dest_needs_confirm=1
    else
        if [[ -t 2 ]]; then red=$'\033[31m'; bold=$'\033[1m'; reset=$'\033[0m'; else red=""; bold=""; reset=""; fi
        printf "%sRefusing to overwrite %s%s\n" "$bold$red" "$dest" "$reset" >&2
        printf "\n" >&2
        printf "A file already exists at that path and it does not look like a cc-statusline install\n" >&2
        printf "(no %s marker found). It may belong to another tool or be a custom script of yours.\n" "$CC_STATUSLINE_MARKER" >&2
        printf "To stay safe, the installer will not touch it.\n" >&2
        printf "\n" >&2
        printf "Options:\n" >&2
        printf "  - Inspect the file and, if you want cc-statusline instead, remove or rename it and re-run.\n" >&2
        printf "  - Install to a different location with: install.sh --path <other-path>\n" >&2
        printf "\n" >&2
        printf "Diff between the existing file and the cc-statusline version follows:\n\n" >&2
        diff -u "$dest" "$src" || true
        exit 1
    fi
fi

settings="$HOME/.claude/settings.json"
new_block="$(jq -n --arg cmd "$dest_display" '{type:"command", command:$cmd, refreshInterval:5}')"

if [[ -e "$settings" ]]; then
    if ! jq -e . "$settings" >/dev/null 2>&1; then
        printf "install.sh: %s is not valid JSON\n" "$settings" >&2
        exit 1
    fi
    old_json="$(cat "$settings")"
else
    old_json='{}'
fi

proposed="$(printf '%s' "$old_json" | jq --argjson new "$new_block" '.statusLine = $new')"

old_norm="$(printf '%s' "$old_json" | jq -S .)"
proposed_norm="$(printf '%s' "$proposed" | jq -S .)"

settings_changed=1
[[ "$old_norm" == "$proposed_norm" ]] && settings_changed=0

if [[ $settings_changed -eq 0 && "$dest_action" == "skip" ]]; then
    printf "Script at %s already up to date\n" "$dest" >&2
    printf "settings.json already configured for %s\n" "$dest_display" >&2
    printf "\ncc-statusline is already installed — nothing to do.\n" >&2
    exit 0
fi

had_block="$(printf '%s' "$old_json" | jq 'has("statusLine")')"
settings_needs_confirm=0
[[ $settings_changed -eq 1 && "$had_block" == "true" ]] && settings_needs_confirm=1

if [[ $dest_needs_confirm -eq 1 ]]; then
    printf "An existing cc-statusline install was found at %s and differs from this version.\n" "$dest" >&2
    printf "Diff (installed -> incoming) below:\n\n" >&2
    diff -u "$dest" "$src" || true
    printf "\n" >&2
fi

if [[ $settings_needs_confirm -eq 1 ]]; then
    printf "Your %s already has a .statusLine configured, and it differs from what we'd set.\n" "$settings" >&2
    printf "Proposed change:\n\n" >&2
    diff -u <(printf '%s\n' "$old_norm") <(printf '%s\n' "$proposed_norm") || true
    printf "\n" >&2
fi

if [[ $dest_needs_confirm -eq 1 || $settings_needs_confirm -eq 1 ]]; then
    if [[ ! -r /dev/tty ]]; then
        printf "install.sh: no TTY available for confirmation; aborting\n" >&2
        exit 1
    fi
    read -r -p "Proceed? [y/N] " ans </dev/tty
    case "$ans" in
        y|Y|yes|YES) ;;
        *) printf "Aborted.\n" >&2; exit 1;;
    esac
fi

mkdir -p "$(dirname "$dest")"
mkdir -p "$(dirname "$settings")"

if [[ "$dest_action" == "write" ]]; then
    tmpdest="${dest}.tmp.$$"
    cp "$src" "$tmpdest"
    chmod 755 "$tmpdest"
    mv "$tmpdest" "$dest"
    if [[ $dest_needs_confirm -eq 1 ]]; then
        printf "Updated %s\n" "$dest" >&2
    else
        printf "Installed %s\n" "$dest" >&2
    fi
else
    printf "Script at %s already up to date\n" "$dest" >&2
fi

if [[ $settings_changed -eq 1 ]]; then
    mode=""
    if [[ -e "$settings" ]]; then
        mode="$(stat -c %a "$settings" 2>/dev/null || true)"
    fi
    tmpset="${settings}.tmp.$$"
    printf '%s\n' "$proposed" | jq . > "$tmpset"
    [[ -n "$mode" ]] && chmod "$mode" "$tmpset"
    mv "$tmpset" "$settings"
    printf "Updated %s\n" "$settings" >&2
else
    printf "settings.json already configured for %s\n" "$dest_display" >&2
fi

printf "\ncc-statusline installed successfully.\n" >&2
printf "Any running Claude Code sessions should pick up the new statusline on the next refresh.\n" >&2
