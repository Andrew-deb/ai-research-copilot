"""dashboard/routes/home.py — Landing page (`/`)."""

from flask import Blueprint, render_template

from dashboard.middleware.auth import current_user_id
from dashboard.services import home_service

bp = Blueprint("home", __name__)


@bp.get("/")
def index():
    overview = home_service.get_overview(current_user_id())
    return render_template("index.html", **overview)
