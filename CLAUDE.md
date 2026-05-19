# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code statusline: a single bash script (`statusline.sh`) that reads Claude Code's status JSON from stdin and prints one line to stdout. Wired in via `~/.claude/settings.json` under `.statusLine`.

Status JSON schema reference: https://code.claude.com/docs/en/statusline#available-data
Sample input lives in `statusline_input_example.json`.

## Files

- `statusline.sh` — the script. Pure bash + `jq` + `awk`/`git`/`date`. No build step.
- `install.sh` — installer. By default curls `statusline.sh` from the GitHub raw URL on `main`; `--local` copies the sibling file. Patches `~/.claude/settings.json` `.statusLine` (prompts on diff when a block already exists). Aborts rather than overwriting a differing destination script.
- `statusline_input_example.json` — example stdin payload, useful for manual testing.

## Architecture notes

- Field-driven rendering: each token in `--fields` (default `cwd,git,model,ctx,cost,rate`) maps to a case branch in `field_value()` that prints its already-computed display string. To add a field: compute its display string above, add the case, and document it in the `usage()` `FIELDS:` block.
- Empty field outputs are skipped (no dangling separators).
- Thresholds (`--ctx-warn/crit`, `--rate-{5h,week}-{warn,crit}`) gate `WARN_STR`/`CRIT_STR` prefixes. Comparisons use `awk` for float safety.
- `--debug PATH` self-re-execs with `set -xv` and stderr appended to PATH; the `STATUSLINE_DEBUG_REENTRY` env var prevents recursion.
- Git info uses `--no-optional-locks` to avoid contending with concurrent git operations in the user's session.

## Testing

No test suite. Pipe the example through the script:

```
./statusline.sh < statusline_input_example.json
./statusline.sh --fields cwd,git,ctx,cost --separator ' • ' < statusline_input_example.json
```

For runtime issues in a live Claude Code session, install with `--debug /tmp/statusline.log` appended to the command in `~/.claude/settings.json` and tail the log.

## Install / iterate

- Local dev install: `./install.sh --local`
- Pull canonical from GitHub: `./install.sh`
- Custom destination: `./install.sh --path ~/somewhere/statusline.sh`
