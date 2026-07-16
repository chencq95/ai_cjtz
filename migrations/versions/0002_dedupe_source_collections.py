"""Deduplicate source collections and enforce platform/code identity."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_dedupe_source_collections"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    groups = bind.execute(
        sa.text(
            """
            SELECT platform_id, code, MIN(id) AS keep_id
            FROM source_collection
            GROUP BY platform_id, code
            HAVING COUNT(*) > 1
            """
        )
    ).mappings().all()
    rank = {"complete": 4, "partial": 3, "blocked": 2, "unknown": 1, "out_of_scope": 0}
    for group in groups:
        rows = bind.execute(
            sa.text(
                """
                SELECT id, coverage_status, expected_count, last_run_at, last_complete_at
                FROM source_collection
                WHERE platform_id = :platform_id AND code = :code
                ORDER BY id
                """
            ),
            {"platform_id": group["platform_id"], "code": group["code"]},
        ).mappings().all()
        keep_id = group["keep_id"]
        duplicate_ids = [row["id"] for row in rows if row["id"] != keep_id]
        ids_param = sa.bindparam("duplicate_ids", expanding=True)
        for table in ("url_state", "catalog_item", "crawl_error"):
            bind.execute(
                sa.text(f"UPDATE {table} SET collection_id = :keep_id WHERE collection_id IN :duplicate_ids").bindparams(ids_param),
                {"keep_id": keep_id, "duplicate_ids": duplicate_ids},
            )
        best = max(rows, key=lambda row: rank.get(row["coverage_status"], 0))
        expected = max((row["expected_count"] for row in rows if row["expected_count"] is not None), default=None)
        last_run = max((row["last_run_at"] for row in rows if row["last_run_at"] is not None), default=None)
        last_complete = max((row["last_complete_at"] for row in rows if row["last_complete_at"] is not None), default=None)
        bind.execute(
            sa.text(
                """
                UPDATE source_collection
                SET coverage_status = :coverage_status,
                    expected_count = :expected_count,
                    last_run_at = :last_run_at,
                    last_complete_at = :last_complete_at
                WHERE id = :keep_id
                """
            ),
            {"coverage_status": best["coverage_status"], "expected_count": expected, "last_run_at": last_run, "last_complete_at": last_complete, "keep_id": keep_id},
        )
        bind.execute(
            sa.text("DELETE FROM source_collection WHERE id IN :duplicate_ids").bindparams(
                sa.bindparam("duplicate_ids", expanding=True)
            ),
            {"duplicate_ids": duplicate_ids},
        )
    constraint_names = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_unique_constraints("source_collection")
    }
    if "uq_collection_platform_code" not in constraint_names:
        with op.batch_alter_table("source_collection") as batch_op:
            batch_op.create_unique_constraint(
                "uq_collection_platform_code",
                ["platform_id", "code"],
            )


def downgrade() -> None:
    with op.batch_alter_table("source_collection") as batch_op:
        batch_op.drop_constraint("uq_collection_platform_code", type_="unique")
