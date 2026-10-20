from collections.abc import Iterator

import pytest

from tests import pg


@pytest.fixture(scope="session")
def pg_cluster() -> Iterator[pg.Cluster]:
    yield from pg.cluster_fixture()


@pytest.fixture
def pg_db(pg_cluster: pg.Cluster) -> Iterator[pg.PgDb]:
    yield from pg.db_fixture(pg_cluster)
