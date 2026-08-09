"""Point the data root at a throwaway directory for the whole test run.

Why this file exists
~~~~~~~~~~~~~~~~~~~~

``DailyRunner.run`` calls ``export_sectors`` and ``export_prices`` with
their default paths, and those resolve through ``core.paths.DATA_ROOT``
to the real tree — on a Mac, ``~/Library/Application Support/
value-council``. ``core/live/tests/test_runner.py`` exercises that path
with a stub whose ticker is the string ``"HOLD"``, so every full test
run overwrote the published ``sectors.json`` with::

    {"HOLD": "unknown"}

which is exactly what was found there, dated to the last time the suite
ran rather than the last time an agent traded. The dashboard's sector
donut had been drawing that one fake holding for as long as anyone had
been running tests. Nothing failed, because the export is wrapped in a
"presentation only; never fails a run" try/except.

``.github/workflows/ci.yml`` already states the rule this enforces:
"nothing here may touch a live API or the on-disk cache". It was a
comment, not a mechanism. This is the mechanism.

Why an environment variable, and why at import time
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``core.paths`` resolves the root once, at import, and modules bind
constants off it — ``SECTORS_PATH = DATA_ROOT / "sectors.json"``. A
fixture that sets the variable later would run after those constants
already exist. pytest imports the rootdir conftest before it collects
anything, so setting it here is early enough, and it covers every
module that reads a path rather than only the two that were caught.

The override is unconditional. Honouring an inherited
``VALUE_COUNCIL_DATA_DIR`` would reintroduce the bug for exactly the
person most likely to have it set: someone running the suite on the
machine that holds the real portfolios.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="value-council-test-data-"))

# Set before any project module is imported. Tests that want to assert
# on written files should still pass an explicit path; this is the net
# under the ones that do not.
os.environ["VALUE_COUNCIL_DATA_DIR"] = str(_TEST_DATA_ROOT)


@atexit.register
def _remove_test_data_root() -> None:
    """Leave no directory behind, and never raise on the way out."""
    shutil.rmtree(_TEST_DATA_ROOT, ignore_errors=True)
