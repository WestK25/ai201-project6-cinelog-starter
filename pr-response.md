# CineLog watchlist: response to review

- **Branch:** `feature/watchlist`, rebased onto `main` at `57ec3f9`.
- **Pull request:** [WestK25/ai201-project6-cinelog-starter#1](https://github.com/WestK25/ai201-project6-cinelog-starter/pull/1), into my fork's `main`.
- **Original review:** [codepath/ai201-project6-cinelog-starter#1](https://github.com/codepath/ai201-project6-cinelog-starter/pull/1).

## AI Usage

I used two AI coding agents on this PR and set the direction they worked under.

**What I decided and required**
- I made the two product decisions: public by default and newest first. The reasoning is in Comments 4 and 5.
- I set the engineering constraints:
  - follow the existing `add_to_collection()` conventions instead of inventing a new pattern;
  - make one logical change per commit, with conventional messages;
  - ship every behaviour change with tests;
  - make sure the nonexistent-film test targets a missing film and not some other UUID edge case;
  - keep the history linear, with no merge commits;
  - open the PR against my fork's `main` only;
  - promise no privacy that the backend cannot enforce.

**Codex (OpenAI)** read the six review comments, compared the watchlist code with the collection service, and applied the rename in my working tree. It reached its usage limit before committing anything.

**Claude (Anthropic)** implemented the changes under those constraints:
- committed the rename and confirmed it with a repository-wide search;
- ran the interactive rebase and resolved the `models.py` conflict;
- wrote the UUID follow-up fix;
- wrote the duplicate check, the nonexistent-film test, the newest-first query, the public view and the stretch features, each with tests;
- ran each new test against the code before its change to confirm the test fails there;
- ran the manual HTTP walkthrough against a live server;
- drafted this document and the PR description from my decisions.

**Corrections from review.** An independent review pass over the branch found three problems before the PR was opened:
- an early UUID test overlapped the required nonexistent-film test;
- the visibility default was defined in two places;
- two inherited commit messages did not describe their changes accurately.

All three were fixed: the test was narrowed, the default became a single constant, and the messages were reworded during the rebase.

## Comment 1: rename `save_to_watchlist()` → `add_to_watchlist()`

Commit `45bc3d0 refactor: rename save_to_watchlist to add_to_watchlist`.

- **References found.** `git grep -n save_to_watchlist` found three references: the definition in `services/watchlist_service.py`, plus the import and the single call in `routes/watchlist/watchlist.py`.
- **References renamed.** All three now use `add_to_watchlist`. The docstring now says "Add a film…" instead of "Save a film…".
- **Completeness check.** A repository-wide search after the change returns no matches. It covered tracked and untracked `.py` and `.md` files and excluded the virtual environment.
- **Convention.** The new name follows the `verb_to_noun` convention in `CONTRIBUTING.md` (`add_to_collection`, `remove_from_collection`).
- **Why a separate commit.** The rename is a `refactor:` with no behaviour change, so it is kept apart from the fixes.

## Comment 2: duplicate entries

**The existing pattern.** `add_to_collection()` in `services/collection_service.py` does three things:
1. It confirms the film exists, raising `FilmNotFoundError` if not.
2. It looks for an existing `CollectionEntry` with the same `(user_id, film_id)` and raises `AlreadyInCollectionError` if there is one.
3. Only then does it insert.

`routes/collection.py` turns that exception into **409 Conflict**. `CollectionEntry` also has a `unique_user_film_collection` constraint as a database-level guard.

**The watchlist change.** Commit `8e74e49 fix: prevent duplicate films on a user's watchlist` applies the same contract to `add_to_watchlist()`:
- After the film-existence check, the service queries `WatchlistEntry` for the same `(user_id, film_id)`. If one exists, it raises a new `AlreadyOnWatchlistError`, defined next to `NotOnWatchlistError`. The existence check still runs first, in the same order as the collection service.
- `POST /watchlist/<user_id>/add` catches `AlreadyOnWatchlistError` and returns **409** with an error message.
- `WatchlistEntry` gains `UniqueConstraint("user_id", "film_id", name="unique_user_film_watchlist")`. If two requests pass the service check at the same moment, the database still refuses the second row.

**Re-adding with a different `public` value is still a 409, not an update.** A duplicate add should never silently change an existing entry. Changing visibility should be an explicit action, not a side effect of a repeated request. The test below checks that the original entry, including its visibility, survives a conflicting re-add.

**Tests.** Commit `a4755c1 test: cover duplicate watchlist additions` adds four tests:

| Test | What it checks |
| --- | --- |
| `test_add_to_watchlist_duplicate_raises` | The second service call raises `AlreadyOnWatchlistError` and exactly one row remains. It mirrors `test_add_to_collection_duplicate_raises`. |
| `test_add_endpoint_returns_409_and_keeps_original_entry` | Over HTTP, the second add returns 409. A re-add with `"public": false` leaves the original public entry untouched. |
| `test_different_users_can_each_add_the_same_film` | Deduplication is per user, not global. |
| `test_database_rejects_duplicate_watchlist_rows` | Inserting a duplicate row directly raises `IntegrityError`. |

Against the code before `8e74e49`, the first, second and fourth tests fail. The per-user test passes on both versions, as a guard should. The suite now passes.

**Existing local databases.** `db.create_all()` does not alter existing tables, so a `cinelog.db` created before this change will not have the new constraint. The README says to recreate it with `python seed.py`.

## Comment 3: test for a nonexistent `film_id`

Commit `4e25133 test: add nonexistent film test for add_to_watchlist` adds `test_add_to_watchlist_nonexistent_film_raises` to `tests/test_watchlist.py`. It follows `test_add_to_collection_nonexistent_film_raises` in `tests/test_collection.py`:

- **Fixtures.** `app` builds a fresh in-memory database, and `sample_user` creates a real user. There is deliberately no film fixture, because the point is a film that doesn't exist.
- **The id.** `fake_film_id = "00000000-0000-0000-0000-000000000000"` is a well-formed UUID that is not in the database, so the test isolates "film missing" from "malformed id".
- **The assertion.** `with pytest.raises(FilmNotFoundError):` around `add_to_watchlist(...)` passes only for that exact exception. A database integrity error, or a silent insert, fails the test.
- **Extra checks.**
  - No `WatchlistEntry` is created.
  - `POST /watchlist/<user_id>/add` returns **404** for the same id. This covers the 404 handling added in the UUID fix (`e51850b`).

**Proof that it tests something.** With the `if film is None: raise FilmNotFoundError(...)` lines removed from `add_to_watchlist()`, the test fails with `DID NOT RAISE FilmNotFoundError`. SQLite does not enforce foreign keys by default, so without the check the bad id would be inserted silently. With the check restored, it passes.

## Comment 4: default visibility

**Decision: public by default.** New entries are public unless the caller sends `"public": false`.

**Why public.** CineLog is a community film-tracking app. Public watchlists reduce the friction of discovery: friends can see what you plan to watch, make recommendations, and start conversations about upcoming films. Most people never change a default, so a private default would leave most watchlists invisible and the social value would never appear. Users who want a more personal list get an explicit private option on each entry.

**The tradeoff I accepted.** Private-by-default optimizes privacy; public-by-default optimizes discovery. A watchlist reveals what someone intends to watch, and making an entry private after it was visible cannot undo the exposure. I chose discovery because the product is social, and I reduced the risk with three controls:

- **An explicit per-entry flag** (`1d94a18`). `"public": false` makes an entry private.
- **Strict validation.** Only JSON booleans are accepted. A string such as `"false"` is truthy in Python and would otherwise make an entry public against the caller's intent, so it gets a 400.
- **A public view that never shows private entries** (`a0efe0b`). `GET /watchlist/<user_id>/public` returns only public entries. Before this change, every entry was returned by the one list endpoint, so marking an entry private had no visible effect.

**A single source of truth.** `DEFAULT_WATCHLIST_PUBLIC = True` is defined once in `models.py` and used by the column default, the service signature and the route. Changing the policy later is a one-line change, and `test_add_endpoint_honors_explicit_and_default_visibility` asserts the literal default for an omitted flag.

**What this does not promise.** CineLog has no authentication. Every endpoint takes `user_id` from the URL, so the full owner view `GET /watchlist/<user_id>` can still be read by any caller, exactly like `GET /collection/<user_id>`. The public view is the correct endpoint for other users' screens. Enforcing privacy on the owner view requires an auth layer, which is outside this PR's scope. I have documented that limitation rather than describe `public: false` as a privacy guarantee.

**Tests.**
- `test_public_view_hides_private_entries`: the public view lists only public entries while the owner view lists all of them, and a film added without a flag appears publicly.
- `test_public_view_of_all_private_watchlist_is_empty`: a watchlist whose entries are all private gives a 200 with an empty list.

Both tests fail before `a0efe0b`.

## Comment 5: sort order

**Decision: newest first** (`date_added` descending). Commit `357d923 feat: sort watchlists newest first`.

**Why.** I agree that most users want to see what they added recently. The films at the top of a watchlist should be the ones that reflect current interests and what the user is about to watch. A film saved last week is far more likely to be watched tonight than one saved a year ago. Newest-first also matches `get_collection()`, so a user's two lists behave consistently.

**What I gave up.** Alphabetical order is better when you are looking for a specific title in a long list. That is a real use case, but it is a lookup, not the everyday "what should I watch next" view. Alphabetical sorting buries the newest additions, so it is not the right default. It would fit well as an optional `?sort=title` parameter later.

**Deterministic ties.** Two entries can share a `date_added` value, for example in bulk imports or tests. The query breaks ties by film title, then by entry id, so the same data always comes back in the same order:

```python
.order_by(
    WatchlistEntry.date_added.desc(),
    Film.title.asc(),
    WatchlistEntry.id.asc(),
)
```

**Tests.** Commit `22da620` adds two tests:
- `test_get_watchlist_returns_newest_first` uses three films whose alphabetical order is the reverse of their date order. It checks both `get_watchlist()` and `GET /watchlist/<user_id>`, and it fails against the previous `Film.title.asc()` query.
- `test_get_watchlist_breaks_date_ties_by_title` gives three films the same timestamp.

## Comment 6: rebase onto `main` and the UUID migration

`main` gained `57ec3f9 refactor: migrate film IDs from integer to UUID`. Claude saved a recovery branch, then rebased `feature/watchlist` onto it with `git rebase -i`.

**The conflict.** Replaying the original watchlist commit stopped on a genuine conflict in `models.py`, where both sides had changed the same line:

```diff
<<<<<<< HEAD (main)
    film_id = db.Column(db.String(36), db.ForeignKey("film.id"), nullable=False)
=======
    film_id = db.Column(db.Integer, db.ForeignKey("film.id"), nullable=False, index=True)
>>>>>>> 9eafe68 (added watchlist model and endpoint fixed a bug more changes)
```

**Resolution.** `main` changed the column to a UUID string; the feature had added an index. Taking either side alone would lose one of those changes, so the resolution keeps both:

```python
film_id = db.Column(db.String(36), db.ForeignKey("film.id"), nullable=False, index=True)
```

**Follow-up fix.** Git only flagged the model, but the watchlist code still assumed integer ids:
- the service documented `film_id (int) … pre-refactor`;
- the route documented `Body: { "film_id": <int> }`;
- a stale integer id crashed the add endpoint with an unhandled `FilmNotFoundError` (HTTP 500), while the collection endpoint returned 404.

Commit `e51850b fix: update watchlist to UUID film IDs after rebasing on main` documents `film_id` as a UUID string and maps `FilmNotFoundError` to 404, matching `routes/collection.py`. `3b72fd7` adds a guard test that a UUID `film_id` is stored and listed unchanged, and the Comment 3 test covers the 404 path.

**History cleanup.** The interactive rebase also reworded two inherited commits, keeping their original author:
- The message "added watchlist model and endpoint fixed a bug more changes" uses phrases `CONTRIBUTING.md` calls unacceptable. The commit became `080a6be feat: add watchlist service, endpoints and film_id index`; the model itself already existed on `main`.
- The next commit's message described only the `db.session.get` change, but the commit also fixed a broken blueprint import that kept the app from starting. It became `ca9d72b fix: correct the watchlist blueprint import and use db.session.get`.

The branch has no merge commits: `git log --merges main..feature/watchlist` is empty.

## Stretch features

- **`remove_from_watchlist(user_id, film_id)`.** Commits `44a4f7a` (feature) and `4c59831` (tests).
  - It mirrors `remove_from_collection()`. It deletes the user's entry, or raises `NotOnWatchlistError` if the film is not on the watchlist.
  - `DELETE /watchlist/<user_id>/remove` returns that error as 404.
  - The tests cover removing one film without touching another, and the missing-entry case at both the service and the HTTP level.
- **Explicit `public` parameter.** Commits `1d94a18` (feature) and `afb0a4a` (tests).
  - `add_to_watchlist(..., public=...)` and `POST /watchlist/<user_id>/add` accept an optional boolean `public`.
  - Omitting the flag uses `DEFAULT_WATCHLIST_PUBLIC`.
  - The tests cover explicit `true`, explicit `false`, omitted and invalid values, in both the response and the database.
- **Second edge-case test** (`afb0a4a`). `test_add_endpoint_rejects_non_boolean_visibility` sends `"public": "false"` and expects 400 with no entry created. Accepting the string would make an entry public when the caller asked for private.
- **Additional edge cases:**
  - per-user deduplication;
  - the database-level unique constraint;
  - date-tie ordering;
  - the all-private public view.

The suite has grown from 11 to 20 tests, and `python -m pytest -q` reports **20 passed**.

## Commit history

Output of `git log --oneline main..feature/watchlist` when the PR was opened. The next commit adds this document.

```text
bd4fb50 docs: document watchlist endpoints and model in README
a9d6c3f test: cover the public watchlist view
a0efe0b feat: add a public watchlist view that excludes private entries
22da620 test: cover newest-first watchlist order and date ties
357d923 feat: sort watchlists newest first
4e25133 test: add nonexistent film test for add_to_watchlist
a4755c1 test: cover duplicate watchlist additions
8e74e49 fix: prevent duplicate films on a user's watchlist
afb0a4a test: cover explicit, omitted and invalid watchlist visibility
1d94a18 feat: let callers choose watchlist visibility when adding a film
4c59831 test: cover removing watchlist films, including missing entries
44a4f7a feat: add remove_from_watchlist service and endpoint
3b72fd7 test: verify the watchlist stores and returns UUID film ids
e51850b fix: update watchlist to UUID film IDs after rebasing on main
45bc3d0 refactor: rename save_to_watchlist to add_to_watchlist
121e77f chore: ignore local environments, caches and databases
ca9d72b fix: correct the watchlist blueprint import and use db.session.get
080a6be feat: add watchlist service, endpoints and film_id index
```

The commits can also be viewed on the [PR's commits tab](https://github.com/WestK25/ai201-project6-cinelog-starter/pull/1/commits). Each review comment maps to its own commit: the rename, the deduplication fix, the missing-film test, the UUID fix, the sort change and the visibility work. Behaviour changes and their tests are separate commits, as `CONTRIBUTING.md` requires.

## Final PR description

> **What this does.** This PR adds a watchlist to CineLog: a list of films a user wants to watch, kept separate from the collection of films they have already watched. A user can add a film once (a duplicate returns 409), see their watchlist newest first, mark each entry public or private, share a public view that never includes private entries, and remove films. It also addresses all six review comments and rebases the branch onto the UUID migration on `main`.
>
> **Default visibility: public.** CineLog is a community app, and public watchlists make discovery and recommendations possible. A private default would leave most lists invisible, because most people never change a default. Private-by-default optimizes privacy; public-by-default optimizes discovery. I chose discovery and paired it with three controls: an explicit `"public": false`, strict boolean validation, and a public view that excludes private entries. CineLog has no authentication, so the full owner view is not access-controlled; enforcing that needs an auth layer.
>
> **Sort order: newest first.** Most users want to see what they added recently, because recent additions reflect what they want to watch now. Newest-first also matches `get_collection()`. Alphabetical order helps find a known title but buries recent additions, so it would fit better as an optional `?sort=` parameter. Ties are broken by title and then id.
>
> **How to test manually.** Recreate the database with `rm -f instance/cinelog.db && python seed.py`, then start the server with `FLASK_APP=app:create_app flask run --port 5050`. Then, with `curl`:
>
> | Step | Request | Expected |
> | --- | --- | --- |
> | 1 | Add Alien with no flag | 201, `public: true` |
> | 2 | Add Paddington 2 with `"public": false` | 201 |
> | 3 | Add Alien again | 409 |
> | 4 | Add the zero UUID | 404 |
> | 5 | Add with `"public": "false"` | 400 |
> | 6 | `GET /watchlist/<user_id>` | Paddington 2 first, then Alien |
> | 7 | `GET /watchlist/<user_id>/public` | Alien only |
> | 8 | Remove Alien | 200 |
> | 9 | Remove Alien again | 404 |
>
> The PR lists the exact commands, and every step was checked against a live server.
>
> **Also included:** `remove_from_watchlist()`, the explicit `public` parameter, extra edge-case tests (20 passing), and README documentation of the watchlist API.
