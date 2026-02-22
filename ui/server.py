"""DevAgent Web UI — FastAPI + HTMX + Tailwind."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import socket

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.matrix.client import MatrixClient
from ui.projects_registry import Project, ProjectRegistry, scan_local_projects

# ── Config ────────────────────────────────────────────────────────────────────

DEVELOPMENT_ROOT  = os.getenv("DEVAGENT_DEVELOPMENT_ROOT", str(Path.home() / "development"))
REGISTRY_FILE     = os.getenv("DEVAGENT_PROJECTS_FILE", "/srv/devagent/state/projects.json")
MATRIX_HOMESERVER = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.org")
MATRIX_TOKEN      = os.getenv("MATRIX_ACCESS_TOKEN", "")
ARTIFACTS_ROOT    = os.getenv("DEVAGENT_ARTIFACTS_ROOT", "/srv/agent-artifacts")
REPOS_ROOT        = os.getenv("DEVAGENT_REPOS_ROOT", "/srv/repos")
MATRIX_INVITE     = [u.strip() for u in os.getenv("DEVAGENT_ALLOWED_USERS", "").split(",") if u.strip()]
LOG_FILE          = os.getenv("DEVAGENT_LOG_FILE", "/var/log/devagent/worker.log")
VERSION           = "0.1.0"
BACKEND_ID        = socket.gethostname()
_START_TIME       = datetime.now(timezone.utc)

# ── Auth config ───────────────────────────────────────────────────────────────
# If DEVAGENT_UI_API_KEY is empty the UI runs without authentication (dev mode).
_UI_API_KEY      = os.getenv("DEVAGENT_UI_API_KEY", "")
_AUTH_COOKIE     = "devagent_session"
# Paths that bypass authentication
_PUBLIC_PATHS    = {"/login", "/api/health"}


def _check_auth(request: Request) -> bool:
    """Return True if the request carries a valid session or API key."""
    if not _UI_API_KEY:
        return True  # auth disabled
    # Cookie-based session
    cookie_val = request.cookies.get(_AUTH_COOKIE, "")
    if cookie_val and hmac.compare_digest(cookie_val, _UI_API_KEY):
        return True
    # Header-based API key (for programmatic access)
    header_key = request.headers.get("X-API-Key", "")
    if header_key and hmac.compare_digest(header_key, _UI_API_KEY):
        return True
    return False

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="DevAgent UI", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ── Auth middleware ───────────────────────────────────────────────────────────

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Redirect unauthenticated HTML requests to /login; reject API calls with 401."""
    if request.url.path in _PUBLIC_PATHS or not _UI_API_KEY:
        return await call_next(request)
    if not _check_auth(request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        next_url = str(request.url)
        return RedirectResponse(url=f"/login?next={next_url}", status_code=302)
    return await call_next(request)


# ── Login / Logout routes ─────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    api_key: Annotated[str, Form()],
    next:    Annotated[str, Form()] = "/",
):
    if _UI_API_KEY and hmac.compare_digest(api_key, _UI_API_KEY):
        resp = RedirectResponse(url=next if next.startswith("/") else "/", status_code=303)
        resp.set_cookie(
            _AUTH_COOKIE,
            _UI_API_KEY,
            httponly=True,
            samesite="lax",
            secure=False,   # flip to True behind HTTPS
            max_age=7 * 24 * 3600,  # 1 week
        )
        return resp
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next": next, "error": "Ungültiger API-Schlüssel."},
        status_code=401,
    )


@app.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(_AUTH_COOKIE)
    return resp


def _registry() -> ProjectRegistry:
    """Always load a fresh registry to avoid stale data and race conditions."""
    return ProjectRegistry.load(REGISTRY_FILE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _matrix_client() -> MatrixClient:
    return MatrixClient(MATRIX_HOMESERVER, MATRIX_TOKEN)


def _registered_projects() -> list[dict]:
    """Return only projects that have been explicitly registered."""
    result = []
    for name, proj in _registry().projects.items():
        result.append({
            "name":             name,
            "local_path":       proj.local_path,
            "initialized":      proj.is_initialized(),
            "matrix_room_id":   proj.matrix_room_id,
            "matrix_room_name": proj.matrix_room_name,
            "created_at":       proj.created_at,
        })
    return sorted(result, key=lambda x: (not x["initialized"], x["name"].lower()))


def _unregistered_local_projects() -> list[dict]:
    """Scan ~/development and return projects NOT yet in the registry."""
    registered = set(_registry().projects.keys())
    return [
        p for p in scan_local_projects(DEVELOPMENT_ROOT)
        if p["name"] not in registered
    ]


def _create_matrix_room(name: str, display_name: str) -> str:
    """Create an unencrypted Matrix room, invite allowed users, return room_id."""
    client = _matrix_client()
    room_id = client.create_room(
        name=display_name,
        topic=f"DevAgent control room for {name}",
        invite=MATRIX_INVITE,
    )
    return room_id


def _get_recent_jobs(limit: int = 10) -> list[dict]:
    root = Path(ARTIFACTS_ROOT)
    jobs = []
    if not root.exists():
        return jobs
    for job_dir in sorted(root.glob("job-*"), reverse=True)[:30]:
        audit = job_dir / "audit.jsonl"
        if not audit.exists():
            continue
        lines = [l for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            continue
        last = json.loads(lines[-1])
        jobs.append({
            "job_id":    last.get("job_id", ""),
            "state":     last.get("state_after", "?"),
            "action":    last.get("action", ""),
            "timestamp": last.get("timestamp", ""),
        })
        if len(jobs) >= limit:
            break
    return jobs


# ── Routes: Pages ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    projects = _registered_projects()
    return templates.TemplateResponse("index.html", {
        "request":  request,
        "projects": projects,
    })


# ── Routes: Project list (HTMX refresh) ───────────────────────────────────────

@app.get("/partials/project-list", response_class=HTMLResponse)
async def projects_partial(request: Request):
    projects = _registered_projects()
    return templates.TemplateResponse("partials/project_list.html", {
        "request":  request,
        "projects": projects,
    })


# ── Routes: Manual add ────────────────────────────────────────────────────────

@app.get("/projects/add-form", response_class=HTMLResponse)
async def add_form(request: Request):
    return templates.TemplateResponse("partials/add_form.html", {"request": request})


@app.post("/projects/add", response_class=HTMLResponse)
async def add_project(
    request: Request,
    name:            Annotated[str, Form()],
    local_path:      Annotated[str, Form()],
    room_action:     Annotated[str, Form()] = "",   # "new" | "existing" | ""
    room_name:       Annotated[str, Form()] = "",
    existing_room_id: Annotated[str, Form()] = "",
):
    name       = name.strip()
    local_path = local_path.strip() or str(Path(DEVELOPMENT_ROOT) / name)

    if not name:
        return HTMLResponse("<p class='text-red-600 text-sm'>Name darf nicht leer sein.</p>")
    if name in _registry().projects:
        return HTMLResponse(f"<p class='text-amber-600 text-sm'>Projekt «{name}» ist bereits registriert.</p>")

    room_id    = ""
    room_label = ""

    if room_action == "new":
        if not MATRIX_TOKEN:
            return HTMLResponse("<p class='text-red-600 text-sm'>MATRIX_ACCESS_TOKEN fehlt in .env</p>")
        room_label = room_name.strip() or f"DevAgent · {name}"
        try:
            room_id = _create_matrix_room(name, room_label)
        except Exception as exc:
            return HTMLResponse(f"<p class='text-red-600 text-sm'>Matrix-Fehler beim Erstellen des Raums. Details im Log.</p>")
    elif room_action == "existing":
        room_id    = existing_room_id.strip()
        room_label = room_name.strip() or room_id

    proj = Project(
        name=name,
        local_path=local_path,
        matrix_room_id=room_id,
        matrix_room_name=room_label,
        repo_name=name,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    reg = _registry()
    reg.upsert(proj)
    reg.setup_repo_symlink(name, REPOS_ROOT)
    reg.ensure_claude_md(name)

    projects = _registered_projects()
    return templates.TemplateResponse("partials/project_list.html", {
        "request":  request,
        "projects": projects,
        "flash":    f"Projekt «{name}» hinzugefügt.",
    })


# ── Routes: Import discovery ──────────────────────────────────────────────────

@app.get("/import", response_class=HTMLResponse)
async def import_panel(request: Request):
    found = _unregistered_local_projects()
    return templates.TemplateResponse("partials/import_panel.html", {
        "request": request,
        "found":   found,
    })


@app.post("/import", response_class=HTMLResponse)
async def do_import(request: Request):
    form   = await request.form()
    names  = form.getlist("projects")
    create = form.get("create_rooms") == "on"
    added  = []
    errors = []

    for name in names:
        local_path = str(Path(DEVELOPMENT_ROOT) / name)
        room_id    = ""
        room_label = ""

        if create:
            if not MATRIX_TOKEN:
                errors.append(f"{name}: MATRIX_ACCESS_TOKEN fehlt")
                continue
            room_label = f"DevAgent · {name}"
            try:
                room_id = _create_matrix_room(name, room_label)
            except Exception:
                errors.append(f"{name}: Matrix-Fehler — Details im Log.")
                continue

        proj = Project(
            name=name,
            local_path=local_path,
            matrix_room_id=room_id,
            matrix_room_name=room_label,
            repo_name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        reg = _registry()
        reg.upsert(proj)
        reg.setup_repo_symlink(name, REPOS_ROOT)
        reg.ensure_claude_md(name)
        added.append(name)

    projects = _registered_projects()
    flash = f"{len(added)} Projekt(e) importiert." if added else ""
    return templates.TemplateResponse("partials/project_list.html", {
        "request":  request,
        "projects": projects,
        "flash":    flash,
        "errors":   errors,
    })


# ── Routes: Project detail ────────────────────────────────────────────────────

@app.get("/projects/{name}", response_class=HTMLResponse)
async def project_detail(request: Request, name: str):
    proj = _registry().projects.get(name)
    local_path = proj.local_path if proj else str(Path(DEVELOPMENT_ROOT) / name)
    jobs = _get_recent_jobs()
    return templates.TemplateResponse("partials/project_detail.html", {
        "request":    request,
        "name":       name,
        "proj":       proj,
        "local_path": local_path,
        "jobs":       jobs,
    })


@app.get("/projects/{name}/room-form", response_class=HTMLResponse)
async def room_form(request: Request, name: str):
    """Return inline room-link/create form for a project card."""
    return templates.TemplateResponse("partials/room_form.html", {
        "request": request,
        "name":    name,
    })


@app.post("/projects/{name}/init-room", response_class=HTMLResponse)
async def init_room(
    request:         Request,
    name:            str,
    room_action:     Annotated[str, Form()] = "new",
    room_name:       Annotated[str, Form()] = "",
    existing_room_id: Annotated[str, Form()] = "",
):
    """Link an existing or create a new Matrix room for an already-registered project."""
    reg  = _registry()
    proj = reg.projects.get(name)
    if not proj:
        return HTMLResponse(f"<p class='text-red-600 text-sm'>Projekt {name} nicht gefunden.</p>")

    if room_action == "existing":
        room_id      = existing_room_id.strip()
        display_name = room_name.strip() or room_id
        if not room_id:
            return HTMLResponse("<p class='text-red-600 text-sm'>Bitte einen Raum auswählen.</p>")
        flash = f"Raum für «{name}» verknüpft: {room_id}"
    else:
        if not MATRIX_TOKEN:
            return HTMLResponse("<p class='text-red-600 text-sm'>MATRIX_ACCESS_TOKEN fehlt in .env</p>")
        display_name = room_name.strip() or f"DevAgent · {name}"
        try:
            room_id = _create_matrix_room(name, display_name)
        except Exception:
            return HTMLResponse("<p class='text-red-600 text-sm'>Matrix-Fehler beim Erstellen des Raums. Details im Log.</p>")
        flash = f"Matrix-Raum für «{name}» erstellt: {room_id}"

    proj.matrix_room_id   = room_id
    proj.matrix_room_name = display_name
    reg.upsert(proj)
    reg.ensure_claude_md(name)

    return templates.TemplateResponse("partials/project_list.html", {
        "request":  request,
        "projects": _registered_projects(),
        "flash":    flash,
    })


@app.post("/projects/{name}/tmux", response_class=HTMLResponse)
async def open_tmux(request: Request, name: str):
    proj    = _registry().projects.get(name)
    cwd     = proj.local_path if proj else str(Path(DEVELOPMENT_ROOT) / name)
    session = f"dev-{name}"
    try:
        has = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True)
        if has.returncode != 0:
            subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", cwd], check=True)
        return HTMLResponse(
            f"<span class='text-green-700 font-mono text-sm'>"
            f"Session <b>{session}</b> bereit — "
            f"<code class='bg-slate-100 px-1 rounded'>tmux attach -t {session}</code></span>"
        )
    except Exception as exc:
        return HTMLResponse(f"<p class='text-red-600 text-sm'>tmux-Fehler: {exc}</p>")


@app.get("/projects/{name}/claude-md", response_class=HTMLResponse)
async def get_claude_md(request: Request, name: str):
    proj = _registry().projects.get(name)
    path = Path(proj.local_path) / "CLAUDE.md" if proj else None
    content = path.read_text(encoding="utf-8") if path and path.exists() else ""
    return templates.TemplateResponse("partials/claude_md_editor.html", {
        "request": request,
        "name":    name,
        "content": content,
        "exists":  bool(path and path.exists()),
    })


@app.post("/projects/{name}/claude-md", response_class=HTMLResponse)
async def save_claude_md(
    request: Request,
    name:    str,
    content: Annotated[str, Form()],
):
    proj = _registry().projects.get(name)
    if not proj:
        return HTMLResponse("<p class='text-red-500 text-xs'>Projekt nicht gefunden.</p>")
    path = Path(proj.local_path) / "CLAUDE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return HTMLResponse(
        "<p class='text-emerald-600 text-xs'>✓ CLAUDE.md gespeichert.</p>",
        headers={"HX-Trigger": "claudeMdSaved"},
    )


@app.delete("/projects/{name}", response_class=HTMLResponse)
async def remove_project(request: Request, name: str):
    _registry().remove(name)
    projects = _registered_projects()
    return templates.TemplateResponse("partials/project_list.html", {
        "request":  request,
        "projects": projects,
    })


# ── JSON API (for multi-backend federation) ───────────────────────────────────

@app.get("/api/health")
async def api_health():
    uptime = int((datetime.now(timezone.utc) - _START_TIME).total_seconds())
    return JSONResponse({
        "status":          "ok",
        "backend_id":      BACKEND_ID,
        "version":         VERSION,
        "uptime_seconds":  uptime,
        "projects_count":  len(_registry().projects),
    })


@app.get("/api/projects")
async def api_projects():
    return JSONResponse(_registered_projects())


@app.get("/api/jobs")
async def api_jobs(limit: int = 20):
    return JSONResponse(_get_recent_jobs(limit=limit))


@app.get("/api/matrix/rooms")
async def api_matrix_rooms():
    """Return all joined Matrix rooms with their display names, sorted by name."""
    if not MATRIX_TOKEN:
        return JSONResponse({"error": "MATRIX_ACCESS_TOKEN nicht gesetzt"}, status_code=503)
    try:
        client  = _matrix_client()
        ids     = client.get_joined_rooms()
        rooms   = [{"id": rid, "name": client.get_room_name(room_id=rid) or rid} for rid in ids]
        rooms.sort(key=lambda r: r["name"].lower())
        return JSONResponse(rooms)
    except Exception:
        return JSONResponse({"error": "Matrix-Anfrage fehlgeschlagen. Details im Log."}, status_code=500)


@app.get("/api/worker/status")
async def api_worker_status(request: Request, format: str = "json"):
    status_path = Path(REGISTRY_FILE).parent / "worker_status.json"
    running = False
    age_seconds = None
    rooms_watched: list = []
    error = None

    if not status_path.exists():
        error = "worker_status.json nicht gefunden"
    else:
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            updated_at  = datetime.fromisoformat(data["updated_at"])
            age_seconds = int((datetime.now(timezone.utc) - updated_at).total_seconds())
            running     = age_seconds < 600
            rooms_watched = data.get("rooms_watched", [])
            data["age_seconds"] = age_seconds
            data["running"]     = running
        except Exception as exc:
            error = str(exc)

    if format == "html" or request.headers.get("HX-Request"):
        if running:
            dot   = "bg-emerald-500"
            label = f"Worker aktiv · {len(rooms_watched)} Raum/Räume"
        else:
            dot   = "bg-red-400"
            label = f"Worker offline{' · ' + error if error else ''}"
        return HTMLResponse(
            f'<span class="flex items-center gap-1.5 text-xs text-slate-500">'
            f'<span class="w-2 h-2 rounded-full {dot} inline-block"></span>'
            f'{label}</span>'
        )

    if error:
        return JSONResponse({"running": False, "error": error})
    return JSONResponse(data)


# ── Routes: Live Log ──────────────────────────────────────────────────────────

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return templates.TemplateResponse("logs.html", {"request": request, "log_file": LOG_FILE})


@app.get("/api/logs/stream")
async def logs_stream():
    """SSE endpoint that tails LOG_FILE and streams new lines."""
    log_path = Path(LOG_FILE)

    async def generate():
        if not log_path.exists():
            yield f"data: [Log-Datei nicht gefunden: {log_path}]\n\n"
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                # Seek to last ~8 KB for initial context
                size = log_path.stat().st_size
                f.seek(max(0, size - 8192))
                if size > 8192:
                    f.readline()  # discard partial first line
                while True:
                    line = f.readline()
                    if line:
                        yield f"data: {line.rstrip()}\n\n"
                    else:
                        await asyncio.sleep(0.5)
        except Exception as exc:
            yield f"data: [Fehler beim Lesen: {exc}]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
