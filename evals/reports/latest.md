# Eval report

| Slice | Cases | Passed | Correct % | Verified resolution % | Dup refunds | Unauthorized | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|
| overall | 64 | 64 | 100.0 | 100.0 | 0 | 0 | 1.04 | 1.57 |
| split:dev | 45 | 45 | 100.0 | 100.0 | 0 | 0 | 1.04 | 1.64 |
| split:heldout | 19 | 19 | 100.0 | 100.0 | 0 | 0 | 1.05 | 1.57 |
| api_failures | 10 | 10 | 100.0 | None | 0 | 0 | 1.11 | 2.61 |
| authorization_adversarial | 10 | 10 | 100.0 | None | 0 | 0 | 1.0 | 1.08 |
| eligible_refund | 10 | 10 | 100.0 | 100.0 | 0 | 0 | 1.07 | 1.13 |
| ineligible_refund | 10 | 10 | 100.0 | None | 0 | 0 | 0.99 | 1.04 |
| missing_or_ambiguous | 10 | 10 | 100.0 | None | 0 | 0 | 0.98 | 1.19 |
| order_status | 10 | 10 | 100.0 | None | 0 | 0 | 1.0 | 1.64 |
| revision_loop | 4 | 4 | 100.0 | 100.0 | 0 | 0 | 1.09 | 1.14 |

Synthetic data, deterministic workflow, no LLM in the decision path yet. Latency excludes reviewer wait time.
Verified resolution = resolvable case passed AND exactly one matching refund exists at the provider.