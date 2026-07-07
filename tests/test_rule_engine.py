"""Tests for vision_agent.rules.engine"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from vision_agent.core.types import BoundingBox, Event, Severity, Track
from vision_agent.rules.engine import (
    CountLineRule,
    DefenseFilter,
    MemoryCache,
    ObjectInZoneRule,
    RuleEngine,
    ZoneEmptyRule,
    create_rule_from_yaml,
)


# ─── Helpers ──────────────────────────────────────────────────


def _track(
    track_id: int = 1,
    class_name: str = "person",
    center: tuple[float, float] = (50.0, 50.0),
    trajectory: list[tuple[float, float, float]] | None = None,
) -> Track:
    """Create a Track centered at *center* with a 10x10 bbox."""
    cx, cy = center
    bbox = BoundingBox(cx - 5, cy - 5, cx + 5, cy + 5)
    return Track(
        track_id=track_id,
        class_name=class_name,
        bbox=bbox,
        trajectory=trajectory or [],
    )


def _frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype=np.uint8)


def _event(**kwargs) -> Event:
    return Event(event_type="test", **kwargs)


def _write_yaml(path: Path, data: dict) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)
        f.flush()
    return path


# ─── 1. MemoryCache ──────────────────────────────────────────


class TestMemoryCache:
    def test_get_missing_key_returns_none(self):
        cache = MemoryCache()
        assert cache.get("no_such_key") is None

    def test_set_and_get(self):
        cache = MemoryCache()
        cache.set("k", 42)
        assert cache.get("k") == 42

    def test_set_overwrites(self):
        cache = MemoryCache()
        cache.set("k", 1)
        cache.set("k", 2)
        assert cache.get("k") == 2

    def test_incr_from_zero(self):
        cache = MemoryCache()
        assert cache.incr("counter") == 1
        assert cache.incr("counter") == 2
        assert cache.incr("counter") == 3

    def test_incr_with_existing_value(self):
        cache = MemoryCache()
        cache.set("counter", 10)
        assert cache.incr("counter") == 11

    def test_clear(self):
        cache = MemoryCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_ttl_param_is_ignored(self):
        """ttl parameter is accepted but not enforced by MemoryCache."""
        cache = MemoryCache()
        cache.set("k", "v", ttl=9999)
        assert cache.get("k") == "v"


# ─── 2. DefenseFilter ────────────────────────────────────────


class TestDefenseFilterSlidingWindow:
    """Layer 1: sliding window de-duplication."""

    def test_below_window_blocks(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_window_size=5, default_cooldown=9999)
        ev = _event()
        # First 4 calls should be blocked (count < 5)
        for _ in range(4):
            assert df.check("r1", "cam1", ev, cooldown=9999) is False

    def test_reaching_window_passes(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_window_size=3, default_cooldown=9999)
        ev = _event()
        assert df.check("r1", "cam1", ev, cooldown=9999) is False  # 1
        assert df.check("r1", "cam1", ev, cooldown=9999) is False  # 2
        assert df.check("r1", "cam1", ev, cooldown=9999) is True   # 3 -> pass

    def test_resets_after_pass(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_window_size=2, default_cooldown=9999)
        ev = _event()
        assert df.check("r1", "cam1", ev, cooldown=9999) is False  # 1
        assert df.check("r1", "cam1", ev, cooldown=9999) is True   # 2 -> pass, reset
        # After reset, counter goes back to 0; next call is count=1 -> blocked
        assert df.check("r1", "cam1", ev, cooldown=9999) is False

    def test_custom_window_size_param(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_window_size=99, default_cooldown=9999)
        ev = _event()
        # Override window_size to 1 -> immediate pass
        assert df.check("r1", "cam1", ev, window_size=1, cooldown=9999) is True


class TestDefenseFilterCooldown:
    """Layer 2: cooldown period."""

    def test_first_call_passes_cooldown(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_cooldown=300)
        ev = _event()
        assert df._cooldown_check("cd", 300) is True

    def test_second_call_within_cooldown_blocked(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_cooldown=300)
        ev = _event()
        df._cooldown_check("cd", 300)
        # Immediately after, should be blocked
        assert df._cooldown_check("cd", 300) is False

    def test_call_after_cooldown_passes(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_cooldown=1)
        df._cooldown_check("cd", 1)
        # Simulate time passing
        cache.set("cd", time.time() - 2)
        assert df._cooldown_check("cd", 1) is True

    def test_full_check_respects_cooldown(self):
        cache = MemoryCache()
        df = DefenseFilter(cache, default_window_size=1, default_cooldown=300)
        ev = _event()
        # First: sliding window=1 passes, cooldown first-time passes
        assert df.check("r1", "cam1", ev, window_size=1, cooldown=300) is True
        # Second: sliding window=1 passes again, but cooldown blocks
        assert df.check("r1", "cam1", ev, window_size=1, cooldown=300) is False


class TestDefenseFilterTimeWindow:
    """Layer 3: time window filtering."""

    def test_no_time_windows_passes(self):
        cache = MemoryCache()
        df = DefenseFilter(cache)
        assert df._time_window_check(None) is True
        assert df._time_window_check([]) is True

    def test_within_normal_window_passes(self):
        """Mock datetime to 14:30 on a Wednesday (weekday=2)."""
        cache = MemoryCache()
        df = DefenseFilter(cache)
        fake_now = type("dt", (), {"hour": 14, "minute": 30, "weekday": lambda self: 2})()
        with patch("vision_agent.rules.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            windows = [{"start": "08:00", "end": "18:00", "days": [0, 1, 2, 3, 4]}]
            assert df._time_window_check(windows) is True

    def test_outside_normal_window_blocks(self):
        cache = MemoryCache()
        df = DefenseFilter(cache)
        fake_now = type("dt", (), {"hour": 20, "minute": 0, "weekday": lambda self: 2})()
        with patch("vision_agent.rules.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            windows = [{"start": "08:00", "end": "18:00", "days": [0, 1, 2, 3, 4]}]
            assert df._time_window_check(windows) is False

    def test_cross_midnight_window_passes_after_midnight(self):
        """22:00-06:00: at 02:30 should pass."""
        cache = MemoryCache()
        df = DefenseFilter(cache)
        fake_now = type("dt", (), {"hour": 2, "minute": 30, "weekday": lambda self: 0})()
        with patch("vision_agent.rules.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            windows = [{"start": "22:00", "end": "06:00"}]
            assert df._time_window_check(windows) is True

    def test_cross_midnight_window_passes_before_midnight(self):
        """22:00-06:00: at 23:30 should pass."""
        cache = MemoryCache()
        df = DefenseFilter(cache)
        fake_now = type("dt", (), {"hour": 23, "minute": 30, "weekday": lambda self: 0})()
        with patch("vision_agent.rules.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            windows = [{"start": "22:00", "end": "06:00"}]
            assert df._time_window_check(windows) is True

    def test_cross_midnight_window_blocks_midday(self):
        """22:00-06:00: at 12:00 should block."""
        cache = MemoryCache()
        df = DefenseFilter(cache)
        fake_now = type("dt", (), {"hour": 12, "minute": 0, "weekday": lambda self: 0})()
        with patch("vision_agent.rules.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            windows = [{"start": "22:00", "end": "06:00"}]
            assert df._time_window_check(windows) is False

    def test_wrong_weekday_blocks(self):
        cache = MemoryCache()
        df = DefenseFilter(cache)
        # Wednesday (2) but window only allows Mon-Fri (0-4) -- wait 2 is in range.
        # Use Saturday (5) which is not in the list.
        fake_now = type("dt", (), {"hour": 14, "minute": 0, "weekday": lambda self: 5})()
        with patch("vision_agent.rules.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            windows = [{"start": "08:00", "end": "18:00", "days": [0, 1, 2, 3, 4]}]
            assert df._time_window_check(windows) is False

    def test_malformed_time_is_skipped(self):
        cache = MemoryCache()
        df = DefenseFilter(cache)
        fake_now = type("dt", (), {"hour": 14, "minute": 0, "weekday": lambda self: 0})()
        with patch("vision_agent.rules.engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            windows = [{"start": "bad", "end": "18:00"}]
            # The malformed window is skipped, no window passes -> False
            assert df._time_window_check(windows) is False


class TestDefenseFilterResetRule:
    def test_reset_rule_clears_keys(self):
        cache = MemoryCache()
        df = DefenseFilter(cache)
        cache.set("rule:r1:camera:cam1:sliding_window", 3)
        cache.set("rule:r1:camera:cam1:cooldown", time.time())
        cache.set("rule:r2:camera:cam1:sliding_window", 2)
        df.reset_rule("r1")
        assert cache.get("rule:r1:camera:cam1:sliding_window") is None
        assert cache.get("rule:r1:camera:cam1:cooldown") is None
        assert cache.get("rule:r2:camera:cam1:sliding_window") == 2


# ─── 3. ObjectInZoneRule ─────────────────────────────────────


class TestObjectInZoneRule:
    # Unit square zone: (0,0)-(100,0)-(100,100)-(0,100)
    ZONE = [(0, 0), (100, 0), (100, 100), (0, 100)]

    def test_target_inside_zone_triggers(self):
        rule = ObjectInZoneRule(name="oz", zone=self.ZONE)
        t = _track(center=(50, 50))
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        assert ev is not None
        assert ev.event_type == "object_in_zone"
        assert len(ev.tracks) == 1

    def test_target_outside_zone_no_trigger(self):
        rule = ObjectInZoneRule(name="oz", zone=self.ZONE)
        t = _track(center=(200, 200))
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        assert ev is None

    def test_class_filtering(self):
        rule = ObjectInZoneRule(
            name="oz", zone=self.ZONE, target_classes=["car"]
        )
        t_person = _track(center=(50, 50), class_name="person")
        t_car = _track(track_id=2, center=(60, 60), class_name="car")
        ev = rule.evaluate([t_person, t_car], _frame(), {"camera_id": "c1"})
        assert ev is not None
        assert len(ev.tracks) == 1
        assert ev.tracks[0].class_name == "car"

    def test_severity_defaults_to_warning(self):
        rule = ObjectInZoneRule(name="oz", zone=self.ZONE)
        assert rule._severity == Severity.WARNING

    def test_severity_critical(self):
        rule = ObjectInZoneRule(name="oz", zone=self.ZONE, severity="critical")
        assert rule._severity == Severity.CRITICAL

    def test_camera_ids_property(self):
        rule = ObjectInZoneRule(
            name="oz", zone=self.ZONE, camera_ids=["cam1", "cam2"]
        )
        assert rule.camera_ids == ["cam1", "cam2"]

    def test_name_property(self):
        rule = ObjectInZoneRule(name="my_rule", zone=self.ZONE)
        assert rule.name == "my_rule"

    def test_reset_noop(self):
        rule = ObjectInZoneRule(name="oz", zone=self.ZONE)
        rule.reset()  # Should not raise


class TestPointInZone:
    def test_inside_triangle(self):
        triangle = [(0, 0), (100, 0), (50, 100)]
        assert ObjectInZoneRule._point_in_zone((50, 30), triangle) is True

    def test_outside_triangle(self):
        triangle = [(0, 0), (100, 0), (50, 100)]
        assert ObjectInZoneRule._point_in_zone((200, 200), triangle) is False

    def test_on_boundary(self):
        """A point exactly on the right edge of a square.
        The ray-casting algorithm's behavior on boundaries can vary;
        here we just ensure no exception is raised."""
        zone = [(0, 0), (100, 0), (100, 100), (0, 100)]
        # This should not raise
        ObjectInZoneRule._point_in_zone((100, 50), zone)


# ─── 4. CountLineRule ────────────────────────────────────────


class TestCountLineRule:
    def test_trajectory_crosses_line_triggers(self):
        """Track moves from left to right, crossing a vertical line at x=50."""
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
            threshold=1,
        )
        # Trajectory: (0,50,t0) -> (100,50,t1) crosses x=50
        t = _track(
            trajectory=[(0, 50, 0.0), (100, 50, 1.0)],
        )
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        assert ev is not None
        assert ev.event_type == "count_line"
        assert ev.metadata["count"] == 1

    def test_trajectory_no_crossing(self):
        """Track stays on one side of the line."""
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
            threshold=1,
        )
        t = _track(trajectory=[(10, 50, 0.0), (20, 50, 1.0)])
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        assert ev is None

    def test_threshold_not_reached(self):
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
            threshold=3,
        )
        t = _track(
            track_id=1,
            trajectory=[(0, 50, 0.0), (100, 50, 1.0)],
        )
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        # count=1, threshold=3 -> no trigger
        assert ev is None

    def test_threshold_reached_multiple_tracks(self):
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
            threshold=2,
        )
        t1 = _track(track_id=1, trajectory=[(0, 30, 0.0), (100, 30, 1.0)])
        t2 = _track(track_id=2, trajectory=[(0, 70, 0.0), (100, 70, 1.0)])
        ev = rule.evaluate([t1, t2], _frame(), {"camera_id": "c1"})
        assert ev is not None
        assert ev.metadata["count"] == 2

    def test_short_trajectory_ignored(self):
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
        )
        t = _track(trajectory=[(60, 50, 0.0)])  # Only 1 point
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        assert ev is None

    def test_same_track_not_counted_twice(self):
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
            threshold=1,
        )
        t = _track(track_id=1, trajectory=[(0, 50, 0.0), (100, 50, 1.0)])
        # First evaluation: counted
        rule.evaluate([t], _frame(), {"camera_id": "c1"})
        # Second evaluation: track_id=1 already in counted_tracks
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        # Count stays at 1 (already counted), but threshold=1 was already met
        # The rule re-checks _counted_tracks >= threshold, so it triggers again
        # with count=1
        assert ev is not None
        assert ev.metadata["count"] == 1

    def test_reset_clears_counted_tracks(self):
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
            threshold=2,
        )
        t = _track(track_id=1, trajectory=[(0, 50, 0.0), (100, 50, 1.0)])
        rule.evaluate([t], _frame(), {"camera_id": "c1"})
        rule.reset()
        assert len(rule._counted_tracks) == 0

    def test_class_filtering(self):
        rule = CountLineRule(
            name="cl",
            line_start=(50, 0),
            line_end=(50, 100),
            threshold=1,
            target_classes=["car"],
        )
        t_person = _track(
            track_id=1, class_name="person",
            trajectory=[(0, 50, 0.0), (100, 50, 1.0)],
        )
        ev = rule.evaluate([t_person], _frame(), {"camera_id": "c1"})
        assert ev is None


# ─── 5. ZoneEmptyRule ────────────────────────────────────────


class TestZoneEmptyRule:
    ZONE = [(0, 0), (100, 0), (100, 100), (0, 100)]

    def test_zone_empty_triggers(self):
        rule = ZoneEmptyRule(name="ze", zone=self.ZONE)
        ev = rule.evaluate([], _frame(), {"camera_id": "c1"})
        assert ev is not None
        assert ev.event_type == "zone_empty"
        assert ev.tracks == []

    def test_object_inside_zone_blocks(self):
        rule = ZoneEmptyRule(name="ze", zone=self.ZONE)
        t = _track(center=(50, 50))
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        assert ev is None

    def test_object_outside_zone_triggers(self):
        rule = ZoneEmptyRule(name="ze", zone=self.ZONE)
        t = _track(center=(200, 200))
        ev = rule.evaluate([t], _frame(), {"camera_id": "c1"})
        assert ev is not None

    def test_class_filtering_unmatched_object_in_zone(self):
        """Zone has a 'car' but rule only watches for 'person' -> triggers."""
        rule = ZoneEmptyRule(
            name="ze", zone=self.ZONE, target_classes=["person"]
        )
        t_car = _track(center=(50, 50), class_name="car")
        ev = rule.evaluate([t_car], _frame(), {"camera_id": "c1"})
        assert ev is not None

    def test_class_filtering_matched_object_blocks(self):
        rule = ZoneEmptyRule(
            name="ze", zone=self.ZONE, target_classes=["person"]
        )
        t_person = _track(center=(50, 50), class_name="person")
        ev = rule.evaluate([t_person], _frame(), {"camera_id": "c1"})
        assert ev is None


# ─── 6. create_rule_from_yaml ────────────────────────────────


class TestCreateRuleFromYaml:
    def test_object_in_zone(self):
        config = {
            "name": "intrusion",
            "conditions": [
                {
                    "type": "object_in_zone",
                    "params": {
                        "zone": [[0, 0], [100, 0], [100, 100], [0, 100]],
                        "target_classes": ["person"],
                    },
                }
            ],
            "severity": "critical",
        }
        rule = create_rule_from_yaml(config)
        assert rule is not None
        assert rule.name == "intrusion"
        assert rule.camera_ids is None

    def test_count_line(self):
        config = {
            "name": "line_count",
            "conditions": [
                {
                    "type": "count_line",
                    "params": {
                        "line_start": [0, 50],
                        "line_end": [100, 50],
                        "threshold": 3,
                    },
                }
            ],
        }
        rule = create_rule_from_yaml(config)
        assert rule is not None
        assert rule.name == "line_count"

    def test_zone_empty(self):
        config = {
            "name": "zone_clear",
            "conditions": [
                {
                    "type": "zone_empty",
                    "params": {
                        "zone": [[0, 0], [50, 0], [50, 50], [0, 50]],
                    },
                }
            ],
        }
        rule = create_rule_from_yaml(config)
        assert rule is not None
        assert rule.name == "zone_clear"

    def test_unknown_type_returns_none(self):
        config = {
            "name": "bad",
            "conditions": [{"type": "nonexistent_rule_type"}],
        }
        rule = create_rule_from_yaml(config)
        assert rule is None

    def test_no_conditions_returns_none(self):
        config = {"name": "no_conds"}
        rule = create_rule_from_yaml(config)
        assert rule is None

    def test_camera_ids_forwarded(self):
        config = {
            "name": "scoped",
            "camera_ids": ["cam_a", "cam_b"],
            "conditions": [
                {
                    "type": "object_in_zone",
                    "params": {"zone": [[0, 0], [10, 0], [10, 10], [0, 10]]},
                }
            ],
        }
        rule = create_rule_from_yaml(config)
        assert rule is not None
        assert rule.camera_ids == ["cam_a", "cam_b"]


# ─── 7. RuleEngine.evaluate ──────────────────────────────────


class TestRuleEngineEvaluate:
    def _make_engine_with_zone_rule(
        self, zone=None, camera_ids=None, **kwargs
    ) -> RuleEngine:
        engine = RuleEngine(
            config={"default_window_size": 1, "default_cooldown": 9999}
        )
        zone = zone or [(0, 0), (100, 0), (100, 100), (0, 100)]
        rule = ObjectInZoneRule(
            name="test_rule",
            zone=zone,
            camera_ids=camera_ids,
            **kwargs,
        )
        engine._rules[rule.name] = rule
        return engine

    def test_rule_triggers_event(self):
        engine = self._make_engine_with_zone_rule()
        t = _track(center=(50, 50))
        events = engine.evaluate("cam1", [t], _frame())
        assert len(events) == 1
        assert events[0].event_type == "object_in_zone"

    def test_rule_no_trigger(self):
        engine = self._make_engine_with_zone_rule()
        t = _track(center=(200, 200))
        events = engine.evaluate("cam1", [t], _frame())
        assert len(events) == 0

    def test_camera_ids_filter_skips_non_matching(self):
        engine = self._make_engine_with_zone_rule(camera_ids=["cam_a"])
        t = _track(center=(50, 50))
        # cam_b is not in camera_ids -> rule is skipped
        events = engine.evaluate("cam_b", [t], _frame())
        assert len(events) == 0

    def test_camera_ids_filter_allows_matching(self):
        engine = self._make_engine_with_zone_rule(camera_ids=["cam_a"])
        t = _track(center=(50, 50))
        events = engine.evaluate("cam_a", [t], _frame())
        assert len(events) == 1

    def test_exception_in_rule_does_not_block_others(self):
        """One rule raises; the other should still be evaluated."""
        engine = RuleEngine(
            config={"default_window_size": 1, "default_cooldown": 9999}
        )

        class BrokenRule:
            name = "broken"
            camera_ids = None

            def evaluate(self, tracks, frame, context):
                raise RuntimeError("boom")

            def reset(self):
                pass

        class GoodRule:
            name = "good"
            camera_ids = None

            def evaluate(self, tracks, frame, context):
                return Event(event_type="good_event")

            def reset(self):
                pass

        engine._rules["broken"] = BrokenRule()
        engine._rules["good"] = GoodRule()
        events = engine.evaluate("cam1", [], _frame())
        assert len(events) == 1
        assert events[0].event_type == "good_event"

    def test_defense_filter_applied(self):
        """With window_size=5 and cooldown=9999, first 4 calls should produce no events."""
        engine = RuleEngine(
            config={"default_window_size": 5, "default_cooldown": 9999}
        )
        rule = ObjectInZoneRule(
            name="r", zone=[(0, 0), (100, 0), (100, 100), (0, 100)]
        )
        engine._rules["r"] = rule
        t = _track(center=(50, 50))
        for _ in range(4):
            assert engine.evaluate("cam1", [t], _frame()) == []
        # 5th call: sliding window reaches 5 -> passes
        events = engine.evaluate("cam1", [t], _frame())
        assert len(events) == 1


# ─── 8. RuleEngine.load_rules ────────────────────────────────


class TestRuleEngineLoadRules:
    def test_loads_yaml_files(self, tmp_path):
        yaml_content = {
            "name": "intrusion",
            "conditions": [
                {
                    "type": "object_in_zone",
                    "params": {
                        "zone": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    },
                }
            ],
        }
        _write_yaml(tmp_path / "rule1.yaml", yaml_content)

        engine = RuleEngine()
        engine.load_rules(str(tmp_path))
        assert engine.get_rule("intrusion") is not None

    def test_loads_yml_extension(self, tmp_path):
        yaml_content = {
            "name": "zone_clear",
            "conditions": [
                {
                    "type": "zone_empty",
                    "params": {"zone": [[0, 0], [50, 0], [50, 50], [0, 50]]},
                }
            ],
        }
        _write_yaml(tmp_path / "rule.yml", yaml_content)

        engine = RuleEngine()
        engine.load_rules(str(tmp_path))
        assert engine.get_rule("zone_clear") is not None

    def test_nonexistent_dir_no_error(self):
        engine = RuleEngine()
        engine.load_rules("/nonexistent/path/that/does/not/exist")
        # Should log a warning but not raise
        assert len(engine._rules) == 0

    def test_skips_invalid_yaml(self, tmp_path, caplog):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("{{{{not valid yaml", encoding="utf-8")
        engine = RuleEngine()
        engine.load_rules(str(tmp_path))
        assert len(engine._rules) == 0

    def test_skips_non_dict_yaml(self, tmp_path):
        list_file = tmp_path / "list.yaml"
        list_file.write_text("- item1\n- item2\n", encoding="utf-8")
        engine = RuleEngine()
        engine.load_rules(str(tmp_path))
        assert len(engine._rules) == 0

    def test_loads_multiple_files(self, tmp_path):
        for i in range(3):
            _write_yaml(
                tmp_path / f"r{i}.yaml",
                {
                    "name": f"rule_{i}",
                    "conditions": [
                        {
                            "type": "object_in_zone",
                            "params": {"zone": [[0, 0], [10, 0], [10, 10], [0, 10]]},
                        }
                    ],
                },
            )
        engine = RuleEngine()
        engine.load_rules(str(tmp_path))
        assert len(engine._rules) == 3


# ─── 9. RuleEngine.reload ────────────────────────────────────


class TestRuleEngineReload:
    def test_reload_picks_up_changes(self, tmp_path):
        config = {
            "name": "v1",
            "conditions": [
                {
                    "type": "object_in_zone",
                    "params": {"zone": [[0, 0], [100, 0], [100, 100], [0, 100]]},
                }
            ],
        }
        rule_file = tmp_path / "rule.yaml"
        _write_yaml(rule_file, config)

        engine = RuleEngine()
        engine.load_rules(str(tmp_path))
        assert engine.get_rule("v1") is not None

        # Ensure mtime changes (Windows has ~1s resolution)
        time.sleep(1.1)
        config["name"] = "v2"
        _write_yaml(rule_file, config)
        engine.reload()
        assert engine.get_rule("v1") is None
        assert engine.get_rule("v2") is not None

    def test_reload_without_rules_dir_is_noop(self):
        engine = RuleEngine()
        engine.reload()  # Should log warning, not raise


# ─── 10. RuleEngine.unload_rule ──────────────────────────────


class TestRuleEngineUnloadRule:
    def test_unload_existing_rule(self):
        engine = RuleEngine(
            config={"default_window_size": 1, "default_cooldown": 9999}
        )
        rule = ObjectInZoneRule(
            name="r", zone=[(0, 0), [100, 0], [100, 100], [0, 100]]
        )
        engine._rules["r"] = rule
        assert engine.unload_rule("r") is True
        assert engine.get_rule("r") is None

    def test_unload_nonexistent_rule(self):
        engine = RuleEngine()
        assert engine.unload_rule("no_such_rule") is False

    def test_unload_clears_defense_state(self):
        cache = MemoryCache()
        engine = RuleEngine(
            config={"default_window_size": 1, "default_cooldown": 9999},
            cache=cache,
        )
        rule = ObjectInZoneRule(
            name="r", zone=[(0, 0), (100, 0), (100, 100), (0, 100)]
        )
        engine._rules["r"] = rule
        engine._rule_sources["r"] = "test.yaml"

        # Simulate some defense state
        cache.set("rule:r:camera:cam1:sliding_window", 3)
        cache.set("rule:r:camera:cam1:cooldown", time.time())

        engine.unload_rule("r")
        assert cache.get("rule:r:camera:cam1:sliding_window") is None
        assert cache.get("rule:r:camera:cam1:cooldown") is None


# ─── 11. RuleEngine misc ─────────────────────────────────────


class TestRuleEngineMisc:
    def test_list_rules(self):
        engine = RuleEngine()
        rule = ObjectInZoneRule(
            name="r1",
            zone=[(0, 0), (100, 0), (100, 100), (0, 100)],
            camera_ids=["cam1"],
        )
        engine._rules["r1"] = rule
        engine._rule_sources["r1"] = "test.yaml"
        summary = engine.list_rules()
        assert len(summary) == 1
        assert summary[0]["name"] == "r1"
        assert summary[0]["camera_ids"] == ["cam1"]

    def test_get_rule_returns_none_for_missing(self):
        engine = RuleEngine()
        assert engine.get_rule("missing") is None

    def test_custom_cache_injection(self):
        class DummyCache:
            def __init__(self):
                self.data = {}

            def get(self, key):
                return self.data.get(key)

            def set(self, key, value, ttl=0):
                self.data[key] = value

            def incr(self, key):
                self.data[key] = self.data.get(key, 0) + 1
                return self.data[key]

        cache = DummyCache()
        engine = RuleEngine(cache=cache)
        assert engine._cache is cache
