import hashlib
import json

import pytest

from edp import fetch
from conftest import END, FIXTURES, START


def test_offline_reads_cached_real_slice():
    client = fetch.EnergiDataServiceClient(FIXTURES / "raw", offline=True)
    res = client.fetch("CO2Emis", START, END)
    assert list(res.frame.columns) == fetch.DATASETS["CO2Emis"]
    assert len(res.frame) == 70  # 3 h x 12 x 2 areas, minus one real missing 5-min slot per area
    page = res.pages[0]
    assert page.from_cache
    assert page.url.startswith("https://api.energidataservice.dk/dataset/CO2Emis?")
    body = open(page.cache_path, "rb").read()
    assert hashlib.sha256(body).hexdigest() == page.sha256


def test_offline_cache_miss_raises(tmp_path):
    client = fetch.EnergiDataServiceClient(tmp_path, offline=True)
    with pytest.raises(fetch.OfflineCacheMiss):
        client.fetch("CO2Emis", START, END)


class FakeResponse:
    def __init__(self, records, url):
        self.content = json.dumps({"records": records}).encode()
        self.url = url
        self.status_code = 200

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.headers = {}
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        return FakeResponse(self.pages[len(self.calls) - 1], f"{url}?offset={params['offset']}")


def test_paginates_and_writes_cache_with_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    monkeypatch.setattr(fetch, "MIN_SECONDS_BETWEEN_REQUESTS", 0)
    rec = {"Minutes5UTC": "2026-10-13T06:00:00", "PriceArea": "DK1", "CO2Emission": 50.0}
    session = FakeSession([[rec, rec], [rec]])
    client = fetch.EnergiDataServiceClient(tmp_path, session=session)
    res = client.fetch("CO2Emis", START, END)
    assert [c["offset"] for c in session.calls] == [0, 2]
    assert all(c["timezone"] == "UTC" for c in session.calls)
    assert len(res.frame) == 3 and len(res.pages) == 2
    meta = json.loads((tmp_path / "CO2Emis" / "20261013T0600_20261013T0900_off2.meta.json").read_text())
    assert set(meta) == {"url", "fetched_at", "sha256"}
    # Second call is served from disk, no new requests.
    again = client.fetch("CO2Emis", START, END)
    assert len(session.calls) == 2 and all(p.from_cache for p in again.pages)
