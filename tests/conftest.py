"""Shared test setup.

Two things a fresh clone needs before the suite is meaningful:

1. A KNOWN OWNER IDENTITY. Prompts and link builders resolve [OWNER]-style
   tokens through owner.py at runtime. Without a config they fall back to the
   example file ("Your Name"), so any test asserting on a rendered name or URL
   would depend on whether the user has run setup.py yet. This pins a fixed
   test identity for the whole session instead.

2. GRACEFUL SKIPS FOR STORE-DEPENDENT TESTS. A number of tests assert on the
   contents of store/ (application_profile.json, answer_bank.json). Those are
   data-hygiene checks against a populated system. On a fresh clone the files
   do not exist yet, so those tests skip rather than fail: a missing store is
   an unconfigured system, not a broken one.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "app", ROOT / "agents"):
    sys.path.insert(0, str(p))

TEST_OWNER = {
    "name": "Alex Rivera",
    "email": "alex@example.com",
    "site": "example.com",
    "company": "Rivera Media",
    "linkedin": "linkedin.com/in/alexrivera",
    "handle": "alexrivera",
    "city": "Austin, TX",
    "voice": "Direct and punchy, no fluff.",
    "what_you_do": "I help teams ship faster",
    "icp": "Small agency owners",
    "offer": "Retainer",
    "current_title": "Operations Lead",
    "years_experience": "6",
}


# Pinned at conftest IMPORT time, deliberately not in a fixture. Several modules
# resolve owner values into module-level constants (BOOK_URL, BRAND_DOMAINS, the
# link regex) the moment they are imported. A session fixture runs after that, so
# those constants would already hold the real owner's site and every link-hygiene
# assertion would depend on whose machine the suite is running on. conftest.py is
# imported before any test module, which is early enough.
try:
    import owner as _owner
    _owner._cache = dict(TEST_OWNER)
except Exception:  # noqa: BLE001 -- suite still runs without the owner layer
    pass


def requires_store(*filenames):
    """Skip marker for tests that assert on real store/ contents."""
    missing = [f for f in filenames if not (ROOT / "store" / f).exists()]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"needs populated store/ ({', '.join(missing)}); run setup.py first",
    )

def _ghl_configured() -> bool:
    """True only if this install actually has GoHighLevel wired up."""
    try:
        cfg = json.loads((ROOT / "store" / "config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(cfg.get("ghl_api_key") or os.environ.get("GHL_API_KEY"))


# Test classes whose assertions require a live GHL connection. On an install
# without it, the API call fails and the test is asserting on an error string
# rather than on behaviour, so it skips instead of failing.
_GHL_CLASSES = ("TestColdFeederLiveGates", "TestStuckDeals")


def pytest_collection_modifyitems(config, items):
    """Skip tests that need data or connections this install does not have."""
    store = ROOT / "store"
    guarded = {
        "test_profile_and_bank_no_longer_assert_fixed_salary": "application_profile.json",
        "test_no_sd_location_in_answer_bank": "answer_bank.json",
        "test_css_numbers_do_not_widen_the_whitelist": "application_profile.json",
    }
    ghl_ok = _ghl_configured()
    for item in items:
        need = guarded.get(item.name)
        if need and not (store / need).exists():
            item.add_marker(pytest.mark.skip(
                reason=f"needs store/{need}; unconfigured clone"))
        if not ghl_ok and any(c in str(item.cls) for c in _GHL_CLASSES if item.cls):
            item.add_marker(pytest.mark.skip(
                reason="needs a configured GoHighLevel connection"))
