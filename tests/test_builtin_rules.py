"""Tests for vision_agent.rules.builtin"""

from __future__ import annotations

import time

import numpy as np
import pytest

from vision_agent.core.types import BoundingBox, Event, Severity, Track
from vision_agent.rules.builtin import (
    AbandonedObjectRule,
    AbsenceRule,
    CountingRule,
    CrowdRule,
    IntrusionRule,
    dist,
    parse_duration,
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


def _ctx(
    camera_id: str = "cam1", timestamp: float | None = None
) -> dict:
    return {
        "camera_id": camera_id,
        "camera_name": "Test Camera",
        "timestamp": timestamp if timestamp is not None else 1000.0,
    }


# Unit square zone: (0,0)-(100,0)-(100,100)-(0,100)
ZONE = [(0, 0), (100, 0), (100, 100), (0, 100)]


# ─── 1. parse_duration ────────────────────────────────────────


class TestParseDuration:
    def test_seconds(self):
        assert parse_duration("10s") == 10.0

    def test_minutes(self):
        assert parse_duration("5m") == 300.0

    def test_hours(self):
        assert parse_duration("1h") == 3600.0

    def test_days(self):
        assert parse_duration("1d") == 86400.0

    def test_fractional_seconds(self):
        assert parse_duration("0.5s") == 0.5

    def test_fractional_minutes(self):
        assert parse_duration("1.5m") == 90.0

    def test_uppercase_unit(self):
        assert parse_duration("10S") == 10.0

    def test_whitespace_around_value(self):
        assert parse_duration("  5m  ") == 300.0

    def test_empty_string_returns_default(self):
        assert parse_duration("") == 300.0

    def test_invalid_format_returns_default(self):
        assert parse_duration("abc") == 300.0

    def test_missing_unit_returns_default(self):
        assert parse_duration("10") == 300.0

    def test_custom_default(self):
        assert parse_duration("bad", default_seconds=60.0) == 60.0

    def test_zero_seconds(self):
        assert parse_duration("0s") == 0.0


# ─── 2. dist utility ──────────────────────────────────────────


class TestDist:
    def test_same_point(self):
        assert dist((0, 0), (0, 0)) == 0.0

    def test_known_distance(self):
        assert dist((0, 0), (3, 4)) == pytest.approx(5.0)


# ─── 3. IntrusionRule ────────────────────────────────────────


class TestIntrusionRule:
    def test_target_in_zone_not_yet_persist(self):
        """Target inside zone but not long enough -> no event."""
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=5.0
        )
        t = _track(center=(50, 50))
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        assert ev is None

    def test_target_in_zone_persist_triggers(self):
        """Target inside zone for >= persist_seconds -> event."""
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=5.0
        )
        t = _track(center=(50, 50))

        # Frame 1: first seen, records entry time
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        assert ev is None

        # Frame 2: 6 seconds later, exceeds persist
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1006.0))
        assert ev is not None
        assert ev.event_type == "intrusion"
        assert ev.rule_name == "intrusion"
        assert ev.severity == Severity.CRITICAL
        assert ev.metadata["persist_seconds"] == 5.0

    def test_target_exits_zone_clears_state(self):
        """Target leaves zone -> cached entry time cleared, timer resets."""
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=5.0
        )
        t_in = _track(track_id=1, center=(50, 50))

        # Frame 1: enter zone
        rule.evaluate([t_in], _frame(), _ctx(timestamp=1000.0))

        # Frame 2: leave zone
        t_out = _track(track_id=1, center=(200, 200))
        rule.evaluate([t_out], _frame(), _ctx(timestamp=1003.0))

        # Frame 3: re-enter zone
        t_in2 = _track(track_id=1, center=(50, 50))
        ev = rule.evaluate([t_in2], _frame(), _ctx(timestamp=1004.0))
        # Re-entered at 1004, persist=5, need 1009+ to trigger
        assert ev is None

        ev = rule.evaluate([t_in2], _frame(), _ctx(timestamp=1010.0))
        assert ev is not None

    def test_class_filtering(self):
        """Only target_classes triggers the rule."""
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=1.0,
            target_classes=["car"],
        )
        t_person = _track(track_id=1, center=(50, 50), class_name="person")
        # Person should be ignored
        rule.evaluate([t_person], _frame(), _ctx(timestamp=1000.0))
        rule.evaluate([t_person], _frame(), _ctx(timestamp=1005.0))
        # No event because person is not in target_classes
        # Re-check: evaluate once more to be sure
        ev = rule.evaluate([t_person], _frame(), _ctx(timestamp=1010.0))
        assert ev is None

    def test_class_filtering_matching_class(self):
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=2.0,
            target_classes=["car"],
        )
        t_car = _track(track_id=1, center=(50, 50), class_name="car")
        ev = rule.evaluate([t_car], _frame(), _ctx(timestamp=1000.0))
        assert ev is None
        ev = rule.evaluate([t_car], _frame(), _ctx(timestamp=1003.0))
        assert ev is not None
        assert ev.event_type == "intrusion"

    def test_target_outside_zone_no_trigger(self):
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=1.0
        )
        t = _track(center=(200, 200))
        rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1010.0))
        assert ev is None

    def test_empty_tracks(self):
        rule = IntrusionRule(name="intrusion", zone=ZONE)
        ev = rule.evaluate([], _frame(), _ctx())
        assert ev is None

    def test_reset_clears_cache(self):
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=2.0
        )
        t = _track(center=(50, 50))
        rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        rule.reset()
        # After reset, entry time is cleared; should not trigger immediately
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1001.0))
        assert ev is None

    def test_name_property(self):
        rule = IntrusionRule(name="my_intrusion", zone=ZONE)
        assert rule.name == "my_intrusion"

    def test_camera_ids_property(self):
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, camera_ids=["cam_a"]
        )
        assert rule.camera_ids == ["cam_a"]

    def test_camera_ids_default_none(self):
        rule = IntrusionRule(name="intrusion", zone=ZONE)
        assert rule.camera_ids is None

    def test_metadata_contains_zone_and_track_ids(self):
        rule = IntrusionRule(
            name="intrusion", zone=ZONE, persist_seconds=1.0
        )
        t = _track(track_id=7, center=(50, 50))
        rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1002.0))
        assert ev is not None
        assert 7 in ev.metadata["track_ids"]
        assert ev.metadata["zone"] == ZONE


# ─── 4. AbsenceRule ───────────────────────────────────────────


class TestAbsenceRule:
    def test_zone_has_person_no_trigger(self):
        rule = AbsenceRule(
            name="absence", zone=ZONE, min_absent_seconds=60.0
        )
        t = _track(center=(50, 50))
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        assert ev is None

    def test_zone_empty_not_long_enough(self):
        rule = AbsenceRule(
            name="absence", zone=ZONE, min_absent_seconds=60.0
        )
        # First frame with no one: records empty_since
        ev = rule.evaluate([], _frame(), _ctx(timestamp=1000.0))
        assert ev is None
        # 30 seconds later: not yet
        ev = rule.evaluate([], _frame(), _ctx(timestamp=1030.0))
        assert ev is None

    def test_zone_empty_triggers_after_duration(self):
        rule = AbsenceRule(
            name="absence", zone=ZONE, min_absent_seconds=60.0
        )
        # Frame 1: start empty
        ev = rule.evaluate([], _frame(), _ctx(timestamp=1000.0))
        assert ev is None
        # Frame 2: 60 seconds later
        ev = rule.evaluate([], _frame(), _ctx(timestamp=1060.0))
        assert ev is not None
        assert ev.event_type == "absence"
        assert ev.severity == Severity.WARNING
        assert ev.metadata["duration"] == pytest.approx(60.0)
        assert ev.metadata["empty_since"] == 1000.0

    def test_person_returns_clears_state(self):
        """Person reappearing should clear the absence timer."""
        rule = AbsenceRule(
            name="absence", zone=ZONE, min_absent_seconds=30.0
        )
        # Empty for 20s
        rule.evaluate([], _frame(), _ctx(timestamp=1000.0))
        ev = rule.evaluate([], _frame(), _ctx(timestamp=1020.0))
        assert ev is None

        # Person shows up -> clears state
        t = _track(center=(50, 50))
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1025.0))
        assert ev is None

        # Zone empty again: timer restarts from 1050 (first empty frame)
        ev = rule.evaluate([], _frame(), _ctx(timestamp=1050.0))
        assert ev is None  # First empty frame sets empty_since=1050

        ev = rule.evaluate([], _frame(), _ctx(timestamp=1070.0))
        assert ev is None  # 20s since restart, < 30s

        ev = rule.evaluate([], _frame(), _ctx(timestamp=1081.0))
        assert ev is not None  # 31s since restart, >= 30s

    def test_class_filtering_person_outside_zone(self):
        """Person exists but outside zone -> zone still empty."""
        rule = AbsenceRule(
            name="absence", zone=ZONE, min_absent_seconds=10.0
        )
        t_outside = _track(center=(200, 200))
        rule.evaluate([t_outside], _frame(), _ctx(timestamp=1000.0))
        ev = rule.evaluate([t_outside], _frame(), _ctx(timestamp=1011.0))
        assert ev is not None

    def test_class_filtering_non_person_ignored(self):
        """Non-target class in zone -> zone still empty."""
        rule = AbsenceRule(
            name="absence", zone=ZONE, min_absent_seconds=10.0,
            target_classes=["person"],
        )
        t_car = _track(center=(50, 50), class_name="car")
        rule.evaluate([t_car], _frame(), _ctx(timestamp=1000.0))
        ev = rule.evaluate([t_car], _frame(), _ctx(timestamp=1011.0))
        assert ev is not None

    def test_empty_tracks(self):
        rule = AbsenceRule(name="absence", zone=ZONE, min_absent_seconds=10.0)
        ev = rule.evaluate([], _frame(), _ctx())
        assert ev is None

    def test_reset_clears_cache(self):
        rule = AbsenceRule(name="absence", zone=ZONE, min_absent_seconds=10.0)
        rule.evaluate([], _frame(), _ctx(timestamp=1000.0))
        rule.reset()
        ev = rule.evaluate([], _frame(), _ctx(timestamp=1005.0))
        # Reset cleared empty_since; this frame sets a new one, so only 0s elapsed
        assert ev is None

    def test_name_property(self):
        rule = AbsenceRule(name="my_absence", zone=ZONE)
        assert rule.name == "my_absence"

    def test_camera_ids_property(self):
        rule = AbsenceRule(name="absence", zone=ZONE, camera_ids=["cam1"])
        assert rule.camera_ids == ["cam1"]


# ─── 5. CrowdRule ─────────────────────────────────────────────


class TestCrowdRule:
    def test_below_threshold_no_trigger(self):
        rule = CrowdRule(name="crowd", threshold=5)
        tracks = [_track(track_id=i, center=(i * 10, 50)) for i in range(4)]
        ev = rule.evaluate(tracks, _frame(), _ctx())
        assert ev is None

    def test_at_threshold_triggers(self):
        rule = CrowdRule(name="crowd", threshold=3)
        tracks = [_track(track_id=i, center=(i * 10, 50)) for i in range(3)]
        ev = rule.evaluate(tracks, _frame(), _ctx())
        assert ev is not None
        assert ev.event_type == "crowd"
        assert ev.metadata["count"] == 3
        assert ev.metadata["threshold"] == 3

    def test_above_threshold_triggers(self):
        rule = CrowdRule(name="crowd", threshold=3)
        tracks = [_track(track_id=i, center=(i * 10, 50)) for i in range(5)]
        ev = rule.evaluate(tracks, _frame(), _ctx())
        assert ev is not None
        assert ev.metadata["count"] == 5

    def test_zone_filtering_outside_not_counted(self):
        rule = CrowdRule(name="crowd", threshold=3, zone=ZONE)
        t_in = [_track(track_id=i, center=(i * 10, 50)) for i in range(3)]
        t_out = [_track(track_id=10, center=(200, 200), class_name="person")]
        ev = rule.evaluate(t_in + t_out, _frame(), _ctx())
        assert ev is not None
        assert ev.metadata["count"] == 3  # Only those inside zone

    def test_zone_filtering_all_outside(self):
        rule = CrowdRule(name="crowd", threshold=2, zone=ZONE)
        tracks = [_track(track_id=i, center=(200, 200)) for i in range(5)]
        ev = rule.evaluate(tracks, _frame(), _ctx())
        assert ev is None

    def test_no_zone_counts_all(self):
        rule = CrowdRule(name="crowd", threshold=3, zone=None)
        tracks = [_track(track_id=i, center=(i * 10, 50)) for i in range(3)]
        ev = rule.evaluate(tracks, _frame(), _ctx())
        assert ev is not None
        assert ev.metadata["count"] == 3

    def test_dedup_same_track_id(self):
        """Same track_id appearing twice in the list should only be counted once."""
        rule = CrowdRule(name="crowd", threshold=2)
        # Two entries with the same track_id=1
        t1a = _track(track_id=1, center=(50, 50))
        t1b = _track(track_id=1, center=(55, 55))
        t2 = _track(track_id=2, center=(70, 70))
        ev = rule.evaluate([t1a, t1b, t2], _frame(), _ctx())
        # 2 unique track_ids, threshold=2 -> triggers
        assert ev is not None
        assert ev.metadata["count"] == 2

    def test_class_filtering(self):
        rule = CrowdRule(
            name="crowd", threshold=2, target_classes=["car"]
        )
        persons = [_track(track_id=i, center=(i * 10, 50)) for i in range(5)]
        ev = rule.evaluate(persons, _frame(), _ctx())
        assert ev is None

    def test_empty_tracks(self):
        rule = CrowdRule(name="crowd", threshold=1)
        ev = rule.evaluate([], _frame(), _ctx())
        assert ev is None

    def test_reset_noop(self):
        rule = CrowdRule(name="crowd", threshold=1)
        rule.reset()  # Should not raise

    def test_name_property(self):
        rule = CrowdRule(name="my_crowd", threshold=5)
        assert rule.name == "my_crowd"

    def test_camera_ids_property(self):
        rule = CrowdRule(name="crowd", threshold=5, camera_ids=["c1"])
        assert rule.camera_ids == ["c1"]

    def test_metadata_contains_track_ids(self):
        rule = CrowdRule(name="crowd", threshold=2)
        tracks = [_track(track_id=3, center=(50, 50)), _track(track_id=4, center=(60, 60))]
        ev = rule.evaluate(tracks, _frame(), _ctx())
        assert ev is not None
        assert set(ev.metadata["track_ids"]) == {3, 4}

    def test_severity_default_warning(self):
        rule = CrowdRule(name="crowd", threshold=1)
        assert rule._severity == Severity.WARNING

    def test_severity_custom(self):
        rule = CrowdRule(name="crowd", threshold=1, severity="critical")
        assert rule._severity == Severity.CRITICAL


# ─── 6. AbandonedObjectRule ───────────────────────────────────


class TestAbandonedObjectRule:
    def test_object_stationary_no_nearby_person_triggers(self):
        """Object stationary long enough with no nearby person -> event."""
        rule = AbandonedObjectRule(
            name="abandoned",
            duration_seconds=60.0,
            max_velocity=5.0,
            proximity_radius=100.0,
        )
        # Backpack trajectory: same position across frames (speed=0)
        t_backpack = _track(
            track_id=1,
            class_name="backpack",
            center=(200, 200),
            trajectory=[(200, 200, 1000.0), (200, 200, 1001.0)],
        )

        # Frame 1: first time stationary
        ev = rule.evaluate([t_backpack], _frame(), _ctx(timestamp=1000.0))
        assert ev is None

        # Frame 2: 61 seconds later, stationary, no people nearby
        t_backpack2 = _track(
            track_id=1,
            class_name="backpack",
            center=(200, 200),
            trajectory=[(200, 200, 1050.0), (200, 200, 1061.0)],
        )
        ev = rule.evaluate([t_backpack2], _frame(), _ctx(timestamp=1061.0))
        assert ev is not None
        assert ev.event_type == "abandoned_object"
        assert ev.severity == Severity.CRITICAL
        assert ev.metadata["class_name"] == "backpack"

    def test_object_moving_resets_timer(self):
        """Object moving above max_velocity -> stationary timer is reset."""
        rule = AbandonedObjectRule(
            name="abandoned",
            duration_seconds=10.0,
            max_velocity=5.0,
            proximity_radius=100.0,
        )
        # Frame 1: object is stationary
        t1 = _track(
            track_id=1,
            class_name="suitcase",
            center=(200, 200),
            trajectory=[(200, 200, 1000.0), (200, 200, 1001.0)],
        )
        rule.evaluate([t1], _frame(), _ctx(timestamp=1000.0))

        # Frame 2: object moved fast (speed = 500px / 1s = 500 px/s >> 5.0)
        t2 = _track(
            track_id=1,
            class_name="suitcase",
            center=(700, 200),
            trajectory=[(200, 200, 1001.0), (700, 200, 1002.0)],
        )
        rule.evaluate([t2], _frame(), _ctx(timestamp=1002.0))

        # Frame 3: object stationary again, but only 1 second since re-entering
        t3 = _track(
            track_id=1,
            class_name="suitcase",
            center=(700, 200),
            trajectory=[(700, 200, 1010.0), (700, 200, 1011.0)],
        )
        ev = rule.evaluate([t3], _frame(), _ctx(timestamp=1011.0))
        assert ev is None

    def test_nearby_person_blocks_trigger(self):
        """Object stationary but person nearby -> no event."""
        rule = AbandonedObjectRule(
            name="abandoned",
            duration_seconds=10.0,
            max_velocity=5.0,
            proximity_radius=100.0,
        )
        backpack = _track(
            track_id=1,
            class_name="backpack",
            center=(200, 200),
            trajectory=[(200, 200, 1000.0), (200, 200, 1001.0)],
        )
        # Frame 1: register as stationary
        rule.evaluate([backpack], _frame(), _ctx(timestamp=1000.0))

        # Frame 2: still stationary, but person within 100px
        backpack2 = _track(
            track_id=1,
            class_name="backpack",
            center=(200, 200),
            trajectory=[(200, 200, 1010.0), (200, 200, 1011.0)],
        )
        person = _track(
            track_id=2,
            class_name="person",
            center=(250, 200),  # 50px away, within proximity_radius=100
        )
        ev = rule.evaluate(
            [backpack2, person], _frame(), _ctx(timestamp=1011.0)
        )
        assert ev is None

    def test_class_filtering(self):
        """Only target_classes are considered abandoned objects."""
        rule = AbandonedObjectRule(
            name="abandoned",
            duration_seconds=5.0,
            max_velocity=5.0,
            proximity_radius=50.0,
            target_classes=["backpack"],
        )
        # A "suitcase" should not trigger since it's not in target_classes
        t_suitcase = _track(
            track_id=1,
            class_name="suitcase",
            center=(200, 200),
            trajectory=[(200, 200, 1000.0), (200, 200, 1001.0)],
        )
        rule.evaluate([t_suitcase], _frame(), _ctx(timestamp=1000.0))
        ev = rule.evaluate([t_suitcase], _frame(), _ctx(timestamp=1010.0))
        assert ev is None

    def test_default_target_classes(self):
        rule = AbandonedObjectRule(name="abandoned")
        assert rule._target_classes == ["backpack", "suitcase", "box"]

    def test_empty_tracks(self):
        rule = AbandonedObjectRule(name="abandoned")
        ev = rule.evaluate([], _frame(), _ctx())
        assert ev is None

    def test_reset_clears_cache(self):
        rule = AbandonedObjectRule(
            name="abandoned", duration_seconds=10.0
        )
        t = _track(
            track_id=1,
            class_name="backpack",
            center=(200, 200),
            trajectory=[(200, 200, 1000.0), (200, 200, 1001.0)],
        )
        rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        rule.reset()
        # After reset, should re-register as stationary
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1001.0))
        assert ev is None  # First frame after reset, just registers

    def test_name_property(self):
        rule = AbandonedObjectRule(name="my_abandoned")
        assert rule.name == "my_abandoned"

    def test_camera_ids_property(self):
        rule = AbandonedObjectRule(name="abandoned", camera_ids=["cam1"])
        assert rule.camera_ids == ["cam1"]

    def test_compute_speed_static(self):
        t = _track(
            trajectory=[(100, 100, 0.0), (100, 100, 1.0)]
        )
        assert AbandonedObjectRule._compute_speed(t) == 0.0

    def test_compute_speed_moving(self):
        t = _track(
            trajectory=[(100, 100, 0.0), (200, 100, 1.0)]
        )
        assert AbandonedObjectRule._compute_speed(t) == pytest.approx(100.0)

    def test_compute_speed_single_point(self):
        t = _track(trajectory=[(100, 100, 0.0)])
        assert AbandonedObjectRule._compute_speed(t) == 0.0

    def test_compute_speed_empty_trajectory(self):
        t = _track(trajectory=[])
        assert AbandonedObjectRule._compute_speed(t) == 0.0

    def test_zone_filtering_object_outside(self):
        """Object outside configured zone -> not tracked."""
        rule = AbandonedObjectRule(
            name="abandoned",
            duration_seconds=5.0,
            max_velocity=5.0,
            zone=[(0, 0), (100, 0), (100, 100), (0, 100)],
        )
        t = _track(
            track_id=1,
            class_name="backpack",
            center=(300, 300),
            trajectory=[(300, 300, 1000.0), (300, 300, 1001.0)],
        )
        rule.evaluate([t], _frame(), _ctx(timestamp=1000.0))
        ev = rule.evaluate([t], _frame(), _ctx(timestamp=1010.0))
        assert ev is None


# ─── 7. CountingRule ──────────────────────────────────────────


class TestCountingRule:
    # Horizontal counting line at y=50, from x=0 to x=100
    LINE_START = (0, 50)
    LINE_END = (100, 50)

    def test_trajectory_crosses_line_triggers(self):
        """Track moves from below to above the line (y decreasing = 'in')."""
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
        )
        # Moving from y=80 to y=20, crossing y=50
        t = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        ev = rule.evaluate([t], _frame(), _ctx())
        assert ev is not None
        assert ev.event_type == "counting"

    def test_trajectory_no_crossing(self):
        """Track stays on one side -> no event."""
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
        )
        t = _track(
            track_id=1,
            trajectory=[(50, 10, 0.0), (50, 20, 1.0)],
        )
        ev = rule.evaluate([t], _frame(), _ctx())
        assert ev is None

    def test_direction_in_only(self):
        """Only count 'in' direction crossings."""
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="in",
        )
        # "in" direction: depends on line normal.
        # Line from (0,50) to (100,50): dx=100, dy=0, normal=(0,100)
        # Move from y=80 to y=20: move_dy = -60, dot = -60*100 < 0 -> "out"
        # Move from y=20 to y=80: move_dy = 60, dot = 60*100 > 0 -> "in"
        t_in = _track(
            track_id=1,
            trajectory=[(50, 20, 0.0), (50, 80, 1.0)],
        )
        ev = rule.evaluate([t_in], _frame(), _ctx())
        assert ev is not None
        assert ev.metadata["in_count"] == 1
        assert ev.metadata["out_count"] == 0

    def test_direction_out_only(self):
        """Only count 'out' direction crossings."""
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="out",
        )
        # "out": y=80 -> y=20, dot < 0 -> "out"
        t_out = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        ev = rule.evaluate([t_out], _frame(), _ctx())
        assert ev is not None
        assert ev.metadata["in_count"] == 0
        assert ev.metadata["out_count"] == 1

    def test_direction_filter_blocks_wrong_direction(self):
        """Direction 'in' filter should block 'out' crossings."""
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="in",
        )
        # "out" direction crossing
        t_out = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        ev = rule.evaluate([t_out], _frame(), _ctx())
        assert ev is None

    def test_threshold_not_reached(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
            alert_threshold=5,
        )
        t = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        ev = rule.evaluate([t], _frame(), _ctx())
        # count=1, threshold=5 -> no event
        assert ev is None

    def test_threshold_reached(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
            alert_threshold=3,
        )
        tracks = [
            _track(track_id=i, trajectory=[(50, 80 + i, 0.0), (50, 20 + i, 1.0)])
            for i in range(3)
        ]
        ev = rule.evaluate(tracks, _frame(), _ctx())
        assert ev is not None
        assert ev.metadata["in_count"] + ev.metadata["out_count"] == 3

    def test_short_trajectory_ignored(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
        )
        t = _track(track_id=1, trajectory=[(50, 50, 0.0)])
        ev = rule.evaluate([t], _frame(), _ctx())
        assert ev is None

    def test_same_track_not_counted_twice(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
        )
        t = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        ev1 = rule.evaluate([t], _frame(), _ctx())
        assert ev1 is not None

        # Same trajectory, same track_id -> already counted, no new crossing
        t2 = _track(
            track_id=1,
            trajectory=[(50, 20, 1.0), (50, 80, 2.0)],
        )
        ev2 = rule.evaluate([t2], _frame(), _ctx())
        # track_id=1 is in _counted_tracks, so no new crossing counted
        assert ev2 is None

    def test_reset_clears_state(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
            alert_threshold=10,
        )
        t = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        rule.evaluate([t], _frame(), _ctx())
        assert rule._in_count + rule._out_count == 1
        rule.reset()
        assert rule._in_count == 0
        assert rule._out_count == 0
        assert len(rule._counted_tracks) == 0

    def test_reset_interval_clears_counts(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
            alert_threshold=2,
            reset_interval_seconds=30.0,
        )
        # Align _last_reset_time with our simulated timestamps
        rule._last_reset_time = 1000.0

        t1 = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        rule.evaluate([t1], _frame(), _ctx(timestamp=1000.0))
        assert rule._in_count + rule._out_count == 1

        # 31 seconds later: reset interval passed, counters should reset
        t2 = _track(
            track_id=2,
            trajectory=[(50, 80, 31.0), (50, 20, 32.0)],
        )
        ev = rule.evaluate([t2], _frame(), _ctx(timestamp=1031.0))
        # Count reset to 0, then new crossing counted = 1, threshold=2 -> None
        assert ev is None

    def test_class_filtering(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            target_classes=["car"],
        )
        t_person = _track(
            track_id=1,
            class_name="person",
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        ev = rule.evaluate([t_person], _frame(), _ctx())
        assert ev is None

    def test_empty_tracks(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
        )
        ev = rule.evaluate([], _frame(), _ctx())
        assert ev is None

    def test_name_property(self):
        rule = CountingRule(
            name="my_counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
        )
        assert rule.name == "my_counting"

    def test_camera_ids_property(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            camera_ids=["cam1"],
        )
        assert rule.camera_ids == ["cam1"]

    def test_camera_ids_default_none(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
        )
        assert rule.camera_ids is None

    def test_severity_default_info(self):
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
        )
        assert rule._severity == Severity.INFO

    def test_both_directions_counted(self):
        """Both 'in' and 'out' crossings counted with direction='both'."""
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
        )
        # "in" crossing
        t_in = _track(
            track_id=1,
            trajectory=[(50, 20, 0.0), (50, 80, 1.0)],
        )
        rule.evaluate([t_in], _frame(), _ctx(timestamp=1000.0))

        # "out" crossing with a different track
        t_out = _track(
            track_id=2,
            trajectory=[(50, 80, 1.0), (50, 20, 2.0)],
        )
        ev = rule.evaluate([t_out], _frame(), _ctx(timestamp=1002.0))
        assert ev is not None
        assert ev.metadata["in_count"] == 1
        assert ev.metadata["out_count"] == 1

    def test_event_without_threshold_fires_every_crossing(self):
        """With alert_threshold=0, every crossing triggers an event."""
        rule = CountingRule(
            name="counting",
            line_start=self.LINE_START,
            line_end=self.LINE_END,
            direction="both",
            alert_threshold=0,
        )
        t1 = _track(
            track_id=1,
            trajectory=[(50, 80, 0.0), (50, 20, 1.0)],
        )
        ev1 = rule.evaluate([t1], _frame(), _ctx(timestamp=1000.0))
        assert ev1 is not None

        t2 = _track(
            track_id=2,
            trajectory=[(50, 80, 1.0), (50, 20, 2.0)],
        )
        ev2 = rule.evaluate([t2], _frame(), _ctx(timestamp=1002.0))
        assert ev2 is not None

    def test_segments_intersect_true(self):
        """Segments that cross should return True."""
        assert CountingRule._segments_intersect(
            (0, 0), (10, 10), (0, 10), (10, 0)
        ) is True

    def test_segments_intersect_false(self):
        """Parallel segments should return False."""
        assert CountingRule._segments_intersect(
            (0, 0), (10, 0), (0, 10), (10, 10)
        ) is False
