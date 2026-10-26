import pytest
from fastapi.testclient import TestClient

from conftest import FIXTURES
from edp import api, pipeline


@pytest.fixture
def client(settings, monkeypatch):
    pipeline.run(settings)
    monkeypatch.setattr(api, "DB_PATH", settings.db_path)
    monkeypatch.setattr(api, "REPORT_DIR", __import__("pathlib").Path(settings.report_dir))
    return TestClient(api.app)


def test_intensity(client):
    r = client.get("/intensity", params={"area": "DK2", "start": "2026-10-13T07:00:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["hours"] == 2
    assert body["records"][0]["quality"] == "warning"  # 07:00 has 11 of 12 values


def test_intensity_rejects_unknown_area(client):
    assert client.get("/intensity", params={"area": "SE3"}).status_code == 422


def test_quality(client):
    body = client.get("/quality").json()
    assert body["total_flags"] == 4


def test_scope2_upload(client):
    data = (FIXTURES / "site_small.csv").read_bytes()
    r = client.post("/scope2", params={"area": "DK1"}, files={"file": ("site.csv", data, "text/csv")})
    assert r.status_code == 200
    assert r.json()["covered_kwh"] == 60


def test_scope2_bad_upload(client):
    r = client.post("/scope2", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 422


def test_dashboard(client):
    assert client.get("/dashboard").status_code == 200
