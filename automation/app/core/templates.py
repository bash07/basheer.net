"""Shared Jinja2 template environment for UI routers."""
import os
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))
templates = Jinja2Templates(directory=TEMPLATES_DIR)
