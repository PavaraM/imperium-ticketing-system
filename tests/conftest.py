import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeMailer

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin-pass-123"
TICKETER_USERNAME = "ticketer1"
TICKETER_PASSWORD = "ticketer-pass-1"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        db_path=tmp_path / "test.db",
        secret_key="test-secret-key-1234567890",
        admin_username=ADMIN_USERNAME,
        admin_password=ADMIN_PASSWORD,
        event_name="Test Party",
        login_max_attempts=5,
        login_window_seconds=300,
    )


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def mailer(app) -> FakeMailer:
    fake = FakeMailer()
    app.state.mailer = fake
    return fake


@pytest.fixture()
def anon_client(app, client):
    """A second, cookie-free browser on the same app."""
    with TestClient(app) as second:
        yield second


@pytest.fixture()
def session(app, client):
    """Direct database access for assertions, after the lifespan has run."""
    with Session(app.state.engine) as db:
        yield db


def login(client: TestClient, username: str, password: str, **kwargs):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
        **kwargs,
    )


@pytest.fixture()
def admin_client(client):
    response = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert response.status_code == 303
    return client


@pytest.fixture()
def ticketer_client(admin_client):
    response = admin_client.post(
        "/admin/users",
        data={"username": TICKETER_USERNAME, "password": TICKETER_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    admin_client.post("/logout", follow_redirects=False)
    response = login(admin_client, TICKETER_USERNAME, TICKETER_PASSWORD)
    assert response.status_code == 303
    return admin_client
