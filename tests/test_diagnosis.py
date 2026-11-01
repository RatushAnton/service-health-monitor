"""Unit tests for incident detection and recovery."""

import pytest

from healthmon.diagnosis import TargetState, evaluate_check


def make_result(success: bool, minute: int) -> dict:
    """Build a synthetic health-check result."""

    return {
        "target_id": "local_demo",
        "success": success,
        "checked_at": f"2026-11-01T12:{minute:02d}:00+00:00",
    }


def test_failure_threshold_opens_incident():
    """The third consecutive failure must open an incident."""

    state = TargetState(target_id="local_demo")

    for minute in (1, 2):
        update = evaluate_check(
            state,
            make_result(False, minute),
            failure_threshold=3,
            recovery_threshold=1,
        )

        assert update.event is None
        assert update.state.incident_open is False
        state = update.state

    update = evaluate_check(
        state,
        make_result(False, 3),
        failure_threshold=3,
        recovery_threshold=1,
    )

    assert update.event == "DOWN"
    assert update.state.incident_open is True
    assert update.state.consecutive_failures == 3
    assert update.state.incident_started_at == ("2026-11-01T12:01:00+00:00")


def test_no_duplicate_down_events():
    """Continued failures must not generate repeated DOWN events."""

    state = TargetState(target_id="local_demo")
    events = []

    for minute in range(1, 6):
        update = evaluate_check(
            state,
            make_result(False, minute),
            failure_threshold=3,
            recovery_threshold=1,
        )

        events.append(update.event)
        state = update.state

    assert events == [None, None, "DOWN", None, None]
    assert state.incident_open is True
    assert state.consecutive_failures == 5


def test_recovery_requires_two_successes():
    """An open incident closes after two consecutive successes."""

    state = TargetState(target_id="local_demo")

    for minute in (1, 2):
        update = evaluate_check(
            state,
            make_result(False, minute),
            failure_threshold=2,
            recovery_threshold=2,
        )
        state = update.state

    assert update.event == "DOWN"

    first_success = evaluate_check(
        state,
        make_result(True, 3),
        failure_threshold=2,
        recovery_threshold=2,
    )

    assert first_success.event is None
    assert first_success.state.incident_open is True
    assert first_success.state.consecutive_successes == 1

    second_success = evaluate_check(
        first_success.state,
        make_result(True, 4),
        failure_threshold=2,
        recovery_threshold=2,
    )

    assert second_success.event == "RECOVERED"
    assert second_success.state.incident_open is False
    assert second_success.state.consecutive_failures == 0
    assert second_success.state.incident_started_at is None


def test_success_resets_pending_failures():
    """A successful check must break an unfinished failure streak."""

    state = TargetState(target_id="local_demo")

    for minute in (1, 2):
        update = evaluate_check(
            state,
            make_result(False, minute),
            failure_threshold=3,
            recovery_threshold=1,
        )
        state = update.state

    success = evaluate_check(
        state,
        make_result(True, 3),
        failure_threshold=3,
        recovery_threshold=1,
    )

    assert success.state.consecutive_failures == 0

    next_failure = evaluate_check(
        success.state,
        make_result(False, 4),
        failure_threshold=3,
        recovery_threshold=1,
    )

    assert next_failure.event is None
    assert next_failure.state.consecutive_failures == 1
    assert next_failure.state.first_failure_at == ("2026-11-01T12:04:00+00:00")


def test_mismatched_target_is_rejected():
    """One target's result cannot update another target's state."""

    state = TargetState(target_id="internet_tcp")

    with pytest.raises(
        ValueError,
        match="Result target does not match state",
    ):
        evaluate_check(
            state,
            make_result(False, 1),
            failure_threshold=3,
            recovery_threshold=1,
        )


@pytest.mark.parametrize(
    ("failure_threshold", "recovery_threshold"),
    [
        (0, 1),
        (3, 0),
        (-1, 1),
    ],
)
def test_invalid_thresholds_are_rejected(
    failure_threshold: int,
    recovery_threshold: int,
):
    """Incident thresholds must be positive integers."""

    state = TargetState(target_id="local_demo")

    with pytest.raises(
        ValueError,
        match="Thresholds must be positive",
    ):
        evaluate_check(
            state,
            make_result(False, 1),
            failure_threshold=failure_threshold,
            recovery_threshold=recovery_threshold,
        )
