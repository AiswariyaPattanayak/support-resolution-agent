from datetime import datetime, timezone

from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, Integer, String,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String, default="INR")
    paid_amount_minor: Mapped[int] = mapped_column(Integer)  # paise
    ordered_at: Mapped[datetime] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    item_name: Mapped[str] = mapped_column(String, default="item")


class Shipment(Base):
    __tablename__ = "shipments"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String)
    last_update_at: Mapped[datetime] = mapped_column(DateTime)
    detail: Mapped[str] = mapped_column(String)


class ReturnRecord(Base):
    __tablename__ = "returns"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    status: Mapped[str] = mapped_column(String)  # initiated|in_transit|received|rejected
    initiated_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inspection_status: Mapped[str] = mapped_column(String, default="pending")  # pending|passed|failed


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")
    summary: Mapped[str] = mapped_column(String, default="")
    public_note: Mapped[str] = mapped_column(String, default="")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    customer_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="running")
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    final_response: Mapped[str | None] = mapped_column(String, nullable=True)
    pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. refund_policy_v2
    kind: Mapped[str] = mapped_column(String)  # refund | damaged
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(DateTime)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON)  # window_days, required_info
    sections: Mapped[list] = mapped_column(JSON)  # [{"id","text"}]


class ActionProposal(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (UniqueConstraint("run_id", "revision", name="uq_proposal_run_revision"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    action_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending_approval")


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"), unique=True)
    reviewer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision: Mapped[str] = mapped_column(String, default="pending")  # pending|approved|rejected|changes_requested
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RefundOperation(Base):
    __tablename__ = "refund_operations"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="ck_refund_amount_positive"),
                      UniqueConstraint("proposal_id", name="uq_refund_proposal"))
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    proposal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    provider_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)  # reserved|submitted|succeeded|failed|unknown
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    actor: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _connect(dbapi_conn, _):
            dbapi_conn.isolation_level = None  # we control BEGIN ourselves

        @event.listens_for(engine, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")  # serialise writers like a row lock would
        return engine
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(url: str):
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def audit(s, run_id, actor, event_type, payload=None):
    s.add(AuditEvent(run_id=run_id, actor=actor, event_type=event_type, payload=payload or {}))
