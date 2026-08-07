ALTER TABLE service_instances
    ADD COLUMN IF NOT EXISTS control_enabled boolean NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_service_instances_control_systemd'
    ) THEN
        ALTER TABLE service_instances
            ADD CONSTRAINT ck_service_instances_control_systemd
            CHECK (control_enabled = false OR runtime_type = 'systemd');
    END IF;
END
$$;
