"""Session-authenticated administration pages; never expose the bearer secret."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qsl, quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from healthmon.admin_security import AdminSecretUnavailable, read_admin_secret
from healthmon.monitor_models import MonitorInput
from healthmon.registry import (
    MonitorConflict,
    MonitorNotFound,
    RegistryNotInitialized,
    archive_monitor,
    create_monitor,
    list_registry_monitors,
    set_monitor_enabled,
    update_monitor,
)
from healthmon.storage import init_db

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
COOKIE_NAME = "healthmon_admin_session"
SESSION_SECONDS = 30 * 60
MAX_FORM_BYTES = 8192
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


def db_path() -> Path:
    path = Path(
        os.environ.get("HEALTHMON_DB_PATH", "/var/lib/service-health-monitor/health.db")
    )
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Monitoring database unavailable")
    return path


def secret() -> str:
    try:
        return read_admin_secret()
    except AdminSecretUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Administration authentication unavailable"
        ) from exc


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_for(session_id: str) -> str:
    return hmac.new(
        secret().encode("utf-8"),
        f"csrf:{session_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def active_session(request: Request) -> str | None:
    """Look up an opaque, revocable session by its hashed identifier."""
    session_id = request.cookies.get(COOKIE_NAME, "")
    if not session_id or len(session_id) > 128:
        return None

    try:
        configured_secret = read_admin_secret()
        with closing(sqlite3.connect(db_path())) as conn:
            row = conn.execute(
                "SELECT expires_at, secret_hash FROM admin_sessions WHERE session_hash = ?",
                (token_hash(session_id),),
            ).fetchone()
    except (AdminSecretUnavailable, sqlite3.Error, HTTPException, OSError):
        return None

    if row is None or row[0] <= int(time.time()):
        return None
    if not hmac.compare_digest(row[1], token_hash(configured_secret)):
        return None
    return session_id


def csrf_required(request: Request, session_id: str, submitted: str) -> None:
    if not submitted or not hmac.compare_digest(submitted, csrf_for(session_id)):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def same_origin_required(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Cross-origin request denied")


def cookie_secure(request: Request) -> bool:
    setting = os.environ.get("HEALTHMON_COOKIE_SECURE", "").lower()
    return request.url.scheme == "https" or setting in ("1", "true", "yes")


def render(request: Request, template: str, *, status_code: int = 200, **context):
    response = templates.TemplateResponse(
        request=request, name=template, context=context, status_code=status_code
    )
    response.headers.update(SECURITY_HEADERS)
    return response


def redirect(path: str):
    response = RedirectResponse(path, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


async def form_fields(request: Request) -> dict[str, str]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Expected URL-encoded form")
    declared_size = request.headers.get("content-length", "")
    if declared_size.isdigit() and int(declared_size) > MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="Form too large")

    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="Form too large")

    try:
        pairs = parse_qsl(
            raw.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=16,
            errors="strict",
        )
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Malformed form") from exc
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise HTTPException(status_code=400, detail="Duplicate form fields")
    return fields


def configured_monitors() -> list[dict]:
    try:
        return list_registry_monitors(db_path())
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Registry unavailable") from exc


def find_monitor(target_id: str) -> dict:
    for monitor in configured_monitors():
        if monitor["id"] == target_id:
            return monitor
    raise HTTPException(status_code=404, detail="Monitor not found")


@router.get("/admin", include_in_schema=False)
def admin_root(request: Request):
    return redirect("/admin/monitors" if active_session(request) else "/admin/login")


@router.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request):
    if active_session(request):
        return redirect("/admin/monitors")
    return render(request, "admin_login.html", error=None)


@router.post("/admin/login", include_in_schema=False)
async def login(request: Request):
    same_origin_required(request)
    fields = await form_fields(request)
    provided = fields.get("token", "")
    expected = secret()
    if not provided or not hmac.compare_digest(provided, expected):
        return render(
            request, "admin_login.html", status_code=401, error="Invalid credentials"
        )

    # Each session has an independent random ID. Only its hash is persisted.
    session_id = secrets.token_urlsafe(32)
    now = int(time.time())
    path = db_path()
    try:
        init_db(path)  # Migrates existing databases and adds admin_sessions if needed.
        with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
            conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                """INSERT INTO admin_sessions
                   (session_hash, secret_hash, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    token_hash(session_id),
                    token_hash(expected),
                    now,
                    now + SESSION_SECONDS,
                ),
            )
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503, detail="Cannot start administration session"
        ) from exc

    response = redirect("/admin/monitors")
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        max_age=SESSION_SECONDS,
        path="/admin",
        httponly=True,
        secure=cookie_secure(request),
        samesite="strict",
    )
    return response


@router.post("/admin/logout", include_in_schema=False)
async def logout(request: Request):
    same_origin_required(request)
    session_id = active_session(request)
    if session_id is None:
        raise HTTPException(status_code=401, detail="Login required")
    fields = await form_fields(request)
    csrf_required(request, session_id, fields.get("csrf_token", ""))

    try:
        with closing(sqlite3.connect(db_path(), timeout=10)) as conn, conn:
            conn.execute(
                "DELETE FROM admin_sessions WHERE session_hash = ?",
                (token_hash(session_id),),
            )
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(status_code=503, detail="Cannot revoke session") from exc
    response = redirect("/admin/login")
    response.delete_cookie(
        COOKIE_NAME, path="/admin", secure=cookie_secure(request), samesite="strict"
    )
    return response


@router.get("/admin/monitors", response_class=HTMLResponse, include_in_schema=False)
def monitor_list(request: Request):
    session_id = active_session(request)
    if session_id is None:
        return redirect("/admin/login")
    return render(
        request,
        "admin_monitors.html",
        monitors=configured_monitors(),
        csrf_token=csrf_for(session_id),
        updated=request.query_params.get("updated", ""),
        created=request.query_params.get("created", ""),
        changed=request.query_params.get("changed", ""),
        archived=request.query_params.get("archived", ""),
    )


@router.get(
    "/admin/monitors/{target_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def edit_page(request: Request, target_id: str):
    session_id = active_session(request)
    if session_id is None:
        return redirect("/admin/login")
    return render(
        request,
        "admin_edit.html",
        monitor=find_monitor(target_id),
        csrf_token=csrf_for(session_id),
        error=None,
    )


@router.post("/admin/monitors/{target_id}/edit", include_in_schema=False)
async def edit_monitor_form(request: Request, target_id: str):
    same_origin_required(request)
    session_id = active_session(request)
    if session_id is None:
        raise HTTPException(status_code=401, detail="Login required")
    fields = await form_fields(request)
    csrf_required(request, session_id, fields.get("csrf_token", ""))
    existing = find_monitor(target_id)
    kind = existing["type"]
    draft = {**existing, "name": fields.get("name", "")}
    payload = {"id": target_id, "name": draft["name"], "type": kind}

    try:
        if kind == "dns":
            draft["host"] = fields.get("host", "")
            draft["record_type"] = fields.get("record_type", "A")
            draft["resolver"] = fields.get("resolver", "")
            payload.update(host=draft["host"], record_type=draft["record_type"])
            if draft["resolver"]:
                payload["resolver"] = draft["resolver"]
        elif kind == "tcp":
            draft["host"] = fields.get("host", "")
            draft["port"] = fields.get("port", "")
            payload.update(host=draft["host"], port=int(draft["port"]))
        elif kind == "http":
            draft["url"] = fields.get("url", "")
            draft["expected_status"] = fields.get("expected_status", "200")
            payload.update(
                url=draft["url"], expected_status=int(draft["expected_status"])
            )
        else:
            raise ValueError("Unsupported monitor type")
        normalized = MonitorInput.model_validate(payload).model_dump(exclude_none=True)
    except (ValidationError, ValueError):
        return render(
            request,
            "admin_edit.html",
            monitor=draft,
            csrf_token=csrf_for(session_id),
            error="Invalid configuration. Check all fields and try again.",
            status_code=422,
        )

    try:
        update_monitor(db_path(), target_id, normalized)
    except MonitorNotFound as exc:
        raise HTTPException(status_code=404, detail="Monitor not found") from exc
    except MonitorConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RegistryNotInitialized, sqlite3.Error, OSError) as exc:
        raise HTTPException(status_code=503, detail="Cannot update monitor") from exc
    return redirect("/admin/monitors?updated=" + quote(target_id, safe=""))


@router.get(
    "/admin/monitors/new",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def new_monitor_page(request: Request):
    session_id = active_session(request)
    if session_id is None:
        return redirect("/admin/login")

    kind = request.query_params.get("type", "dns")
    if kind not in ("dns", "tcp", "http"):
        kind = "dns"

    return render(
        request,
        "admin_new.html",
        monitor={
            "type": kind,
            "record_type": "A",
            "expected_status": 200,
        },
        csrf_token=csrf_for(session_id),
        error=None,
    )


@router.post(
    "/admin/monitors/new",
    include_in_schema=False,
)
async def create_monitor_form(request: Request):
    same_origin_required(request)

    session_id = active_session(request)
    if session_id is None:
        raise HTTPException(status_code=401, detail="Login required")

    fields = await form_fields(request)
    csrf_required(request, session_id, fields.get("csrf_token", ""))

    kind = fields.get("type", "")
    draft = {
        key: fields.get(key, "")
        for key in (
            "id",
            "name",
            "type",
            "host",
            "port",
            "url",
            "record_type",
            "resolver",
            "expected_status",
        )
    }

    def validation_error(message: str, code: int = 422):
        return render(
            request,
            "admin_new.html",
            monitor=draft,
            csrf_token=csrf_for(session_id),
            error=message,
            status_code=code,
        )

    payload = {
        "id": fields.get("id", ""),
        "name": fields.get("name", ""),
        "type": kind,
    }

    try:
        if kind == "dns":
            payload["host"] = fields.get("host", "")
            payload["record_type"] = fields.get("record_type", "A")

            resolver = fields.get("resolver", "")
            if resolver:
                payload["resolver"] = resolver

        elif kind == "tcp":
            payload["host"] = fields.get("host", "")
            payload["port"] = int(fields.get("port", ""))

        elif kind == "http":
            payload["url"] = fields.get("url", "")
            payload["expected_status"] = int(fields.get("expected_status", "200"))

        else:
            raise ValueError("Unsupported monitor type")

        normalized = MonitorInput.model_validate(payload).model_dump(exclude_none=True)

    except (ValueError, ValidationError):
        return validation_error(
            "Invalid monitor configuration. Check the supplied fields."
        )

    try:
        create_monitor(db_path(), normalized)
    except MonitorConflict:
        return validation_error(
            "A monitor with this ID already exists.",
            409,
        )
    except (RegistryNotInitialized, sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Cannot create monitor",
        ) from exc

    return redirect("/admin/monitors?created=" + quote(normalized["id"], safe=""))


@router.post(
    "/admin/monitors/{target_id}/enabled",
    include_in_schema=False,
)
async def change_enabled_form(request: Request, target_id: str):
    same_origin_required(request)

    session_id = active_session(request)
    if session_id is None:
        raise HTTPException(status_code=401, detail="Login required")

    fields = await form_fields(request)
    csrf_required(request, session_id, fields.get("csrf_token", ""))

    requested = fields.get("enabled")
    if requested not in ("0", "1"):
        raise HTTPException(
            status_code=422,
            detail="Enabled must be 0 or 1",
        )

    try:
        set_monitor_enabled(db_path(), target_id, requested == "1")
    except MonitorNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Monitor not found",
        ) from exc
    except (RegistryNotInitialized, sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Cannot update monitor",
        ) from exc

    return redirect("/admin/monitors?changed=" + quote(target_id, safe=""))


@router.get(
    "/admin/monitors/{target_id}/archive",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def archive_page(request: Request, target_id: str):
    session_id = active_session(request)
    if session_id is None:
        return redirect("/admin/login")

    return render(
        request,
        "admin_archive.html",
        monitor=find_monitor(target_id),
        csrf_token=csrf_for(session_id),
        error=None,
    )


@router.post(
    "/admin/monitors/{target_id}/archive",
    include_in_schema=False,
)
async def archive_monitor_form(request: Request, target_id: str):
    same_origin_required(request)

    session_id = active_session(request)
    if session_id is None:
        raise HTTPException(status_code=401, detail="Login required")

    fields = await form_fields(request)
    csrf_required(request, session_id, fields.get("csrf_token", ""))

    if fields.get("confirm_id") != target_id:
        return render(
            request,
            "admin_archive.html",
            monitor=find_monitor(target_id),
            csrf_token=csrf_for(session_id),
            error="Enter the exact monitor ID to confirm archival.",
            status_code=422,
        )

    try:
        archive_monitor(db_path(), target_id)
    except MonitorNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Monitor not found",
        ) from exc
    except (RegistryNotInitialized, sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Cannot archive monitor",
        ) from exc

    return redirect("/admin/monitors?archived=" + quote(target_id, safe=""))
