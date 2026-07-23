from aieos.event_bus import EventBus
from aieos.skill_runtime import ExecutionAttemptRunner


def test_event_bus_exposes_events_only() -> None:
    assert hasattr(EventBus, "publish")
    assert not hasattr(EventBus, "dispatch")
    assert not hasattr(EventBus, "publish_command")


def test_skill_runtime_exposes_no_retry_decision() -> None:
    assert hasattr(ExecutionAttemptRunner, "execute_attempt")
    assert not hasattr(ExecutionAttemptRunner, "retry")
    assert not hasattr(ExecutionAttemptRunner, "permits_new_attempt")
