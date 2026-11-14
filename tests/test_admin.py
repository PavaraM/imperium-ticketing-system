from sqlmodel import Session, select

from app.models import Role, User
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    TICKETER_PASSWORD,
    TICKETER_USERNAME,
    login,
)


def _users(app) -> list[User]:
    with Session(app.state.engine) as session:
        return list(session.exec(select(User)).all())


def _toggle(client, user_id: int):
    return client.post(f"/admin/users/{user_id}/toggle", follow_redirects=False)


def _reset_password(client, user_id: int, password: str):
    return client.post(
        f"/admin/users/{user_id}/password",
        data={"password": password},
        follow_redirects=False,
    )


def test_anonymous_cannot_open_staff_page(client):
    response = client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_ticketer_cannot_open_staff_page(ticketer_client):
    response = ticketer_client.get("/admin/users", follow_redirects=False)

    assert response.status_code == 403


def test_ticketer_cannot_create_accounts(ticketer_client):
    response = ticketer_client.post(
        "/admin/users",
        data={"username": "sneaky", "password": "sneaky-pass-1"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_ticketer_cannot_reset_password(ticketer_client, app):
    account = next(user for user in _users(app) if user.role is Role.ticketer)

    response = _reset_password(ticketer_client, account.id, "new-fake-password")

    assert response.status_code == 403


def test_anonymous_cannot_reset_password(anon_client):
    response = _reset_password(anon_client, 1, "new-fake-password")

    assert response.status_code == 401


def test_admin_creates_ticketer(admin_client, app):
    response = admin_client.post(
        "/admin/users",
        data={"username": "DoorStaff", "password": "door-pass-123"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users"

    created = [u for u in _users(app) if u.username == "doorstaff"]
    assert len(created) == 1
    assert created[0].role is Role.ticketer
    assert created[0].is_active is True
    assert created[0].password_hash != "door-pass-123"


def test_admin_resets_ticketer_password(admin_client, app):
    admin_client.post(
        "/admin/users",
        data={"username": "password-reset", "password": "old-fake-pass"},
        follow_redirects=False,
    )
    account = next(user for user in _users(app) if user.username == "password-reset")
    old_hash = account.password_hash
    new_password = "new-fake-pass-123"

    response = _reset_password(admin_client, account.id, new_password)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users"
    assert new_password not in response.headers["location"]
    page = admin_client.get("/admin/users")
    assert f"Password changed for ticketer “{account.username}”." in page.text
    assert new_password not in page.text
    assert next(user for user in _users(app) if user.id == account.id).password_hash != old_hash

    admin_client.post("/logout", follow_redirects=False)
    assert login(admin_client, account.username, "old-fake-pass").status_code == 401
    assert login(admin_client, account.username, new_password).status_code == 303


def test_short_password_reset_does_not_change_password(admin_client, app):
    admin_client.post(
        "/admin/users",
        data={"username": "short-reset", "password": "old-fake-pass"},
        follow_redirects=False,
    )
    account = next(user for user in _users(app) if user.username == "short-reset")
    old_hash = account.password_hash

    response = _reset_password(admin_client, account.id, "tiny")

    assert response.status_code == 303
    assert "tiny" not in response.headers["location"]
    assert "at least 8" in admin_client.get("/admin/users").text
    assert next(user for user in _users(app) if user.id == account.id).password_hash == old_hash

    admin_client.post("/logout", follow_redirects=False)
    assert login(admin_client, account.username, "old-fake-pass").status_code == 303


def test_admin_account_cannot_be_reset(admin_client, app):
    admin = next(user for user in _users(app) if user.role is Role.admin)
    old_hash = admin.password_hash
    new_password = "new-admin-fake-pass"

    response = _reset_password(admin_client, admin.id, new_password)

    assert response.status_code == 303
    page = admin_client.get("/admin/users")
    assert "admin account password cannot be changed" in page.text
    assert new_password not in page.text
    assert next(user for user in _users(app) if user.id == admin.id).password_hash == old_hash

    admin_client.post("/logout", follow_redirects=False)
    assert login(admin_client, ADMIN_USERNAME, ADMIN_PASSWORD).status_code == 303


def test_reset_password_unknown_user_is_404(admin_client):
    response = _reset_password(admin_client, 9999, "new-fake-password")

    assert response.status_code == 404
    assert "User not found" in response.text


def test_new_ticketer_can_log_in(admin_client):
    admin_client.post(
        "/admin/users",
        data={"username": TICKETER_USERNAME, "password": TICKETER_PASSWORD},
        follow_redirects=False,
    )
    admin_client.post("/logout", follow_redirects=False)

    response = login(admin_client, TICKETER_USERNAME, TICKETER_PASSWORD)

    assert response.status_code == 303
    assert admin_client.get("/tickets").status_code == 200


def test_duplicate_username_is_rejected(admin_client):
    admin_client.post(
        "/admin/users",
        data={"username": "dup-user", "password": "first-pass-123"},
        follow_redirects=False,
    )

    response = admin_client.post(
        "/admin/users",
        data={"username": "DUP-USER", "password": "second-pass-123"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "already taken" in response.text


def test_short_username_is_rejected(admin_client):
    response = admin_client.post(
        "/admin/users",
        data={"username": "ab", "password": "valid-pass-123"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Username must be" in response.text


def test_short_password_is_rejected(admin_client):
    response = admin_client.post(
        "/admin/users",
        data={"username": "short-pass", "password": "short"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "at least 8" in response.text


def test_disabled_ticketer_cannot_log_in(admin_client, app):
    admin_client.post(
        "/admin/users",
        data={"username": "door-two", "password": "door-two-pass"},
        follow_redirects=False,
    )
    account = next(u for u in _users(app) if u.username == "door-two")

    response = _toggle(admin_client, account.id)
    assert response.status_code == 303

    rejected = login(admin_client, "door-two", "door-two-pass")
    assert rejected.status_code == 401


def test_disabling_ticketer_kills_existing_session(ticketer_client, app):
    account = next(u for u in _users(app) if u.username == TICKETER_USERNAME)

    assert ticketer_client.get("/tickets").status_code == 200

    ticketer_client.post("/logout", follow_redirects=False)
    login(ticketer_client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _toggle(ticketer_client, account.id)
    ticketer_client.post("/logout", follow_redirects=False)

    ticketer_client.cookies.clear()
    login(ticketer_client, TICKETER_USERNAME, TICKETER_PASSWORD)
    assert ticketer_client.get("/", follow_redirects=False).status_code == 303


def test_ticketer_can_be_re_enabled(admin_client, app):
    admin_client.post(
        "/admin/users",
        data={"username": "door-three", "password": "door-three-pass"},
        follow_redirects=False,
    )
    account = next(u for u in _users(app) if u.username == "door-three")

    _toggle(admin_client, account.id)
    _toggle(admin_client, account.id)

    assert login(admin_client, "door-three", "door-three-pass").status_code == 303


def test_admin_cannot_disable_itself(admin_client, app):
    admin = next(u for u in _users(app) if u.role is Role.admin)

    response = _toggle(admin_client, admin.id)

    assert response.status_code == 303
    assert admin_client.get("/").status_code == 200
    refreshed = next(u for u in _users(app) if u.id == admin.id)
    assert refreshed.is_active is True


def test_staff_page_lists_accounts(admin_client):
    admin_client.post(
        "/admin/users",
        data={"username": "reset-form-user", "password": "fake-password-123"},
        follow_redirects=False,
    )
    account = next(user for user in _users(admin_client.app) if user.username == "reset-form-user")
    response = admin_client.get("/admin/users")

    assert response.status_code == 200
    assert ADMIN_USERNAME in response.text
    assert "Add a ticketer" in response.text
    assert f'action="/admin/users/{account.id}/password"' in response.text
    assert 'type="password" name="password"' in response.text
    assert 'autocomplete="new-password"' in response.text
