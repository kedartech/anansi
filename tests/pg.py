"""Scratch Postgres databases for tests (build plan Layer 8).

Driven by ``$WYRD_TEST_PG_ADMIN_DSN``. A session builds one template database,
migrated as admin and granted to a fresh least-privilege app role with a
generated password; each test gets its own copy of the template and connects
as that role. Without the DSN these tests skip, unless ``$WYRD_REQUIRE_PG=1``
(as in CI), where a missing database is a failure.
"""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from wyrd.engine.pg_store import migrate

ADMIN_ENV = "WYRD_TEST_PG_ADMIN_DSN"


@dataclass(frozen=True)
class PgDb:
    admin_dsn: str
    app_dsn: str
    """A ``postgresql://`` URL, so it also works as ``$WYRD_DB``."""
    app_role: str


def admin_dsn_or_skip() -> str:
    dsn = os.environ.get(ADMIN_ENV)
    if dsn:
        return dsn
    if os.environ.get("WYRD_REQUIRE_PG") == "1":
        pytest.fail(f"WYRD_REQUIRE_PG=1 but {ADMIN_ENV} is unset")
    pytest.skip(f"{ADMIN_ENV} is unset")


def url(base: str, *, dbname: str, user: str | None = None, password: str | None = None) -> str:
    """``base`` with the database (and optionally the login) swapped, as a URL."""
    params = {k: str(v) for k, v in conninfo_to_dict(base).items() if v is not None}
    params["dbname"] = dbname
    if user is not None:
        params["user"] = user
        params["password"] = password or ""
    login = quote(params.pop("user", ""), safe="")
    if "password" in params:
        login += ":" + quote(params.pop("password"), safe="")
    host = params.pop("host", "")
    port = params.pop("port", "")
    db = quote(params.pop("dbname"), safe="")
    netloc = (login + "@" if login else "") + ("" if host.startswith("/") else host)
    if port:
        netloc += f":{port}"
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    if host.startswith("/"):
        query = "&".join(x for x in (f"host={quote(host, safe='')}", query) if x)
    return f"postgresql://{netloc}/{db}" + (f"?{query}" if query else "")


class Cluster:
    """One template database and app role per test session."""

    def __init__(self, admin: str) -> None:
        self.admin = admin
        tag = secrets.token_hex(4)
        self.role = f"wyrd_app_{tag}"
        self.password = secrets.token_urlsafe(18)
        self.template = f"wyrd_tpl_{tag}"
        self._n = 0
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(self.role), sql.Literal(self.password)
                )
            )
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.template)))
        migrate(url(admin, dbname=self.template), self.role)

    def fresh(self) -> PgDb:
        self._n += 1
        name = f"{self.template}_{self._n}"
        with psycopg.connect(self.admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(name), sql.Identifier(self.template)
                )
            )
        return PgDb(
            admin_dsn=url(self.admin, dbname=name),
            app_dsn=url(self.admin, dbname=name, user=self.role, password=self.password),
            app_role=self.role,
        )

    def drop(self, db: PgDb) -> None:
        name = str(conninfo_to_dict(db.admin_dsn)["dbname"])
        with psycopg.connect(self.admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )

    def close(self) -> None:
        with psycopg.connect(self.admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(self.template)
                )
            )
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(self.role)))


def cluster_fixture() -> Iterator[Cluster]:
    cluster = Cluster(admin_dsn_or_skip())
    yield cluster
    cluster.close()


def db_fixture(cluster: Cluster) -> Iterator[PgDb]:
    db = cluster.fresh()
    yield db
    cluster.drop(db)
