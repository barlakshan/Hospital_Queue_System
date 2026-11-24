from flask import abort, redirect, render_template, url_for
from flask_login import current_user
from flask_socketio import join_room
from app.views import display_bp
from app.models import Branch
from app.extensions import socketio


@display_bp.route("/display")
@display_bp.route("/display/")
def screen_default():
    """Bare /display: show the first active location (single-site installs)."""
    branch = Branch.query.filter_by(is_active=True).order_by(Branch.id.asc()).first()
    if not branch:
        return redirect(url_for("index"))
    return render_template("display/screen.html", branch=branch)


@display_bp.route("/display/<string:branch_code>")
def screen(branch_code):
    # An unknown or deactivated code is a 404, never another location's
    # queue: patients would follow the wrong calls.
    branch = Branch.query.filter_by(code=branch_code.upper(), is_active=True).first_or_404()
    return render_template("display/screen.html", branch=branch)


@display_bp.route("/<string:branch_code>")
def short_screen(branch_code):
    if not branch_code.replace("-", "").replace("_", "").isalnum() or len(branch_code) > 10:
        abort(404)
    branch = Branch.query.filter_by(code=branch_code.upper(), is_active=True).first_or_404()
    return render_template("display/screen.html", branch=branch)


@socketio.on("connect")
def on_connect(auth=None):
    # Per-user room for events meant only for this staff member's consoles.
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")


@socketio.on("join_branch_display")
def on_join_display(data):
    """Public rooms carry display-level data only (token numbers, counters)."""
    if not isinstance(data, dict):
        return
    branch_code = str(data.get("branch_code") or "").upper()[:10]
    branch = Branch.query.filter_by(code=branch_code, is_active=True).first()
    if branch:
        join_room(f"branch_{branch.id}")


@socketio.on("join_branch")
def on_join_branch(data):
    if not isinstance(data, dict):
        return
    try:
        branch_id = int(data.get("branch_id"))
    except (TypeError, ValueError):
        return
    if not Branch.query.filter_by(id=branch_id, is_active=True).first():
        return
    join_room(f"branch_{branch_id}")
    # Staff-only events go to a separate room that requires a signed-in
    # member of that location.
    if current_user.is_authenticated and current_user.branch_id == branch_id:
        join_room(f"staff_branch_{branch_id}")
