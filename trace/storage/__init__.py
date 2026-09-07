"""
TRACE Storage Package: PostgreSQL + pgvector persistence layer (PRD §11.1, §12.3, §29).
"""

from trace.storage.postgres import PostgresStore

__all__ = ["PostgresStore"]
