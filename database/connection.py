"""PostgreSQL database connection and transaction management for BlackSmith.

Provides connection pooling, context managers for safe transactions,
database creation helpers, and schema initialization.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
from urllib.parse import urlparse

import psycopg2
from psycopg2 import pool, sql
from psycopg2.extensions import connection, cursor

from core.config import get_config

logger = logging.getLogger(__name__)

_pool: Optional[pool.ThreadedConnectionPool] = None


def get_connection_pool(minconn: int = 1, maxconn: int = 10) -> pool.ThreadedConnectionPool:
    """Retrieve or initialize the thread-safe connection pool."""
    global _pool
    if _pool is None or _pool.closed:
        config = get_config()
        # Ensure database exists before creating the pool
        create_database_if_not_exists(config.database_url)
        _pool = pool.ThreadedConnectionPool(minconn, maxconn, config.database_url)
    return _pool


def close_connection_pool() -> None:
    """Close all connections in the pool."""
    global _pool
    if _pool is not None and not _pool.closed:
        _pool.closeall()
        _pool = None


@contextmanager
def get_connection() -> Generator[connection, None, None]:
    """Context manager for obtaining a database connection from the pool."""
    conn_pool = get_connection_pool()
    conn = conn_pool.getconn()
    try:
        yield conn
    finally:
        conn_pool.putconn(conn)


@contextmanager
def get_transaction(conn: Optional[connection] = None) -> Generator[cursor, None, None]:
    """Context manager executing operations within an ACID transaction.
    
    Automatically commits on success or rolls back on exception.
    """
    if conn is not None:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    else:
        with get_connection() as managed_conn:
            cur = managed_conn.cursor()
            try:
                yield cur
                managed_conn.commit()
            except Exception:
                managed_conn.rollback()
                raise
            finally:
                cur.close()


def create_database_if_not_exists(database_url: Optional[str] = None) -> None:
    """Connect to postgres maintenance database to create target database if it does not exist."""
    if database_url is None:
        database_url = get_config().database_url

    parsed = urlparse(database_url)
    target_dbname = parsed.path.lstrip("/")

    if not target_dbname:
        raise ValueError("DATABASE_URL must specify a database name")

    # Construct maintenance URL pointing to 'postgres'
    maintenance_url = database_url.rsplit("/", 1)[0] + "/postgres"

    try:
        conn = psycopg2.connect(maintenance_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (target_dbname,))
            exists = cur.fetchone() is not None
            if not exists:
                logger.info("Database '%s' does not exist. Creating...", target_dbname)
                cur.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(target_dbname)))
                logger.info("Database '%s' created successfully.", target_dbname)
        conn.close()
    except Exception as e:
        logger.warning(
            "Could not check or create database '%s' via maintenance DB: %s. "
            "Proceeding under assumption that target DB exists.",
            target_dbname,
            e,
        )


def initialize_schema(conn: Optional[connection] = None) -> None:
    """Read schema.sql and apply all tables and indexes idempotently."""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    sql_statements = schema_path.read_text(encoding="utf-8")

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql_statements)
        conn.commit()
    else:
        with get_connection() as managed_conn:
            with managed_conn.cursor() as cur:
                cur.execute(sql_statements)
            managed_conn.commit()
    logger.info("Schema successfully initialized.")
