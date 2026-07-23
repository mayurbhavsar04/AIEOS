from datetime import UTC, datetime, timedelta

from aieos.testing import DeterministicClock, DeterministicIdentifiers


def test_clock_advances_without_wall_time() -> None:
    clock = DeterministicClock(datetime(2026, 1, 1, tzinfo=UTC))
    clock.advance(timedelta(seconds=5))
    assert clock.now() == datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)


def test_identifiers_are_stable_and_monotonic() -> None:
    identifiers = DeterministicIdentifiers(prefix="execution")
    assert [identifiers.new(), identifiers.new()] == ["execution-0001", "execution-0002"]
