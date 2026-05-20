#!/usr/bin/env python3
"""Tests for statusline.py. Run: python3 -m unittest test_statusline"""

import io
import json
import os
import unittest
from unittest import mock

import statusline as sl


SAMPLE = {
    "session_id": "abc-123",
    "transcript_path": "/tmp/t.jsonl",
    "session_name": "demo",
    "effort": {"level": "high"},
    "model": {"id": "claude-opus-4-7", "display_name": "Opus 4.7"},
    "workspace": {"project_dir": "/home/u/proj"},
    "version": "2.1.139",
    "cost": {
        "total_cost_usd": 6.760513999999999,
        "total_duration_ms": 16856282,
        "total_api_duration_ms": 1463827,
        "total_lines_added": 604,
        "total_lines_removed": 82,
    },
    "context_window": {
        "context_window_size": 1000000,
        "current_usage": {
            "input_tokens": 1,
            "output_tokens": 1667,
            "cache_creation_input_tokens": 675,
            "cache_read_input_tokens": 78645,
        },
        "used_percentage": 8,
    },
    "rate_limits": {
        "five_hour": {"used_percentage": 6, "resets_at": 1000},
        "seven_day": {"used_percentage": 10, "resets_at": 5000},
    },
}


def opts(**overrides):
    o = dict(sl.DEFAULTS)
    o["custom_fields"] = {}
    o.update(overrides)
    return o


def _no_git(_pd):
    return (None, None)


def _fake_git(branch, changes):
    return lambda _pd: (branch, changes)


def values_for(status, **kw):
    """Build unified values dict (raw + composites). Test helper."""
    o = opts(**kw.pop("opts_overrides", {}))
    now = kw.pop("now", 0)
    git_fn = kw.pop("git_fn", _no_git)
    raw = sl.compute_raw(status, o, now=now, git_fn=git_fn)
    v = dict(raw)
    for n in sl.COMPOSITE_FIELDS:
        v[n] = sl.composite_value(n, status, o, raw)
    return v


def raw_for(status, **kw):
    o = opts(**kw.pop("opts_overrides", {}))
    now = kw.pop("now", 0)
    git_fn = kw.pop("git_fn", _no_git)
    return sl.compute_raw(status, o, now=now, git_fn=git_fn)


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        o = sl.parse_args([])
        expected = dict(sl.DEFAULTS)
        expected["custom_fields"] = {}
        self.assertEqual(o, expected)

    def test_space_form(self):
        o = sl.parse_args(["--ctx-warn", "42", "--separator", " / "])
        self.assertEqual(o["ctx_warn"], 42)
        self.assertEqual(o["separator"], " / ")

    def test_equals_form(self):
        o = sl.parse_args(["--ctx-warn=42", "--separator= / "])
        self.assertEqual(o["ctx_warn"], 42)
        self.assertEqual(o["separator"], " / ")

    def test_unknown_flag(self):
        with self.assertRaises(SystemExit) as cm:
            sl.parse_args(["--nope"])
        self.assertEqual(cm.exception.code, 2)

    def test_missing_value(self):
        with self.assertRaises(SystemExit):
            sl.parse_args(["--ctx-warn"])

    def test_help(self):
        with self.assertRaises(SystemExit) as cm:
            sl.parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_custom_field_space_form(self):
        o = sl.parse_args(["--custom-field", "foo={limit_5h_pct:.1f}%"])
        self.assertEqual(o["custom_fields"], {"foo": "{limit_5h_pct:.1f}%"})

    def test_custom_field_equals_form(self):
        o = sl.parse_args(["--custom-field=foo={limit_5h_pct:.1f}%"])
        self.assertEqual(o["custom_fields"], {"foo": "{limit_5h_pct:.1f}%"})

    def test_custom_field_repeatable(self):
        o = sl.parse_args([
            "--custom-field=a={ctx_pct}",
            "--custom-field=b={limit_5h_pct}",
        ])
        self.assertEqual(set(o["custom_fields"].keys()), {"a", "b"})

    def test_custom_field_template_with_commas(self):
        o = sl.parse_args(["--custom-field=x=a,b,c"])
        self.assertEqual(o["custom_fields"]["x"], "a,b,c")

    def test_custom_field_missing_equals(self):
        with self.assertRaises(SystemExit):
            sl.parse_args(["--custom-field=just_a_name"])

    def test_custom_field_invalid_name(self):
        with self.assertRaises(SystemExit):
            sl.parse_args(["--custom-field=bad-name={ctx_pct}"])

    def test_custom_field_name_may_match_builtin(self):
        # Custom fields live in their own namespace (referenced as custom:X
        # in --fields), so a name matching a built-in is allowed.
        o = sl.parse_args(["--custom-field=ctx=hello"])
        self.assertEqual(o["custom_fields"], {"ctx": "hello"})


class TestHumanDuration(unittest.TestCase):
    def test_negative(self):
        self.assertEqual(sl.human_duration(-1), "❓")

    def test_seconds(self):
        self.assertEqual(sl.human_duration(45), "45s")

    def test_minutes(self):
        self.assertEqual(sl.human_duration(125), "2m:05s")

    def test_hours(self):
        self.assertEqual(sl.human_duration(3600 + 7 * 60), "1h:07m")

    def test_days(self):
        self.assertEqual(sl.human_duration(86400 * 2 + 3 * 3600), "2d:03h")


class TestRawValues(unittest.TestCase):
    def test_ctx(self):
        r = raw_for(SAMPLE)
        self.assertEqual(r["ctx_pct"], 8.0)
        self.assertEqual(r["ctx_tokens_k"], 80)
        self.assertEqual(r["ctx_warning"], "")

    def test_ctx_warn(self):
        s = json.loads(json.dumps(SAMPLE))
        s["context_window"]["used_percentage"] = 16
        self.assertEqual(raw_for(s)["ctx_warning"], sl.DEFAULTS["warn_str"])

    def test_ctx_crit(self):
        s = json.loads(json.dumps(SAMPLE))
        s["context_window"]["used_percentage"] = 25
        self.assertEqual(raw_for(s)["ctx_warning"], sl.DEFAULTS["crit_str"])

    def test_ctx_missing(self):
        r = raw_for({"workspace": {"project_dir": "/x"}})
        self.assertIsNone(r["ctx_pct"])
        self.assertIsNone(r["ctx_tokens_k"])
        self.assertIsNone(r["ctx_warning"])

    def test_limits(self):
        r = raw_for(SAMPLE)
        self.assertEqual(r["limit_5h_pct"], 6.0)
        self.assertEqual(r["limit_week_pct"], 10.0)
        self.assertEqual(r["limit_5h_reset_sec"], 1000)
        self.assertEqual(r["limit_5h_reset"], "16m:40s")
        self.assertEqual(r["limit_week_reset"], "1h:23m")

    def test_limits_warn_crit(self):
        s = json.loads(json.dumps(SAMPLE))
        s["rate_limits"]["five_hour"]["used_percentage"] = 80
        s["rate_limits"]["seven_day"]["used_percentage"] = 100
        r = raw_for(s)
        self.assertEqual(r["limit_5h_warning"], sl.DEFAULTS["warn_str"])
        self.assertEqual(r["limit_week_warning"], sl.DEFAULTS["crit_str"])

    def test_session_cost(self):
        self.assertAlmostEqual(raw_for(SAMPLE)["session_cost_usd"], 6.760513999999999)

    def test_turn_cost(self):
        self.assertAlmostEqual(raw_for(SAMPLE)["turn_cost_usd"], 0.08522125)

    def test_turn_cost_unknown_model(self):
        s = json.loads(json.dumps(SAMPLE))
        s["model"]["id"] = "unknown-model"
        self.assertIsNone(raw_for(s)["turn_cost_usd"])

    def test_turn_cost_missing_usage(self):
        s = json.loads(json.dumps(SAMPLE))
        del s["context_window"]["current_usage"]["input_tokens"]
        self.assertIsNone(raw_for(s)["turn_cost_usd"])

    def test_cache_hit(self):
        r = raw_for(SAMPLE)
        self.assertAlmostEqual(r["cache_hit_pct"], 78645 * 100 / 79321, places=4)
        self.assertEqual(r["cache_hit_warning"], "")

    def test_cache_hit_zero_total(self):
        s = {"workspace": {"project_dir": "/x"},
             "context_window": {"current_usage": {
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                 "input_tokens": 0}}}
        self.assertIsNone(raw_for(s)["cache_hit_pct"])

    def test_lines(self):
        r = raw_for(SAMPLE)
        self.assertEqual(r["lines_added"], 604)
        self.assertEqual(r["lines_removed"], 82)
        self.assertEqual(r["lines_changed"], 686)

    def test_git(self):
        r = raw_for(SAMPLE, git_fn=_fake_git("main", 3))
        self.assertEqual(r["git_branch"], "main")
        self.assertEqual(r["git_changes"], 3)


class TestComposites(unittest.TestCase):
    def test_ctx(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("ctx", SAMPLE, opts(), r),
                         "ctx: ~80k (8%)")

    def test_ctx_warn(self):
        s = json.loads(json.dumps(SAMPLE))
        s["context_window"]["used_percentage"] = 16
        r = raw_for(s)
        out = sl.composite_value("ctx", s, opts(), r)
        self.assertTrue(out.startswith(f"ctx: {sl.DEFAULTS['warn_str']}"))

    def test_session_cost(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("session_cost", SAMPLE, opts(), r),
                         "$6.761")

    def test_turn_cost(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("turn_cost", SAMPLE, opts(), r),
                         "$0.085")

    def test_limits(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("limits", SAMPLE, opts(), r),
                         "lmt: 6%/10% (16m:40s/1h:23m)")

    def test_limits_warn(self):
        s = json.loads(json.dumps(SAMPLE))
        s["rate_limits"]["five_hour"]["used_percentage"] = 80
        r = raw_for(s)
        out = sl.composite_value("limits", s, opts(), r)
        self.assertTrue(out.startswith(f"lmt: {sl.DEFAULTS['warn_str']}"))

    def test_limits_crit_via_week(self):
        s = json.loads(json.dumps(SAMPLE))
        s["rate_limits"]["seven_day"]["used_percentage"] = 100
        r = raw_for(s)
        out = sl.composite_value("limits", s, opts(), r)
        self.assertTrue(out.startswith(f"lmt: {sl.DEFAULTS['crit_str']}"))

    def test_cache_hit(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("cache_hit", SAMPLE, opts(), r),
                         "c↑: 99.15%")

    def test_git(self):
        r = raw_for(SAMPLE, git_fn=_fake_git("main", 3))
        self.assertEqual(sl.composite_value("git", SAMPLE, opts(), r),
                         "main (3 changes)")

    def test_git_one_change(self):
        r = raw_for(SAMPLE, git_fn=_fake_git("dev", 1))
        self.assertEqual(sl.composite_value("git", SAMPLE, opts(), r),
                         "dev (1 change)")

    def test_git_clean(self):
        r = raw_for(SAMPLE, git_fn=_fake_git("main", 0))
        self.assertEqual(sl.composite_value("git", SAMPLE, opts(), r), "main")

    def test_git_not_repo(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("git", SAMPLE, opts(), r), "")

    def test_changes(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("changes", SAMPLE, opts(), r), "Δ686")
        self.assertEqual(sl.composite_value("added", SAMPLE, opts(), r), "+604")
        self.assertEqual(sl.composite_value("removed", SAMPLE, opts(), r), "-82")

    def test_cwd_home_substitution(self):
        s = {"workspace": {"project_dir": os.path.expanduser("~") + "/proj"}}
        r = raw_for(s)
        self.assertEqual(sl.composite_value("cwd", s, opts(), r), "~/proj")

    def test_model(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("model", SAMPLE, opts(), r), "Opus 4.7")

    def test_model_fallback_to_id(self):
        s = json.loads(json.dumps(SAMPLE))
        del s["model"]["display_name"]
        r = raw_for(s)
        self.assertEqual(sl.composite_value("model", s, opts(), r),
                         "claude-opus-4-7")

    def test_version(self):
        r = raw_for(SAMPLE)
        self.assertEqual(sl.composite_value("version", SAMPLE, opts(), r),
                         "v2.1.139")

    def test_session_unnamed(self):
        s = {"workspace": {"project_dir": "/x"}}
        r = raw_for(s)
        self.assertEqual(sl.composite_value("session", s, opts(), r), "UNNAMED")


class TestRenderCustom(unittest.TestCase):
    def test_happy_path(self):
        v = values_for(SAMPLE)
        self.assertEqual(sl.render_custom("t", "5h: {limit_5h_pct:.1f}%", v),
                         "5h: 6.0%")

    def test_references_composite(self):
        v = values_for(SAMPLE)
        self.assertEqual(sl.render_custom("t", "say [{ctx}]", v),
                         "say [ctx: ~80k (8%)]")

    def test_skips_when_raw_none(self):
        v = values_for({"workspace": {"project_dir": "/x"}})
        self.assertEqual(sl.render_custom("t", "{limit_5h_pct:.1f}", v), "")

    def test_skips_when_composite_empty(self):
        # No rate limits → composite `limits` is "".
        s = json.loads(json.dumps(SAMPLE))
        del s["rate_limits"]
        v = values_for(s)
        self.assertEqual(sl.render_custom("t", "[{limits}]", v), "")

    def test_skips_if_any_ref_unavailable(self):
        s = json.loads(json.dumps(SAMPLE))
        del s["rate_limits"]
        v = values_for(s)
        self.assertEqual(sl.render_custom("t", "{ctx_pct} / {limit_5h_pct}", v), "")

    def test_unknown_field_warns_and_returns_empty(self):
        v = values_for(SAMPLE)
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render_custom("t", "{bogus}", v)
            self.assertIn("unknown field", err.getvalue())
        self.assertEqual(out, "")

    def test_blocks_attribute_access(self):
        v = values_for(SAMPLE)
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render_custom("t", "{ctx_pct.__class__}", v)
            self.assertIn("invalid field reference", err.getvalue())
        self.assertEqual(out, "")

    def test_blocks_indexing(self):
        v = values_for(SAMPLE)
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render_custom("t", "{ctx_pct[0]}", v)
            self.assertIn("invalid field reference", err.getvalue())
        self.assertEqual(out, "")

    def test_blocks_nested_braces_in_spec(self):
        v = values_for(SAMPLE)
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render_custom("t", "{ctx_pct:{limit_5h_pct}}", v)
            self.assertIn("nested replacement", err.getvalue())
        self.assertEqual(out, "")

    def test_invalid_template_syntax(self):
        v = values_for(SAMPLE)
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render_custom("t", "{unterminated", v)
            self.assertIn("invalid custom template", err.getvalue())
        self.assertEqual(out, "")

    def test_conversion_allowed(self):
        v = values_for(SAMPLE)
        self.assertEqual(sl.render_custom("t", "{ctx_pct!s}", v), "8.0")

    def test_static_string_only(self):
        v = values_for(SAMPLE)
        self.assertEqual(sl.render_custom("t", "hello world", v), "hello world")


class TestRender(unittest.TestCase):
    def test_default_fields_skip_empty(self):
        out = sl.render(SAMPLE, opts(), now=0, git_fn=_no_git)
        parts = out.split(" | ")
        # cwd | model | ctx | session_cost | limits  (git skipped)
        self.assertEqual(len(parts), 5)
        self.assertEqual(parts[1], "Opus 4.7")
        self.assertEqual(parts[2], "ctx: ~80k (8%)")
        self.assertEqual(parts[3], "$6.761")

    def test_custom_separator_and_fields(self):
        out = sl.render(SAMPLE, opts(fields="session,version", separator=" / "),
                        now=0, git_fn=_no_git)
        self.assertEqual(out, "demo / v2.1.139")

    def test_missing_project_dir_exits(self):
        with self.assertRaises(SystemExit) as cm:
            sl.render({}, opts(), git_fn=_no_git)
        self.assertEqual(cm.exception.code, 1)

    def test_unknown_field_warns_but_continues(self):
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render(SAMPLE, opts(fields="model,bogus,session"),
                            now=0, git_fn=_no_git)
            self.assertIn("unknown field: bogus", err.getvalue())
        self.assertEqual(out, "Opus 4.7 | demo")

    def test_raw_field_in_render(self):
        out = sl.render(SAMPLE, opts(fields="ctx_pct,ctx_tokens_k"),
                        now=0, git_fn=_no_git)
        self.assertEqual(out, "8.0 | 80")

    def test_custom_field_referencing_raw(self):
        o = opts(fields="model,custom:my5h")
        o["custom_fields"] = {"my5h": "5h: {limit_5h_pct:.1f}%"}
        out = sl.render(SAMPLE, o, now=0, git_fn=_no_git)
        self.assertEqual(out, "Opus 4.7 | 5h: 6.0%")

    def test_custom_field_referencing_composite(self):
        o = opts(fields="custom:wrap")
        o["custom_fields"] = {"wrap": "[{ctx}]"}
        out = sl.render(SAMPLE, o, now=0, git_fn=_no_git)
        self.assertEqual(out, "[ctx: ~80k (8%)]")

    def test_custom_name_matching_builtin_resolves_to_builtin_in_template(self):
        # Custom named "model" can use {model} in its template — that's the
        # built-in `model` field, not a self-reference.
        o = opts(fields="custom:model")
        o["custom_fields"] = {"model": "model: {model}"}
        out = sl.render(SAMPLE, o, now=0, git_fn=_no_git)
        self.assertEqual(out, "model: Opus 4.7")

    def test_custom_and_builtin_with_same_name_in_fields(self):
        # --fields can include both; they refer to different things.
        o = opts(fields="model,custom:model")
        o["custom_fields"] = {"model": "[{model}]"}
        out = sl.render(SAMPLE, o, now=0, git_fn=_no_git)
        self.assertEqual(out, "Opus 4.7 | [Opus 4.7]")

    def test_template_cannot_reference_another_custom(self):
        # Templates can only reference built-in field names; other custom names
        # are reported as unknown.
        o = opts(fields="custom:b")
        o["custom_fields"] = {"a": "{ctx_pct}", "b": "{a}"}
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render(SAMPLE, o, now=0, git_fn=_no_git)
            self.assertIn("unknown field", err.getvalue())
        self.assertEqual(out, "")

    def test_custom_field_skipped_when_data_missing(self):
        s = {"workspace": {"project_dir": "/x"}}
        o = opts(fields="session,custom:x")
        o["custom_fields"] = {"x": "{limit_5h_pct}"}
        out = sl.render(s, o, now=0, git_fn=_no_git)
        self.assertEqual(out, "UNNAMED")

    def test_undefined_custom_field(self):
        o = opts(fields="model,custom:missing")
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render(SAMPLE, o, now=0, git_fn=_no_git)
            self.assertIn("undefined custom field: missing", err.getvalue())
        self.assertEqual(out, "Opus 4.7")


class TestGitInfo(unittest.TestCase):
    def test_not_a_repo(self):
        def fake_run(cmd, **kw):
            class R:
                returncode = 128
                stdout = ""
            return R()
        self.assertEqual(sl.git_info("/tmp", run=fake_run), (None, None))

    def test_clean_repo(self):
        outputs = iter([
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="main\n"),
            mock.Mock(returncode=0, stdout="## main\n"),
        ])
        self.assertEqual(sl.git_info("/x", run=lambda *a, **k: next(outputs)),
                         ("main", 0))

    def test_one_change(self):
        outputs = iter([
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="dev\n"),
            mock.Mock(returncode=0, stdout="## dev\n M foo.py\n"),
        ])
        self.assertEqual(sl.git_info("/x", run=lambda *a, **k: next(outputs)),
                         ("dev", 1))


class TestModelPrices(unittest.TestCase):
    def test_lookups(self):
        self.assertEqual(sl.MODEL_PRICES["claude-opus-4-7"], (5.0, 25.0))
        self.assertEqual(sl.MODEL_PRICES["claude-haiku-3-5"], (0.80, 4.0))


if __name__ == "__main__":
    unittest.main()
