-- 给已有 AIOMS PostgreSQL 数据库补齐服务运行时身份。
-- 新数据库会由 SQLAlchemy create_all() 直接创建这些列；已有表必须手动执行本文件。
-- 执行方式：psql -d aioms -v ON_ERROR_STOP=1 -f scripts/database/20260731_add_service_runtime_identity.sql

BEGIN;

ALTER TABLE service_instances
    ADD COLUMN IF NOT EXISTS runtime_type VARCHAR(20);

ALTER TABLE service_instances
    ADD COLUMN IF NOT EXISTS runtime_name VARCHAR(200);

-- 旧版发现注册生成的实例名格式为：
--   <server.name>:<runtime_type>:<runtime_name>
-- 这里只回填能明确识别为 systemd/docker/process 的记录；手工实例保持 NULL，等待人工补充。
WITH legacy_runtime AS (
    SELECT
        si.id,
        substring(si.name FROM char_length(s.name) + 2) AS runtime_identity
    FROM service_instances AS si
    JOIN servers AS s ON s.id = si.server_id
    WHERE si.runtime_type IS NULL
      AND si.runtime_name IS NULL
      AND si.name LIKE s.name || ':%:%'
),
parsed_runtime AS (
    SELECT
        id,
        split_part(runtime_identity, ':', 1) AS runtime_type,
        substring(
            runtime_identity
            FROM char_length(split_part(runtime_identity, ':', 1)) + 2
        ) AS runtime_name
    FROM legacy_runtime
)
UPDATE service_instances AS si
SET
    runtime_type = parsed_runtime.runtime_type,
    runtime_name = parsed_runtime.runtime_name
FROM parsed_runtime
WHERE si.id = parsed_runtime.id
  AND parsed_runtime.runtime_type IN ('systemd', 'docker', 'process')
  AND parsed_runtime.runtime_name <> '';

-- 迁移期继续给现有 AIOMS 日志查询提供 service_name 映射。
UPDATE service_instances AS si
SET log_service = s.name
FROM services AS s
WHERE s.id = si.service_id
  AND si.log_service IS NULL
  AND si.runtime_type IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_service_instances_runtime_type'
    ) THEN
        ALTER TABLE service_instances
            ADD CONSTRAINT ck_service_instances_runtime_type
            CHECK (
                runtime_type IS NULL
                OR runtime_type IN ('systemd', 'docker', 'process')
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_service_instances_server_runtime'
    ) THEN
        ALTER TABLE service_instances
            ADD CONSTRAINT uq_service_instances_server_runtime
            UNIQUE (server_id, runtime_type, runtime_name);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_service_instances_runtime_type
    ON service_instances (runtime_type);

CREATE INDEX IF NOT EXISTS ix_service_instances_runtime_name
    ON service_instances (runtime_name);

COMMIT;
