"""The phone page: one server-rendered page, POST-redirect-GET."""

import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .session import Manager, SessionError

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
CLIENT_COOKIE = "samplemark_client"
NAME_COOKIE = "samplemark_name"
YEAR = 365 * 24 * 3600

Text = Annotated[str, Form()]


def _back(msg: str | None = None, error: str | None = None) -> RedirectResponse:
    query = urlencode({k: v for k, v in (("msg", msg), ("error", error)) if v})
    return RedirectResponse("/" + (f"?{query}" if query else ""), status_code=303)


def create_app(manager: Manager) -> FastAPI:
    app = FastAPI(title="samplemark")

    def client_of(request: Request) -> str:
        client = request.cookies.get(CLIENT_COOKIE)
        if not client:
            raise SessionError("Reload the page first.")
        return client

    def act(fn, msg: str) -> RedirectResponse:
        try:
            fn()
        except (SessionError, ValueError, OSError) as error:
            return _back(error=str(error))
        return _back(msg=msg)

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request, msg: str | None = None, error: str | None = None):
        if manager.check_limit():
            msg = "Session stopped: time limit reached."
        client = request.cookies.get(CLIENT_COOKIE) or uuid.uuid4().hex
        context = {
            "msg": msg, "error": error,
            "name": request.cookies.get(NAME_COOKIE),
            "status": manager.status(),
            "marked": manager.marked_at(client),
            "pending": manager.pending_info(client),
        }
        response = TEMPLATES.TemplateResponse(request, "page.html", context)
        response.set_cookie(CLIENT_COOKIE, client, max_age=YEAR, samesite="lax")
        return response

    @app.post("/name")
    def name(name: Text):
        response = _back(msg=f"Hello, {name.strip()}.")
        response.set_cookie(NAME_COOKIE, name.strip(), max_age=YEAR, samesite="lax")
        return response

    @app.post("/start")
    def start(title: Text, year: Text = "", kind: Text = "", notes: Text = "",
              position: Text = ""):
        meta = {"year": year.strip(), "type": kind.strip(), "notes": notes.strip()}
        return act(lambda: manager.start(title, meta, position), "Session started.")

    @app.post("/sync")
    def sync(position: Text):
        return act(lambda: manager.sync(position), "Position synced.")

    @app.post("/mark")
    def mark(request: Request):
        return act(lambda: manager.mark(client_of(request)), "Marked.")

    @app.post("/end")
    def end(request: Request):
        return act(lambda: manager.end(client_of(request)), "Clip captured.")

    @app.post("/finish")
    def finish(request: Request, text: Text = "", tags: Text = ""):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        marked_by = request.cookies.get(NAME_COOKIE, "")
        saved = []

        def keep():
            saved.append(manager.finish(client_of(request), marked_by, text, tag_list))

        response = act(keep, "Saved.")
        return response if not saved else _back(msg=f"Saved {saved[0].name}.")

    @app.post("/discard")
    def discard(request: Request):
        return act(lambda: manager.discard(client_of(request)), "Discarded.")

    @app.post("/stop")
    def stop():
        return act(manager.stop, "Session stopped.")

    @app.post("/restart")
    def restart():
        return act(manager.restart_capture, "Capture restarted.")

    return app
