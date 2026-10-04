"""Temporary PR-00.4 enforcement probe; removed after the blocked-merge check."""


def test_required_checks_enforcement_probe() -> None:
    """Intentionally fail only on the isolated validation branch."""
    assert False, "PR-00.4 deliberate required-check failure probe"
