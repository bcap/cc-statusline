# claude code statusline

A custom statusline for [Claude Code](https://claude.com/claude-code). By default it shows your working directory, git branch, model, context usage, total session cost, and rate-limit status on a single line. [More fields available](#available-fields), configurable via flags.

Example:

![statusline screenshot](screenshot.png)

(Above ran with `--ctx-warn 40 --ctx-crit 50` to demonstrate the warn indicator.)

## Requirements

- `python3` (3.8+) and `git` (standard on Linux/macOS)
- `bash`, `jq`, `curl` (only used by `install.sh`)

## Install

One-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/bcap/cc-statusline/main/install.sh | bash
```

Or clone and install from a local copy:

```bash
git clone https://github.com/bcap/cc-statusline.git
cd cc-statusline
./install.sh --local
```

The installer:

- Writes `statusline.py` to `~/.claude/statusline.py` (override with `--path`)
- Adds a `statusLine` block to `~/.claude/settings.json`
- Refuses to overwrite an existing different `statusline.py`; shows a diff and prompts before changing an existing `statusLine` block
- Is safe to re-run: identical content is a no-op

Once installed, your statusline should update on the next refresh. If it doesn't, restart Claude Code.

## Uninstall

Remove `~/.claude/statusline.py` and delete the `statusLine` key from `~/.claude/settings.json`.

## Configuration

After installing, you can pass flags to `statusline.py` in your `~/.claude/settings.json`. Flags accept both `--key value` and `--key=value` forms.

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.py --fields=cwd,git,model,ctx,sessioncost --separator=' • '",
    "refreshInterval": 5
  }
}
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--fields LIST` | `cwd,git,model,ctx,sessioncost,limits` | Comma-list of fields; order = display order |
| `--separator STR` | ` \| ` | Separator between fields |
| `--cost-precision N` | `3` | Decimal places for cost |
| `--ctx-warn K` | `150` | Context warn threshold (k-tokens) |
| `--ctx-crit K` | `200` | Context critical threshold (k-tokens) |
| `--limits-5h-warn P` | `75` | 5-hour rate-limit warn % |
| `--limits-5h-crit P` | `100` | 5-hour rate-limit critical % |
| `--limits-week-warn P` | `75` | Weekly rate-limit warn % |
| `--limits-week-crit P` | `100` | Weekly rate-limit critical % |
| `--cache-warn P` | `80` | Cache hit ratio warn % (warn when below) |
| `--cache-crit P` | `50` | Cache hit ratio critical % (crit when below) |
| `--warn-str STR` | `⚠️` | Warn indicator prefix |
| `--crit-str STR` | `🔥` | Critical indicator prefix |
| `-h`, `--help` | — | Show help |

### Available fields

Default-on:

- `cwd` — current working directory (`$HOME` shown as `~`)
- `git` — branch + change count, e.g. `main (3 changes)`
- `model` — model display name, e.g. `Opus 4.7`
- `ctx` — context usage %, k-tokens; warn/crit indicator over threshold
- `sessioncost` — total session cost in USD
- `limits` — 5h/weekly rate-limit %s + reset countdowns; warn/crit indicator over threshold

Opt-in (add to `--fields`):

- `turncost` — estimated USD cost of the last API call, computed from `current_usage` tokens and per-model pricing (5m cache write multiplier). Empty before the first API call, after `/compact`, or for unknown models.
- `cachehit` — prompt cache hit ratio for the last API call, `cache_read / (cache_read + cache_creation + input)`. Warn/crit fire when **below** `--cache-warn` / `--cache-crit` (inverted direction; low is bad).
- `session` — session name (or `UNNAMED`)
- `session_id` — full session UUID
- `effort` — effort level (e.g. `high`)
- `version` — Claude Code version
- `agent` — active subagent name, prefixed with `@`
- `worktree` — worktree name, prefixed with `wt:`
- `transcript_path` — path to the session transcript JSONL
- `api_duration` — total API time this session
- `duration` — total wall-clock time this session
- `changes` — total lines added + removed, prefixed with `Δ`
- `added` — total lines added, prefixed with `+`
- `removed` — total lines removed, prefixed with `-`

## Troubleshooting

If the statusline doesn't appear or looks wrong:

1. Run it against the example payload:
   ```bash
   ./statusline.py < statusline_input_example.json
   ```
2. Run the test suite: `python3 -m unittest test_statusline`
3. Confirm `python3` is installed and on `PATH`.

## License

MIT
