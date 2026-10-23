"""Reserve-then-call refund execution.

1. short DB transaction: verify stored approval + payload hash, lock the order row,
   revalidate, insert a refund_operations row (unique idempotency key) => amount reserved
2. COMMIT, then call the provider with the stable key (never hold a tx across a network call)
3. persist the provider's answer; uncertain => 'unknown', reconcile by key later
"""
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import ActionProposal, Approval, Order, RefundOperation, Ticket, WorkflowRun, audit, utcnow
from .payments import PaymentClient, ProviderRejected, ProviderUncertain
from .replies import inr
from .service import op_dict, validate_refund


class RefundBlocked(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _run_id(s, op):
    prop = s.get(ActionProposal, op.proposal_id) if op.proposal_id else None
    return prop.run_id if prop else None


def idem_key(proposal_id: str) -> str:
    return "refund-" + proposal_id


def execute_approved_refund(Session, proposal_id: str, pay: PaymentClient) -> dict:
    try:
        with Session.begin() as s:
            prop = s.get(ActionProposal, proposal_id)
            appr = s.scalars(select(Approval).where(Approval.proposal_id == proposal_id)).first()
            if prop is None or appr is None:
                raise RefundBlocked("NO_PROPOSAL")
            if appr.decision != "approved":
                raise RefundBlocked("NOT_APPROVED")
            if appr.expires_at < (appr.decided_at or utcnow()):
                raise RefundBlocked("APPROVAL_EXPIRED")
            if payload_hash(prop.payload) != prop.payload_hash:
                raise RefundBlocked("PAYLOAD_CHANGED_AFTER_APPROVAL")
            p = prop.payload
            order = s.execute(select(Order).where(Order.id == p["order_id"]).with_for_update()).scalar_one()
            existing = s.scalars(select(RefundOperation).where(
                RefundOperation.idempotency_key == idem_key(proposal_id))).first()
            if existing:  # retry / duplicate call: never create a second operation
                return op_dict(existing) | {"created": False}
            ok, code = validate_refund(s, order, p["amount_minor"], p["reason_code"])
            if not ok:
                audit(s, prop.run_id, "system", "refund_blocked_at_validation", {"code": code})
                raise RefundBlocked(code)
            op = RefundOperation(id="op_" + proposal_id[-12:], order_id=order.id, proposal_id=proposal_id,
                                 amount_minor=p["amount_minor"], idempotency_key=idem_key(proposal_id),
                                 status="reserved")
            s.add(op)
            audit(s, prop.run_id, "system", "refund_reserved", {"op": op.id, "amount_minor": op.amount_minor})
            op_id = op.id
    except IntegrityError:  # lost a race on the unique key: someone else owns this operation
        with Session() as s:
            op = s.scalars(select(RefundOperation).where(
                RefundOperation.idempotency_key == idem_key(proposal_id))).one()
            return op_dict(op) | {"created": False}
    return _call_provider(Session, op_id, pay) | {"created": True}


def _call_provider(Session, op_id: str, pay: PaymentClient) -> dict:
    with Session() as s:
        op = s.get(RefundOperation, op_id)
        key, order_id, amount = op.idempotency_key, op.order_id, op.amount_minor
    status, ref, note = "unknown", None, None
    try:
        resp = pay.create_refund(key, order_id, amount)
        status = "succeeded" if resp["status"] == "succeeded" else "submitted"
        ref = resp["refund_id"]
    except ProviderUncertain as e:
        note = str(e)
    except ProviderRejected as e:
        status, note = "failed", str(e)
    with Session.begin() as s:
        op = s.get(RefundOperation, op_id)
        op.status, op.provider_reference = status, ref or op.provider_reference
        audit(s, _run_id(s, op), "system", "refund_provider_result", {"op": op_id, "status": status, "note": note})
        return op_dict(op)


def reconcile(Session, op_id: str, pay: PaymentClient) -> dict:
    """Ask the provider about an operation by its idempotency key and update our record.

    - reserved/unknown: if the provider never saw it, re-send with the SAME key (safe by contract)
    - submitted: sync status; becomes 'succeeded' once the provider settles it
    A provider outage never downgrades 'submitted'; it just leaves things as they are."""
    with Session() as s:
        op = s.get(RefundOperation, op_id)
        if op.status not in ("reserved", "unknown", "submitted"):
            return op_dict(op)
        prior, key = op.status, op.idempotency_key
    try:
        rec = pay.get_by_key(key)
    except ProviderUncertain:
        if prior == "submitted":
            with Session() as s:
                return op_dict(s.get(RefundOperation, op_id))
        return _set(Session, op_id, "unknown", None)
    if rec is None:
        return _call_provider(Session, op_id, pay)
    return _set(Session, op_id, "succeeded" if rec["status"] == "succeeded" else "submitted", rec["refund_id"])


def _set(Session, op_id, status, ref):
    with Session.begin() as s:
        op = s.get(RefundOperation, op_id)
        changed = op.status != status
        op.status, op.provider_reference = status, ref or op.provider_reference
        run_id = _run_id(s, op)
        audit(s, run_id, "system", "refund_reconciled", {"op": op_id, "status": status})
        if status == "succeeded" and changed and run_id:
            # only now may we tell the customer the provider confirmed it
            t = s.query(Ticket).join(WorkflowRun, WorkflowRun.ticket_id == Ticket.id).filter(
                WorkflowRun.id == run_id).first()
            if t:
                t.status = "resolved"
                t.public_note = (f"The payment provider confirmed your refund of {inr(op.amount_minor)} "
                                 f"(ref {op.provider_reference}).")
                audit(s, run_id, "system", "ticket_updated", {"status": "resolved"})
        return op_dict(op)
