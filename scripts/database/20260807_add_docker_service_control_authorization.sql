-- 将服务控制授权从仅 systemd 扩展为 systemd 与 Docker 共用。
ALTER TABLE service_instances
    DROP CONSTRAINT IF EXISTS ck_service_instances_control_systemd;

ALTER TABLE service_instances
    DROP CONSTRAINT IF EXISTS ck_service_instances_control_runtime;

ALTER TABLE service_instances
    ADD CONSTRAINT ck_service_instances_control_runtime
    CHECK (
        control_enabled = false
        OR runtime_type IN ('systemd', 'docker')
    );
