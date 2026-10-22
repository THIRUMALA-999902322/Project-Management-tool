from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fieldbook.app import create_app
from fieldbook.config import load_settings
from fieldbook.seed import seed


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path, assistant_mode="rules", public_url="http://test")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def db(app):
    session = app.state.sessions()
    yield session
    session.close()


@pytest.fixture
def seeded(app, db, settings):
    """The brief's sample company. Returns the links seed() prints."""
    return seed(db, settings)


def _token(url: str) -> str:
    return url.rsplit("/", 1)[-1]


@pytest.fixture
def owner(app, seeded):
    """A client signed in as the owner (via the login link, like a browser)."""
    c = TestClient(app)
    r = c.get(f"/login/{_token(seeded['owner_login'])}", follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture
def field(app, seeded):
    """field('J. Ortiz') -> a client authenticated with that person's field link."""

    def make(name: str) -> TestClient:
        c = TestClient(app)
        c.headers["Authorization"] = f"Bearer {_token(seeded['field_links'][name])}"
        return c

    return make
