"""In-memory incident detection and recovery logic."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TargetState:
    """Track a target's counters and incident state."""

    target_id: str
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    incident_open: bool = False
    first_failure_at: str | None = None
    incident_started_at: str | None = None


@dataclass(frozen=True)
class StateUpdate:
    """Hold the next state and optional incident event."""

    state: TargetState
    event: str | None = None


def evaluate_check(
    state: TargetState,
    result: dict,
    failure_threshold: int,
    recovery_threshold: int,
) -> StateUpdate:
    """Update counters and detect DOWN or RECOVERED."""

    if state.target_id != result["target_id"]:
        raise ValueError("Result target does not match state")

    if failure_threshold < 1 or recovery_threshold < 1:
        raise ValueError("Thresholds must be positive")

    if result["success"]:
        successes = state.consecutive_successes + 1

        if state.incident_open and successes >= recovery_threshold:
            new_state = replace(
                state,
                consecutive_failures=0,
                consecutive_successes=successes,
                incident_open=False,
                first_failure_at=None,
                incident_started_at=None,
            )
            return StateUpdate(new_state, "RECOVERED")

        new_state = replace(
            state,
            consecutive_failures=0,
            consecutive_successes=successes,
            first_failure_at=None,
        )
        return StateUpdate(new_state)

    # The current check failed.
    failures = state.consecutive_failures + 1
    first_failure_at = state.first_failure_at or result["checked_at"]

    if not state.incident_open and failures >= failure_threshold:
        new_state = replace(
            state,
            consecutive_failures=failures,
            consecutive_successes=0,
            incident_open=True,
            first_failure_at=first_failure_at,
            incident_started_at=first_failure_at,
        )
        return StateUpdate(new_state, "DOWN")

    new_state = replace(
        state,
        consecutive_failures=failures,
        consecutive_successes=0,
        first_failure_at=first_failure_at,
    )
    return StateUpdate(new_state)
