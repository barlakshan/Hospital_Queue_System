from flask import render_template, redirect, url_for
from app.views import customer_bp
from app.models import Branch, ServiceType, QueueToken
from app.services.queue_service import QueueService


@customer_bp.route("/register/")
def register_index():
    branches = (Branch.query.filter_by(is_active=True)
                .order_by(Branch.name).all())
    if len(branches) == 1:
        return redirect(url_for("customer.register", branch_code=branches[0].code))
    return render_template("customer/branches.html", branches=branches)


@customer_bp.route("/register/<string:branch_code>")
def register(branch_code):
    branch = Branch.query.filter_by(code=branch_code.upper(), is_active=True).first_or_404()
    services = ServiceType.query.filter_by(is_active=True).order_by(ServiceType.id).all()
    return render_template("customer/register.html",
                            branch=branch, services=services)


@customer_bp.route("/appointments/")
def appointment_branches():
    branches = (Branch.query.filter_by(is_active=True)
                .order_by(Branch.name).all())
    if len(branches) == 1:
        return redirect(url_for("customer.appointment_request", branch_code=branches[0].code))
    return render_template("customer/appointment_branches.html", branches=branches)


@customer_bp.route("/appointments/<string:branch_code>")
def appointment_request(branch_code):
    branch = Branch.query.filter_by(code=branch_code.upper(), is_active=True).first_or_404()
    services = ServiceType.query.filter_by(is_active=True).order_by(ServiceType.name).all()
    return render_template(
        "customer/appointment_request.html",
        branch=branch,
        services=services,
    )


@customer_bp.route("/register/token/<string:token_ref>")
def token_page(token_ref):
    # Public page: addressed by an unguessable reference, never the
    # sequential database id.
    token = QueueToken.query.filter_by(public_ref=token_ref).first_or_404()
    position  = QueueService.get_position(token)
    mobile    = token.customer.mobile
    # Reveal only the last two digits of the mobile number.
    masked    = "•" * max(0, len(mobile) - 2) + mobile[-2:]
    return render_template("customer/token.html",
        token_ref=token.public_ref,
        token_number=token.token_number,
        service_name=token.service_type.name,
        branch_name=token.branch.name,
        mobile_masked=masked,
        position=position,
        estimated_wait=QueueService.estimate_wait(token, position),
        is_priority=token.is_priority,
        priority_reason=token.priority_reason.value.title() if token.is_priority else "",
        priority_requested=(token.priority_requested.value.title()
                            if token.priority_requested and not token.is_priority else ""),
        wait_pct=5,
    )
