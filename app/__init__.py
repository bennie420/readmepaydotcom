"""Open-Source Sponsorship & Dynamic README Badge Ad Platform."""

import sys

# Ensure pytest test session module alias consistency between conftest and tests.conftest
if "conftest" in sys.modules and "tests.conftest" not in sys.modules:
    sys.modules["tests.conftest"] = sys.modules["conftest"]
