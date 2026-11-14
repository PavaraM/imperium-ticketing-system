from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.main import create_app
from app.models import Role, User
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login


def test_healthz_is_public(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_requires_login(client, settings):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_page_is_public(client, settings):
    response = client.get("/login")

    assert response.status_code == 200
    assert settings.event_name in response.text


def test_login_sets_httponly_lax_cookie(client):
    response = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_valid_login_reaches_dashboard(client):
    response = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Tickets sold" in dashboard.text


def test_wrong_password_is_rejected(client):
    response = login(client, ADMIN_USERNAME, "definitely-wrong")

    assert response.status_code == 401
    assert "Wrong username or password" in response.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_unknown_username_is_rejected(client):
    response = login(client, "no-such-user", ADMIN_PASSWORD)

    assert response.status_code == 401
    assert "Wrong username or password" in response.text


def test_logout_clears_session(client):
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_is_rate_limited(client):
    for _ in range(5):
        assert login(client, ADMIN_USERNAME, "wrong").status_code == 401

    blocked = login(client, ADMIN_USERNAME, "wrong")
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text


def test_successful_login_resets_rate_limit(client):
    for _ in range(4):
        assert login(client, ADMIN_USERNAME, "wrong").status_code == 401

    assert login(client, ADMIN_USERNAME, ADMIN_PASSWORD).status_code == 303

    assert login(client, ADMIN_USERNAME, "wrong").status_code == 401


def test_next_redirect_is_limited_to_same_site(client):
    response = client.post(
        "/login",
        data={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "next": "//evil.example/steal",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_admin_is_seeded_once_with_hashed_password(client, app, settings):
    assert app.state.settings is settings

    with Session(app.state.engine) as session:
        admins = session.exec(select(User).where(User.role == Role.admin)).all()

    assert len(admins) == 1
    assert admins[0].username == settings.admin_username
    assert admins[0].password_hash != settings.admin_password
    assert admins[0].password_hash.startswith("$argon2")


def test_second_startup_does_not_duplicate_admin(client, app, settings):
    assert client.get("/healthz").status_code == 200

    # A fresh app on the same DB file runs startup again.
    with TestClient(create_app(settings)) as second_client:
        assert second_client.get("/healthz").status_code == 200

        with Session(second_client.app.state.engine) as session:
            admins = session.exec(select(User).where(User.role == Role.admin)).all()

    assert len(admins) == 1


def test_database_refuses_a_second_admin(client, app):
    with Session(app.state.engine) as session:
        session.add(
            User(
                username="second-admin",
                password_hash="not-used",
                role=Role.admin,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:  # pragma: no cover - the partial unique index must exist
            raise AssertionError("a second admin row was accepted")
