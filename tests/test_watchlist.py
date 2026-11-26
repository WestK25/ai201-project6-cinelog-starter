"""
tests/test_watchlist.py — CineLog

Tests for the watchlist service and endpoints.
Fixtures follow the same pattern as tests/test_collection.py.
"""

import pytest
from app import create_app, db
from models import User, Film, WatchlistEntry
from services.watchlist_service import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    NotOnWatchlistError,
)


@pytest.fixture
def app():
    """Create an isolated test app with an in-memory database."""
    app = create_app(config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def sample_user(app):
    """A user to use in tests."""
    with app.app_context():
        user = User(username="testuser", email="test@example.com")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def sample_film(app):
    """A film to use in tests."""
    with app.app_context():
        film = Film(title="Paddington 2", year=2017, genre="Comedy")
        db.session.add(film)
        db.session.commit()
        return film.id


# ── UUID film IDs ────────────────────────────────────────────────────────────

def test_watchlist_stores_and_returns_uuid_film_ids(app, sample_user, sample_film):
    """
    After the integer-to-UUID migration on main, a watchlist entry should
    store the film's UUID string unchanged, and the watchlist should return
    that same UUID as the film's id.
    """
    with app.app_context():
        client = app.test_client()
        assert isinstance(sample_film, str) and len(sample_film) == 36

        response = client.post(f"/watchlist/{sample_user}/add", json={"film_id": sample_film})
        assert response.status_code == 201
        assert response.get_json()["film_id"] == sample_film

        entry = WatchlistEntry.query.filter_by(user_id=sample_user).one()
        assert entry.film_id == sample_film

        listed = client.get(f"/watchlist/{sample_user}").get_json()
        assert [film["id"] for film in listed] == [sample_film]


# ── Remove ───────────────────────────────────────────────────────────────────

def test_remove_from_watchlist_deletes_only_that_entry(app, sample_user, sample_film):
    """
    Removing a film should delete that user's entry and leave other films
    on the watchlist untouched.
    """
    with app.app_context():
        other = Film(title="Alien", year=1979, genre="Horror")
        db.session.add(other)
        db.session.commit()
        add_to_watchlist(user_id=sample_user, film_id=sample_film)
        add_to_watchlist(user_id=sample_user, film_id=other.id)

        assert remove_from_watchlist(user_id=sample_user, film_id=sample_film) is True

        remaining = [film["id"] for film in get_watchlist(sample_user)]
        assert remaining == [other.id]


def test_remove_film_not_on_watchlist_raises_and_returns_404(app, sample_user, sample_film):
    """
    Removing a film that was never added should raise NotOnWatchlistError in
    the service and return 404 from the endpoint, not silently succeed.
    """
    with app.app_context():
        with pytest.raises(NotOnWatchlistError):
            remove_from_watchlist(user_id=sample_user, film_id=sample_film)

        response = app.test_client().delete(
            f"/watchlist/{sample_user}/remove", json={"film_id": sample_film}
        )
        assert response.status_code == 404
