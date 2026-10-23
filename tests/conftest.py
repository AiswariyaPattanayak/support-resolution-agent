import httpx
import pytest

from app.config import Settings
from app.devserver import ThreadedServer
from app.main import create_app
from app.payments import PaymentClient
from mock_services.payments.app import app as payment_app


@pytest.fixture(scope="session")
def provider():
    srv = ThreadedServer(payment_app).start()
    yield srv
    srv.stop()


@pytest.fixture()
def env(tmp_path, provider):
    c = httpx.Client(base_url=provider.url)
    c.post("/admin/reset")
    settings = Settings(database_url=f"sqlite:///{tmp_path/'t.db'}", payment_url=provider.url)
    app = create_app(settings, PaymentClient(provider.url))
    api_srv = ThreadedServer(app).start()
    api = httpx.Client(base_url=api_srv.url, timeout=30)

    class E:
        pass
    e = E()
    e.api, e.prov, e.app, e.deps = api, c, app, app.state.deps

    def tok(sub, role="customer"):
        return {"Authorization": "Bearer " + api.post("/dev/token", json={"sub": sub, "role": role}).json()["token"]}
    e.tok, e.rev = tok, tok("reviewer_1", "reviewer")
    yield e
    api_srv.stop()
