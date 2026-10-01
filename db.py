"""Tiny database layer: SQLite for local dev/tests, Postgres (e.g. Neon) in production.

The target is a SQLite file path or a postgres:// URL. Callers write SQL once, using
`?` or `:name` placeholders; for Postgres they are rewritten to psycopg's style.
Rows support both row["col"] and row[0] on either backend.
"""
import os
import re
import sqlite3

DSN_ENVS = ("DATABASE_URL", "POSTGRES_URL")


def default_target():
    for k in DSN_ENVS:
        if os.environ.get(k):
            return os.environ[k]
    return os.environ.get("WATER_DB", "water.db")


def is_pg(target):
    return str(target).startswith(("postgres://", "postgresql://"))


_NAMED = re.compile(r"(?<![:\w]):(\w+)")


def pg_sql(sql):
    """`?` -> %s and :name -> %(name)s. Our SQL has no literal '%' or ':' inside strings."""
    return _NAMED.sub(r"%(\1)s", sql.replace("?", "%s"))


class Row(dict):
    """dict that also answers row[0], like sqlite3.Row."""
    def __getitem__(self, k):
        return list(self.values())[k] if isinstance(k, int) else super().__getitem__(k)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else None
    return (lambda values: Row(zip(names, values))) if names else (lambda values: values)


class Conn:
    """Minimal connection wrapper exposing the same calls for both backends.
    `with connect(...) as c:` commits (or rolls back) and closes."""

    def __init__(self, raw, pg):
        self.raw, self.pg = raw, pg

    def execute(self, sql, params=()):
        if not self.pg:
            return self.raw.execute(sql, params)
        cur = self.raw.cursor()
        cur.execute(pg_sql(sql), params or None)
        return cur

    def executemany(self, sql, seq):
        if not self.pg:
            return self.raw.executemany(sql, seq)
        cur = self.raw.cursor()
        cur.executemany(pg_sql(sql), seq)
        return cur

    def executescript(self, script):
        if self.pg:
            self.raw.execute(script)  # several statements, no parameters
        else:
            self.raw.executescript(script)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            self.raw.rollback() if exc_type else self.raw.commit()
        finally:
            self.raw.close()


def connect(target=None):
    target = target or default_target()
    if is_pg(target):
        import psycopg  # imported lazily so SQLite-only setups don't need it
        # prepare_threshold=None: server-side prepared statements break behind PgBouncer
        # (Neon's pooled connection string), so keep every statement unprepared.
        raw = psycopg.connect(target, row_factory=_row_factory, prepare_threshold=None, connect_timeout=10)
        return Conn(raw, True)
    raw = sqlite3.connect(target)
    raw.row_factory = sqlite3.Row
    return Conn(raw, False)
