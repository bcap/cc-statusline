#!/usr/bin/env bash

if [[ -n "${STATUSLINE_DEBUG_REENTRY:-}" ]]; then
    set -xv
fi

# --- Flag defaults ---
CTX_WARN=150
CTX_CRIT=200
RATE_5H_WARN=75
RATE_5H_CRIT=100
RATE_WEEK_WARN=75
RATE_WEEK_CRIT=100
FIELDS="cwd,git,model,ctx,cost,rate"
SEPARATOR=" | "
COST_PRECISION=3
WARN_STR="⚠️"
CRIT_STR="🔥"
DEBUG_FILE=""

# --- Arg parsing ---
usage() {
    cat <<EOF
Usage: statusline.sh [FLAGS]

Reads the Claude Code status JSON from stdin and prints a single-line status.

Flags (defaults in []):
  --ctx-warn K          context warn threshold, k-tokens [$CTX_WARN]
  --ctx-crit K          context critical threshold, k-tokens [$CTX_CRIT]
  --rate-5h-warn P      5h rate-limit warn % [$RATE_5H_WARN]
  --rate-5h-crit P      5h rate-limit critical % [$RATE_5H_CRIT]
  --rate-week-warn P    weekly rate-limit warn % [$RATE_WEEK_WARN]
  --rate-week-crit P    weekly rate-limit critical % [$RATE_WEEK_CRIT]
  --fields LIST         comma-list; order = display order [$FIELDS]
                        see FIELDS section below for valid names
  --separator STR       field separator [$SEPARATOR]
  --cost-precision N    cost decimal places [$COST_PRECISION]
  --warn-str STR        warn indicator [$WARN_STR]
  --crit-str STR        critical indicator [$CRIT_STR]
  --debug PATH          trace this execution (set -xv) and append the
                        trace output to the file at PATH
  -h, --help            show this help and exit

FIELDS:
  cwd              current working directory (\$HOME shown as ~)
  git              git branch + change count, e.g. "main (3 changes)"
  model            model display name, e.g. "Opus 4.7"
  ctx              context window usage %, k-tokens; warn/crit indicator
                   when over --ctx-warn / --ctx-crit thresholds
  cost             session cost in USD, e.g. "\$0.123"
  rate             5h/weekly rate-limit %s + reset countdowns; warn/crit
                   indicator when over --rate-*-warn / --rate-*-crit
  session          session name (or "UNNAMED")
  session_id       full session UUID
  effort           effort level (e.g. "high")
  version          Claude Code version, e.g. "v1.2.3"
  agent            active subagent name, prefixed with "@"
  worktree         worktree name, prefixed with "wt:"
  transcript_path  path to the session transcript JSONL
  api_duration     total API time this session (human duration)
  duration         total wall-clock time this session (human duration)
  changes          total lines added + removed, prefixed with "Δ"
  added            total lines added, prefixed with "+"
  removed          total lines removed, prefixed with "-"
EOF
}

ORIG_ARGV=("$@")
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ctx-warn)        CTX_WARN="$2";        shift 2;;
        --ctx-crit)        CTX_CRIT="$2";        shift 2;;
        --rate-5h-warn)    RATE_5H_WARN="$2";    shift 2;;
        --rate-5h-crit)    RATE_5H_CRIT="$2";    shift 2;;
        --rate-week-warn)  RATE_WEEK_WARN="$2";  shift 2;;
        --rate-week-crit)  RATE_WEEK_CRIT="$2";  shift 2;;
        --fields)          FIELDS="$2";          shift 2;;
        --separator)       SEPARATOR="$2";       shift 2;;
        --cost-precision)  COST_PRECISION="$2";  shift 2;;
        --warn-str)        WARN_STR="$2";        shift 2;;
        --crit-str)        CRIT_STR="$2";        shift 2;;
        --debug)           DEBUG_FILE="$2";      shift 2;;
        -h|--help)         usage; exit 0;;
        *) printf "statusline: unknown flag: %s\n" "$1" >&2; usage >&2; exit 2;;
    esac
done

# --- Debug self-re-exec ---

# When --debug PATH is set, re-exec ourselves with stderr appended to PATH and
# enable `set -xv`. The sentinel env var prevents recursion.
if [[ -n "$DEBUG_FILE" && -z "${STATUSLINE_DEBUG_REENTRY:-}" ]]; then
    {
        printf '\n'
        printf '=============================\n'
        printf '==== %s ====\n' "$(date '+%Y-%m-%d %H:%M:%S')"
        printf '=============================\n'
    } >> "$DEBUG_FILE"
    export STATUSLINE_DEBUG_REENTRY=1
    exec "$0" "${ORIG_ARGV[@]}" 2>>"$DEBUG_FILE"
fi

# --- Program ---

# Claude code passes current status data as a json string through stdin.
# Available data at: https://code.claude.com/docs/en/statusline#available-data
current_status="$(cat)"

# Fetch last status line for comparison
project_dir="$(echo "$current_status" | jq -r '.workspace.project_dir // empty')"
if [[ -z "$project_dir" ]]; then
    printf "No workspace project_dir found in current_status JSON\n" >&2
    exit 1
fi

# Session
session_name="$(echo "$current_status" | jq -r '.session_name // "UNNAMED"')"

# Model
model="$(echo "$current_status" | jq -r '.model.display_name // .model.id // ""')"

# Context usage
ctx_display=""
ctx_used=$(echo "$current_status" | jq -r '.context_window.used_percentage // empty')
ctx_window_size=$(echo "$current_status" | jq -r '.context_window.context_window_size // empty')
if [[ -n "$ctx_used" ]]; then
    tokens_used_k=""
    if [[ -n "$ctx_window_size" ]]; then
        tokens_used_k=$(awk -v used="$ctx_used" -v size="$ctx_window_size" 'BEGIN { printf "%d", used * size / 100 / 1000 }')
    fi
    if [[ -n "$tokens_used_k" ]] && awk -v t="$tokens_used_k" -v threshold="$CTX_CRIT" 'BEGIN { exit !(t >= threshold) }'; then
        ctx_display=$(printf "%s~%sk (%d%%)" "$CRIT_STR" "$tokens_used_k" "$ctx_used")
    elif [[ -n "$tokens_used_k" ]] && awk -v t="$tokens_used_k" -v threshold="$CTX_WARN" 'BEGIN { exit !(t >= threshold) }'; then
        ctx_display=$(printf "%s~%sk (%d%%)" "$WARN_STR" "$tokens_used_k" "$ctx_used")
    else
        ctx_display=$(printf "~%sk (%d%%)" "$tokens_used_k" "$ctx_used")
    fi
fi

# Cost
cost_display=""
cost="$(echo "$current_status" | jq -r '.cost.total_cost_usd // empty')"
if [[ -n "$cost" ]]; then
    cost_display=$(awk -v c="$cost" -v p="$COST_PRECISION" 'BEGIN { printf "%.*f", p, c }')
fi

# Rate Limits
function human_duration() {
    local seconds="$(cat -)"
    if (( seconds < 0 )); then
        printf "❓"
    elif (( seconds < 60 )); then
        printf "%ds" "$seconds"
    elif (( seconds < $((60 * 60)) )); then
        printf "%dm:%02ds" "$((seconds / 60))" "$((seconds % 60))"
    elif (( seconds < $((60 * 60 * 24)) )); then
        printf "%dh:%02dm" "$((seconds / 3600))" "$((seconds % 3600 / 60))"
    else
        printf "%dd:%02dh" "$((seconds / 86400))" "$((seconds % 86400 / 3600))"
    fi
}
ratelimit=""
ratelimit_5hr="$(echo "$current_status" | jq -r '.rate_limits.five_hour.used_percentage // empty')"
ratelimit_week="$(echo "$current_status" | jq -r '.rate_limits.seven_day.used_percentage // empty')"
ratelimit_5hr_reset="$(echo "$current_status" | jq -r '.rate_limits.five_hour.resets_at // empty')"
ratelimit_week_reset="$(echo "$current_status" | jq -r '.rate_limits.seven_day.resets_at // empty')"
if [[ -n $ratelimit_5hr && -n $ratelimit_week ]]; then
    now=$(date +%s)
    ratelimit_5hr_reset="$(awk -v r="$ratelimit_5hr_reset" -v n="$now" 'BEGIN { printf "%d", r - n }' | human_duration)"
    ratelimit_week_reset="$(awk -v r="$ratelimit_week_reset" -v n="$now" 'BEGIN { printf "%d", r - n }' | human_duration)"
    ratelimit_token=""
    if awk -v a="$ratelimit_5hr" -v b="$ratelimit_week" -v ca="$RATE_5H_CRIT" -v cb="$RATE_WEEK_CRIT" 'BEGIN { exit !(a >= ca || b >= cb) }'; then
        ratelimit_token="$CRIT_STR"
    elif awk -v a="$ratelimit_5hr" -v b="$ratelimit_week" -v wa="$RATE_5H_WARN" -v wb="$RATE_WEEK_WARN" 'BEGIN { exit !(a >= wa || b >= wb) }'; then
        ratelimit_token="$WARN_STR"
    fi
    ratelimit=$(printf "%s%.0f%%/%.0f%% (%s/%s)" "$ratelimit_token" "$ratelimit_5hr" "$ratelimit_week" "$ratelimit_5hr_reset" "$ratelimit_week_reset")
fi

# Git info
git_info=""
if git -C "${project_dir}" --no-optional-locks rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    branch=$(git -C "${project_dir}" --no-optional-locks rev-parse --abbrev-ref HEAD 2>/dev/null)
    git_info="$branch"
    git_status=$(git -C "${project_dir}" --no-optional-locks status --porcelain --branch 2>/dev/null)
    changes=$(echo "$git_status" | grep -v '^##' | grep -c . || true)
    if [[ "$changes" -gt 0 ]]; then
        if [[ "$changes" -eq 1 ]]; then
            changes="1 change"
        else
            changes="$changes changes"
        fi
        git_info="$git_info ($changes)"
    fi
fi

# Extra opt-in fields (not in default FIELDS list)
session_id="$(echo "$current_status" | jq -r '.session_id // ""')"
effort_level="$(echo "$current_status" | jq -r '.effort.level // ""')"
version="$(echo "$current_status" | jq -r '.version // ""')"
agent_name="$(echo "$current_status" | jq -r '.agent.name // ""')"
worktree_name="$(echo "$current_status" | jq -r '.worktree.name // ""')"
transcript_path="$(echo "$current_status" | jq -r '.transcript_path // ""')"
lines_added="$(echo "$current_status" | jq -r '.cost.total_lines_added // empty')"
lines_removed="$(echo "$current_status" | jq -r '.cost.total_lines_removed // empty')"
lines_changes=""
if [[ -n "$lines_added" || -n "$lines_removed" ]]; then
    lines_changes=$(( ${lines_added:-0} + ${lines_removed:-0} ))
fi
api_duration_ms="$(echo "$current_status" | jq -r '.cost.total_api_duration_ms // empty')"
total_duration_ms="$(echo "$current_status" | jq -r '.cost.total_duration_ms // empty')"
api_duration_display=""
total_duration_display=""
[[ -n "$api_duration_ms" ]]   && api_duration_display="$(echo "$((api_duration_ms / 1000))" | human_duration)"
[[ -n "$total_duration_ms" ]] && total_duration_display="$(echo "$((total_duration_ms / 1000))" | human_duration)"

# --- Compose status line ---
# Field tokens map to their already-built display strings. cwd/git/model/session
# render as-is; ctx/cost/rate keep the label prefixes the old fixed printf used.
field_value() {
    case "$1" in
        cwd)             printf "%s" "${project_dir/$HOME/\~}";;
        git)             [[ -n "$git_info" ]]              && printf "%s" "$git_info";;
        model)           [[ -n "$model" ]]                 && printf "%s" "$model";;
        ctx)             [[ -n "$ctx_display" ]]           && printf "ctx: %s" "$ctx_display";;
        cost)            [[ -n "$cost_display" ]]          && printf "\$%s" "$cost_display";;
        rate)            [[ -n "$ratelimit" ]]             && printf "lmt: %s" "$ratelimit";;
        session)         [[ -n "$session_name" ]]          && printf "%s" "$session_name";;
        session_id)      [[ -n "$session_id" ]]            && printf "%s" "$session_id";;
        effort)          [[ -n "$effort_level" ]]          && printf "%s" "$effort_level";;
        version)         [[ -n "$version" ]]               && printf "v%s" "$version";;
        agent)            [[ -n "$agent_name" ]]           && printf "@%s" "$agent_name";;
        worktree)        [[ -n "$worktree_name" ]]         && printf "wt:%s" "$worktree_name";;
        transcript_path) [[ -n "$transcript_path" ]]       && printf "%s" "$transcript_path";;
        api_duration)    [[ -n "$api_duration_display" ]]  && printf "api:%s" "$api_duration_display";;
        duration)        [[ -n "$total_duration_display" ]] && printf "dur:%s" "$total_duration_display";;
        changes)         [[ -n "$lines_changes" ]]         && printf "Δ%s" "$lines_changes";;
        added)           [[ -n "$lines_added" ]]           && printf "+%s" "$lines_added";;
        removed)         [[ -n "$lines_removed" ]]         && printf -- "-%s" "$lines_removed";;
        *) printf "statusline: unknown field: %s\n" "$1" >&2;;
    esac
}

out=""
IFS=',' read -ra field_list <<< "$FIELDS"
for f in "${field_list[@]}"; do
    val="$(field_value "$f")"
    [[ -z "$val" ]] && continue
    if [[ -z "$out" ]]; then
        out="$val"
    else
        out="${out}${SEPARATOR}${val}"
    fi
done
printf "%s" "$out"
