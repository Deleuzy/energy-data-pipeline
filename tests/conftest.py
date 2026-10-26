from pathlib import Path

import pytest

from edp import pipeline

FIXTURES = Path(__file__).parent / "fixtures"
START, END = "2026-10-13T06:00", "2026-10-13T09:00"


@pytest.fixture
def settings(tmp_path) -> pipeline.Settings:
    """Offline settings: raw responses come from a small real slice saved in tests/fixtures/raw."""
    return pipeline.Settings(
        start=START,
        end=END,
        db_path=str(tmp_path / "test.db"),
        cache_dir=str(FIXTURES / "raw"),
        report_dir=str(tmp_path / "reports"),
        site_csv=str(FIXTURES / "site_small.csv"),
        site_area="DK1",
        offline=True,
    )
