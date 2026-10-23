"""Mock payment provider with an idempotency contract and injectable failures.

Contract: same key + same payload -> same refund returned; same key + different payload -> 409.
Failure modes (POST /admin/mode {"mode": ..., "times": N}):
  normal | slow | error_500 (nothing recorded) | timeout_after_accept (recorded, then 504)
"""
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock Payment Provider")
LOCK = threading.Lock()
STATE = {"mode": "normal", "times": 0, "refunds": {}}


class RefundReq(BaseModel):
    idempotency_key: str
    order_id: str
    amount_minor: int


class ModeReq(BaseModel):
    mode: str
    times: int = 1


def _take_mode() -> str:
    if STATE["times"] != 0:
        STATE["times"] -= 1
        return STATE["mode"]
    return "normal"


@app.post("/refunds")
def create_refund(req: RefundReq):
    with LOCK:
        mode = _take_mode()
    if mode == "error_500":
        raise HTTPException(500, "provider internal error")
    if mode == "slow":
        time.sleep(1.5)
    with LOCK:
        existing = STATE["refunds"].get(req.idempotency_key)
        if existing:
            if (existing["order_id"], existing["amount_minor"]) != (req.order_id, req.amount_minor):
                raise HTTPException(409, "idempotency key reused with different payload")
            rec = existing
        else:
            rec = {"refund_id": "re_" + uuid.uuid4().hex[:10], "idempotency_key": req.idempotency_key,
                   "order_id": req.order_id, "amount_minor": req.amount_minor, "status": "pending"}
            STATE["refunds"][req.idempotency_key] = rec
    if mode == "timeout_after_accept":
        raise HTTPException(504, "gateway timeout (refund WAS recorded)")
    return rec


@app.get("/refunds/by-key/{key}")
def get_by_key(key: str):
    rec = STATE["refunds"].get(key)
    if not rec:
        raise HTTPException(404, "not found")
    return rec


@app.get("/admin/refunds")
def list_refunds():
    return list(STATE["refunds"].values())


@app.post("/admin/mode")
def set_mode(req: ModeReq):
    STATE["mode"], STATE["times"] = req.mode, req.times
    return STATE["mode"]


@app.post("/admin/settle/{refund_id}")
def settle(refund_id: str):
    for r in STATE["refunds"].values():
        if r["refund_id"] == refund_id:
            r["status"] = "succeeded"
            return r
    raise HTTPException(404, "not found")


@app.post("/admin/reset")
def reset():
    STATE.update(mode="normal", times=0, refunds={})
    return "ok"
