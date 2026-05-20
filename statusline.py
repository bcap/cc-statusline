#!/usr/bin/env python3
# cc-statusline (https://github.com/bcap/cc-statusline)
"""Claude Code statusline: reads status JSON from stdin, prints one line."""

import json
import os
import subprocess
import sys
import time
from typing import Any, List, Optional, Tuple

# --- Defaults ---
DEFAULTS = {
    "ctx_warn": 150,
    "ctx_crit": 200,
    "limits_5h_warn": 75,
    "limits_5h_crit": 100,
    "limits_week_warn": 75,
    "limits_week_crit": 100,
    "cache_warn": 80,
    "cache_crit": 50,
    "fields": "cwd,git,model,ctx,sessioncost,limits",
    "separator": " | ",
    "cost_precision": 3,
    "warn_str": "⚠️",  # warning sign
    "crit_str": "\U0001f525",   # fire
    "debug": "",
}

# Per-model pricing in USD per million tokens (input, output).
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# Cache write 5m = 1.25x input, cache read = 0.1x input.
MODEL_PRICES = {
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

FLAG_SPEC = {
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
    "--cost-precision": ("cost_precision", int),
    "--warn-str": ("warn_str", str),
    "--crit-str": ("crit_str", str),
    "--debug": ("debug", str),
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
  --separator STR       field separator [{separator!r}]
  --cost-precision N    cost decimal places [{cost_precision}]
  --warn-str STR        warn indicator
  --crit-str STR        critical indicator
  --debug PATH          append a trace of this execution to PATH
  -h, --help            show this help and exit

FIELDS:
  cwd, git, model, ctx, sessioncost, turncost, limits, session, session_id,
  effort, version, agent, worktree, transcript_path, api_duration, duration,
  changes, added, removed, cachehit
""".format(**DEFAULTS)


def parse_args(argv: List[str]) -> dict:
    opts = dict(DEFAULTS)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            sys.stdout.write(USAGE)
            sys.exit(0)
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
    return opts


def dig(d: Any, *keys: str) -> Any:
    """Walk nested dicts; return None on any miss."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def human_duration(seconds: int) -> str:
    if seconds < 0:
        return "❓"  # ❓
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m:{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h:{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d:{(seconds % 86400) // 3600:02d}h"


def git_info(project_dir: str, run=subprocess.run) -> str:
    base = ["git", "-C", project_dir, "--no-optional-locks"]
    try:
        r = run(base + ["rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, check=False)
        if r.returncode != 0:
            return ""
        branch = run(base + ["rev-parse", "--abbrev-ref", "HEAD"],
                      capture_output=True, text=True, check=False).stdout.strip()
        status = run(base + ["status", "--porcelain", "--branch"],
                      capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        return ""
    changes = sum(1 for line in status.splitlines() if line and not line.startswith("##"))
    if changes == 0:
        return branch
    label = "1 change" if changes == 1 else f"{changes} changes"
    return f"{branch} ({label})"


def compute_ctx(status: dict, opts: dict) -> str:
    used_pct = dig(status, "context_window", "used_percentage")
    win = dig(status, "context_window", "context_window_size")
    if used_pct is None:
        return ""
    if win is None:
        return ""
    tokens_k = int(float(used_pct) * float(win) / 100 / 1000)
    base = f"~{tokens_k}k ({int(used_pct)}%)"
    if tokens_k >= opts["ctx_crit"]:
        return f"{opts['crit_str']}{base}"
    if tokens_k >= opts["ctx_warn"]:
        return f"{opts['warn_str']}{base}"
    return base


def compute_sessioncost(status: dict, opts: dict) -> str:
    cost = dig(status, "cost", "total_cost_usd")
    if cost is None:
        return ""
    return f"{float(cost):.{opts['cost_precision']}f}"


def compute_turncost(status: dict, opts: dict) -> str:
    mid = dig(status, "model", "id")
    cu = dig(status, "context_window", "current_usage") or {}
    needed = ("input_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens", "output_tokens")
    if not mid or any(cu.get(k) is None for k in needed):
        return ""
    prices = MODEL_PRICES.get(mid)
    if not prices:
        return ""
    pi, po = prices
    i = cu["input_tokens"]
    cc = cu["cache_creation_input_tokens"]
    cr = cu["cache_read_input_tokens"]
    o = cu["output_tokens"]
    val = (i * pi + cc * pi * 1.25 + cr * pi * 0.1 + o * po) / 1_000_000
    return f"{val:.{opts['cost_precision']}f}"


def compute_cachehit(status: dict, opts: dict) -> str:
    cu = dig(status, "context_window", "current_usage") or {}
    cr = cu.get("cache_read_input_tokens")
    cc = cu.get("cache_creation_input_tokens")
    ci = cu.get("input_tokens")
    if cr is None or cc is None or ci is None:
        return ""
    total = cr + cc + ci
    if total <= 0:
        return ""
    pct = cr * 100 / total
    base = f"{pct:.2f}%"
    if pct < opts["cache_crit"]:
        return f"{opts['crit_str']}{base}"
    if pct < opts["cache_warn"]:
        return f"{opts['warn_str']}{base}"
    return base


def compute_limits(status: dict, opts: dict, now: Optional[int] = None) -> str:
    fh = dig(status, "rate_limits", "five_hour", "used_percentage")
    wk = dig(status, "rate_limits", "seven_day", "used_percentage")
    if fh is None or wk is None:
        return ""
    fh_reset = dig(status, "rate_limits", "five_hour", "resets_at") or 0
    wk_reset = dig(status, "rate_limits", "seven_day", "resets_at") or 0
    if now is None:
        now = int(time.time())
    fh_left = human_duration(int(fh_reset) - now)
    wk_left = human_duration(int(wk_reset) - now)
    token = ""
    if fh >= opts["limits_5h_crit"] or wk >= opts["limits_week_crit"]:
        token = opts["crit_str"]
    elif fh >= opts["limits_5h_warn"] or wk >= opts["limits_week_warn"]:
        token = opts["warn_str"]
    return f"{token}{int(fh)}%/{int(wk)}% ({fh_left}/{wk_left})"


def field_value(name: str, status: dict, opts: dict, derived: dict) -> Optional[str]:
    """Return rendered field, or None for unknown field, or '' to skip."""
    g = lambda *k: dig(status, *k)

    def nonempty(s):
        return s if s else ""

    if name == "cwd":
        pd = derived["project_dir"]
        home = os.path.expanduser("~")
        if pd == home:
            return "~"
        if pd.startswith(home + "/"):
            return "~" + pd[len(home):]
        return pd
    if name == "git":
        return nonempty(derived["git_info"])
    if name == "model":
        return nonempty(g("model", "display_name") or g("model", "id") or "")
    if name == "ctx":
        v = derived["ctx_display"]
        return f"ctx: {v}" if v else ""
    if name == "sessioncost":
        v = derived["cost_display"]
        return f"${v}" if v else ""
    if name == "turncost":
        v = derived["turncost_display"]
        return f"${v}" if v else ""
    if name == "limits":
        v = derived["limits_display"]
        return f"lmt: {v}" if v else ""
    if name == "session":
        return g("session_name") or "UNNAMED"
    if name == "session_id":
        return nonempty(g("session_id") or "")
    if name == "effort":
        return nonempty(g("effort", "level") or "")
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
        return nonempty(g("transcript_path") or "")
    if name == "api_duration":
        ms = g("cost", "total_api_duration_ms")
        return f"api:{human_duration(int(ms) // 1000)}" if ms is not None else ""
    if name == "duration":
        ms = g("cost", "total_duration_ms")
        return f"dur:{human_duration(int(ms) // 1000)}" if ms is not None else ""
    if name == "changes":
        a = g("cost", "total_lines_added")
        r = g("cost", "total_lines_removed")
        if a is None and r is None:
            return ""
        return f"Δ{int(a or 0) + int(r or 0)}"
    if name == "added":
        a = g("cost", "total_lines_added")
        return f"+{a}" if a is not None else ""
    if name == "removed":
        r = g("cost", "total_lines_removed")
        return f"-{r}" if r is not None else ""
    if name == "cachehit":
        v = derived["cache_display"]
        return f"c↑: {v}" if v else ""
    return None


def render(status: dict, opts: dict, now: Optional[int] = None,
           git_fn=git_info) -> str:
    pd = dig(status, "workspace", "project_dir")
    if not pd:
        sys.stderr.write("No workspace project_dir found in current_status JSON\n")
        sys.exit(1)
    derived = {
        "project_dir": pd,
        "git_info": git_fn(pd),
        "ctx_display": compute_ctx(status, opts),
        "cost_display": compute_sessioncost(status, opts),
        "turncost_display": compute_turncost(status, opts),
        "cache_display": compute_cachehit(status, opts),
        "limits_display": compute_limits(status, opts, now=now),
    }
    parts: List[str] = []
    for f in (x.strip() for x in opts["fields"].split(",")):
        if not f:
            continue
        v = field_value(f, status, opts, derived)
        if v is None:
            sys.stderr.write(f"statusline: unknown field: {f}\n")
            continue
        if v:
            parts.append(v)
    return opts["separator"].join(parts)


def debug_reexec(opts: dict, argv: List[str]) -> None:
    path = opts["debug"]
    if not path or os.environ.get("STATUSLINE_DEBUG_REENTRY"):
        return
    with open(path, "a") as f:
        f.write("\n=============================\n")
        f.write(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n")
        f.write("=============================\n")
    env = dict(os.environ, STATUSLINE_DEBUG_REENTRY="1")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.dup2(fd, 2)
    os.close(fd)
    # Re-exec under python tracing
    os.execvpe(sys.executable, [sys.executable, "-v", __file__] + argv, env)


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    opts = parse_args(argv)
    if opts["debug"]:
        debug_reexec(opts, argv)
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
