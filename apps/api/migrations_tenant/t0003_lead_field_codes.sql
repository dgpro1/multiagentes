-- Tenant revision t0003: lead_fields.code and coded select options, added to
-- the central schema by migration 0080_lead_field_codes. The statements are
-- the same ones that migration runs, so a client's own database ends up with
-- the numbers the central one would have given it.

ALTER TABLE lead_fields ADD COLUMN code INTEGER;

-- Each client numbers from 1000: a field, then its options, then the next field.
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

-- A lead's choice was stored as the option's label; it is now the option's code.
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
