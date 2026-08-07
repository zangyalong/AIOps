-- 仅在已经启动过阶段四、operation_records 表已存在时需要执行。
-- 尚未创建该表的本地环境直接重启后端即可由 SQLAlchemy 按最新模型建表。
ALTER TABLE operation_records
    ADD COLUMN IF NOT EXISTS actor_id VARCHAR(200) NOT NULL DEFAULT 'legacy-local-user',
    ADD COLUMN IF NOT EXISTS actor_role VARCHAR(20) NOT NULL DEFAULT 'admin',
    ADD COLUMN IF NOT EXISTS run_mode VARCHAR(20) NOT NULL DEFAULT 'interactive',
    ADD COLUMN IF NOT EXISTS environment VARCHAR(30) NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS policy_reason TEXT,
    ADD COLUMN IF NOT EXISTS approver_id VARCHAR(200),
    ADD COLUMN IF NOT EXISTS approver_role VARCHAR(20);

ALTER TABLE operation_records
    DROP CONSTRAINT IF EXISTS ck_operation_records_actor_role,
    ADD CONSTRAINT ck_operation_records_actor_role
        CHECK (actor_role IN ('viewer', 'operator', 'approver', 'admin', 'system')),
    DROP CONSTRAINT IF EXISTS ck_operation_records_run_mode,
    ADD CONSTRAINT ck_operation_records_run_mode
        CHECK (run_mode IN ('interactive', 'unattended')),
    DROP CONSTRAINT IF EXISTS ck_operation_records_environment,
    ADD CONSTRAINT ck_operation_records_environment
        CHECK (environment IN ('production', 'non_production', 'unknown')),
    DROP CONSTRAINT IF EXISTS ck_operation_records_approver_role,
    ADD CONSTRAINT ck_operation_records_approver_role
        CHECK (
            approver_role IS NULL OR
            approver_role IN ('viewer', 'operator', 'approver', 'admin', 'system')
        );
