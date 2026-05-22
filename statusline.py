#!/usr/bin/env python3
# cc-statusline (https://github.com/bcap/cc-statusline)
"""Claude Code statusline: reads status JSON from stdin, prints one line."""

import json
import os
import string
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# --- Defaults ---
DEFAULTS: Dict[str, Any] = {
    "ctx_warn": 150,
    "ctx_crit": 200,
    "limits_5h_warn": 75,
    "limits_5h_crit": 100,
    "limits_week_warn": 75,
    "limits_week_crit": 100,
    "cache_warn": 80,
    "cache_crit": 50,
    "fields": "cwd,git,model,ctx,session_cost,limits",
    "separator": " | ",
    "warn_str": "⚠️",
    "crit_str": "🔥",
}

# Cost decimal places used by built-in cost fields. Users wanting different
# precision should define a custom field over session_cost_usd / turn_cost_usd.
COST_PRECISION = 3

# Per-model pricing in USD per million tokens (input, output).
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# Cache write 5m = 1.25x input, cache read = 0.1x input.
MODEL_PRICES: Dict[str, Tuple[float, float]] = {
    "claude-opus-4-7":   (5.0, 25.0),
    "claude-opus-4-6":   (5.0, 25.0),
    "claude-opus-4-5":   (5.0, 25.0),
    "claude-opus-4-1":   (15.0, 75.0),
    "claude-opus-4":     (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4":   (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
    "claude-haiku-3-5":  (0.80, 4.0),
}

# Built-in field names. Composites are pre-formatted strings (label/warning
# prefixes baked in); the rest are raw typed values. The split exists only to
# order the --help output — at render time all fields share one namespace and
# can be referenced from --custom-field templates.
COMPOSITE_FIELDS: Tuple[str, ...] = (
    "cwd", "git", "model", "ctx", "session_cost", "turn_cost", "limits",
    "cache_hit", "session", "session_id", "effort", "version", "agent",
    "worktree", "transcript_path", "api_duration", "duration",
    "changes", "added", "removed",
    "session_tokens_in", "session_tokens_out",
    "turn_tokens_in", "turn_tokens_out",
    "turn_cache_write", "turn_cache_read",
)
RAW_FIELDS: Tuple[str, ...] = (
    "ctx_pct", "ctx_tokens_k", "ctx_warning",
    "limit_5h_pct", "limit_week_pct",
    "limit_5h_reset_sec", "limit_week_reset_sec",
    "limit_5h_reset", "limit_week_reset",
    "limit_5h_warning", "limit_week_warning",
    "session_cost_usd", "turn_cost_usd",
    "cache_hit_pct", "cache_hit_warning",
    "lines_added", "lines_removed", "lines_changed",
    "git_branch", "git_changes",
    "session_input_tokens", "session_output_tokens",
    "turn_input_tokens", "turn_output_tokens",
    "turn_cache_creation_tokens", "turn_cache_read_tokens",
)
FLAG_SPEC: Dict[str, Tuple[str, Callable[[str], Any]]] = {
    "--ctx-warn": ("ctx_warn", float),
    "--ctx-crit": ("ctx_crit", float),
    "--limits-5h-warn": ("limits_5h_warn", float),
    "--limits-5h-crit": ("limits_5h_crit", float),
    "--limits-week-warn": ("limits_week_warn", float),
    "--limits-week-crit": ("limits_week_crit", float),
    "--cache-warn": ("cache_warn", float),
    "--cache-crit": ("cache_crit", float),
    "--fields": ("fields", str),
    "--separator": ("separator", str),
    "--warn-str": ("warn_str", str),
    "--crit-str": ("crit_str", str),
}


USAGE = """\
Usage: statusline.py [FLAGS]

Reads the Claude Code status JSON from stdin and prints a single-line status.

Flags accept both `--flag value` and `--flag=value` forms.

Flags (defaults in []):
  --ctx-warn K          context warn threshold, k-tokens [{ctx_warn}]
  --ctx-crit K          context critical threshold, k-tokens [{ctx_crit}]
  --limits-5h-warn P    5h rate-limit warn % [{limits_5h_warn}]
  --limits-5h-crit P    5h rate-limit critical % [{limits_5h_crit}]
  --limits-week-warn P  weekly rate-limit warn % [{limits_week_warn}]
  --limits-week-crit P  weekly rate-limit critical % [{limits_week_crit}]
  --cache-warn P        cache hit ratio warn % (warn when below) [{cache_warn}]
  --cache-crit P        cache hit ratio critical % (crit when below) [{cache_crit}]
  --fields LIST         comma-list; order = display order [{fields}]
                        see FIELDS section below for valid names
  --separator STR       field separator [{separator}]
  --warn-str STR        warn indicator [{warn_str}]
  --crit-str STR        critical indicator [{crit_str}]
  --custom-field SPEC   define a custom field as NAME=TEMPLATE. Reference in
                        --fields as `custom:NAME`. Template uses Python format
                        syntax over the built-in field names below
                        (e.g. '{{limit_5h_pct:.1f}}%%'). Custom names live in
                        their own namespace — a custom may share a name with a
                        built-in (referencing it in --fields disambiguates via
                        the `custom:` prefix). Templates can only reference
                        built-in fields, not other customs. Repeatable.
  -h, --help            show this help and exit

FIELDS:
  Composite (pre-formatted with labels / warning prefixes):
    cwd              current working directory ($HOME shown as ~)
    git              git branch + change count, e.g. "main (3 changes)"
    model            model display name, e.g. "Opus 4.7"
    ctx              context window usage %, k-tokens; warn/crit indicator
                     when over --ctx-warn / --ctx-crit thresholds
    session_cost     total session cost in USD, e.g. "$0.123"
    turn_cost        estimated cost in USD of the last API call. Empty before
                     the first API call, after /compact, or for unknown models.
    limits           5h/weekly rate-limit %s + reset countdowns; warn/crit
                     indicator when over --limits-*-warn / --limits-*-crit
    cache_hit        prompt cache hit ratio for the last API call,
                     cache_read / (cache_read + cache_creation + input).
                     Warn/crit when below --cache-warn / --cache-crit
                     (inverted: low is bad). Empty before the first API call
                     and after /compact.
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
    session_tokens_in   session input tokens (human), e.g. "↑79.3k"
    session_tokens_out  session output tokens (human), e.g. "↓1.7k"
    turn_tokens_in   last API call input tokens (human), e.g. "↑1"
    turn_tokens_out  last API call output tokens (human), e.g. "↓1.7k"
    turn_cache_write last API call cache-creation tokens, e.g. "✎ 675"
    turn_cache_read  last API call cache-read tokens, e.g. "👁 78.6k"

  Raw (typed values, useful inside --custom-field templates or standalone):
    ctx_pct (float)          context usage %
    ctx_tokens_k (int)       context usage in k-tokens
    ctx_warning (str)        warn/crit indicator for ctx, or empty
    limit_5h_pct (float)     5h rate-limit usage %
    limit_week_pct (float)   weekly rate-limit usage %
    limit_5h_reset_sec (int) seconds until 5h limit resets
    limit_week_reset_sec     seconds until weekly limit resets
    limit_5h_reset (str)     5h reset countdown (human, e.g. "2h:44m")
    limit_week_reset (str)   weekly reset countdown (human)
    limit_5h_warning (str)   warn/crit indicator for 5h limit, or empty
    limit_week_warning (str) warn/crit indicator for weekly limit, or empty
    session_cost_usd (float) total session cost in USD (raw)
    turn_cost_usd (float)    last API call cost in USD (raw)
    cache_hit_pct (float)    prompt cache hit ratio %
    cache_hit_warning (str)  warn/crit indicator for cache, or empty
    lines_added (int)        total lines added this session
    lines_removed (int)      total lines removed this session
    lines_changed (int)      lines_added + lines_removed
    git_branch (str)         current git branch
    git_changes (int)        count of uncommitted changes
    session_input_tokens (int)       session total input tokens
    session_output_tokens (int)      session total output tokens
    turn_input_tokens (int)          last API call input tokens
    turn_output_tokens (int)         last API call output tokens
    turn_cache_creation_tokens (int) last API call cache-creation tokens
    turn_cache_read_tokens (int)     last API call cache-read tokens

A field whose value is unavailable renders empty when used standalone in
--fields. A custom field is skipped entirely (including its separator) if any
referenced field is unavailable, so partial output never appears.
""".format(**DEFAULTS)


def _die(msg: str) -> None:
    """Write a statusline error to stderr and exit with code 2."""
    sys.stderr.write(f"statusline: {msg}\n")
    sys.exit(2)


def _builtin_field_names() -> set:
    return set(RAW_FIELDS) | set(COMPOSITE_FIELDS)


def _validate_custom_template(name: str, template: str, known: set) -> None:
    """Parse-time validation for a custom-field template. Hard fails on error."""
    parsed = _parse_template(name, template)
    if parsed is None:
        sys.exit(2)
    for _literal, field, _spec, _conv in parsed:
        if field is None:
            continue
        if field not in known:
            _die(f"unknown field {field!r} in custom {name!r}")


def _validate_fields(fields: str, customs: Dict[str, str], known: set) -> None:
    """Validate every --fields token. Hard fails on unknown name or undefined custom."""
    for f in (x.strip() for x in fields.split(",")):
        if not f:
            continue
        if f.startswith("custom:"):
            cname = f[len("custom:"):]
            if cname not in customs:
                _die(f"undefined custom field in --fields: {cname}")
        elif f not in known:
            _die(f"unknown field in --fields: {f}")


def parse_args(argv: List[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = dict(DEFAULTS)
    opts["custom_fields"] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            sys.stdout.write(USAGE)
            sys.exit(0)
        # --custom-field handled specially (value is NAME=TEMPLATE).
        if a == "--custom-field" or a.startswith("--custom-field="):
            if a.startswith("--custom-field="):
                _, _, val = a.partition("=")
                i += 1
            else:
                if i + 1 >= len(argv):
                    sys.stderr.write("statusline: flag requires value: --custom-field\n")
                    sys.exit(2)
                val = argv[i + 1]
                i += 2
            if "=" not in val:
                sys.stderr.write(f"statusline: --custom-field expects NAME=TEMPLATE, got: {val!r}\n")
                sys.exit(2)
            name, _, template = val.partition("=")
            name = name.strip()
            if not name.isidentifier():
                sys.stderr.write(f"statusline: invalid custom field name: {name!r}\n")
                sys.exit(2)
            opts["custom_fields"][name] = template
            continue
        # --key=value form
        if a.startswith("--") and "=" in a:
            key, _, val = a.partition("=")
            if key not in FLAG_SPEC:
                sys.stderr.write(f"statusline: unknown flag: {key}\n")
                sys.stderr.write(USAGE)
                sys.exit(2)
            name, cast = FLAG_SPEC[key]
            opts[name] = cast(val)
            i += 1
            continue
        # --key value form
        if a in FLAG_SPEC:
            if i + 1 >= len(argv):
                sys.stderr.write(f"statusline: flag requires value: {a}\n")
                sys.exit(2)
            name, cast = FLAG_SPEC[a]
            opts[name] = cast(argv[i + 1])
            i += 2
            continue
        sys.stderr.write(f"statusline: unknown flag: {a}\n")
        sys.stderr.write(USAGE)
        sys.exit(2)
    known = _builtin_field_names()
    for cname, template in opts["custom_fields"].items():
        _validate_custom_template(cname, template, known)
    _validate_fields(opts["fields"], opts["custom_fields"], known)
    return opts


def dig(d: Any, *keys: str) -> Any:
    """Walk nested dicts; return None on any miss."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def human_tokens(n: int) -> str:
    """Format a token count compactly: 1234 / 1.2k / 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.1f}k"
    return f"{n/1_000_000:.1f}M"


def human_duration(seconds: int) -> str:
    if seconds < 0:
        return "❓"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m:{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h:{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d:{(seconds % 86400) // 3600:02d}h"


def git_info(project_dir: str, run=subprocess.run) -> Tuple[Optional[str], Optional[int]]:
    """Return (branch, changes_count). Both None if not a git repo / git missing."""
    base = ["git", "-C", project_dir, "--no-optional-locks"]
    try:
        r = run(base + ["rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, check=False)
        if r.returncode != 0:
            return None, None
        branch = run(base + ["rev-parse", "--abbrev-ref", "HEAD"],
                      capture_output=True, text=True, check=False).stdout.strip()
        status = run(base + ["status", "--porcelain", "--branch"],
                      capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        return None, None
    changes = sum(1 for line in status.splitlines() if line and not line.startswith("##"))
    return branch or None, changes


def _warn_token(value: float, warn: float, crit: float, opts: Dict[str, Any],
                inverted: bool = False) -> str:
    """Return warn/crit indicator string. Crit takes precedence."""
    if inverted:
        if value < crit:
            return opts["crit_str"]
        if value < warn:
            return opts["warn_str"]
    else:
        if value >= crit:
            return opts["crit_str"]
        if value >= warn:
            return opts["warn_str"]
    return ""


def compute_raw(status: Dict[str, Any], opts: Dict[str, Any],
                now: Optional[int] = None,
                git_fn: Callable[[str], Tuple[Optional[str], Optional[int]]] = git_info
                ) -> Dict[str, Any]:
    """Compute all raw-valued (typed) fields. None = unavailable."""
    p: Dict[str, Any] = {k: None for k in RAW_FIELDS}

    used_pct = dig(status, "context_window", "used_percentage")
    win = dig(status, "context_window", "context_window_size")
    if used_pct is not None and win is not None:
        p["ctx_pct"] = float(used_pct)
        p["ctx_tokens_k"] = int(float(used_pct) * float(win) / 100 / 1000)
        p["ctx_warning"] = _warn_token(float(p["ctx_tokens_k"]),
                                        opts["ctx_warn"], opts["ctx_crit"], opts)

    fh = dig(status, "rate_limits", "five_hour", "used_percentage")
    wk = dig(status, "rate_limits", "seven_day", "used_percentage")
    if now is None:
        now = int(time.time())
    if fh is not None:
        p["limit_5h_pct"] = float(fh)
        fh_reset = dig(status, "rate_limits", "five_hour", "resets_at") or 0
        p["limit_5h_reset_sec"] = int(fh_reset) - now
        p["limit_5h_reset"] = human_duration(p["limit_5h_reset_sec"])
        p["limit_5h_warning"] = _warn_token(float(fh),
                                             opts["limits_5h_warn"],
                                             opts["limits_5h_crit"], opts)
    if wk is not None:
        p["limit_week_pct"] = float(wk)
        wk_reset = dig(status, "rate_limits", "seven_day", "resets_at") or 0
        p["limit_week_reset_sec"] = int(wk_reset) - now
        p["limit_week_reset"] = human_duration(p["limit_week_reset_sec"])
        p["limit_week_warning"] = _warn_token(float(wk),
                                               opts["limits_week_warn"],
                                               opts["limits_week_crit"], opts)

    sc = dig(status, "cost", "total_cost_usd")
    if sc is not None:
        p["session_cost_usd"] = float(sc)

    mid = dig(status, "model", "id")
    cu = dig(status, "context_window", "current_usage") or {}
    needed = ("input_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens", "output_tokens")
    if mid and all(cu.get(k) is not None for k in needed):
        prices = MODEL_PRICES.get(mid)
        if prices is not None:
            pi, po = prices
            val = (cu["input_tokens"] * pi
                   + cu["cache_creation_input_tokens"] * pi * 1.25
                   + cu["cache_read_input_tokens"] * pi * 0.1
                   + cu["output_tokens"] * po) / 1_000_000
            p["turn_cost_usd"] = val

    cr = cu.get("cache_read_input_tokens")
    cc = cu.get("cache_creation_input_tokens")
    ci = cu.get("input_tokens")
    if cr is not None and cc is not None and ci is not None:
        total = cr + cc + ci
        if total > 0:
            pct = cr * 100 / total
            p["cache_hit_pct"] = pct
            p["cache_hit_warning"] = _warn_token(pct, opts["cache_warn"],
                                                  opts["cache_crit"], opts,
                                                  inverted=True)

    sin = dig(status, "context_window", "total_input_tokens")
    sout = dig(status, "context_window", "total_output_tokens")
    if sin is not None:
        p["session_input_tokens"] = int(sin)
    if sout is not None:
        p["session_output_tokens"] = int(sout)
    tin = cu.get("input_tokens")
    tout = cu.get("output_tokens")
    tcc = cu.get("cache_creation_input_tokens")
    tcr = cu.get("cache_read_input_tokens")
    if tin is not None:
        p["turn_input_tokens"] = int(tin)
    if tout is not None:
        p["turn_output_tokens"] = int(tout)
    if tcc is not None:
        p["turn_cache_creation_tokens"] = int(tcc)
    if tcr is not None:
        p["turn_cache_read_tokens"] = int(tcr)

    a = dig(status, "cost", "total_lines_added")
    r = dig(status, "cost", "total_lines_removed")
    if a is not None:
        p["lines_added"] = int(a)
    if r is not None:
        p["lines_removed"] = int(r)
    if a is not None or r is not None:
        p["lines_changed"] = int(a or 0) + int(r or 0)

    pd = dig(status, "workspace", "project_dir")
    if pd:
        branch, changes = git_fn(pd)
        p["git_branch"] = branch
        p["git_changes"] = changes

    return p


# --- Built-in composite rendering ---

def _composite_ctx(raw: Dict[str, Any]) -> str:
    if raw["ctx_tokens_k"] is None or raw["ctx_pct"] is None:
        return ""
    return f"ctx: {raw['ctx_warning']}~{raw['ctx_tokens_k']}k ({int(raw['ctx_pct'])}%)"


def _composite_limits(raw: Dict[str, Any], opts: Dict[str, Any]) -> str:
    if raw["limit_5h_pct"] is None or raw["limit_week_pct"] is None:
        return ""
    crit = opts["crit_str"]
    warn = opts["warn_str"]
    token = ""
    if raw["limit_5h_warning"] == crit or raw["limit_week_warning"] == crit:
        token = crit
    elif raw["limit_5h_warning"] == warn or raw["limit_week_warning"] == warn:
        token = warn
    return (f"lmt: {token}{int(raw['limit_5h_pct'])}%/{int(raw['limit_week_pct'])}% "
            f"({raw['limit_5h_reset']}/{raw['limit_week_reset']})")


def _composite_cache_hit(raw: Dict[str, Any]) -> str:
    if raw["cache_hit_pct"] is None:
        return ""
    return f"c↑: {raw['cache_hit_warning']}{raw['cache_hit_pct']:.2f}%"


def _composite_git(raw: Dict[str, Any]) -> str:
    if not raw["git_branch"]:
        return ""
    n = raw["git_changes"] or 0
    if n == 0:
        return raw["git_branch"]
    label = "1 change" if n == 1 else f"{n} changes"
    return f"{raw['git_branch']} ({label})"


def composite_value(name: str, status: Dict[str, Any], opts: Dict[str, Any],
                    raw: Dict[str, Any]) -> str:
    """Render a built-in composite by name. Returns '' when data unavailable."""
    g = lambda *k: dig(status, *k)
    if name == "cwd":
        pd = dig(status, "workspace", "project_dir") or ""
        home = os.path.expanduser("~")
        if pd == home:
            return "~"
        if pd.startswith(home + "/"):
            return "~" + pd[len(home):]
        return pd
    if name == "git":
        return _composite_git(raw)
    if name == "model":
        return g("model", "display_name") or g("model", "id") or ""
    if name == "ctx":
        return _composite_ctx(raw)
    if name == "session_cost":
        v = raw["session_cost_usd"]
        return f"${v:.{COST_PRECISION}f}" if v is not None else ""
    if name == "turn_cost":
        v = raw["turn_cost_usd"]
        return f"${v:.{COST_PRECISION}f}" if v is not None else ""
    if name == "limits":
        return _composite_limits(raw, opts)
    if name == "cache_hit":
        return _composite_cache_hit(raw)
    if name == "session":
        return g("session_name") or "UNNAMED"
    if name == "session_id":
        return g("session_id") or ""
    if name == "effort":
        return g("effort", "level") or ""
    if name == "version":
        v = g("version")
        return f"v{v}" if v else ""
    if name == "agent":
        v = g("agent", "name")
        return f"@{v}" if v else ""
    if name == "worktree":
        v = g("worktree", "name")
        return f"wt:{v}" if v else ""
    if name == "transcript_path":
        return g("transcript_path") or ""
    if name == "api_duration":
        ms = g("cost", "total_api_duration_ms")
        return f"api:{human_duration(int(ms) // 1000)}" if ms is not None else ""
    if name == "duration":
        ms = g("cost", "total_duration_ms")
        return f"dur:{human_duration(int(ms) // 1000)}" if ms is not None else ""
    if name == "changes":
        v = raw["lines_changed"]
        return f"Δ{v}" if v is not None else ""
    if name == "added":
        v = raw["lines_added"]
        return f"+{v}" if v is not None else ""
    if name == "removed":
        v = raw["lines_removed"]
        return f"-{v}" if v is not None else ""
    if name == "session_tokens_in":
        v = raw["session_input_tokens"]
        return f"↑{human_tokens(v)}" if v is not None else ""
    if name == "session_tokens_out":
        v = raw["session_output_tokens"]
        return f"↓{human_tokens(v)}" if v is not None else ""
    if name == "turn_tokens_in":
        v = raw["turn_input_tokens"]
        return f"↑{human_tokens(v)}" if v is not None else ""
    if name == "turn_tokens_out":
        v = raw["turn_output_tokens"]
        return f"↓{human_tokens(v)}" if v is not None else ""
    if name == "turn_cache_write":
        v = raw["turn_cache_creation_tokens"]
        return f"✎ {human_tokens(v)}" if v is not None else ""
    if name == "turn_cache_read":
        v = raw["turn_cache_read_tokens"]
        return f"👁 {human_tokens(v)}" if v is not None else ""
    raise KeyError(name)


# --- Custom field template rendering (sandboxed) ---

_FORMATTER = string.Formatter()
_ALLOWED_CONVERSIONS = (None, "s", "r", "a")


def _parse_template(name: str, template: str) -> Optional[List[Tuple[str, Optional[str], Optional[str], Optional[str]]]]:
    """Parse and validate a template. Returns parsed segments or None on error.
    Errors are written to stderr.
    """
    try:
        parsed = list(_FORMATTER.parse(template))
    except ValueError as e:
        sys.stderr.write(f"statusline: invalid custom template {name!r}: {e}\n")
        return None
    for _literal, field, spec, conv in parsed:
        if field is None:
            continue
        if field == "" or not field.isidentifier():
            sys.stderr.write(
                f"statusline: invalid field reference {field!r} in custom {name!r} "
                "(only bare field names allowed)\n")
            return None
        if spec and ("{" in spec or "}" in spec):
            sys.stderr.write(
                f"statusline: nested replacement fields not allowed in custom {name!r}\n")
            return None
        if conv not in _ALLOWED_CONVERSIONS:
            sys.stderr.write(
                f"statusline: invalid conversion !{conv} in custom {name!r}\n")
            return None
    return parsed


def render_custom(name: str, template: str, values: Dict[str, Any]) -> str:
    """Render a custom template against the unified field-values dict.

    Returns "" to skip (any referenced field is unavailable, or the template
    has an invalid reference). Only bare-identifier field references are
    allowed (no attribute access, indexing, or nested replacement fields).
    """
    parsed = _parse_template(name, template)
    if parsed is None:
        sys.exit(2)
    refs: List[str] = []
    for _literal, field, _spec, _conv in parsed:
        if field is None:
            continue
        if field not in values:
            _die(f"unknown field {field!r} in custom {name!r}")
        refs.append(field)

    # Skip whole field if any referenced value is unavailable (None or "").
    for r in refs:
        v = values[r]
        if v is None or v == "":
            return ""

    out: List[str] = []
    for literal, field, spec, conv in parsed:
        if literal:
            out.append(literal)
        if field is None:
            continue
        v = values[field]
        if conv == "s":
            v = str(v)
        elif conv == "r":
            v = repr(v)
        elif conv == "a":
            v = ascii(v)
        try:
            out.append(format(v, spec or ""))
        except (ValueError, TypeError) as e:
            _die(f"format error for {field!r} in custom {name!r}: {e}")
    return "".join(out)


def render(status: Dict[str, Any], opts: Dict[str, Any],
           now: Optional[int] = None,
           git_fn: Callable[[str], Tuple[Optional[str], Optional[int]]] = git_info) -> str:
    pd = dig(status, "workspace", "project_dir")
    if not pd:
        sys.stderr.write("No workspace project_dir found in current_status JSON\n")
        sys.exit(1)

    raw = compute_raw(status, opts, now=now, git_fn=git_fn)
    values: Dict[str, Any] = dict(raw)
    for name in COMPOSITE_FIELDS:
        values[name] = composite_value(name, status, opts, raw)

    customs: Dict[str, str] = opts.get("custom_fields", {})

    parts: List[str] = []
    for f in (x.strip() for x in opts["fields"].split(",")):
        if not f:
            continue
        if f.startswith("custom:"):
            cname = f[len("custom:"):]
            if cname not in customs:
                _die(f"undefined custom field: {cname}")
            v = render_custom(cname, customs[cname], values)
        elif f in values:
            raw_v = values[f]
            v = "" if raw_v is None else str(raw_v)
        else:
            _die(f"unknown field: {f}")
        if v:
            parts.append(v)
    return opts["separator"].join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    opts = parse_args(argv)
    raw = sys.stdin.read()
    try:
        status = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"statusline: invalid JSON on stdin: {e}\n")
        return 1
    line = render(status, opts)
    sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
