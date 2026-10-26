"""Fetch datasets from Energinet's Energi Data Service with an on-disk cache.

Every response page is saved as JSON next to a small metadata record holding
the exact request URL, the fetch time (UTC) and a SHA-256 of the body, so any
number in the pipeline can be traced back to the bytes it came from.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.energidataservice.dk/dataset"
USER_AGENT = "energy-data-pipeline/0.1 (portfolio demo; github.com/Deleuzy)"
PAGE_SIZE = 5000
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
RATE_LIMIT_WAIT_SECONDS = 65

# Dataset name -> columns we keep. Names are exactly as published by Energinet.
DATASETS = {
    "CO2Emis": ["Minutes5UTC", "PriceArea", "CO2Emission"],
    # Settled-method CO2 per kWh. Filtered to FuelAllocationMethod 125% in validate.py.
    "DeclarationGridEmission": ["HourUTC", "PriceArea", "FuelAllocationMethod", "Version", "CO2PerkWh"],
    "ProductionConsumptionSettlement": [
        "HourUTC",
        "PriceArea",
        "GrossConsumptionMWh",
        "OffshoreWindLt100MW_MWh",
        "OffshoreWindGe100MW_MWh",
        "OnshoreWindLt50kW_MWh",
        "OnshoreWindGe50kW_MWh",
        "SolarPowerLt10kW_MWh",
        "SolarPowerGe10Lt40kW_MWh",
        "SolarPowerGe40kW_MWh",
        "SolarPowerSelfConMWh",
    ],
}


class OfflineCacheMiss(RuntimeError):
    """Raised when offline mode is on and a page is not in the cache."""


@dataclass
class FetchRecord:
    dataset: str
    url: str
    fetched_at: str
    n_records: int
    cache_path: str
    sha256: str
    from_cache: bool


@dataclass
class FetchResult:
    dataset: str
    frame: pd.DataFrame
    pages: list[FetchRecord]


class EnergiDataServiceClient:
    def __init__(self, cache_dir: Path | str, offline: bool = False, session: requests.Session | None = None):
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last_request = 0.0

    def _cache_file(self, dataset: str, start: str, end: str, offset: int) -> Path:
        safe = lambda s: s.replace(":", "").replace("-", "")
        return self.cache_dir / dataset / f"{safe(start)}_{safe(end)}_off{offset}.json"

    def _get(self, url: str, params: dict) -> requests.Response:
        """GET with rate limiting. The API answers 429 with "try again in ~60 seconds",
        so a rate-limited request waits a full minute; server errors back off briefly."""
        for attempt in range(5):
            wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            resp = self.session.get(url, params=params, timeout=60)
            if resp.status_code == 429:
                time.sleep(RATE_LIMIT_WAIT_SECONDS)
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(2 ** attempt * 2)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def fetch(self, dataset: str, start: str, end: str, refresh: bool = False) -> FetchResult:
        """Fetch all records with start <= time < end (UTC, 'YYYY-MM-DDTHH:MM')."""
        columns = DATASETS[dataset]
        url = f"{BASE_URL}/{dataset}"
        records: list[dict] = []
        pages: list[FetchRecord] = []
        offset = 0
        while True:
            path = self._cache_file(dataset, start, end, offset)
            meta_path = path.with_suffix(".meta.json")
            if path.exists() and meta_path.exists() and not refresh:
                body = path.read_bytes()
                meta = json.loads(meta_path.read_text())
                from_cache = True
            else:
                if self.offline:
                    raise OfflineCacheMiss(f"{path} not cached and offline mode is on")
                params = {
                    "start": start,
                    "end": end,
                    "timezone": "UTC",
                    "columns": ",".join(columns),
                    "sort": f"{columns[0]} ASC,PriceArea ASC",
                    "offset": offset,
                    "limit": PAGE_SIZE,
                }
                resp = self._get(url, params)
                body = resp.content
                meta = {
                    "url": resp.url,
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                meta_path.write_text(json.dumps(meta, indent=2))
                from_cache = False
            page = json.loads(body)["records"]
            records.extend(page)
            pages.append(
                FetchRecord(dataset, meta["url"], meta["fetched_at"], len(page), str(path), meta["sha256"], from_cache)
            )
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        frame = pd.DataFrame.from_records(records, columns=columns)
        return FetchResult(dataset, frame, pages)
