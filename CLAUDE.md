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

- **Two-namespace model.** Built-in fields (raw + composite) share one namespace; custom fields live in their own. In `--fields`, a bare name resolves against built-ins, `custom:X` resolves against customs — so a custom may share a name with a built-in without conflict. Inside a template, `{name}` always resolves against built-ins; templates cannot reference other custom fields. This is intentional — no cycle detection is needed and `string.Formatter` parses `:` as the format-spec separator so a `{custom:X}` syntax inside `{...}` isn't viable anyway.
- Field storage. At render time built-ins are merged into a single `values: Dict[str, Any]` (raw values stay typed; composites become strings). Customs are rendered lazily as the `--fields` walk encounters them — they are never inserted into `values`. The `COMPOSITE_FIELDS` and `RAW_FIELDS` tuples drive `--help` output ordering and tell `render()` which producer fills each built-in entry.
- Producers:
  - `compute_raw()` computes typed raw fields (`ctx_pct`, `limit_5h_pct`, `limit_5h_warning`, `git_branch`, …) — value is `None` when source data is unavailable.
  - `composite_value(name, status, opts, raw)` renders one built-in composite to a string (`""` when data unavailable). Composites consume raw values where possible.
  - `render_custom(name, template, values)` renders one custom template against the built-in values dict.
- Render flow (`render()`): compute raw → populate `values` with raw → fill in each `COMPOSITE_FIELDS` entry → walk `--fields`. For each token: `custom:X` → look up `opts["custom_fields"][X]`, render via `render_custom`; bare name → look up in `values`; else stderr "unknown field" and skip. Empty outputs are skipped (no dangling separators).
- **Custom fields** (`--custom-field NAME=TEMPLATE`, repeatable; referenced in `--fields` as `custom:NAME`): skip-when-unavailable applies if any referenced built-in is `None` OR empty string. The custom field is dropped entirely (with its separator).
- **Sandboxed template rendering** (`render_custom` + `_parse_template`): walks `string.Formatter().parse()` manually — we do NOT call `str.format` / `format_map` (those allow `{x.__class__}` attribute traversal). Field names must be bare identifiers; `format_spec` may not contain nested `{}`; only `!s`/`!r`/`!a` conversions are allowed.
- To add a new raw field: extend `RAW_FIELDS` + `compute_raw()` (initialize key to `None`, populate when data available), document in `USAGE` + README Raw table. Automatically usable as a standalone field and inside custom templates.
- To add a new composite: extend `COMPOSITE_FIELDS` + add a branch in `composite_value()`, document in `USAGE` + README Composite list.
- CLI accepts both `--key value` and `--key=value` (see `FLAG_SPEC`). `--custom-field` is special-cased in `parse_args()` because its value is `NAME=TEMPLATE` (contains `=`).
- Thresholds (`--ctx-warn/crit`, `--limits-{5h,week}-{warn,crit}`, `--cache-warn/crit`) drive the `*_warning` raw fields via `_warn_token()`. `--cache-*` is inverted (low hit ratio is bad). Cost decimal places are hard-coded to `COST_PRECISION = 3` in the script; users wanting different precision should define a `--custom-field` over `session_cost_usd` / `turn_cost_usd`.
- Per-model pricing lives in `MODEL_PRICES` near the top of the script, sourced from https://platform.claude.com/docs/en/about-claude/pricing. `turn_cost_usd` applies the prompt-cache multipliers (5m write = 1.25×, read = 0.1×) from https://platform.claude.com/docs/en/build-with-claude/prompt-caching. Claude Code always writes at 5m TTL — 1h is not used. Unknown model id → `None` (composite renders empty).
- Git info: `git_info()` returns `Tuple[Optional[str], Optional[int]]` of `(branch, changes)`. Shells out via `subprocess.run` with `--no-optional-locks` to avoid contending with concurrent git operations in the user's session. Injectable via the `run`/`git_fn` parameters for testing.

## Testing

```
python3 -m unittest test_statusline
./statusline.py < statusline_input_example.json
./statusline.py --fields=cwd,git,ctx,session_cost --separator=' • ' < statusline_input_example.json
```

## Install / iterate

- Local dev install: `./install.sh --local`
- Pull canonical from GitHub: `./install.sh`
- Custom destination: `./install.sh --path ~/somewhere/statusline.py`
