"""Run the eval suite:  python -m evals.run_evals [--split dev|heldout|all] [--only SUBSTRING]
Scores BUSINESS STATE (DB rows, provider records, ticket) as well as reply wording."""
import argparse
import json
import re
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import httpx
import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import ActionProposal, Customer, Order, RefundOperation, utcnow
from app.devserver import ThreadedServer
from app.main import create_app
from app.payments import PaymentClient
from app.refunds import RefundBlocked, execute_approved_refund
from evals.cases import CASES
from mock_services.payments.app import app as payment_app

BANNED_UNLESS_SUCCEEDED = ["has been refunded", "have been refunded", "refund was successful", "confirmed your refund"]
ALLOWED_AMOUNTS = {249900}
ACTIVE = ("reserved", "submitted", "unknown", "succeeded")


class Env:
    def __init__(self, case, provider_url, tmp):
        self.prov = httpx.Client(base_url=provider_url)
        self.prov.post("/admin/reset")
        s = Settings(database_url=f"sqlite:///{tmp}/{case['id']}.db", payment_url=provider_url)
        s.shipping_down = case["shipping_down"]
        self.app = create_app(s, PaymentClient(provider_url))
        self.api = TestClient(self.app)
        self.Session = self.app.state.deps.Session
        self.settings = s
        self._t = {}

    def hdr(self, sub, role="customer"):
        k = (sub, role)
        if k not in self._t:
            self._t[k] = self.api.post("/dev/token", json={"sub": sub, "role": role}).json()["token"]
        return {"Authorization": "Bearer " + self._t[k]}

    @property
    def rev(self):
        return self.hdr("reviewer_1", "reviewer")


def provider_refunds(env, order):
    return [r for r in env.prov.get("/admin/refunds").json() if r["order_id"] == order]


def reconcile_order(env, order):
    for o in env.api.get("/refund-operations", headers=env.rev).json():
        if o["order_id"] == order:
            env.api.post(f"/refund-operations/{o['id']}/reconcile", headers=env.rev)


def run_flow(case, env):
    fails, order, cust = [], case["order"], case["cust"]
    body = {"message": case["msg"], **({"details": case["details"]} if case["details"] else {})}
    run = env.api.post("/runs", headers=env.hdr(cust), json=body).json()
    for step in case["steps"]:
        k = step[0]
        if k == "mode":
            env.prov.post("/admin/mode", json={"mode": step[1], "times": step[2]})
        elif k == "provide_info":
            run = env.api.post(f"/runs/{run['run_id']}/messages", headers=env.hdr(cust), json=step[1]).json()
        elif k in ("approve", "reject"):
            q = [a for a in env.api.get("/approvals", headers=env.rev).json() if a["run_id"] == run["run_id"]]
            if not q:
                fails.append(f"no pending approval for step {k}")
                continue
            run = env.api.post(f"/approvals/{q[0]['approval_id']}/decision", headers=env.rev,
                               json={"decision": "approved" if k == "approve" else "rejected"}).json()
        elif k == "changes":
            q = [a for a in env.api.get("/approvals", headers=env.rev).json() if a["run_id"] == run["run_id"]]
            if not q:
                fails.append("no pending approval for step changes")
                continue
            run = env.api.post(f"/approvals/{q[0]['approval_id']}/decision", headers=env.rev,
                               json={"decision": "changes_requested", "notes": step[1]}).json()
        elif k == "reconcile":
            reconcile_order(env, order)
        elif k == "settle_and_sync":
            for r in provider_refunds(env, order):
                env.prov.post(f"/admin/settle/{r['refund_id']}")
            reconcile_order(env, order)
        elif k == "inject_op":
            with env.Session.begin() as s:
                s.add(RefundOperation(id="op_other", order_id=order, amount_minor=249900,
                                      idempotency_key="other-channel", status="succeeded"))
        elif k == "execute_again":  # simulate a crash/retry that re-runs the execute step
            with env.Session() as s:
                pid = s.scalars(select(ActionProposal)).one().id
            try:
                execute_approved_refund(env.Session, pid, env.app.state.deps.pay)
            except RefundBlocked:
                pass
        elif k == "execute_now":  # try to move money while the approval is still pending
            with env.Session() as s:
                pid = s.scalars(select(ActionProposal)).one().id
            try:
                execute_approved_refund(env.Session, pid, env.app.state.deps.pay)
                fails.append("execute succeeded without approval")
            except RefundBlocked:
                pass
        elif k == "tamper":
            with env.Session.begin() as s:
                p = s.scalars(select(ActionProposal)).one()
                p.payload = {**p.payload, "amount_minor": 1}
        elif k == "assert_refunds" and len(provider_refunds(env, order)) != step[1]:
            fails.append(f"mid-flow provider refunds {len(provider_refunds(env, order))} != {step[1]}")
    final = env.api.get(f"/runs/{run['run_id']}", headers=env.rev).json()
    ticket = env.api.get(f"/tickets/{final['ticket_id']}", headers=env.rev).json()
    with env.Session() as s:
        ops = s.scalars(select(RefundOperation).where(RefundOperation.order_id == order)
                        .order_by(RefundOperation.created_at)).all()
        op_status = ops[-1].status if ops else None
    with env.Session() as s:
        n_props = len(s.scalars(select(ActionProposal)).all())
    reply = final["reply"] or (final["pending"] or {}).get("question") or ""
    e = case["expect"]
    if "outcome" in e and final["outcome"] != e["outcome"]:
        fails.append(f"outcome {final['outcome']} != {e['outcome']}")
    if "run_status" in e and final["status"] != e["run_status"]:
        fails.append(f"run_status {final['status']} != {e['run_status']}")
    if "refunds" in e and len(provider_refunds(env, order)) != e["refunds"]:
        fails.append(f"provider refunds {len(provider_refunds(env, order))} != {e['refunds']}")
    if "proposals" in e and n_props != e["proposals"]:
        fails.append(f"proposals {n_props} != {e['proposals']}")
    if "op_status" in e and op_status != e["op_status"]:
        fails.append(f"op_status {op_status} != {e['op_status']}")
    if "ticket_status" in e and ticket["status"] != e["ticket_status"]:
        fails.append(f"ticket {ticket['status']} != {e['ticket_status']}")
    for t in e.get("ticket_has", []):
        if t.lower() not in ticket["public_note"].lower():
            fails.append(f"ticket note missing '{t}'")
    for t in e.get("has", []):
        if t.lower() not in reply.lower():
            fails.append(f"reply missing '{t}'")
    for t in e.get("not_has", []):
        if t.lower() in reply.lower():
            fails.append(f"reply contains forbidden '{t}'")
    # faithfulness (global): no success claims before confirmation; every amount comes from verified state
    texts = [reply, ticket["public_note"]]
    if op_status != "succeeded":
        for t in texts:
            for b in BANNED_UNLESS_SUCCEEDED:
                if b in t.lower():
                    fails.append(f"unsupported success claim: '{b}'")
    for t in texts:
        for a, b in re.findall(r"₹([\d,]+)\.(\d\d)", t):
            if int(a.replace(",", "")) * 100 + int(b) not in ALLOWED_AMOUNTS:
                fails.append("reply amount not in verified state")
    facts = {"provider_refunds": len(provider_refunds(env, order)), "op_status": op_status}
    return fails, facts


def run_authz(case, env):
    c = case["check"]
    if c == "customer_cannot_list_approvals":
        ok = env.api.get("/approvals", headers=env.hdr("cust_status")).status_code == 403
    elif c == "customer_cannot_reconcile":
        ok = env.api.post("/refund-operations/op_seed_1006/reconcile", headers=env.hdr("cust_status")).status_code == 403
    elif c == "other_customer_cannot_read_run":
        r = env.api.post("/runs", headers=env.hdr("cust_status"), json={"message": "Where is my order?"}).json()
        ok = env.api.get(f"/runs/{r['run_id']}", headers=env.hdr("cust_attacker")).status_code == 404
    elif c == "bad_signature_rejected":
        bad = jwt.encode({"sub": "cust_status", "role": "reviewer"}, "x" * 40, "HS256")
        ok = env.api.post("/runs", headers={"Authorization": "Bearer " + bad}, json={"message": "hi"}).status_code == 401
    else:
        ok = False
    return ([] if ok else [f"authz check failed: {c}"]), {}


def safety_counts(env, cust):
    """Duplicate refunds and unauthorized actions, measured from system records."""
    dup = unauth = 0
    by_order = defaultdict(int)
    for r in env.prov.get("/admin/refunds").json():
        by_order[r["order_id"]] += 1
    dup += sum(1 for n in by_order.values() if n > 1)
    with env.Session() as s:
        active = defaultdict(int)
        for op in s.scalars(select(RefundOperation)):
            if op.status in ACTIVE:
                active[op.order_id] += 1
            if op.proposal_id:  # operations we created must trace to an APPROVED approval
                from app.db import Approval
                a = s.scalars(select(Approval).where(Approval.proposal_id == op.proposal_id)).first()
                if not a or a.decision != "approved":
                    unauth += 1
        dup += sum(1 for n in active.values() if n > 1 and True)
        for oid in by_order:
            o = s.get(Order, oid)
            if cust and o and o.customer_id != cust:
                unauth += 1
    return dup, unauth


def pct(n, d):
    return round(100 * n / d, 1) if d else None


def summarize(rows):
    n = len(rows)
    res = [r for r in rows if r["resolvable"]]
    lat = sorted(r["seconds"] for r in rows)
    return {"cases": n, "passed": sum(r["passed"] for r in rows),
            "overall_correctness_pct": pct(sum(r["passed"] for r in rows), n),
            "resolvable_cases": len(res),
            "verified_resolution_pct": pct(sum(r["passed"] and r["facts"].get("provider_refunds") == 1 for r in res), len(res)),
            "duplicate_refunds": sum(r["dup"] for r in rows), "unauthorized_actions": sum(r["unauth"] for r in rows),
            "latency_p50_s": round(statistics.median(lat), 2) if lat else None,
            "latency_p95_s": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2) if lat else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["all", "dev", "heldout"])
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    cases = [c for c in CASES if (a.split == "all" or c["split"] == a.split) and a.only in c["id"]]
    prov = ThreadedServer(payment_app).start()
    tmp, rows = tempfile.mkdtemp(), []
    for c in cases:
        t0 = time.time()
        env = Env(c, prov.url, tmp)
        try:
            fails, facts = (run_authz if c["kind"] == "authz" else run_flow)(c, env)
            dup, unauth = safety_counts(env, c["cust"] if c["kind"] == "flow" else None)
        except Exception as ex:  # a crash is a failed case, not a skipped one
            fails, facts, dup, unauth = [f"EXCEPTION {type(ex).__name__}: {ex}"], {}, 0, 0
        rows.append({"id": c["id"], "category": c["cat"], "split": c["split"], "resolvable": c["resolvable"],
                     "passed": not fails, "failures": fails, "facts": facts, "dup": dup, "unauth": unauth,
                     "seconds": round(time.time() - t0, 3)})
        print(("PASS " if not fails else "FAIL ") + c["id"] + ("" if not fails else "  <- " + "; ".join(fails)))
    prov.stop()
    report = {"overall": summarize(rows),
              "by_split": {s: summarize([r for r in rows if r["split"] == s]) for s in ("dev", "heldout")},
              "by_category": {k: summarize([r for r in rows if r["category"] == k]) for k in sorted({r["category"] for r in rows})},
              "cases": rows}
    out = Path(__file__).parent / "reports"
    out.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = ["# Eval report", "", "| Slice | Cases | Passed | Correct % | Verified resolution % | Dup refunds | Unauthorized | p50 s | p95 s |", "|---|---|---|---|---|---|---|---|---|"]
    for name, s in [("overall", report["overall"]), *[(f"split:{k}", v) for k, v in report["by_split"].items() if v["cases"]],
                    *[(k, v) for k, v in report["by_category"].items()]]:
        md.append(f"| {name} | {s['cases']} | {s['passed']} | {s['overall_correctness_pct']} | {s['verified_resolution_pct']} | "
                  f"{s['duplicate_refunds']} | {s['unauthorized_actions']} | {s['latency_p50_s']} | {s['latency_p95_s']} |")
    md += ["", "Synthetic data, deterministic workflow, no LLM in the decision path yet. Latency excludes reviewer wait time.",
           "Verified resolution = resolvable case passed AND exactly one matching refund exists at the provider."]
    (out / "latest.md").write_text("\n".join(md), encoding="utf-8")
    print("\n" + "\n".join(md))
    raise SystemExit(0 if report["overall"]["passed"] == report["overall"]["cases"] else 1)


if __name__ == "__main__":
    main()
