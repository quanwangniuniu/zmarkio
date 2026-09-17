"""
Tests for booking-link availability math.

Pure-function tests: no DB, no HTTP. They pin the arithmetic that decides what
a prospect is offered — half-open interval handling, buffer semantics, DST, and
the notice/horizon bounds.
"""

from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

import pytest

from calendars.availability import (
    BookingRules,
    WeeklyWindow,
    compute_available_slots,
    expand_weekly_windows,
    generate_slots,
    merge_intervals,
    pad_busy_intervals,
    subtract_intervals,
)


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


WEEKDAYS_9_TO_5 = [
    WeeklyWindow(weekday=d, start=time(9, 0), end=time(17, 0)) for d in range(5)
]


class TestMergeIntervals:
    def test_merges_overlapping_and_touching(self):
        merged = merge_intervals([
            (utc(2026, 9, 1, 9), utc(2026, 9, 1, 10)),
            (utc(2026, 9, 1, 9, 30), utc(2026, 9, 1, 11)),  # overlaps
            (utc(2026, 9, 1, 11), utc(2026, 9, 1, 12)),     # exactly adjacent
        ])
        assert merged == [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 12))]

    def test_keeps_disjoint_intervals_separate(self):
        merged = merge_intervals([
            (utc(2026, 9, 1, 14), utc(2026, 9, 1, 15)),
            (utc(2026, 9, 1, 9), utc(2026, 9, 1, 10)),
        ])
        assert merged == [
            (utc(2026, 9, 1, 9), utc(2026, 9, 1, 10)),
            (utc(2026, 9, 1, 14), utc(2026, 9, 1, 15)),
        ]

    def test_drops_zero_and_negative_length(self):
        assert merge_intervals([(utc(2026, 9, 1, 9), utc(2026, 9, 1, 9))]) == []
        assert merge_intervals([(utc(2026, 9, 1, 10), utc(2026, 9, 1, 9))]) == []


class TestSubtractIntervals:
    def test_punches_a_hole_in_the_middle(self):
        remaining = subtract_intervals(
            [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 17))],
            [(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))],
        )
        assert remaining == [
            (utc(2026, 9, 1, 9), utc(2026, 9, 1, 12)),
            (utc(2026, 9, 1, 13), utc(2026, 9, 1, 17)),
        ]

    def test_fully_blocked_leaves_nothing(self):
        assert subtract_intervals(
            [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 17))],
            [(utc(2026, 9, 1, 8), utc(2026, 9, 1, 18))],
        ) == []

    def test_touching_block_does_not_consume_time(self):
        # Half-open intervals: a block ending exactly at 09:00 blocks nothing.
        assert subtract_intervals(
            [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 17))],
            [(utc(2026, 9, 1, 8), utc(2026, 9, 1, 9))],
        ) == [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 17))]

    def test_overlapping_blocks_are_handled_together(self):
        assert subtract_intervals(
            [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 17))],
            [
                (utc(2026, 9, 1, 10), utc(2026, 9, 1, 12)),
                (utc(2026, 9, 1, 11), utc(2026, 9, 1, 13)),
            ],
        ) == [
            (utc(2026, 9, 1, 9), utc(2026, 9, 1, 10)),
            (utc(2026, 9, 1, 13), utc(2026, 9, 1, 17)),
        ]


class TestBuffers:
    def test_buffers_pad_in_the_crossed_direction(self):
        rules = BookingRules(
            duration_minutes=30, buffer_before_minutes=15, buffer_after_minutes=10
        )
        padded = pad_busy_intervals(
            [(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))], rules
        )
        # buffer_after pulls the start back; buffer_before pushes the end out.
        assert padded == [(utc(2026, 9, 1, 11, 50), utc(2026, 9, 1, 13, 15))]

    def test_no_buffers_is_a_plain_merge(self):
        rules = BookingRules(duration_minutes=30)
        busy = [(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))]
        assert pad_busy_intervals(busy, rules) == busy


class TestGenerateSlots:
    def test_slots_align_to_local_midnight_not_the_free_edge(self):
        # Free time starts at an awkward 10:07; slots should still land on :15s.
        slots = generate_slots(
            [(utc(2026, 9, 1, 10, 7), utc(2026, 9, 1, 11, 30))],
            BookingRules(duration_minutes=30, slot_increment_minutes=15),
            "UTC",
        )
        assert slots[0][0] == utc(2026, 9, 1, 10, 15)
        assert [s.minute for s, _ in slots] == [15, 30, 45, 0]

    def test_alignment_respects_half_hour_offset_zones(self):
        # Asia/Kolkata is UTC+05:30 — aligning on UTC midnight would be wrong.
        slots = generate_slots(
            [(utc(2026, 9, 1, 4, 0), utc(2026, 9, 1, 6, 0))],
            BookingRules(duration_minutes=30, slot_increment_minutes=30),
            "Asia/Kolkata",
        )
        # 04:00Z is 09:30 local, already on a 30-minute local boundary.
        assert slots[0][0] == utc(2026, 9, 1, 4, 0)

    def test_slot_must_fit_entirely_inside_free_time(self):
        slots = generate_slots(
            [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 9, 45))],
            BookingRules(duration_minutes=30, slot_increment_minutes=15),
            "UTC",
        )
        assert slots == [
            (utc(2026, 9, 1, 9), utc(2026, 9, 1, 9, 30)),
            (utc(2026, 9, 1, 9, 15), utc(2026, 9, 1, 9, 45)),
        ]

    def test_no_slots_when_free_time_is_shorter_than_duration(self):
        assert generate_slots(
            [(utc(2026, 9, 1, 9), utc(2026, 9, 1, 9, 20))],
            BookingRules(duration_minutes=30),
            "UTC",
        ) == []


class TestWeeklyWindows:
    def test_only_configured_weekdays_are_open(self):
        # 2026-09-05 is a Saturday; Mon–Fri windows must not produce anything.
        intervals = expand_weekly_windows(
            WEEKDAYS_9_TO_5, utc(2026, 9, 5), utc(2026, 9, 6), "UTC"
        )
        assert intervals == []

    def test_window_holds_local_time_across_dst(self):
        # US DST ends 2026-11-01. A 09:00 America/New_York window is 13:00Z
        # before the change and 14:00Z after it.
        before = expand_weekly_windows(
            [WeeklyWindow(weekday=4, start=time(9, 0), end=time(10, 0))],
            utc(2026, 10, 30), utc(2026, 10, 31), "America/New_York",
        )
        after = expand_weekly_windows(
            [WeeklyWindow(weekday=4, start=time(9, 0), end=time(10, 0))],
            utc(2026, 11, 6), utc(2026, 11, 7), "America/New_York",
        )
        assert before[0][0].hour == 13
        assert after[0][0].hour == 14

    def test_windows_are_clipped_to_the_requested_range(self):
        intervals = expand_weekly_windows(
            WEEKDAYS_9_TO_5,
            utc(2026, 9, 1, 12),   # Tuesday midday
            utc(2026, 9, 1, 14),
            "UTC",
        )
        assert intervals == [(utc(2026, 9, 1, 12), utc(2026, 9, 1, 14))]


class TestComputeAvailableSlots:
    def _call(self, **overrides):
        kwargs = dict(
            windows=WEEKDAYS_9_TO_5,
            busy=[],
            rules=BookingRules(duration_minutes=60, slot_increment_minutes=60),
            tz_name="UTC",
            range_start=utc(2026, 9, 1),
            range_end=utc(2026, 9, 2),
            now=utc(2026, 8, 31, 12),
        )
        kwargs.update(overrides)
        return compute_available_slots(**kwargs)

    def test_full_open_day_yields_hourly_slots(self):
        slots = self._call()
        assert len(slots) == 8  # 09:00–17:00
        assert slots[0] == (utc(2026, 9, 1, 9), utc(2026, 9, 1, 10))
        assert slots[-1] == (utc(2026, 9, 1, 16), utc(2026, 9, 1, 17))

    def test_busy_time_removes_the_overlapping_slot(self):
        slots = self._call(busy=[(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))])
        starts = [s for s, _ in slots]
        assert utc(2026, 9, 1, 12) not in starts
        assert len(slots) == 7

    def test_buffers_remove_the_neighbouring_slots_too(self):
        slots = self._call(
            busy=[(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))],
            rules=BookingRules(
                duration_minutes=60,
                slot_increment_minutes=60,
                buffer_before_minutes=30,
                buffer_after_minutes=30,
            ),
        )
        starts = [s for s, _ in slots]
        # 11:00 would end at 12:00 with no gap, 13:00 starts with no gap.
        assert utc(2026, 9, 1, 11) not in starts
        assert utc(2026, 9, 1, 13) not in starts

    def test_min_notice_hides_imminent_slots(self):
        # "Now" is 09:00 on the day itself with 4 hours' notice required.
        slots = self._call(
            now=utc(2026, 9, 1, 9),
            rules=BookingRules(
                duration_minutes=60, slot_increment_minutes=60, min_notice_minutes=240
            ),
        )
        assert slots[0][0] >= utc(2026, 9, 1, 13)

    def test_horizon_caps_how_far_ahead_bookings_go(self):
        slots = self._call(
            range_start=utc(2026, 9, 1),
            range_end=utc(2026, 12, 1),
            now=utc(2026, 9, 1, 0),
            rules=BookingRules(
                duration_minutes=60, slot_increment_minutes=60, max_advance_days=2
            ),
        )
        assert slots
        assert max(e for _, e in slots) <= utc(2026, 9, 3)

    def test_no_windows_means_no_availability(self):
        assert self._call(windows=[]) == []

    def test_returns_empty_when_notice_exceeds_the_horizon(self):
        assert self._call(
            rules=BookingRules(
                duration_minutes=60,
                min_notice_minutes=60 * 24 * 10,
                max_advance_days=1,
            )
        ) == []


class TestRuleValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"duration_minutes": 0},
            {"duration_minutes": -30},
            {"duration_minutes": 30, "slot_increment_minutes": 0},
            {"duration_minutes": 30, "max_advance_days": 0},
            {"duration_minutes": 30, "buffer_before_minutes": -5},
            {"duration_minutes": 30, "min_notice_minutes": -1},
        ],
    )
    def test_invalid_rules_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            BookingRules(**kwargs)

    def test_window_must_start_before_it_ends(self):
        with pytest.raises(ValueError):
            WeeklyWindow(weekday=0, start=time(17, 0), end=time(9, 0))

    def test_weekday_must_be_in_range(self):
        with pytest.raises(ValueError):
            WeeklyWindow(weekday=7, start=time(9, 0), end=time(17, 0))
