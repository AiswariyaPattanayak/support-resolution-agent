# Support Resolution Agent (V1)

[![tests](https://github.com/AiswariyaPattanayak/support-resolution-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/AiswariyaPattanayak/support-resolution-agent/actions)

Complaint -> verified outcome, for a fictional electronics retailer. Python, LangGraph, FastAPI, SQLAlchemy (SQLite by default, PostgreSQL supported).

## Quick start (no Docker, no API keys)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m scripts.demo        # walks 7 scenarios end to end
pytest -q                     # 45 unit/scenario/concurrency tests
python -m evals.run_evals     # 64-case eval suite, report in evals/reports/latest.md
```

Interactive API (two terminals):

```bash
uvicorn mock_services.payments.app:app --port 8001
uvicorn app.main:app_factory --factory --port 8000      # then open http://127.0.0.1:8000/docs
```
Get a token via `POST /dev/token` (`{"sub":"cust_eligible"}` or `{"sub":"reviewer_1","role":"reviewer"}`), seeded customers are listed in `app/seed.py`.

## V1 scope
In: one store, one currency (INR, integer paise), order status, refund request, damaged item, human approval on **every** refund, mocked shipping and payment, one issue per workflow, messaging only in the API/demo.
Out: chargebacks, fraud adjudication, partial returns, real email/SMS, multi-issue workflows, production claims. All data is synthetic.

## Design rules
- **Model proposes, code decides.** Eligibility is `app/eligibility.py` (pure, table-tested). Customer replies are templates keyed on verified state (`app/replies.py`) and a verifier rejects any â‚¹ amount not found in state.
- **Identity** comes from the JWT; order ownership is checked in the service layer. Foreign and non-existent orders look identical.
- **Approval** is a stored record bound to a SHA-256 of the exact proposal payload; the graph reads the decision from the DB, never from the resume payload. Tampering or expiry blocks execution.
- **Refund safety** (`app/refunds.py`): verify approval, lock the order row, revalidate, insert a refund operation with a unique idempotency key (reserve), commit, then call the provider. Uncertain results become `unknown` and are resolved by key via `/refund-operations/{id}/reconcile`, never a blind retry.
- **Honest wording:** `submitted` != `succeeded` != `unknown` in every reply.

## Layout
`app/` API, graph, services Â· `mock_services/payments/` provider with idempotency contract + failure modes (`/admin/mode`: `normal|slow|error_500|timeout_after_accept`) Â· `tests/` scenario evals Â· `scripts/demo.py`

## Known limitations (read before claiming results)
- Schema is created with `create_all`; **no Alembic migrations yet**.
- Default checkpointer is in-memory (a restart loses paused runs). Postgres checkpointing is wired in `make_checkpointer` but **untested here**; install `psycopg[binary]` and `langgraph-checkpoint-postgres`, run `docker compose up -d postgres`, set `DATABASE_URL`.
- Policy "retrieval" is lookup by effective date + reason code. **No pgvector/embeddings yet.**
- Classification is keyword-based; an optional Claude classifier activates with `ANTHROPIC_API_KEY` (untested without a key, falls back to keywords on any error).
- Reviewers can **reject** (final, escalates) or **request changes** (asks the customer for more info, then a new proposal revision needs a fresh approval; capped at 2 revisions). The revision does not change the amount: eligibility stays deterministic. Damaged-item replacement execution is not built.
- **Schema change:** proposals now have a `revision` column. With no migrations yet, delete `support.db` after upgrading.
- SQLite serialises writers; the row-lock behaviour (`SELECT ... FOR UPDATE`) is only exercised on PostgreSQL, which has not been run here.
- The mock "timeout" is a 504 after recording the refund, not a hung connection.
- The eval split is real but currently decorative: the workflow is deterministic code with no prompts to tune. It becomes meaningful once an LLM agent is added (tune on `dev`, report `heldout`).
- Evals are synthetic and passing them says nothing about production accuracy.

## Evaluation
`python -m evals.run_evals [--split dev|heldout] [--only text]` runs 64 cases (10 each: order status, eligible refunds, ineligible refunds, missing/ambiguous info, API failures and replays, authorization and adversarial; plus 4 reviewer-revision cases). Each case gets a fresh database and a reset mock provider, and is scored on business state (provider records, refund operations, ticket) plus reply wording, not reply text alone.
Metrics: overall correctness, verified resolution (resolvable case passed AND exactly one matching refund at the provider), duplicate refund count, unauthorized action count, latency p50/p95 (excludes reviewer wait). One in three cases per category is held out.

**Mutation check:** the suite was validated by breaking the system on purpose. It fails when you (1) ignore in-progress refunds, (2) drop order-ownership checks, (3) make replies claim success early, (5) skip the proposal payload-hash check, (6) execute without approval, or (7) remove all replay protections. Removing only the random-idempotency-key layer (4) is not caught because the unique proposal constraint, the active-refund validation and eligibility revalidation still prevent a duplicate: defence in depth, not a gap.

## Next steps
Alembic migrations, pgvector policy retrieval, LLM investigation/proposal agents with the rules-based flow kept as the baseline to beat on the held-out split.
