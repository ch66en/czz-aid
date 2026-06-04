from __future__ import annotations

"""SQLite schema migration helpers for the local RAG index."""

import sqlite3


RAG_SCHEMA_VERSION = "2"


def ensure_rag_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_documents (
            doc_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            project TEXT NOT NULL,
            module TEXT,
            title TEXT NOT NULL,
            uri TEXT,
            content TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            updated_at TEXT,
            metadata_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            source TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            project TEXT NOT NULL,
            module TEXT,
            title TEXT NOT NULL,
            heading_path_json TEXT NOT NULL,
            child_type TEXT NOT NULL DEFAULT '',
            section_name TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(doc_id) REFERENCES rag_documents(doc_id)
        )
        """
    )
    _add_missing_column(conn, "rag_documents", "content", "TEXT NOT NULL DEFAULT ''")
    _add_missing_column(conn, "rag_chunks", "child_type", "TEXT NOT NULL DEFAULT ''")
    _add_missing_column(conn, "rag_chunks", "section_name", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
            chunk_id UNINDEXED,
            doc_id UNINDEXED,
            project UNINDEXED,
            doc_type UNINDEXED,
            module UNINDEXED,
            title,
            heading_path,
            content,
            tokenize = 'unicode61 tokenchars ''._:-/$'''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_project_doc_type ON rag_chunks(project, doc_type)")
    previous_version = get_index_meta(conn, "schema_version")
    if previous_version != RAG_SCHEMA_VERSION:
        rebuild_fts(conn)
        set_index_meta(conn, "schema_version", RAG_SCHEMA_VERSION)


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM rag_chunks_fts")
    conn.execute(
        """
        INSERT INTO rag_chunks_fts (chunk_id, doc_id, project, doc_type, module, title, heading_path, content)
        SELECT chunk_id, doc_id, project, doc_type, COALESCE(module, ''), title, heading_path_json, content
        FROM rag_chunks
        """
    )


def get_index_meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM rag_index_meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else ""


def set_index_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO rag_index_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _add_missing_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
