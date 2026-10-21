import os
from collections.abc import Iterator, Mapping

import pytest

from revisionist.engine.discord import rest
from tests import pg

# The Postgres harness reads these; everything else under REVISIONIST_ is the caller's.
KEEP_ENV = {"REVISIONIST_TEST_PG_ADMIN_DSN", "REVISIONIST_REQUIRE_PG"}


@pytest.fixture(scope="session")
def pg_cluster() -> Iterator[pg.Cluster]:
    yield from pg.cluster_fixture()


@pytest.fixture
def pg_db(pg_cluster: pg.Cluster) -> Iterator[pg.PgDb]:
    yield from pg.db_fixture(pg_cluster)


@pytest.fixture(autouse=True)
def clean_revisionist_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sourced ``revisionist.env`` in the shell would redirect paths and Discord."""
    for name in list(os.environ):
        if name.startswith("REVISIONIST_") and name not in KEEP_ENV:
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def no_discord_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real HTTP client fails every test that reaches it."""

    def refuse(
        self: rest.UrllibHttp, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        raise AssertionError(f"a test tried to reach Discord: {method} {url}")

    monkeypatch.setattr(rest.UrllibHttp, "request", refuse)
