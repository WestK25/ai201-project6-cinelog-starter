"""
tests/test_watchlist.py — CineLog

Tests for the watchlist service and endpoints.
Fixtures follow the same pattern as tests/test_collection.py.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from app import create_app, db
from models import User, Film, WatchlistEntry
from services.collection_service import FilmNotFoundError
from services.watchlist_service import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    AlreadyOnWatchlistError,
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


# ── Deduplication ────────────────────────────────────────────────────────────

def test_add_to_watchlist_duplicate_raises(app, sample_user, sample_film):
    """
    Adding the same film twice should raise AlreadyOnWatchlistError,
    not silently create a duplicate entry (same contract as
    test_add_to_collection_duplicate_raises).
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        with pytest.raises(AlreadyOnWatchlistError):
            add_to_watchlist(user_id=sample_user, film_id=sample_film)

        count = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).count()
        assert count == 1


def test_add_endpoint_returns_409_and_keeps_original_entry(app, sample_user, sample_film):
    """
    A duplicate add over HTTP should return 409 Conflict. Re-adding with a
    different visibility must not overwrite the original entry: a re-add is
    a conflict, not an update.
    """
    with app.app_context():
        client = app.test_client()
        first = client.post(f"/watchlist/{sample_user}/add", json={"film_id": sample_film})
        assert first.status_code == 201

        second = client.post(
            f"/watchlist/{sample_user}/add", json={"film_id": sample_film, "public": False}
        )
        assert second.status_code == 409

        entry = WatchlistEntry.query.filter_by(user_id=sample_user, film_id=sample_film).one()
        assert entry.id == first.get_json()["id"]
        assert entry.public is True


def test_different_users_can_each_add_the_same_film(app, sample_user, sample_film):
    """
    Deduplication is per user: another user adding the same film is not a
    duplicate.
    """
    with app.app_context():
        other_user = User(username="otheruser", email="other@example.com")
        db.session.add(other_user)
        db.session.commit()

        add_to_watchlist(user_id=sample_user, film_id=sample_film)
        add_to_watchlist(user_id=other_user.id, film_id=sample_film)

        assert WatchlistEntry.query.filter_by(film_id=sample_film).count() == 2


def test_database_rejects_duplicate_watchlist_rows(app, sample_user, sample_film):
    """
    The unique (user_id, film_id) constraint is the backstop if two requests
    pass the service check at the same time.
    """
    with app.app_context():
        db.session.add(WatchlistEntry(user_id=sample_user, film_id=sample_film))
        db.session.commit()

        db.session.add(WatchlistEntry(user_id=sample_user, film_id=sample_film))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ── Nonexistent film ─────────────────────────────────────────────────────────

def test_add_to_watchlist_nonexistent_film_raises(app, sample_user):
    """
    Adding a film_id that doesn't exist in the database should raise
    FilmNotFoundError, not a database integrity error, and must not create
    an entry. The endpoint should report the missing film as 404.
    """
    with app.app_context():
        fake_film_id = "00000000-0000-0000-0000-000000000000"

        with pytest.raises(FilmNotFoundError):
            add_to_watchlist(user_id=sample_user, film_id=fake_film_id)

        assert WatchlistEntry.query.filter_by(user_id=sample_user).count() == 0

        response = app.test_client().post(
            f"/watchlist/{sample_user}/add", json={"film_id": fake_film_id}
        )
        assert response.status_code == 404
        assert WatchlistEntry.query.filter_by(user_id=sample_user).count() == 0


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


# ── Sort order ───────────────────────────────────────────────────────────────

def _add_films_with_dates(user_id, titles_and_ages):
    """Create films and watchlist entries added `age` days ago."""
    now = datetime.now(timezone.utc)
    for title, age_days in titles_and_ages:
        film = Film(title=title)
        db.session.add(film)
        db.session.flush()
        db.session.add(WatchlistEntry(
            user_id=user_id, film_id=film.id, date_added=now - timedelta(days=age_days)
        ))
    db.session.commit()


def test_get_watchlist_returns_newest_first(app, sample_user):
    """
    get_watchlist() should return films by date_added descending. The titles
    are chosen so alphabetical order would give the opposite result.
    """
    with app.app_context():
        _add_films_with_dates(sample_user, [
            ("Alien", 10),
            ("Spirited Away", 0),
            ("Blade Runner", 5),
        ])

        titles = [film["title"] for film in get_watchlist(sample_user)]
        assert titles == ["Spirited Away", "Blade Runner", "Alien"]

        listed = app.test_client().get(f"/watchlist/{sample_user}").get_json()
        assert [film["title"] for film in listed] == titles


def test_get_watchlist_breaks_date_ties_by_title(app, sample_user):
    """
    Films saved at the same instant should come back in a stable order
    (by title), not in whatever order the database happens to return.
    """
    with app.app_context():
        same_time = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        for title in ["The Godfather", "Alien", "Paddington 2"]:
            film = Film(title=title)
            db.session.add(film)
            db.session.flush()
            db.session.add(WatchlistEntry(
                user_id=sample_user, film_id=film.id, date_added=same_time
            ))
        db.session.commit()

        titles = [film["title"] for film in get_watchlist(sample_user)]
        assert titles == ["Alien", "Paddington 2", "The Godfather"]


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


# ── Visibility ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body_extra, expected_public", [
    ({"public": True}, True),
    ({"public": False}, False),
    ({}, True),  # omitted: the documented default (public)
])
def test_add_endpoint_honors_explicit_and_default_visibility(
    app, sample_user, sample_film, body_extra, expected_public
):
    """
    An explicit public flag should be stored as given. Omitting it should fall
    back to the documented default, both in the response and in the database.
    """
    with app.app_context():
        response = app.test_client().post(
            f"/watchlist/{sample_user}/add", json={"film_id": sample_film, **body_extra}
        )
        assert response.status_code == 201
        assert response.get_json()["public"] is expected_public

        entry = WatchlistEntry.query.filter_by(user_id=sample_user, film_id=sample_film).one()
        assert entry.public is expected_public


def test_public_view_hides_private_entries(app, sample_user, sample_film):
    """
    The public view should list only public entries (newest first), while
    the owner's view still lists everything. A film added without a
    visibility flag is public by default, so it must appear publicly.
    """
    with app.app_context():
        private_film = Film(title="Alien", year=1979, genre="Horror")
        db.session.add(private_film)
        db.session.commit()

        client = app.test_client()
        client.post(f"/watchlist/{sample_user}/add", json={"film_id": sample_film})
        client.post(
            f"/watchlist/{sample_user}/add",
            json={"film_id": private_film.id, "public": False},
        )

        owner_view = client.get(f"/watchlist/{sample_user}").get_json()
        assert {film["id"] for film in owner_view} == {sample_film, private_film.id}

        public_view = client.get(f"/watchlist/{sample_user}/public").get_json()
        assert [film["id"] for film in public_view] == [sample_film]
        assert get_watchlist(sample_user, public_only=True) == public_view


def test_public_view_of_all_private_watchlist_is_empty(app, sample_user, sample_film):
    """
    If every entry is private, the public view should return an empty list
    with 200, not an error.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film, public=False)

        response = app.test_client().get(f"/watchlist/{sample_user}/public")
        assert response.status_code == 200
        assert response.get_json() == []


def test_add_endpoint_rejects_non_boolean_visibility(app, sample_user, sample_film):
    """
    A string such as "false" is truthy in Python, so accepting it would make an
    entry public when the caller asked for private. The endpoint should reject
    it with 400 and create nothing.
    """
    with app.app_context():
        response = app.test_client().post(
            f"/watchlist/{sample_user}/add", json={"film_id": sample_film, "public": "false"}
        )
        assert response.status_code == 400
        assert WatchlistEntry.query.filter_by(user_id=sample_user).count() == 0
