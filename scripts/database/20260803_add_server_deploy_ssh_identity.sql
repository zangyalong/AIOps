-- 给已有 AIOMS PostgreSQL 数据库增加服务器部署 SSH 身份。
-- 新数据库由 SQLAlchemy create_all() 直接创建；已有 servers 表执行本文件补列。
-- 执行：psql -d aioms -v ON_ERROR_STOP=1 -f scripts/database/20260803_add_server_deploy_ssh_identity.sql

BEGIN;

ALTER TABLE servers
    ADD COLUMN IF NOT EXISTS deploy_ssh_user VARCHAR(50);

ALTER TABLE servers
    ADD COLUMN IF NOT EXISTS deploy_ssh_key_filename VARCHAR(255);

COMMIT;
