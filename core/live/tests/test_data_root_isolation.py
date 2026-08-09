"""The test suite must never write into the real data tree.

This is the regression guard for a bug that destroyed published data
every time anyone ran ``pytest``.

``DailyRunner.run`` calls ``export_sectors`` and ``export_prices`` with
their default paths, which resolve through ``core.paths.DATA_ROOT`` to
``~/Library/Application Support/value-council`` on a Mac.
``test_runner.py`` drives that path with a stub position whose ticker is
the literal string ``"HOLD"``, so a full run replaced the real
``sectors.json`` with ``{"HOLD": "unknown"}``. The dashboard's sector
donut had been drawing that single fake holding, and nothing ever
failed: the export is wrapped in a "presentation only; never fails a
run" try/except, so the corruption was silent in both directions.

The fix is the rootdir ``conftest.py``, which points
``VALUE_COUNCIL_DATA_DIR`` at a throwaway directory before any project
module is imported. These tests fail without it.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.live.price_export import PRICES_DIR
from core.live.sector_export import SECTORS_PATH
from core.paths import DATA_ROOT


def _real_data_roots() -> list[Path]:
    """The locations that hold a human's actual portfolios."""
    home = Path.home()
    return [
        home / "Library" / "Application Support" / "value-council",
        Path(__file__).resolve().parents[3] / "data",
    ]


class TestDataRootIsolation:
    def test_data_root_is_not_a_real_one(self) -> None:
        resolved = DATA_ROOT.resolve()
        for real in _real_data_roots():
            assert resolved != real.resolve(), (
                f"tests are pointed at the real data tree ({resolved}). "
                "Anything calling an exporter with its default path will "
                "overwrite published data. The rootdir conftest.py sets "
                "VALUE_COUNCIL_DATA_DIR to prevent this."
            )

    def test_env_var_is_set_for_the_run(self) -> None:
        # If this is unset, core.paths fell back to a default — which on a
        # Mac is the real tree — and the isolation above held only by
        # luck.
        assert os.environ.get("VALUE_COUNCIL_DATA_DIR"), (
            "VALUE_COUNCIL_DATA_DIR is not set; the rootdir conftest.py "
            "did not run before core.paths was imported."
        )

    def test_module_level_export_paths_follow_the_test_root(self) -> None:
        """Constants bound at import time, not just DATA_ROOT itself.

        ``SECTORS_PATH`` and ``PRICES_DIR`` are computed once when their
        module is imported. Redirecting the root in a fixture would be
        too late for them, which is why conftest.py sets the variable at
        import time rather than in a fixture.
        """
        root = DATA_ROOT.resolve()
        assert SECTORS_PATH.resolve().parent == root
        assert PRICES_DIR.resolve().parent == root
