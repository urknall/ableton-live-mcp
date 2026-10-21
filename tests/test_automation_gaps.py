"""Regressions missing from the combined automation write/read PRs."""

from types import SimpleNamespace

import pytest
from test_automation import MAX_STEPS, FakeClip, _bridge, steps_for, write_automation
from test_read_automation import _bridge as read_bridge
from test_read_automation import read_automation


def test_default_points_hold_until_next_point_and_clip_end():
    clip = FakeClip()
    write_automation(
        _bridge(clip),
        0,
        0,
        0,
        "Frequency",
        [
            {"time": 0, "value": 0.2},
            {"time": 4, "value": 0.8},
        ],
    )
    assert clip.envelope.value_at_time(2) == 0.2
    assert clip.envelope.value_at_time(8) == 0.8
    assert all(length > 0 for _, length, _ in clip.envelope.steps)


def test_single_default_point_holds_and_settles_at_clip_start():
    clip = FakeClip()
    result = write_automation(_bridge(clip), 0, 0, 0, "Frequency", [{"time": 0, "value": 0.2}])
    assert clip.envelope.value_at_time(0) == 0.2
    assert clip.envelope.value_at_time(15.99) == 0.2
    assert result["start_settled"] is True


@pytest.mark.parametrize(
    "points",
    [
        [{"time": 0, "value": 0.2, "duration": 0}],
        [{"time": 4, "value": 0.2}, {"time": 2, "value": 0.8}],
        [{"time": 0, "value": 0.2}, {"time": 0, "value": 0.8}],
        [{"time": 20, "value": 0.2}],
    ],
)
def test_invalid_default_write_does_not_clear_existing_envelope(points):
    clip = FakeClip()
    with pytest.raises(ValueError):
        write_automation(_bridge(clip), 0, 0, 0, "Frequency", points)
    assert clip.cleared == []


def test_points_budget_applies_before_clearing():
    clip = FakeClip()
    points = [{"time": i, "value": 0.2, "duration": 1} for i in range(MAX_STEPS + 1)]
    with pytest.raises(ValueError, match="limit"):
        write_automation(_bridge(clip), 0, 0, 0, "Frequency", points)
    assert clip.cleared == []


def test_unknown_default_hold_end_is_rejected():
    with pytest.raises(ValueError, match="duration|end"):
        steps_for([{"time": 0, "value": 0.2}])


def test_failed_clear_does_not_append_to_existing_envelope():
    clip = FakeClip()

    def fail(_):
        raise RuntimeError("cannot clear")

    clip.clear_envelope = fail
    with pytest.raises(RuntimeError, match="cannot clear"):
        write_automation(
            _bridge(clip), 0, 0, 0, "Frequency", [{"time": 0, "value": 0.2, "duration": 4}]
        )
    assert clip.envelope.steps == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"times": [-1]},
        {"times": [float("nan")]},
        {"times": [float("inf")]},
        {"times": []},
        {"start": -1, "end": 4},
        {"start": float("nan")},
        {"end": float("inf")},
        {"step": float("inf")},
        {"step": 1e-320},
    ],
)
def test_invalid_read_times_never_reach_live(kwargs):
    queried = []
    envelope = SimpleNamespace(value_at_time=lambda t: queried.append(t) or 0.2)
    with pytest.raises(ValueError):
        read_automation(read_bridge(envelope), 0, 0, 0, "Frequency", **kwargs)
    assert queried == []


def test_read_grid_includes_non_aligned_end():
    envelope = SimpleNamespace(value_at_time=lambda t: t)
    result = read_automation(read_bridge(envelope), 0, 0, 0, "Frequency", start=0, end=1, step=0.3)
    assert [s["time"] for s in result["samples"]] == pytest.approx([0, 0.3, 0.6, 0.9, 1])


def test_old_bridge_is_rejected_before_any_write(monkeypatch):
    from ableton_live_mcp.tools import arrangement

    sent = []

    def send(command, params=None):
        sent.append(command)
        return {"bridge_version": "1.8.1"}

    monkeypatch.setattr(
        arrangement, "get_ableton_connection", lambda: SimpleNamespace(send_command=send)
    )
    with pytest.raises(RuntimeError, match="Remote Script"):
        arrangement.write_automation(None, 0, 0, 0, "Frequency", [{"time": 0, "value": 0.2}])
    assert sent == ["get_session_info"]


def test_write_is_marked_as_replacing_existing_content():
    import asyncio

    from ableton_live_mcp.app import mcp

    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "write_automation")
    assert tool.annotations.destructiveHint is True


@pytest.mark.parametrize("nested", [False, True])
def test_old_bridge_is_rejected_before_automation_batch(monkeypatch, nested):
    from ableton_live_mcp.tools import session

    sent = []

    def send(command, params=None):
        sent.append(command)
        return {"bridge_version": "1.8.1"}

    monkeypatch.setattr(
        session, "get_ableton_connection", lambda: SimpleNamespace(send_command=send)
    )
    commands = [{"type": "write_automation", "params": {"points": []}}]
    if nested:
        commands = [{"type": "batch_commands", "params": {"commands": commands}}]
    with pytest.raises(RuntimeError, match="Remote Script"):
        session.batch_commands(None, commands)
    assert sent == ["get_session_info"]


def test_zero_span_ramp_does_not_clear_existing_automation():
    clip = FakeClip()
    with pytest.raises(ValueError, match="No playable"):
        write_automation(
            _bridge(clip),
            0,
            0,
            0,
            "Frequency",
            [
                {"time": 16, "value": 0.2},
                {"time": 16, "value": 0.8},
            ],
            "ramp",
        )
    assert clip.cleared == []


def test_tiny_ramp_resolution_is_rejected_before_clearing():
    clip = FakeClip()
    with pytest.raises(ValueError, match="limit"):
        write_automation(
            _bridge(clip),
            0,
            0,
            0,
            "Frequency",
            [
                {"time": 0, "value": 0.2},
                {"time": 4, "value": 0.8},
            ],
            "ramp",
            1e-320,
        )
    assert clip.cleared == []


def test_point_at_clip_end_is_a_boundary_not_a_zero_length_step():
    steps = steps_for([{"time": 0, "value": 0.2}, {"time": 16, "value": 0.8}], hold_until=16)
    assert steps == [(0, 16, 0.2)]


def test_read_endpoint_counts_toward_sample_limit():
    envelope = SimpleNamespace(value_at_time=lambda t: t)
    with pytest.raises(ValueError, match="limit"):
        read_automation(read_bridge(envelope), 0, 0, 0, "Frequency", end=1023.5, step=1)
