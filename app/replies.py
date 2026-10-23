"""Customer replies are templates keyed on VERIFIED state; the model never writes money statements."""
import re

BLOCK_TEXT = {
    "NO_RETURN": "we don't have a return on record for this order",
    "RETURN_REJECTED": "the return was rejected",
    "OUTSIDE_WINDOW": "the return was started outside the return window that applied to this order",
    "RETURN_NOT_RECEIVED": "we haven't received your return yet, so a refund can't start until it arrives",
    "INSPECTION_PENDING": "your return has arrived but its inspection isn't finished yet",
    "INSPECTION_FAILED": "the returned item did not pass inspection",
}


def inr(minor: int) -> str:
    return f"₹{minor // 100:,}.{minor % 100:02d}"


def compose_reply(outcome: str, c: dict) -> str:
    oid = c.get("order_id")
    if outcome == "needs_order_id":
        return "Could you tell us the order number (for example ord_1234) this is about?"
    if outcome == "order_not_found":
        return "We couldn't find that order on your account. Please check the order number."
    if outcome == "order_status":
        sh = c["shipment"]
        return (f"Order {oid} is currently '{sh['status']}'. Latest update ({sh['last_update_at']}): "
                f"{sh['detail']}.")
    if outcome == "order_status_unavailable":
        return f"We couldn't retrieve the latest shipment status for order {oid} right now. Please try again shortly."
    if outcome == "already_processing":
        op = c["detail"]["op"]
        return (f"A refund of {inr(op['amount_minor'])} for order {oid} is already in progress "
                f"(status: {op['status']}, started {op['created_at']}). We have not issued a second refund.")
    if outcome == "already_refunded":
        op = c["detail"]["op"]
        return (f"A refund of {inr(op['amount_minor'])} for order {oid} was recorded as succeeded "
                f"(started {op['created_at']}, ref {op['provider_reference']}). Banks can take a few days to "
                f"show it. We have not issued another refund.")
    if outcome == "blocked":
        why = BLOCK_TEXT.get(c["reason_code"], "it doesn't meet our refund conditions")
        return (f"We can't issue a refund for order {oid} right now because {why}. "
                f"(Policy reference: {', '.join(c.get('policy_refs', []))}.) We can pass this to a specialist if you'd like.")
    if outcome == "refund_succeeded":
        e = c["execution"]
        return f"The payment provider confirmed your refund of {inr(e['amount_minor'])} (ref {e['provider_reference']})."
    if outcome == "refund_submitted":
        e = c["execution"]
        return (f"The payment provider accepted your refund request of {inr(e['amount_minor'])} "
                f"(ref {e['provider_reference']}). It is still processing; we don't have final confirmation yet.")
    if outcome == "refund_unknown":
        return ("We couldn't confirm whether your refund went through, so we're checking with the payment "
                "provider. We will not issue a duplicate.")
    if outcome == "refund_failed":
        return "The payment provider declined the refund request. We've passed your case to a specialist."
    if outcome == "rejected":
        return "A reviewer did not approve the proposed refund. We've passed your case to a specialist."
    if outcome == "approval_expired":
        return "The approval for your refund expired before it could be processed. A specialist will follow up."
    if outcome == "revalidation_failed":
        return "Your case changed while it was under review, so we paused the refund and passed it to a specialist."
    return "We've passed your case to a human specialist who will follow up."


def verify_reply(reply: str, allowed_amounts: set[int]) -> str:
    """Every ₹ amount in the reply must come from verified state; otherwise fall back to a safe message."""
    for m in re.findall(r"₹([\d,]+)\.(\d\d)", reply):
        if int(m[0].replace(",", "")) * 100 + int(m[1]) not in allowed_amounts:
            return "We've passed your case to a human specialist who will follow up."
    return reply
