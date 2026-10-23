import httpx


class ProviderUncertain(Exception):
    """Timeout / 5xx / transport error: the refund MAY have been recorded."""


class ProviderRejected(Exception):
    """Definitive 4xx: the provider will not process this request."""


class PaymentClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=base_url, timeout=5.0)

    def create_refund(self, key: str, order_id: str, amount_minor: int) -> dict:
        try:
            r = self.http.post("/refunds", json={"idempotency_key": key, "order_id": order_id,
                                                 "amount_minor": amount_minor})
        except httpx.HTTPError as e:
            raise ProviderUncertain(str(e)) from e
        if r.status_code >= 500:
            raise ProviderUncertain(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ProviderRejected(f"HTTP {r.status_code}: {r.text}")
        return r.json()

    def get_by_key(self, key: str) -> dict | None:
        try:
            r = self.http.get(f"/refunds/by-key/{key}")
        except httpx.HTTPError as e:
            raise ProviderUncertain(str(e)) from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ProviderUncertain(f"HTTP {r.status_code}")
        return r.json()
