# claude code statusline

A custom statusline for [Claude Code](https://claude.com/claude-code). Shows your working directory, git branch, model, context usage, session cost, and rate-limit status on a single line.

Example:

![statusline screenshot](screenshot.png)

(Above ran with `--ctx-warn 40 --ctx-crit 50` to demonstrate the warn indicator.)

## Requirements

- `bash`, `jq`, `git`, `awk`, `date` (standard on Linux/macOS)
- `curl` (only for the default remote install)

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

- Writes `statusline.sh` to `~/.claude/statusline.sh` (override with `--path`)
- Adds a `statusLine` block to `~/.claude/settings.json`
- Refuses to overwrite an existing different `statusline.sh`; shows a diff and prompts before changing an existing `statusLine` block
- Is safe to re-run: identical content is a no-op

Once installed, your statusline should update on the next refresh. If it doesn't, restart Claude Code.

## Uninstall

Remove `~/.claude/statusline.sh` and delete the `statusLine` key from `~/.claude/settings.json`.

## Configuration

Pass flags to `statusline.sh` in your `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh --fields cwd,git,model,ctx,cost --separator ' • '",
    "refreshInterval": 5
  }
}
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--fields LIST` | `cwd,git,model,ctx,cost,rate` | Comma-list of fields; order = display order |
| `--separator STR` | ` \| ` | Separator between fields |
| `--cost-precision N` | `3` | Decimal places for cost |
| `--ctx-warn K` | `150` | Context warn threshold (k-tokens) |
| `--ctx-crit K` | `200` | Context critical threshold (k-tokens) |
| `--rate-5h-warn P` | `75` | 5-hour rate-limit warn % |
| `--rate-5h-crit P` | `100` | 5-hour rate-limit critical % |
| `--rate-week-warn P` | `75` | Weekly rate-limit warn % |
| `--rate-week-crit P` | `100` | Weekly rate-limit critical % |
| `--warn-str STR` | `⚠️` | Warn indicator prefix |
| `--crit-str STR` | `🔥` | Critical indicator prefix |
| `--debug PATH` | — | Append a trace of each invocation to PATH |
| `-h`, `--help` | — | Show help |

### Available fields

Default-on:

- `cwd` — current working directory (`$HOME` shown as `~`)
- `git` — branch + change count, e.g. `main (3 changes)`
- `model` — model display name, e.g. `Opus 4.7`
- `ctx` — context usage %, k-tokens; warn/crit indicator over threshold
- `cost` — session cost in USD
- `rate` — 5h/weekly rate-limit %s + reset countdowns; warn/crit indicator over threshold

Opt-in (add to `--fields`):

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
   ./statusline.sh < statusline_input_example.json
   ```
2. Enable tracing by adding `--debug /tmp/statusline.log` to the `command` in `settings.json`, then `tail -f /tmp/statusline.log`.
3. Confirm `jq` is installed and on `PATH`.

## License

MIT
