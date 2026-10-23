from sqlalchemy import select

from .db import PolicyDocument

# reason code -> policy section cited in replies / proposals
SECTION_FOR = {
    "REFUND_SUCCEEDED": "section_4", "REFUND_IN_PROGRESS": "section_4",
    "NO_RETURN": "section_2", "RETURN_REJECTED": "section_2", "RETURN_NOT_RECEIVED": "section_2",
    "OUTSIDE_WINDOW": "section_1", "INSPECTION_PENDING": "section_2", "INSPECTION_FAILED": "section_2",
    "RETURN_RECEIVED_INSPECTION_PASSED": "section_3", "DAMAGED_ITEM": "section_1",
}


def find_policy(s, kind: str, on_date):
    docs = s.scalars(select(PolicyDocument).where(PolicyDocument.kind == kind)
                     .order_by(PolicyDocument.effective_from.desc())).all()
    for d in docs:
        if d.effective_from <= on_date and (d.effective_to is None or on_date < d.effective_to):
            return d
    return None


def cite(doc, reason_code: str) -> str:
    return f"{doc.id}:{SECTION_FOR.get(reason_code, 'section_1')}"
