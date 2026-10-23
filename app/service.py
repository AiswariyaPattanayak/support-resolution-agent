from sqlalchemy import select

from .db import Order, RefundOperation, ReturnRecord
from .eligibility import Verdict, assess_refund
from .policies import find_policy

ACTIVE = ("reserved", "submitted", "unknown", "succeeded")


def op_dict(op: RefundOperation) -> dict:
    return {"id": op.id, "status": op.status, "amount_minor": op.amount_minor,
            "provider_reference": op.provider_reference, "created_at": op.created_at.date().isoformat()}


def get_owned_order(s, customer_id: str, order_id: str):
    """Ownership is enforced here, inside the service. Returns None for 'not yours' and 'does not exist'."""
    return s.scalars(select(Order).where(Order.id == order_id, Order.customer_id == customer_id)).first()


def assess_order_refund(s, order: Order):
    ret = s.scalars(select(ReturnRecord).where(ReturnRecord.order_id == order.id)
                    .order_by(ReturnRecord.initiated_at.desc())).first()
    ret_d = None if ret is None else {"status": ret.status, "initiated_at": ret.initiated_at,
                                      "received_at": ret.received_at, "inspection_status": ret.inspection_status}
    ops = [op_dict(o) for o in s.scalars(select(RefundOperation).where(RefundOperation.order_id == order.id))]
    doc = find_policy(s, "refund", order.ordered_at)
    if doc is None:
        from .eligibility import Assessment
        return Assessment(Verdict.NEEDS_REVIEW, "NO_APPLICABLE_POLICY"), None
    a = assess_refund(paid_amount_minor=order.paid_amount_minor, delivered_at=order.delivered_at,
                      ret=ret_d, refund_ops=ops, window_days=doc.meta["window_days"])
    return a, doc


def validate_refund(s, order: Order, amount_minor: int, reason_code: str):
    """Re-run immediately before money moves. Returns (ok, error_code)."""
    ops = s.scalars(select(RefundOperation).where(RefundOperation.order_id == order.id)).all()
    if any(o.status in ACTIVE for o in ops):
        return False, "REFUND_ALREADY_ACTIVE"
    if amount_minor <= 0 or amount_minor > order.paid_amount_minor:
        return False, "AMOUNT_EXCEEDS_PAID"
    if reason_code == "DAMAGED_ITEM":
        return True, ""
    a, _ = assess_order_refund(s, order)
    if a.verdict != Verdict.ELIGIBLE_NEW_REFUND:
        return False, a.reason_code
    if a.amount_minor != amount_minor:
        return False, "AMOUNT_MISMATCH"
    return True, ""
