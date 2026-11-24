from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, logout_user

from app.services.audit_service import AuditService
from app.views import auth_view_bp


@auth_view_bp.get("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("auth/login.html")


@auth_view_bp.post("/logout")
@login_required
def logout():
    """POST only: a GET logout link can be triggered by any page (<img src>)."""
    AuditService.log(user_id=current_user.id, action="logout")
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth_view.login_page"))


@auth_view_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    from app.api.auth import change_user_password
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        if new_password != request.form.get("confirm_password", ""):
            flash("The new passwords do not match.", "danger")
        else:
            error = change_user_password(current_user, request.form.get("current_password"),
                                         new_password)
            if error:
                flash(error + ".", "danger")
            else:
                flash("Password changed.", "success")
                return redirect(url_for("index"))
    return render_template("auth/change_password.html",
                           forced=current_user.must_change_password)
