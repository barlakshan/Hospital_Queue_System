from sqlalchemy import event
from sqlalchemy.orm import Session

from app.extensions import db


class Counter(db.Model):
    __tablename__ = "counters"
    id            = db.Column(db.Integer, primary_key=True)
    branch_id     = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id"))
    label         = db.Column(db.String(20), nullable=False)
    service_scope = db.Column(db.String(20))
    is_active     = db.Column(db.Boolean, default=True)
    branch  = db.relationship("Branch", back_populates="counters")
    officer = db.relationship("User", foreign_keys=[user_id])
    tokens  = db.relationship("QueueToken", back_populates="counter", lazy="dynamic")


@event.listens_for(Session, "before_flush")
def _counter_staff_same_branch(session, flush_context, instances):
    """A counter may only be assigned to staff of its own location."""
    from app.models.user import User
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Counter) and obj.user_id is not None:
            staff = session.get(User, obj.user_id)
            if staff is not None and staff.branch_id != obj.branch_id:
                raise ValueError("A counter can only be assigned to staff of the same location")
