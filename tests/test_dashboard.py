"""Tests for the dashboard and packaged static assets."""

from fastapi.testclient import TestClient

from healthmon.api import app


def test_dashboard_returns_html():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Service Health Monitor" in response.text
    assert "/static/dashboard.css" in response.text
    assert "/static/dashboard.js" in response.text


def test_static_assets():
    with TestClient(app) as client:
        css = client.get("/static/dashboard.css")
        js = client.get("/static/dashboard.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "monitor-grid" in css.text

    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "refreshDashboard" in js.text


def test_dashboard_loads_without_database(tmp_path, monkeypatch):
    """Page shell works even when the database is unavailable."""
    monkeypatch.setenv(
        "HEALTHMON_DB_PATH",
        str(tmp_path / "missing.db"),
    )

    with TestClient(app) as client:
        page = client.get("/")
        monitors = client.get("/api/monitors")

    assert page.status_code == 200
    assert monitors.status_code == 503
