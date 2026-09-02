"""Gemeinsame Jinja2-Umgebung für Kiosk- und Admin-Templates."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["site_title"] = get_settings().site_title
