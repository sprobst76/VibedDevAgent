"""DevAgent Web UI — FastAPI + HTMX + Tailwind."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import socket

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.matrix.client import MatrixClient
from core.todo_parser import parse_todo_file as _parse_todo
from ui.projects_registry import Project, ProjectRegistry, scan_local_projects

# ── Config ────────────────────────────────────────────────────────────────────

TODO_FILE         = os.getenv("DEVAGENT_TODO_FILE", str(Path(__file__).parent.parent / "TODO.md"))
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

# Multi-backend federation: DEVAGENT_BACKENDS=home=http://100.x.x.x:20042,vps=http://...
BACKENDS: dict[str, str] = {}
for _entry in os.getenv("DEVAGENT_BACKENDS", "").split(","):
    _entry = _entry.strip()
    if "=" in _entry:
        _n, _, _u = _entry.partition("=")
        BACKENDS[_n.strip()] = _u.strip()

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


def _get_recent_jobs(limit: int = 20) -> list[dict]:
    root = Path(ARTIFACTS_ROOT)
    jobs = []
    if not root.exists():
        return jobs
    for job_dir in sorted(root.glob("job-*"), reverse=True)[:50]:
        audit = job_dir / "audit.jsonl"
        if not audit.exists():
            continue
        lines = [line for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            continue
        first = json.loads(lines[0])
        last  = json.loads(lines[-1])
        job_id = last.get("job_id", "")
        jobs.append({
            "job_id":       job_id,
            "short_id":     job_id[:8] if len(job_id) > 8 else job_id,
            "state":        last.get("state_after", "?"),
            "action":       last.get("action", ""),
            "project":      first.get("extra", {}).get("repo", ""),
            "requested_by": first.get("user_id", ""),
            "created_at":   first.get("timestamp", ""),
            "updated_at":   last.get("timestamp", ""),
        })
        if len(jobs) >= limit:
            break
    return jobs


def _get_job_audit(job_id: str) -> list[dict]:
    """Return the full audit trail for a single job."""
    audit = Path(ARTIFACTS_ROOT) / f"job-{job_id}" / "audit.jsonl"
    if not audit.exists():
        return []
    lines = [line for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


_JOB_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


def _valid_job_id(job_id: str) -> bool:
    """Guard against path traversal: only allow safe characters in job_id."""
    return bool(job_id and len(job_id) <= 128 and _JOB_ID_RE.match(job_id))


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
        except Exception:
            return HTMLResponse("<p class='text-red-600 text-sm'>Matrix-Fehler beim Erstellen des Raums. Details im Log.</p>")
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
            f"<span class='text-[#50fa7b] font-mono text-sm'>"
            f"Session <b>{session}</b> bereit — "
            f"<code class='bg-[#44475a] px-1 rounded text-[#f8f8f2]'>tmux attach -t {session}</code></span>"
        )
    except Exception as exc:
        return HTMLResponse(f"<p class='text-[#ff5555] text-sm'>tmux-Fehler: {exc}</p>")


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
        "<p class='text-[#50fa7b] text-xs'>✓ CLAUDE.md gespeichert.</p>",
        headers={"HX-Trigger": "claudeMdSaved"},
    )


@app.get("/projects/{name}/todos", response_class=HTMLResponse)
async def project_todos_partial(request: Request, name: str):
    """HTMX partial: open TODO items for a single project."""
    proj = _registry().projects.get(name)
    if not proj:
        return HTMLResponse("<p class='text-xs text-drac-comment italic'>Projekt nicht gefunden.</p>")
    sections = _parse_todo(Path(proj.local_path) / "TODO.md") if proj.local_path else []
    return templates.TemplateResponse("partials/project_todos.html", {
        "request":  request,
        "name":     name,
        "sections": sections,
    })


@app.get("/api/todos/projects")
async def api_todos_projects():
    """Return open TODO counts for all registered projects."""
    result = []
    for name, proj in _registry().projects.items():
        if not proj.local_path:
            continue
        proj_sections = _parse_todo(Path(proj.local_path) / "TODO.md")
        open_count = sum(len(s.open_items) for s in proj_sections)
        done_count = sum(s.done_count for s in proj_sections)
        result.append({
            "name":       name,
            "open_count": open_count,
            "done_count": done_count,
            "has_todo":   bool(proj_sections),
        })
    return JSONResponse(sorted(result, key=lambda x: (-x["open_count"], x["name"])))


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
            dot   = "bg-[#50fa7b]"
            label = f"Worker aktiv · {len(rooms_watched)} Raum/Räume"
            text  = "text-[#50fa7b]"
        else:
            dot   = "bg-[#ff5555]"
            label = f"Worker offline{' · ' + error if error else ''}"
            text  = "text-[#ff5555]"
        return HTMLResponse(
            f'<span class="flex items-center gap-1.5 text-xs {text}">'
            f'<span class="w-2 h-2 rounded-full {dot} inline-block"></span>'
            f'{label}</span>'
        )

    if error:
        return JSONResponse({"running": False, "error": error})
    return JSONResponse(data)


# ── Routes: Jobs ──────────────────────────────────────────────────────────────

@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    jobs = _get_recent_jobs(limit=50)
    return templates.TemplateResponse("jobs.html", {"request": request, "jobs": jobs})


@app.get("/todos", response_class=HTMLResponse)
async def todos_page(request: Request):
    sections = _parse_todo(TODO_FILE)
    # Collect per-project TODO summaries
    project_todos: list[dict] = []
    for name, proj in _registry().projects.items():
        if not proj.local_path:
            continue
        proj_sections = _parse_todo(Path(proj.local_path) / "TODO.md")
        if not proj_sections:
            continue
        open_count = sum(len(s.open_items) for s in proj_sections)
        done_count = sum(s.done_count for s in proj_sections)
        project_todos.append({
            "name":       name,
            "open_count": open_count,
            "done_count": done_count,
            "sections":   proj_sections,
        })
    project_todos.sort(key=lambda x: (-x["open_count"], x["name"]))
    return templates.TemplateResponse("todos.html", {
        "request":       request,
        "sections":      sections,
        "project_todos": project_todos,
    })


@app.get("/api/todos")
async def api_todos():
    sections = _parse_todo(TODO_FILE)
    return JSONResponse([
        {
            "priority":   s.priority,
            "title":      s.title,
            "open_items": s.open_items,
            "done_count": s.done_count,
            "total":      s.total,
        }
        for s in sections
    ])


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail_partial(request: Request, job_id: str):
    if not _valid_job_id(job_id):
        return HTMLResponse("<p class='text-red-500 text-sm'>Ungültige Job-ID.</p>", status_code=400)
    audit = _get_job_audit(job_id)
    runner_log = Path(ARTIFACTS_ROOT) / f"job-{job_id}" / "runner.log"
    state = audit[-1].get("state_after", "?") if audit else "?"
    return templates.TemplateResponse("partials/job_detail.html", {
        "request":    request,
        "job_id":     job_id,
        "audit":      audit,
        "state":      state,
        "has_log":    runner_log.exists(),
    })


# ── JSON API: Job detail ───────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/audit")
async def api_job_audit(job_id: str):
    if not _valid_job_id(job_id):
        return JSONResponse({"error": "invalid job_id"}, status_code=400)
    return JSONResponse(_get_job_audit(job_id))


@app.get("/api/jobs/{job_id}/log/stream")
async def job_log_stream(job_id: str, follow: bool = True):
    """SSE endpoint: tail the runner.log for a specific job."""
    if not _valid_job_id(job_id):
        async def _err():
            yield "data: [Ungültige Job-ID]\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    log_path = Path(ARTIFACTS_ROOT) / f"job-{job_id}" / "runner.log"

    async def generate():
        if not log_path.exists():
            yield f"data: [runner.log nicht gefunden für Job {job_id}]\n\n"
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                size = log_path.stat().st_size
                f.seek(max(0, size - 32768))
                if size > 32768:
                    f.readline()  # discard partial first line
                while True:
                    line = f.readline()
                    if line:
                        yield f"data: {line.rstrip()}\n\n"
                    elif follow:
                        await asyncio.sleep(0.5)
                    else:
                        break
        except Exception as exc:
            yield f"data: [Fehler beim Lesen: {exc}]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── JSON API: Stats ────────────────────────────────────────────────────────────

@app.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request):
    """HTMX partial: dashboard stat cards."""
    jobs = _get_recent_jobs(limit=200)
    running = sum(1 for j in jobs if j["state"] == "RUNNING")
    projects_count = len(_registry().projects)
    last_job_at = jobs[0]["updated_at"][:16] if jobs else "—"

    # Worker status
    status_path = Path(REGISTRY_FILE).parent / "worker_status.json"
    worker_ok = False
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(data["updated_at"])
            age = int((datetime.now(timezone.utc) - updated_at).total_seconds())
            worker_ok = age < 600
        except Exception:
            pass

    worker_dot = "bg-[#50fa7b]" if worker_ok else "bg-[#ff5555]"
    worker_label = "aktiv" if worker_ok else "offline"
    worker_text = "text-[#50fa7b]" if worker_ok else "text-[#ff5555]"

    cards = [
        ("Projekte", str(projects_count), "text-[#bd93f9]"),
        ("Laufend", str(running), "text-[#8be9fd]" if running else "text-[#6272a4]"),
        ("Jobs gesamt", str(len(jobs)), "text-[#f8f8f2]"),
        ("Letzter Job", last_job_at, "text-[#6272a4]"),
    ]

    html = '<div class="grid grid-cols-2 sm:grid-cols-5 gap-3">'
    for label, value, text_cls in cards:
        html += (
            f'<div class="bg-[#21222c] border border-[#44475a] rounded-xl p-4">'
            f'<p class="text-xs text-[#6272a4] font-medium">{label}</p>'
            f'<p class="text-xl font-bold {text_cls} mt-1 truncate">{value}</p>'
            f'</div>'
        )
    # Worker status card
    html += (
        f'<div class="bg-[#21222c] border border-[#44475a] rounded-xl p-4">'
        f'<p class="text-xs text-[#6272a4] font-medium">Worker</p>'
        f'<div class="flex items-center gap-2 mt-1">'
        f'<span class="w-2.5 h-2.5 rounded-full {worker_dot} inline-block shrink-0"></span>'
        f'<span class="text-sm font-semibold {worker_text}">{worker_label}</span>'
        f'</div>'
        f'</div>'
    )
    html += '</div>'
    return HTMLResponse(html)


@app.get("/api/stats")
async def api_stats(request: Request):
    """Return summary statistics for the dashboard (JSON or HTMX HTML fragment)."""
    jobs = _get_recent_jobs(limit=200)
    running = sum(1 for j in jobs if j["state"] == "RUNNING")
    last_job_at = jobs[0]["updated_at"] if jobs else None
    data = {
        "total_jobs":     len(jobs),
        "running_jobs":   running,
        "projects_count": len(_registry().projects),
        "last_job_at":    last_job_at,
    }
    if request.headers.get("HX-Request"):
        # Return only the running-badge fragment for the navbar
        badge = (
            f' <span class="ml-1 bg-blue-500 text-white text-[10px] font-bold'
            f' rounded-full px-1.5 py-0.5 align-middle">{running}</span>'
        ) if running else ""
        return HTMLResponse(
            f'<span id="nav-running-badge"'
            f' hx-get="/api/stats" hx-trigger="every 30s"'
            f' hx-swap="outerHTML" hx-select="#nav-running-badge">{badge}</span>'
        )
    return JSONResponse(data)


# ── Multi-backend federation ──────────────────────────────────────────────────

def _fetch_backend(url: str, path: str, timeout: float = 3.0) -> dict | list | None:
    """Fetch JSON from a remote backend. Returns None on any error."""
    full_url = url.rstrip("/") + path
    req = urllib.request.Request(full_url)
    if _UI_API_KEY:
        req.add_header("X-API-Key", _UI_API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _poll_backends() -> list[dict]:
    """Poll all configured backends in parallel and return status dicts."""
    if not BACKENDS:
        return []

    def _probe(name: str, url: str) -> dict:
        health = _fetch_backend(url, "/api/health") or {}
        stats  = _fetch_backend(url, "/api/stats")  or {}
        worker = _fetch_backend(url, "/api/worker/status") or {}
        online = bool(health.get("status") == "ok")
        return {
            "name":           name,
            "url":            url,
            "online":         online,
            "backend_id":     health.get("backend_id", name),
            "uptime_seconds": health.get("uptime_seconds"),
            "projects_count": stats.get("projects_count", health.get("projects_count", 0)),
            "running_jobs":   stats.get("running_jobs", 0),
            "total_jobs":     stats.get("total_jobs", 0),
            "worker_running": worker.get("running", False),
        }

    workers = max(1, len(BACKENDS))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda item: _probe(*item), BACKENDS.items()))
    return results


@app.get("/backends", response_class=HTMLResponse)
async def backends_page(request: Request):
    backends = await asyncio.get_event_loop().run_in_executor(None, _poll_backends)

    all_projects: list[dict] = []
    all_jobs: list[dict] = []

    def _fetch_data(b: dict) -> None:
        if not b["online"]:
            return
        projs = _fetch_backend(b["url"], "/api/projects") or []
        if isinstance(projs, list):
            for p in projs:
                p["backend"] = b["name"]
                all_projects.append(p)
        jobs = _fetch_backend(b["url"], "/api/jobs?limit=20") or []
        if isinstance(jobs, list):
            for j in jobs:
                j["backend"] = b["name"]
                all_jobs.append(j)

    for b in backends:
        _fetch_data(b)

    all_jobs.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    all_projects.sort(key=lambda x: (x.get("backend", ""), x.get("name", "").lower()))

    return templates.TemplateResponse("backends.html", {
        "request":      request,
        "backends":     backends,
        "all_projects": all_projects,
        "all_jobs":     all_jobs[:50],
    })


@app.get("/api/backends")
async def api_backends():
    """Return health/stats for all configured backends (no projects/jobs)."""
    backends = await asyncio.get_event_loop().run_in_executor(None, _poll_backends)
    return JSONResponse(backends)


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
