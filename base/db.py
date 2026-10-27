"""Доступ к БД сервиса «База» (psycopg 3)."""
import os

import psycopg
from psycopg.rows import dict_row

DB = {
    "host": os.getenv("BASE_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("BASE_DB_PORT", "5432")),
    "dbname": os.getenv("BASE_DB_NAME", "base"),
    "user": os.getenv("BASE_DB_USER", "base"),
    "password": os.getenv("BASE_DB_PASSWORD", ""),
}


def connect():
    return psycopg.connect(**DB, row_factory=dict_row)


def query(sql, params=None):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
