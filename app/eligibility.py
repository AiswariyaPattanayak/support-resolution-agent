"""Deterministic refund eligibility. No LLM, no I/O: easy to test exhaustively."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class Verdict(str, Enum):
    ELIGIBLE_NEW_REFUND = "eligible_new_refund"
    ALREADY_PROCESSING = "already_processing"
    ALREADY_REFUNDED = "already_refunded"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class Assessment:
    verdict: Verdict
    reason_code: str
    amount_minor: int = 0
    detail: dict = field(default_factory=dict)


def assess_refund(*, paid_amount_minor: int, delivered_at: datetime | None, ret: dict | None,
                  refund_ops: list[dict], window_days: int) -> Assessment:
    for op in refund_ops:
        if op["status"] == "succeeded":
            return Assessment(Verdict.ALREADY_REFUNDED, "REFUND_SUCCEEDED", detail={"op": op})
    for op in refund_ops:
        if op["status"] in ("reserved", "submitted", "unknown"):
            return Assessment(Verdict.ALREADY_PROCESSING, "REFUND_IN_PROGRESS", detail={"op": op})
    if ret is None:
        return Assessment(Verdict.BLOCKED, "NO_RETURN")
    if ret["status"] == "rejected":
        return Assessment(Verdict.BLOCKED, "RETURN_REJECTED")
    if delivered_at is None:
        return Assessment(Verdict.NEEDS_REVIEW, "MISSING_DELIVERY_DATE")
    if ret["initiated_at"] - delivered_at > timedelta(days=window_days):
        return Assessment(Verdict.BLOCKED, "OUTSIDE_WINDOW", detail={"window_days": window_days})
    if ret["status"] in ("initiated", "in_transit"):
        return Assessment(Verdict.BLOCKED, "RETURN_NOT_RECEIVED", detail={"return_status": ret["status"]})
    if ret["status"] == "received":
        if ret.get("received_at") is None:
            return Assessment(Verdict.NEEDS_REVIEW, "RECEIVED_WITHOUT_TIMESTAMP")
        if ret["inspection_status"] == "pending":
            return Assessment(Verdict.BLOCKED, "INSPECTION_PENDING")
        if ret["inspection_status"] == "failed":
            return Assessment(Verdict.BLOCKED, "INSPECTION_FAILED")
        if ret["inspection_status"] == "passed":
            return Assessment(Verdict.ELIGIBLE_NEW_REFUND, "RETURN_RECEIVED_INSPECTION_PASSED",
                              amount_minor=paid_amount_minor)
    return Assessment(Verdict.NEEDS_REVIEW, "UNRECOGNISED_RETURN_STATE")
