from collections.abc import Iterator, Mapping

import pytest

from tests import pg
from wyrd.engine.discord import rest


@pytest.fixture(scope="session")
def pg_cluster() -> Iterator[pg.Cluster]:
    yield from pg.cluster_fixture()


@pytest.fixture
def pg_db(pg_cluster: pg.Cluster) -> Iterator[pg.PgDb]:
    yield from pg.db_fixture(pg_cluster)


@pytest.fixture(autouse=True)
def no_discord_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real HTTP client fails every test that reaches it."""

    def refuse(
        self: rest.UrllibHttp, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        raise AssertionError(f"a test tried to reach Discord: {method} {url}")

    monkeypatch.setattr(rest.UrllibHttp, "request", refuse)
