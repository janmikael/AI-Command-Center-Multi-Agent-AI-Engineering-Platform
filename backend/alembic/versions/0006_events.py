"""append-only, month-partitioned events log

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24

Guarantees, enforced by the database (not just by application code):
  1. A BEFORE UPDATE OR DELETE row trigger rejects any change to an existing event, for
     every role including the owner. Row triggers on a partitioned table are cloned to
     every partition, so writing to a partition directly doesn't bypass it.
  2. The runtime role acc_app has no UPDATE/DELETE/TRUNCATE on the parent or on any
     partition (default privileges would grant them, so they are revoked explicitly,
     including on partitions created later by acc_ensure_event_partitions).
  3. Monthly partitions are created ahead of time by acc_ensure_event_partitions(), a
     SECURITY DEFINER function acc_app may execute (it can't run DDL itself). The API
     calls it on startup and every few hours.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "acc_app"


def upgrade() -> None:
    op.execute("CREATE SEQUENCE events_seq AS bigint")
    op.execute(
        """
        CREATE TABLE events (
            id          uuid        NOT NULL,
            ts          timestamptz NOT NULL DEFAULT now(),
            seq         bigint      NOT NULL DEFAULT nextval('events_seq'),
            project_id  uuid,
            task_id     uuid,
            agent_id    uuid,
            actor       text        NOT NULL,
            type        text        NOT NULL,
            severity    text        NOT NULL DEFAULT 'info',
            payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT pk_events PRIMARY KEY (id, ts),
            CONSTRAINT ck_events_severity CHECK (severity IN ('debug', 'info', 'warn', 'error')),
            CONSTRAINT fk_events_project_id_projects
                FOREIGN KEY (project_id) REFERENCES projects (id),
            CONSTRAINT fk_events_task_id_tasks
                FOREIGN KEY (task_id) REFERENCES tasks (id),
            CONSTRAINT fk_events_agent_id_agents
                FOREIGN KEY (agent_id) REFERENCES agents (id)
        ) PARTITION BY RANGE (ts)
        """
    )
    op.execute("ALTER SEQUENCE events_seq OWNED BY events.seq")
    op.execute("CREATE INDEX ix_events_seq ON events (seq)")
    op.execute("CREATE INDEX ix_events_task_id_ts ON events (task_id, ts)")
    op.execute("CREATE INDEX ix_events_agent_id_ts ON events (agent_id, ts)")
    op.execute("CREATE INDEX ix_events_project_id_ts ON events (project_id, ts)")
    op.execute("CREATE INDEX ix_events_type_ts ON events (type, ts)")

    # Safety net for timestamps outside any monthly partition. Normally stays empty.
    op.execute("CREATE TABLE events_default PARTITION OF events DEFAULT")

    op.execute(
        """
        CREATE FUNCTION acc_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'restrict_violation';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER events_append_only
        BEFORE UPDATE OR DELETE ON events
        FOR EACH ROW EXECUTE FUNCTION acc_events_append_only()
        """
    )
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON events, events_default FROM {RUNTIME_ROLE}")

    op.execute(
        f"""
        CREATE FUNCTION acc_ensure_event_partitions(months_ahead integer DEFAULT 3)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            month_start timestamptz;
            month_end   timestamptz;
            part_name   text;
            created     integer := 0;
        BEGIN
            IF months_ahead < 0 OR months_ahead > 24 THEN
                RAISE EXCEPTION 'months_ahead must be between 0 and 24';
            END IF;
            -- Serialize concurrent callers (several API processes starting at once).
            PERFORM pg_advisory_xact_lock(hashtext('acc_ensure_event_partitions'));
            FOR i IN 0..months_ahead LOOP
                month_start := (date_trunc('month', now() AT TIME ZONE 'UTC')
                                + make_interval(months => i)) AT TIME ZONE 'UTC';
                month_end := ((month_start AT TIME ZONE 'UTC') + interval '1 month')
                             AT TIME ZONE 'UTC';
                part_name := 'events_y' || to_char(month_start AT TIME ZONE 'UTC', 'YYYY"m"MM');
                IF to_regclass(part_name) IS NULL THEN
                    EXECUTE format(
                        'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
                        part_name, month_start, month_end
                    );
                    EXECUTE format(
                        'REVOKE UPDATE, DELETE, TRUNCATE ON %I FROM {RUNTIME_ROLE}', part_name
                    );
                    created := created + 1;
                END IF;
            END LOOP;
            RETURN created;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION acc_ensure_event_partitions(integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION acc_ensure_event_partitions(integer) TO {RUNTIME_ROLE}")
    op.execute("SELECT acc_ensure_event_partitions(3)")


def downgrade() -> None:
    op.execute("DROP FUNCTION acc_ensure_event_partitions(integer)")
    op.execute("DROP TABLE events")  # drops partitions, trigger and the owned sequence
    op.execute("DROP FUNCTION acc_events_append_only()")
