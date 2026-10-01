"""Give every lead field and every option of a select a number.

``lead_fields.code`` is the field's public identity, the number a prompt cites.
The options of a select become ``[{"id": code, "label": text}]``, numbered from
the same per-client sequence, and a lead's choice is stored as that code
instead of the label, so an option can be renamed without losing the leads
that chose it. Each client numbers from 1000: a field, then its options, then
the next field.

The same statements run in a client's own database as tenant revision
``t0003_lead_field_codes``.

Revision ID: 0080_lead_field_codes
Revises: 0079_conversation_pinned
"""

from alembic import op


revision = "0080_lead_field_codes"
down_revision = "0079_conversation_pinned"
branch_labels = None
depends_on = None


UPGRADE = """
ALTER TABLE lead_fields ADD COLUMN code INTEGER;

UPDATE lead_fields AS lf SET code = numbered.code
FROM (
    SELECT id, 1000 + COALESCE(SUM(1 + json_array_length(options)) OVER (
        PARTITION BY client_id ORDER BY position, created_at, id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ), 0) AS code
    FROM lead_fields
) AS numbered
WHERE lf.id = numbered.id;

UPDATE lead_fields AS lf SET options = coded.options
FROM (
    SELECT f.id, json_agg(json_build_object('id', f.code + e.ord, 'label', e.label) ORDER BY e.ord) AS options
    FROM lead_fields AS f, json_array_elements_text(f.options) WITH ORDINALITY AS e(label, ord)
    GROUP BY f.id, f.code
) AS coded
WHERE lf.id = coded.id;

ALTER TABLE lead_fields ALTER COLUMN code SET NOT NULL;
ALTER TABLE lead_fields ADD CONSTRAINT uq_lead_fields_client_code UNIQUE (client_id, code);

DO $$
DECLARE
    f RECORD;
    o RECORD;
BEGIN
    FOR f IN SELECT client_id, key, options FROM lead_fields WHERE type = 'select' LOOP
        FOR o IN SELECT (e->>'id')::int AS id, e->>'label' AS label FROM json_array_elements(f.options) AS e LOOP
            UPDATE conversations
            SET custom_values = jsonb_set(custom_values::jsonb, ARRAY[f.key], to_jsonb(o.id))::json
            WHERE client_id = f.client_id
              AND json_typeof(custom_values -> f.key) = 'string'
              AND custom_values ->> f.key = o.label;
        END LOOP;
    END LOOP;
END $$;
"""


DOWNGRADE = """
DO $$
DECLARE
    f RECORD;
    o RECORD;
BEGIN
    FOR f IN SELECT client_id, key, options FROM lead_fields WHERE type = 'select' LOOP
        FOR o IN SELECT (e->>'id')::int AS id, e->>'label' AS label FROM json_array_elements(f.options) AS e LOOP
            UPDATE conversations
            SET custom_values = jsonb_set(custom_values::jsonb, ARRAY[f.key], to_jsonb(o.label))::json
            WHERE client_id = f.client_id
              AND json_typeof(custom_values -> f.key) = 'number'
              AND (custom_values ->> f.key)::numeric = o.id;
        END LOOP;
    END LOOP;
END $$;

UPDATE lead_fields AS lf SET options = plain.options
FROM (
    SELECT f.id, json_agg(e.value ->> 'label' ORDER BY e.ord) AS options
    FROM lead_fields AS f, json_array_elements(f.options) WITH ORDINALITY AS e(value, ord)
    GROUP BY f.id
) AS plain
WHERE lf.id = plain.id;

ALTER TABLE lead_fields DROP CONSTRAINT uq_lead_fields_client_code;
ALTER TABLE lead_fields DROP COLUMN code;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    # contract: reviewed. The previous release reads options as labels and a
    # lead's choice as its label, so both are written back that way; only the
    # numbers themselves are lost, and a prompt that cites one no longer
    # resolves until the upgrade hands the same numbers out again.
    op.get_bind().exec_driver_sql(DOWNGRADE)
