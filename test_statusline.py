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
    o.update(overrides)
    return o


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(sl.parse_args([]), sl.DEFAULTS)

    def test_space_form(self):
        o = sl.parse_args(["--ctx-warn", "42", "--separator", " / "])
        self.assertEqual(o["ctx_warn"], 42)
        self.assertEqual(o["separator"], " / ")

    def test_equals_form(self):
        o = sl.parse_args(["--ctx-warn=42", "--separator= / "])
        self.assertEqual(o["ctx_warn"], 42)
        self.assertEqual(o["separator"], " / ")

    def test_cost_precision_int(self):
        self.assertEqual(sl.parse_args(["--cost-precision=5"])["cost_precision"], 5)

    def test_unknown_flag(self):
        with self.assertRaises(SystemExit) as cm:
            sl.parse_args(["--nope"])
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_flag_equals(self):
        with self.assertRaises(SystemExit):
            sl.parse_args(["--nope=1"])

    def test_missing_value(self):
        with self.assertRaises(SystemExit):
            sl.parse_args(["--ctx-warn"])

    def test_help(self):
        with self.assertRaises(SystemExit) as cm:
            sl.parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)


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


class TestCtx(unittest.TestCase):
    def test_normal(self):
        s = {"context_window": {"used_percentage": 8, "context_window_size": 1000000}}
        self.assertEqual(sl.compute_ctx(s, opts()), "~80k (8%)")

    def test_warn(self):
        s = {"context_window": {"used_percentage": 16, "context_window_size": 1000000}}
        out = sl.compute_ctx(s, opts())
        self.assertTrue(out.startswith(sl.DEFAULTS["warn_str"]))

    def test_crit(self):
        s = {"context_window": {"used_percentage": 25, "context_window_size": 1000000}}
        out = sl.compute_ctx(s, opts())
        self.assertTrue(out.startswith(sl.DEFAULTS["crit_str"]))

    def test_missing(self):
        self.assertEqual(sl.compute_ctx({}, opts()), "")


class TestSessionCost(unittest.TestCase):
    def test_precision(self):
        s = {"cost": {"total_cost_usd": 1.23456}}
        self.assertEqual(sl.compute_sessioncost(s, opts(cost_precision=2)), "1.23")
        self.assertEqual(sl.compute_sessioncost(s, opts(cost_precision=4)), "1.2346")

    def test_missing(self):
        self.assertEqual(sl.compute_sessioncost({}, opts()), "")


class TestTurnCost(unittest.TestCase):
    def test_known_model(self):
        # opus-4-7: input=5, output=25 per Mtok
        # 1*5 + 675*5*1.25 + 78645*5*0.1 + 1667*25 = 5 + 4218.75 + 39322.5 + 41675 = 85221.25
        # / 1e6 = 0.0852...
        out = sl.compute_turncost(SAMPLE, opts(cost_precision=4))
        self.assertEqual(out, "0.0852")

    def test_unknown_model(self):
        s = json.loads(json.dumps(SAMPLE))
        s["model"]["id"] = "unknown-model"
        self.assertEqual(sl.compute_turncost(s, opts()), "")

    def test_missing_usage(self):
        s = json.loads(json.dumps(SAMPLE))
        del s["context_window"]["current_usage"]["input_tokens"]
        self.assertEqual(sl.compute_turncost(s, opts()), "")


class TestCacheHit(unittest.TestCase):
    def test_normal(self):
        # cr=78645, cc=675, ci=1 -> 78645/79321 = 99.15%
        out = sl.compute_cachehit(SAMPLE, opts())
        self.assertEqual(out, "99.15%")

    def test_warn(self):
        s = {"context_window": {"current_usage": {
            "cache_read_input_tokens": 70, "cache_creation_input_tokens": 30,
            "input_tokens": 0}}}
        out = sl.compute_cachehit(s, opts())
        self.assertTrue(out.startswith(sl.DEFAULTS["warn_str"]))

    def test_crit(self):
        s = {"context_window": {"current_usage": {
            "cache_read_input_tokens": 10, "cache_creation_input_tokens": 90,
            "input_tokens": 0}}}
        out = sl.compute_cachehit(s, opts())
        self.assertTrue(out.startswith(sl.DEFAULTS["crit_str"]))

    def test_zero_total(self):
        s = {"context_window": {"current_usage": {
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "input_tokens": 0}}}
        self.assertEqual(sl.compute_cachehit(s, opts()), "")

    def test_missing(self):
        self.assertEqual(sl.compute_cachehit({}, opts()), "")


class TestLimits(unittest.TestCase):
    def test_normal(self):
        out = sl.compute_limits(SAMPLE, opts(), now=0)
        self.assertEqual(out, "6%/10% (16m:40s/1h:23m)")

    def test_warn(self):
        s = json.loads(json.dumps(SAMPLE))
        s["rate_limits"]["five_hour"]["used_percentage"] = 80
        out = sl.compute_limits(s, opts(), now=0)
        self.assertTrue(out.startswith(sl.DEFAULTS["warn_str"]))

    def test_crit_via_week(self):
        s = json.loads(json.dumps(SAMPLE))
        s["rate_limits"]["seven_day"]["used_percentage"] = 100
        out = sl.compute_limits(s, opts(), now=0)
        self.assertTrue(out.startswith(sl.DEFAULTS["crit_str"]))

    def test_missing(self):
        self.assertEqual(sl.compute_limits({}, opts()), "")


class TestFieldValue(unittest.TestCase):
    def _derived(self, status):
        return {
            "project_dir": sl.dig(status, "workspace", "project_dir"),
            "git_info": "main (3 changes)",
            "ctx_display": sl.compute_ctx(status, opts()),
            "cost_display": sl.compute_sessioncost(status, opts()),
            "turncost_display": sl.compute_turncost(status, opts()),
            "cache_display": sl.compute_cachehit(status, opts()),
            "limits_display": sl.compute_limits(status, opts(), now=0),
        }

    def test_cwd_home_substitution(self):
        s = {"workspace": {"project_dir": os.path.expanduser("~") + "/proj"}}
        d = self._derived(s)
        self.assertEqual(sl.field_value("cwd", s, opts(), d), "~/proj")

    def test_cwd_outside_home(self):
        s = {"workspace": {"project_dir": "/etc/foo"}}
        d = self._derived(s)
        self.assertEqual(sl.field_value("cwd", s, opts(), d), "/etc/foo")

    def test_model(self):
        d = self._derived(SAMPLE)
        self.assertEqual(sl.field_value("model", SAMPLE, opts(), d), "Opus 4.7")

    def test_model_fallback_to_id(self):
        s = json.loads(json.dumps(SAMPLE))
        del s["model"]["display_name"]
        d = self._derived(s)
        self.assertEqual(sl.field_value("model", s, opts(), d), "claude-opus-4-7")

    def test_ctx_prefix(self):
        d = self._derived(SAMPLE)
        self.assertEqual(sl.field_value("ctx", SAMPLE, opts(), d), "ctx: ~80k (8%)")

    def test_sessioncost_prefix(self):
        d = self._derived(SAMPLE)
        self.assertEqual(sl.field_value("sessioncost", SAMPLE, opts(), d), "$6.761")

    def test_limits_prefix(self):
        d = self._derived(SAMPLE)
        self.assertTrue(sl.field_value("limits", SAMPLE, opts(), d).startswith("lmt: "))

    def test_session_unnamed(self):
        s = {"workspace": {"project_dir": "/x"}}
        d = self._derived(s)
        self.assertEqual(sl.field_value("session", s, opts(), d), "UNNAMED")

    def test_version_prefix(self):
        d = self._derived(SAMPLE)
        self.assertEqual(sl.field_value("version", SAMPLE, opts(), d), "v2.1.139")

    def test_agent_prefix(self):
        s = {"workspace": {"project_dir": "/x"}, "agent": {"name": "researcher"}}
        d = self._derived(s)
        self.assertEqual(sl.field_value("agent", s, opts(), d), "@researcher")

    def test_changes(self):
        d = self._derived(SAMPLE)
        self.assertEqual(sl.field_value("changes", SAMPLE, opts(), d), "Δ686")

    def test_added_removed(self):
        d = self._derived(SAMPLE)
        self.assertEqual(sl.field_value("added", SAMPLE, opts(), d), "+604")
        self.assertEqual(sl.field_value("removed", SAMPLE, opts(), d), "-82")

    def test_durations(self):
        d = self._derived(SAMPLE)
        self.assertTrue(sl.field_value("api_duration", SAMPLE, opts(), d).startswith("api:"))
        self.assertTrue(sl.field_value("duration", SAMPLE, opts(), d).startswith("dur:"))

    def test_cachehit_prefix(self):
        d = self._derived(SAMPLE)
        out = sl.field_value("cachehit", SAMPLE, opts(), d)
        self.assertTrue(out.startswith("c↑: "))

    def test_unknown_returns_none(self):
        d = self._derived(SAMPLE)
        self.assertIsNone(sl.field_value("bogus", SAMPLE, opts(), d))

    def test_empty_when_data_missing(self):
        s = {"workspace": {"project_dir": "/x"}}
        d = self._derived(s)
        self.assertEqual(sl.field_value("git", s, opts(), d.copy() | {"git_info": ""}), "")
        self.assertEqual(sl.field_value("model", s, opts(), d), "")
        self.assertEqual(sl.field_value("added", s, opts(), d), "")


class TestRender(unittest.TestCase):
    def test_default_fields_skip_empty(self):
        s = json.loads(json.dumps(SAMPLE))
        # No git in /tmp; mock git_info to ""
        out = sl.render(s, opts(), now=0, git_fn=lambda p: "")
        # Expect: cwd | model | ctx | sessioncost | limits  (git skipped)
        parts = out.split(" | ")
        self.assertEqual(len(parts), 5)
        self.assertEqual(parts[1], "Opus 4.7")
        self.assertEqual(parts[2], "ctx: ~80k (8%)")
        self.assertEqual(parts[3], "$6.761")

    def test_custom_separator_and_fields(self):
        out = sl.render(SAMPLE, opts(fields="session,version", separator=" / "),
                        now=0, git_fn=lambda p: "")
        self.assertEqual(out, "demo / v2.1.139")

    def test_missing_project_dir_exits(self):
        with self.assertRaises(SystemExit) as cm:
            sl.render({}, opts(), git_fn=lambda p: "")
        self.assertEqual(cm.exception.code, 1)

    def test_unknown_field_warns_but_continues(self):
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            out = sl.render(SAMPLE, opts(fields="model,bogus,session"),
                            now=0, git_fn=lambda p: "")
            self.assertIn("unknown field: bogus", err.getvalue())
        self.assertEqual(out, "Opus 4.7 | demo")


class TestGitInfo(unittest.TestCase):
    def test_not_a_repo(self):
        def fake_run(cmd, **kw):
            class R:
                returncode = 128
                stdout = ""
            return R()
        self.assertEqual(sl.git_info("/tmp", run=fake_run), "")

    def test_clean_repo(self):
        outputs = iter([
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="main\n"),
            mock.Mock(returncode=0, stdout="## main\n"),
        ])
        self.assertEqual(sl.git_info("/x", run=lambda *a, **k: next(outputs)), "main")

    def test_one_change(self):
        outputs = iter([
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="dev\n"),
            mock.Mock(returncode=0, stdout="## dev\n M foo.py\n"),
        ])
        self.assertEqual(sl.git_info("/x", run=lambda *a, **k: next(outputs)),
                         "dev (1 change)")

    def test_many_changes(self):
        outputs = iter([
            mock.Mock(returncode=0, stdout=""),
            mock.Mock(returncode=0, stdout="dev\n"),
            mock.Mock(returncode=0, stdout="## dev\n M a\n M b\n?? c\n"),
        ])
        self.assertEqual(sl.git_info("/x", run=lambda *a, **k: next(outputs)),
                         "dev (3 changes)")


class TestModelPrices(unittest.TestCase):
    def test_lookups(self):
        self.assertEqual(sl.MODEL_PRICES["claude-opus-4-7"], (5.0, 25.0))
        self.assertEqual(sl.MODEL_PRICES["claude-haiku-3-5"], (0.80, 4.0))
        self.assertNotIn("claude-future-9", sl.MODEL_PRICES)


if __name__ == "__main__":
    unittest.main()
