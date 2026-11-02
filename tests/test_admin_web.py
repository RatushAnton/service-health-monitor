"""Tests for revocable browser sessions, CSRF protection and edit forms."""

import re
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from healthmon.admin_api import app
from healthmon.registry import (
    bootstrap_registry,
    list_registry_monitors,
    read_registry_config,
)
from healthmon.storage import get_history, save_result

CONFIG = """
settings:
  timeout_seconds: 3
  failure_threshold: 3
  recovery_threshold: 1
targets:
  - id: demo_dns
    name: Original DNS
    type: dns
    host: example.com
"""


@pytest.fixture
def browser(tmp_path, monkeypatch):
    db = tmp_path / "health.db"
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(CONFIG, encoding="utf-8")
    bootstrap_registry(db, yaml_file)
    bearer = "example-secret-" + "a" * 48
    secret_file = tmp_path / "admin-token.txt"
    secret_file.write_text(bearer, encoding="utf-8")
    monkeypatch.setenv("HEALTHMON_ADMIN_TOKEN_FILE", str(secret_file))
    monkeypatch.setenv("HEALTHMON_DB_PATH", str(db))
    monkeypatch.delenv("HEALTHMON_COOKIE_SECURE", raising=False)
    with TestClient(app) as client:
        yield client, bearer, secret_file, db


def login(client, bearer):
    return client.post("/admin/login", data={"token": bearer}, follow_redirects=False)


def get_csrf(html):
    result = re.search(
        r'name="csrf_token"\s+value="([^"]+)"',
        html,
    )
    assert result is not None, "CSRF token not found in HTML"
    return result.group(1)


def test_unauthenticated_pages_redirect(browser):
    client, _, _, _ = browser
    assert client.get("/admin/monitors", follow_redirects=False).status_code == 303
    assert (
        client.get("/admin/monitors/demo_dns/edit", follow_redirects=False).status_code
        == 303
    )


def test_invalid_login_rejected(browser):
    client, _, _, _ = browser
    response = login(client, "incorrect")
    assert response.status_code == 401
    assert "healthmon_admin_session" not in client.cookies


def test_valid_login_sets_httponly_cookie_and_lists_monitors(browser):
    client, bearer, _, _ = browser
    response = login(client, bearer)
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert bearer not in response.headers["set-cookie"]
    page = client.get("/admin/monitors")
    assert page.status_code == 200
    assert "Original DNS" in page.text
    assert bearer not in page.text
    assert "no-store" in page.headers["cache-control"]


def test_edit_form_saves_configuration(browser):
    client, bearer, _, db = browser
    login(client, bearer)
    page = client.get("/admin/monitors/demo_dns/edit")
    assert page.status_code == 200
    assert "Original DNS" in page.text
    response = client.post(
        "/admin/monitors/demo_dns/edit",
        data={
            "csrf_token": get_csrf(page.text),
            "name": "Updated DNS",
            "host": "example.org",
            "record_type": "AAAA",
            "resolver": "system",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    saved = list_registry_monitors(db)[0]
    assert saved["name"] == "Updated DNS"
    assert saved["host"] == "example.org"
    assert saved["record_type"] == "AAAA"
    assert saved["enabled"] is True


def test_csrf_rejection_preserves_configuration(browser):
    client, bearer, _, db = browser
    login(client, bearer)
    response = client.post(
        "/admin/monitors/demo_dns/edit",
        data={"csrf_token": "wrong", "name": "Attacker", "host": "x.example"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert list_registry_monitors(db)[0]["name"] == "Original DNS"


def test_invalid_form_returns_422_and_does_not_save(browser):
    client, bearer, _, db = browser
    login(client, bearer)
    csrf = get_csrf(client.get("/admin/monitors/demo_dns/edit").text)
    response = client.post(
        "/admin/monitors/demo_dns/edit",
        data={"csrf_token": csrf, "name": "", "host": "example.org"},
    )
    assert response.status_code == 422
    assert list_registry_monitors(db)[0]["name"] == "Original DNS"


def test_logout_revokes_session(browser):
    client, bearer, _, _ = browser
    login(client, bearer)
    csrf = get_csrf(client.get("/admin/monitors").text)
    response = client.post(
        "/admin/logout", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    assert client.get("/admin/monitors", follow_redirects=False).status_code == 303


def test_expired_session_is_rejected(browser):
    client, bearer, _, db = browser
    login(client, bearer)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE admin_sessions SET expires_at = 0")
    assert client.get("/admin/monitors", follow_redirects=False).status_code == 303


def test_rotated_secret_invalidates_session(browser):
    client, bearer, secret_file, _ = browser
    login(client, bearer)
    secret_file.write_text("different-secret-" + "z" * 48, encoding="utf-8")
    assert client.get("/admin/monitors", follow_redirects=False).status_code == 303


def test_bearer_api_stays_separate_from_browser_session(browser):
    client, bearer, _, _ = browser
    login(client, bearer)
    assert client.get("/api/admin/monitors").status_code == 401
    assert (
        client.get(
            "/api/admin/monitors", headers={"Authorization": f"Bearer {bearer}"}
        ).status_code
        == 200
    )


def test_missing_origin_may_use_csrf_but_cross_origin_is_rejected(browser):
    client, bearer, _, db = browser
    login(client, bearer)
    csrf = get_csrf(client.get("/admin/monitors/demo_dns/edit").text)
    result = client.post(
        "/admin/monitors/demo_dns/edit",
        data={"csrf_token": csrf, "name": "Changed", "host": "example.org"},
        headers={"Origin": "https://attacker.example"},
    )
    assert result.status_code == 403
    assert list_registry_monitors(db)[0]["name"] == "Original DNS"


def test_login_origin_policy(browser):
    client, bearer, _, _ = browser

    page = client.get("/admin/login")
    assert page.headers["referrer-policy"] == "same-origin"

    # Requests with opaque origins must remain blocked.
    rejected = client.post(
        "/admin/login",
        data={"token": bearer},
        headers={"Origin": "null"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403

    # A legitimate same-origin form submission must succeed.
    accepted = client.post(
        "/admin/login",
        data={"token": bearer},
        headers={"Origin": str(client.base_url).rstrip("/")},
        follow_redirects=False,
    )
    assert accepted.status_code == 303


@pytest.mark.parametrize(
    "fields",
    [
        {
            "id": "new_dns",
            "name": "New DNS",
            "type": "dns",
            "host": "example.org",
            "record_type": "A",
        },
        {
            "id": "new_tcp",
            "name": "New TCP",
            "type": "tcp",
            "host": "example.org",
            "port": "443",
        },
        {
            "id": "new_http",
            "name": "New HTTP",
            "type": "http",
            "url": "https://example.org/",
            "expected_status": "200",
        },
    ],
)
def test_browser_can_create_monitors(browser, fields):
    client, bearer, _, db = browser
    login(client, bearer)

    page = client.get("/admin/monitors/new")
    csrf = get_csrf(page.text)

    response = client.post(
        "/admin/monitors/new",
        data={"csrf_token": csrf, **fields},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert fields["id"] in {target["id"] for target in list_registry_monitors(db)}


def test_new_monitor_requires_login(browser):
    client, _, _, _ = browser

    assert (
        client.get(
            "/admin/monitors/new",
            follow_redirects=False,
        ).status_code
        == 303
    )

    assert (
        client.post(
            "/admin/monitors/new",
            data={"id": "unauthorized"},
        ).status_code
        == 401
    )


def test_duplicate_monitor_returns_409(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    csrf = get_csrf(client.get("/admin/monitors/new").text)
    response = client.post(
        "/admin/monitors/new",
        data={
            "csrf_token": csrf,
            "id": "demo_dns",
            "name": "Duplicate",
            "type": "dns",
            "host": "example.org",
        },
    )

    assert response.status_code == 409
    assert len(list_registry_monitors(db)) == 1


def test_new_monitor_csrf_protection(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    response = client.post(
        "/admin/monitors/new",
        data={
            "csrf_token": "wrong",
            "id": "new_dns",
            "name": "New DNS",
            "type": "dns",
            "host": "example.org",
        },
    )

    assert response.status_code == 403
    assert len(list_registry_monitors(db)) == 1


def test_pause_and_resume(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    csrf = get_csrf(client.get("/admin/monitors").text)
    endpoint = "/admin/monitors/demo_dns/enabled"

    paused = client.post(
        endpoint,
        data={"csrf_token": csrf, "enabled": "0"},
        follow_redirects=False,
    )

    assert paused.status_code == 303
    assert list_registry_monitors(db)[0]["enabled"] is False
    assert read_registry_config(db)["targets"] == []

    resumed = client.post(
        endpoint,
        data={"csrf_token": csrf, "enabled": "1"},
        follow_redirects=False,
    )

    assert resumed.status_code == 303
    assert list_registry_monitors(db)[0]["enabled"] is True


def test_pause_rejects_invalid_state(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    csrf = get_csrf(client.get("/admin/monitors").text)

    response = client.post(
        "/admin/monitors/demo_dns/enabled",
        data={"csrf_token": csrf, "enabled": "invalid"},
    )

    assert response.status_code == 422
    assert list_registry_monitors(db)[0]["enabled"] is True


def test_pause_requires_valid_csrf(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    response = client.post(
        "/admin/monitors/demo_dns/enabled",
        data={"csrf_token": "wrong", "enabled": "0"},
    )

    assert response.status_code == 403
    assert list_registry_monitors(db)[0]["enabled"] is True


def test_create_rejects_cross_origin(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    csrf = get_csrf(client.get("/admin/monitors/new").text)

    response = client.post(
        "/admin/monitors/new",
        data={
            "csrf_token": csrf,
            "id": "new_dns",
            "name": "New DNS",
            "type": "dns",
            "host": "example.org",
        },
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert len(list_registry_monitors(db)) == 1


def test_pause_requires_login(browser):
    client, _, _, db = browser

    response = client.post(
        "/admin/monitors/demo_dns/enabled",
        data={"enabled": "0"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert list_registry_monitors(db)[0]["enabled"] is True


def test_pause_rejects_cross_origin(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    page = client.get("/admin/monitors")
    csrf = get_csrf(page.text)

    response = client.post(
        "/admin/monitors/demo_dns/enabled",
        data={"csrf_token": csrf, "enabled": "0"},
        headers={"Origin": "https://attacker.example"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert list_registry_monitors(db)[0]["enabled"] is True


def test_archive_requires_session(browser):
    client, _, _, db = browser
    url = "/admin/monitors/demo_dns/archive"

    assert client.get(url, follow_redirects=False).status_code == 303
    assert (
        client.post(
            url,
            data={"confirm_id": "demo_dns"},
            follow_redirects=False,
        ).status_code
        == 401
    )

    assert len(list_registry_monitors(db)) == 1


def test_archive_confirmation_is_read_only(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    page = client.get("/admin/monitors/demo_dns/archive")

    assert page.status_code == 200
    assert "Original DNS" in page.text
    assert 'name="confirm_id"' in page.text
    assert len(list_registry_monitors(db)) == 1


def test_archive_requires_exact_confirmation(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    page = client.get("/admin/monitors/demo_dns/archive")

    response = client.post(
        "/admin/monitors/demo_dns/archive",
        data={
            "csrf_token": get_csrf(page.text),
            "confirm_id": "wrong_id",
        },
    )

    assert response.status_code == 422
    assert len(list_registry_monitors(db)) == 1


def test_archive_rejects_invalid_csrf(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    response = client.post(
        "/admin/monitors/demo_dns/archive",
        data={
            "csrf_token": "invalid",
            "confirm_id": "demo_dns",
        },
    )

    assert response.status_code == 403
    assert len(list_registry_monitors(db)) == 1


def test_archive_rejects_cross_origin(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    page = client.get("/admin/monitors/demo_dns/archive")

    response = client.post(
        "/admin/monitors/demo_dns/archive",
        data={
            "csrf_token": get_csrf(page.text),
            "confirm_id": "demo_dns",
        },
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert len(list_registry_monitors(db)) == 1


def test_archive_preserves_history_and_incidents(browser):
    client, bearer, _, db = browser

    save_result(
        db,
        {
            "target_id": "demo_dns",
            "check_type": "dns",
            "checked_at": "2026-11-02T08:00:00+00:00",
            "success": True,
            "latency_ms": 5.0,
            "message": "OK",
        },
    )

    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            """
            INSERT INTO incident_events
                (target_id, event_type, occurred_at, message)
            VALUES (?, ?, ?, ?)
            """,
            ("demo_dns", "DOWN", "2026-11-02T08:00:00+00:00", "Failed"),
        )
        conn.execute(
            "INSERT INTO target_state (target_id) VALUES (?)",
            ("demo_dns",),
        )

    login(client, bearer)
    page = client.get("/admin/monitors/demo_dns/archive")

    response = client.post(
        "/admin/monitors/demo_dns/archive",
        data={
            "csrf_token": get_csrf(page.text),
            "confirm_id": "demo_dns",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert list_registry_monitors(db) == []
    assert read_registry_config(db)["targets"] == []
    assert len(get_history(db, "demo_dns")) == 1

    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT enabled, archived_at FROM monitor_registry WHERE target_id = ?",
            ("demo_dns",),
        ).fetchone()
        assert row[0] == 0
        assert row[1] is not None

        events = conn.execute(
            "SELECT COUNT(*) FROM incident_events WHERE target_id = ?",
            ("demo_dns",),
        ).fetchone()[0]
        assert events == 1

        state = conn.execute(
            "SELECT COUNT(*) FROM target_state WHERE target_id = ?",
            ("demo_dns",),
        ).fetchone()[0]
        assert state == 0


def test_archive_cannot_be_repeated_or_resumed(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    csrf = get_csrf(client.get("/admin/monitors").text)

    first = client.post(
        "/admin/monitors/demo_dns/archive",
        data={"csrf_token": csrf, "confirm_id": "demo_dns"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    second = client.post(
        "/admin/monitors/demo_dns/archive",
        data={"csrf_token": csrf, "confirm_id": "demo_dns"},
    )
    assert second.status_code == 404

    resume = client.post(
        "/admin/monitors/demo_dns/enabled",
        data={"csrf_token": csrf, "enabled": "1"},
    )
    assert resume.status_code == 404
    assert list_registry_monitors(db) == []


def test_archived_id_cannot_be_recreated_from_browser(browser):
    client, bearer, _, db = browser
    login(client, bearer)

    csrf = get_csrf(client.get("/admin/monitors").text)

    archived = client.post(
        "/admin/monitors/demo_dns/archive",
        data={"csrf_token": csrf, "confirm_id": "demo_dns"},
        follow_redirects=False,
    )
    assert archived.status_code == 303

    response = client.post(
        "/admin/monitors/new",
        data={
            "csrf_token": csrf,
            "id": "demo_dns",
            "name": "Replacement",
            "type": "dns",
            "host": "example.org",
        },
    )

    assert response.status_code == 409
    assert list_registry_monitors(db) == []
