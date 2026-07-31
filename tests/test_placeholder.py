"""Placeholder test proving the Epic E1 CI foundation runs for real.

Replace/expand once packages/contracts (Epic E2) lands.
"""

from tradeops_sentinel import __version__


def test_package_version_is_set() -> None:
    assert __version__ == "0.1.0"
