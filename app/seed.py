from datetime import datetime, timedelta

from .db import (Customer, Order, PolicyDocument, ReturnRecord, RefundOperation, Shipment, utcnow)

D = datetime


def seed(s):
    now = utcnow()
    P = 249900  # ₹2,499.00 in paise

    s.add_all([
        PolicyDocument(id="refund_policy_v1", kind="refund", version=1, effective_from=D(2020, 1, 1),
                       effective_to=D(2026, 1, 1), meta={"window_days": 30}, sections=[
            {"id": "section_1", "text": "Returns must be initiated within 30 days of delivery."},
            {"id": "section_2", "text": "Refunds start only after the return is received and inspection passes."},
            {"id": "section_3", "text": "A support reviewer must approve every refund."},
            {"id": "section_4", "text": "Never issue a second refund while one is pending or succeeded."}]),
        PolicyDocument(id="refund_policy_v2", kind="refund", version=2, effective_from=D(2026, 1, 1),
                       meta={"window_days": 15}, sections=[
            {"id": "section_1", "text": "Returns must be initiated within 15 days of delivery."},
            {"id": "section_2", "text": "Refunds start only after the return is received and inspection passes."},
            {"id": "section_3", "text": "A support reviewer must approve every refund."},
            {"id": "section_4", "text": "Never issue a second refund while one is pending or succeeded."}]),
        PolicyDocument(id="damaged_policy_v1", kind="damaged", version=1, effective_from=D(2020, 1, 1),
                       meta={"window_days": 14, "required_info": ["damage_description", "photo_reference"]},
                       sections=[{"id": "section_1", "text": "Damage must be reported within 14 days of delivery "
                                  "with a description and a photo reference. Approved claims are refunded in full."}]),
    ])

    def cust(cid, name):
        s.add(Customer(id=cid, name=name, email=f"{cid}@example.test"))

    def order(oid, cid, status, item, ordered, delivered, ship_status=None, ship_detail=""):
        s.add(Order(id=oid, customer_id=cid, status=status, paid_amount_minor=P, ordered_at=ordered,
                    delivered_at=delivered, item_name=item))
        s.add(Shipment(order_id=oid, status=ship_status or status, last_update_at=(delivered or now) - timedelta(hours=2),
                       detail=ship_detail or status))

    def ret(rid, oid, status, initiated, received, inspection):
        s.add(ReturnRecord(id=rid, order_id=oid, status=status, initiated_at=initiated,
                           received_at=received, inspection_status=inspection))

    d10, d5 = now - timedelta(days=10), now - timedelta(days=5)

    cust("cust_status", "Asha (status)");        order("ord_1001", "cust_status", "delivered", "Headphones", now - timedelta(days=9), d5, ship_detail="Delivered, signed by resident")
    cust("cust_transit", "Bala (in transit)");   order("ord_1002", "cust_transit", "in_transit", "Laptop", now - timedelta(days=3), None, ship_detail="Departed Bhubaneswar hub")
    cust("cust_eligible", "Chitra (eligible)");  order("ord_1003", "cust_eligible", "delivered", "Headphones", now - timedelta(days=14), d10); ret("ret_1003", "ord_1003", "received", d5 - timedelta(days=1), d5 + timedelta(days=1), "passed")
    cust("cust_rettransit", "Dev (return moving)"); order("ord_1004", "cust_rettransit", "delivered", "Speaker", now - timedelta(days=14), d10); ret("ret_1004", "ord_1004", "in_transit", d5, None, "pending")
    cust("cust_outside", "Esha (outside window)"); order("ord_1005", "cust_outside", "delivered", "Tablet", now - timedelta(days=80), now - timedelta(days=60)); ret("ret_1005", "ord_1005", "received", now - timedelta(days=5), now - timedelta(days=2), "passed")
    cust("cust_processing", "Farid (already processing)"); order("ord_1006", "cust_processing", "delivered", "Headphones", now - timedelta(days=14), d10); ret("ret_1006", "ord_1006", "received", d5, d5, "passed")
    s.add(RefundOperation(id="op_seed_1006", order_id="ord_1006", proposal_id=None, amount_minor=P, idempotency_key="seed-1006",
                          provider_reference="re_seed1006", status="submitted", created_at=now - timedelta(days=2)))
    cust("cust_refunded", "Gita (already refunded)"); order("ord_1007", "cust_refunded", "delivered", "Headphones", now - timedelta(days=14), d10); ret("ret_1007", "ord_1007", "received", d5, d5, "passed")
    s.add(RefundOperation(id="op_seed_1007", order_id="ord_1007", proposal_id=None, amount_minor=P, idempotency_key="seed-1007",
                          provider_reference="re_seed1007", status="succeeded", created_at=now - timedelta(days=3)))
    cust("cust_conflict", "Hari (conflicting records)"); order("ord_1008", "cust_conflict", "delivered", "Camera", now - timedelta(days=14), d10); ret("ret_1008", "ord_1008", "received", d5, None, "passed")
    cust("cust_damaged", "Indu (damaged, no details)"); order("ord_1009", "cust_damaged", "delivered", "Monitor", now - timedelta(days=6), now - timedelta(days=3))
    cust("cust_damaged_full", "Jay (damaged, full details)"); order("ord_1010", "cust_damaged_full", "delivered", "Monitor", now - timedelta(days=6), now - timedelta(days=3))
    cust("cust_other", "Kiran (another customer)"); order("ord_1011", "cust_other", "delivered", "Watch", now - timedelta(days=9), d5)
    cust("cust_attacker", "Leela (tries ord_1011)"); order("ord_1012", "cust_attacker", "delivered", "Phone", now - timedelta(days=9), d5)
    cust("cust_pol_v1", "Mohan (old policy)")
    order("ord_1013", "cust_pol_v1", "delivered", "Headphones", D(2025, 11, 20), D(2025, 11, 25)); ret("ret_1013", "ord_1013", "received", D(2025, 12, 20), D(2025, 12, 24), "passed")
    cust("cust_pol_v2", "Nisha (new policy)")
    order("ord_1014", "cust_pol_v2", "delivered", "Headphones", D(2026, 3, 1), D(2026, 3, 5)); ret("ret_1014", "ord_1014", "received", D(2026, 3, 25), D(2026, 3, 28), "passed")
