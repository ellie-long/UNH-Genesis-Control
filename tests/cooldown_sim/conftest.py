"""pytest configuration local to the cooldown_sim tests.

Registers the ``cooldown_sim`` marker for the opt-in full-stack tests.  This
lives in the subdirectory (``pytest_configure`` is a historic hook, so a
non-root conftest still registers it) so the rest of the repository's test
behaviour is untouched: no root conftest.py or pytest.ini is added.

Full-stack tests additionally skip unless GENESIS_COOLDOWN_SIM=1.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "cooldown_sim: full-stack simulated cool-down test (needs GENESIS_COOLDOWN_SIM=1)",
    )
