import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select

from .config import Settings
from .db import (ActionProposal, Approval, AuditEvent, Customer, Ticket, WorkflowRun, audit,
                 make_session_factory, utcnow)
from .graph import build_graph
from .payments import PaymentClient
from .refunds import reconcile
from .seed import seed


@dataclass
class Deps:
    Session: object
    pay: PaymentClient
    settings: Settings


def make_checkpointer(settings: Settings):
    """MemorySaver by default (state lost on restart). With a Postgres URL and the optional
    langgraph-checkpoint-postgres package installed, runs survive restarts."""
    if settings.database_url.startswith("postgresql"):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            cm = PostgresSaver.from_conn_string(settings.database_url.replace("+psycopg", ""))
            saver = cm.__enter__()
            saver.setup()
            return saver, lambda: cm.__exit__(None, None, None)
        except ImportError:
            print("langgraph-checkpoint-postgres not installed; falling back to in-memory checkpointer")
    if settings.database_url.startswith("sqlite:///") and ":memory:" not in settings.database_url:
        try:  # durable on a single machine: paused runs survive an API restart
            import sqlite3
            from langgraph.checkpoint.sqlite import SqliteSaver
            path = Path(settings.database_url[len("sqlite:///"):]).with_suffix(".checkpoints.db")
            conn = sqlite3.connect(str(path), check_same_thread=False)
            saver = SqliteSaver(conn)
            saver.setup()
            return saver, conn.close
        except ImportError:
            print("langgraph-checkpoint-sqlite not installed; paused runs will NOT survive a restart")
    return MemorySaver(), lambda: None


def create_app(settings: Settings | None = None, pay: PaymentClient | None = None, seed_data: bool = True):
    settings = settings or Settings()
    Session = make_session_factory(settings.database_url)
    if seed_data:
        with Session.begin() as s:
            if s.scalars(select(Customer)).first() is None:
                seed(s)
    deps = Deps(Session, pay or PaymentClient(settings.payment_url), settings)
    checkpointer, closer = make_checkpointer(settings)
    graph = build_graph(deps, checkpointer)

    @asynccontextmanager
    async def lifespan(_):
        yield
        closer()

    app = FastAPI(title="Support Resolution Agent", lifespan=lifespan)
    app.state.deps, app.state.graph = deps, graph

    # ---------- auth ----------
    def user(authorization: str = Header(None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            return jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(401, "invalid token")

    def reviewer(u=Depends(user)):
        if u.get("role") != "reviewer":
            raise HTTPException(403, "reviewer role required")
        return u

    class TokenReq(BaseModel):
        sub: str
        role: str = "customer"

    @app.post("/dev/token")
    def dev_token(req: TokenReq):
        if not settings.dev_mode:
            raise HTTPException(404)
        exp = datetime.now(timezone.utc) + timedelta(hours=8)
        return {"token": jwt.encode({"sub": req.sub, "role": req.role, "exp": exp}, settings.jwt_secret, "HS256")}

    @app.get("/ui", response_class=HTMLResponse)
    def ui():
        """Dev console (customer + reviewer in one page). Disabled when DEV_MODE=0."""
        if not settings.dev_mode:
            raise HTTPException(404)
        return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

    @app.get("/health")
    def health():
        return {"ok": True}

    # ---------- helpers ----------
    def view(s, run: WorkflowRun):
        return {"run_id": run.id, "status": run.status, "outcome": run.outcome, "reply": run.final_response,
                "pending": run.pending, "ticket_id": run.ticket_id}

    def apply_result(run_id: str, result: dict):
        with Session.begin() as s:
            run = s.get(WorkflowRun, run_id)
            intr = result.get("__interrupt__")
            if intr:
                val = intr[0].value
                run.status = "waiting_approval" if val["type"] == "approval" else "waiting_customer"
                run.pending = val
            else:
                run.status, run.pending = "completed", None
                run.outcome, run.final_response = result.get("outcome"), result.get("reply")
                run.completed_at = utcnow()
            audit(s, run_id, "system", "run_status", {"status": run.status})
            return view(s, run)

    def owned_run(s, run_id, u):
        run = s.get(WorkflowRun, run_id)
        if run is None or (u["role"] != "reviewer" and run.customer_id != u["sub"]):
            raise HTTPException(404, "run not found")
        return run

    # ---------- customer ----------
    class RunReq(BaseModel):
        message: str
        order_id: str | None = None
        details: dict | None = None

    @app.post("/runs")
    def start_run(req: RunReq, u=Depends(user)):
        if u.get("role") != "customer":
            raise HTTPException(403, "customer role required")
        with Session.begin() as s:
            if s.get(Customer, u["sub"]) is None:
                raise HTTPException(403, "unknown customer")
            t = Ticket(customer_id=u["sub"], summary=req.message[:200])
            s.add(t)
            s.flush()
            run_id = "run_" + uuid.uuid4().hex[:12]
            s.add(WorkflowRun(id=run_id, ticket_id=t.id, customer_id=u["sub"]))
            audit(s, run_id, "customer", "run_started", {"message": req.message[:200]})
        # identity comes from the token only, never from the message text
        result = graph.invoke({"run_id": run_id, "customer_id": u["sub"], "message": req.message,
                               "order_id": req.order_id, "details": req.details or {}},
                              {"configurable": {"thread_id": run_id}})
        return apply_result(run_id, result)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, u=Depends(user)):
        with Session() as s:
            return view(s, owned_run(s, run_id, u))

    @app.get("/runs/{run_id}/events")
    def events(run_id: str, u=Depends(user)):
        with Session() as s:
            owned_run(s, run_id, u)
            rows = s.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.id)).all()
            return [{"at": r.created_at.isoformat(), "actor": r.actor, "event": r.event_type,
                     "payload": r.payload if u["role"] == "reviewer" else {}} for r in rows]

    class MessageReq(BaseModel):
        damage_description: str | None = None
        additional_info: str | None = None
        photo_reference: str | None = None

    @app.post("/runs/{run_id}/messages")
    def add_info(run_id: str, req: MessageReq, u=Depends(user)):
        with Session() as s:
            run = owned_run(s, run_id, u)
            if run.status != "waiting_customer":
                raise HTTPException(409, "run is not waiting for customer information")
        result = graph.invoke(Command(resume=req.model_dump(exclude_none=True)),
                              {"configurable": {"thread_id": run_id}})
        return apply_result(run_id, result)

    # ---------- reviewer ----------
    @app.get("/approvals")
    def list_approvals(_=Depends(reviewer)):
        with Session() as s:
            rows = s.execute(select(Approval, ActionProposal).join(
                ActionProposal, ActionProposal.id == Approval.proposal_id).where(
                Approval.decision == "pending")).all()
            return [{"approval_id": a.id, "run_id": p.run_id, "expires_at": a.expires_at.isoformat(),
                     "revision": p.revision, "proposal": p.payload} for a, p in rows]

    class DecisionReq(BaseModel):
        decision: str  # approved | rejected
        notes: str | None = None

    @app.post("/approvals/{approval_id}/decision")
    def decide(approval_id: str, req: DecisionReq, u=Depends(reviewer)):
        if req.decision not in ("approved", "rejected", "changes_requested"):
            raise HTTPException(422, "decision must be approved, rejected or changes_requested")
        if req.decision == "changes_requested" and not (req.notes or "").strip():
            raise HTTPException(422, "notes are required when requesting changes")
        with Session.begin() as s:
            a = s.get(Approval, approval_id)
            if a is None:
                raise HTTPException(404)
            if a.decision != "pending":
                raise HTTPException(409, f"already {a.decision}")
            if a.expires_at < utcnow():
                raise HTTPException(409, "approval expired")
            prop = s.get(ActionProposal, a.proposal_id)
            a.decision, a.reviewer_id, a.notes, a.decided_at = req.decision, u["sub"], req.notes, utcnow()
            audit(s, prop.run_id, f"reviewer:{u['sub']}", f"approval_{req.decision}", {"notes": req.notes})
            run_id = prop.run_id
        result = graph.invoke(Command(resume={"approval_id": approval_id}),
                              {"configurable": {"thread_id": run_id}})
        return apply_result(run_id, result)

    @app.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: int, u=Depends(user)):
        with Session() as s:
            t = s.get(Ticket, ticket_id)
            if t is None or (u["role"] != "reviewer" and t.customer_id != u["sub"]):
                raise HTTPException(404, "ticket not found")
            return {"ticket_id": t.id, "status": t.status, "category": t.category, "public_note": t.public_note}

    @app.post("/refund-operations/{op_id}/reconcile")
    def reconcile_op(op_id: str, _=Depends(reviewer)):
        """Resolve an 'unknown' refund by asking the provider by idempotency key (never a blind retry)."""
        return reconcile(Session, op_id, deps.pay)

    @app.get("/refund-operations")
    def list_ops(_=Depends(reviewer)):
        from .db import RefundOperation
        from .service import op_dict
        with Session() as s:
            return [op_dict(o) | {"order_id": o.order_id} for o in s.scalars(select(RefundOperation))]

    return app


def app_factory():  # uvicorn --factory app.main:app_factory
    return create_app()
