"""FastAPI assembly for the live gateway.

Protocol behavior lives in the mounted routers:
- `/v1/*` for OpenAI-compatible chat/models and the universal/task API
- `/config/*` for admin/config management

This module only wires those routers together and serves the lightweight static
UI entrypoints (`/`, `/chat`, `/config`).
"""

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from .server.routes import router as api_router
from .server.config_routes import router as config_router

app = FastAPI(
    title="Open LLM Auth",
    description="LLM gateway with OpenAI-compatible chat and universal task APIs",
)

# API routes: chat/models plus the universal task surface.
app.include_router(api_router)
app.include_router(config_router)

# Static files back the lightweight config/chat GUIs.
static_dir = Path(__file__).parent / "server" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Templates for the advanced admin dashboard.
templates_dir = Path(__file__).parent / "server" / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root(request: Request):
    """Serve the advanced admin dashboard."""
    dashboard_file = templates_dir / "dashboard.html"
    if dashboard_file.exists():
        return templates.TemplateResponse(request, "dashboard.html")
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Open LLM Auth API", "docs": "/docs", "gui": "/config"}


@app.get("/chat")
def chat_gui():
    """Serve the browser chat UI when static assets are present."""
    chat_file = static_dir / "chat.html"
    if chat_file.exists():
        return FileResponse(str(chat_file))
    return {"error": "Chat GUI not found"}


