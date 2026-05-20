# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code statusline: a single Python script (`statusline.py`) that reads Claude Code's status JSON from stdin and prints one line to stdout. Wired in via `~/.claude/settings.json` under `.statusLine`.

Status JSON schema reference: https://code.claude.com/docs/en/statusline#available-data
Sample input lives in `statusline_input_example.json`.

## Files

- `statusline.py` — the script. Python 3.8+, stdlib only (subprocess for `git`). No build step. Line 2 carries a `# cc-statusline` marker the installer uses to recognize prior installs; do not remove it.
- `test_statusline.py` — `unittest` suite. Run with `python3 -m unittest test_statusline`.
- `install.sh` — installer (bash). By default curls `statusline.py` from the GitHub raw URL on `main`; `--local` copies the sibling file. Patches `~/.claude/settings.json` `.statusLine` (prompts on diff when a block already exists). If a destination script already exists: identical → no-op; differs but contains the `# cc-statusline` marker → diff + prompt (upgrade); differs without the marker → abort (foreign file).
- `statusline_input_example.json` — example stdin payload, useful for manual testing.

## Architecture notes

- Field-driven rendering: each token in `--fields` (default `cwd,git,model,ctx,sessioncost,limits`) maps to a branch in `field_value()` returning its already-computed display string. To add a field: compute it in `render()`'s `derived` dict (if multi-step), add the case in `field_value()`, and document it in the `USAGE` `FIELDS:` block + README.
- Empty field outputs are skipped (no dangling separators); unknown field names print a warning to stderr but don't fail the line.
- CLI accepts both `--key value` and `--key=value` (see `FLAG_SPEC`).
- Thresholds (`--ctx-warn/crit`, `--limits-{5h,week}-{warn,crit}`, `--cache-warn/crit`) gate `warn_str`/`crit_str` prefixes. `--cache-*` is inverted (low hit ratio is bad).
- Per-model pricing lives in `MODEL_PRICES` near the top of the script, sourced from https://platform.claude.com/docs/en/about-claude/pricing. `turncost` applies the prompt-cache multipliers (5m write = 1.25×, read = 0.1×) from https://platform.claude.com/docs/en/build-with-claude/prompt-caching. Claude Code always writes at 5m TTL — 1h is not used. Unknown model id → empty turncost.
- Git info shells out via `subprocess.run` with `--no-optional-locks` to avoid contending with concurrent git operations in the user's session. Injectable via the `run`/`git_fn` parameters for testing.

## Testing

```
python3 -m unittest test_statusline
./statusline.py < statusline_input_example.json
./statusline.py --fields=cwd,git,ctx,sessioncost --separator=' • ' < statusline_input_example.json
```

## Install / iterate

- Local dev install: `./install.sh --local`
- Pull canonical from GitHub: `./install.sh`
- Custom destination: `./install.sh --path ~/somewhere/statusline.py`
