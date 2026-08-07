-- 单一非 root AIOMS SSH 身份已统一使用 ssh_user / ssh_key_filename。
-- 仅在确认新单角色配置可用、无需回退旧双角色方案后手工执行。
-- 执行：psql -d aioms -v ON_ERROR_STOP=1 -f scripts/database/20260806_remove_server_deploy_ssh_identity.sql

BEGIN;

ALTER TABLE servers
    DROP COLUMN IF EXISTS deploy_ssh_user,
    DROP COLUMN IF EXISTS deploy_ssh_key_filename;

COMMIT;
