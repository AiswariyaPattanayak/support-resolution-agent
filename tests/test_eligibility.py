from datetime import datetime, timedelta

import pytest

from app.eligibility import Verdict, assess_refund

T = datetime(2026, 5, 1)
DELIVERED = T


def ret(status="received", inspection="passed", initiated=T + timedelta(days=3), received=T + timedelta(days=5)):
    return {"status": status, "initiated_at": initiated, "received_at": received, "inspection_status": inspection}


def run(ret_=None, ops=(), delivered=DELIVERED, window=15):
    return assess_refund(paid_amount_minor=1000, delivered_at=delivered, ret=ret_, refund_ops=list(ops), window_days=window)


@pytest.mark.parametrize("kw,verdict,code", [
    (dict(ret_=ret()), Verdict.ELIGIBLE_NEW_REFUND, "RETURN_RECEIVED_INSPECTION_PASSED"),
    (dict(ret_=ret(), ops=[{"status": "submitted"}]), Verdict.ALREADY_PROCESSING, "REFUND_IN_PROGRESS"),
    (dict(ret_=ret(), ops=[{"status": "unknown"}]), Verdict.ALREADY_PROCESSING, "REFUND_IN_PROGRESS"),
    (dict(ret_=ret(), ops=[{"status": "succeeded"}]), Verdict.ALREADY_REFUNDED, "REFUND_SUCCEEDED"),
    (dict(ret_=ret(), ops=[{"status": "failed"}]), Verdict.ELIGIBLE_NEW_REFUND, "RETURN_RECEIVED_INSPECTION_PASSED"),
    (dict(ret_=None), Verdict.BLOCKED, "NO_RETURN"),
    (dict(ret_=ret(status="rejected")), Verdict.BLOCKED, "RETURN_REJECTED"),
    (dict(ret_=ret(status="in_transit", inspection="pending", received=None)), Verdict.BLOCKED, "RETURN_NOT_RECEIVED"),
    (dict(ret_=ret(initiated=T + timedelta(days=16))), Verdict.BLOCKED, "OUTSIDE_WINDOW"),
    (dict(ret_=ret(initiated=T + timedelta(days=16)), window=30), Verdict.ELIGIBLE_NEW_REFUND, "RETURN_RECEIVED_INSPECTION_PASSED"),
    (dict(ret_=ret(received=None)), Verdict.NEEDS_REVIEW, "RECEIVED_WITHOUT_TIMESTAMP"),
    (dict(ret_=ret(inspection="pending")), Verdict.BLOCKED, "INSPECTION_PENDING"),
    (dict(ret_=ret(inspection="failed")), Verdict.BLOCKED, "INSPECTION_FAILED"),
    (dict(ret_=ret(), delivered=None), Verdict.NEEDS_REVIEW, "MISSING_DELIVERY_DATE"),
])
def test_verdicts(kw, verdict, code):
    a = run(**kw)
    assert (a.verdict, a.reason_code) == (verdict, code)
    assert (a.amount_minor == 1000) == (verdict == Verdict.ELIGIBLE_NEW_REFUND)
