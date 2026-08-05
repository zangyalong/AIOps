-- 给已有 AIOMS PostgreSQL 数据库增加目标服务器环境部署配置。
-- 只保存组件开关、镜像、地址和网络等非敏感参数；数据库密码不落库。
-- 新数据库由 SQLAlchemy create_all() 自动创建该列，只有已有 servers 表需要执行本文件。

BEGIN;

ALTER TABLE servers
    ADD COLUMN IF NOT EXISTS deployment_config JSONB;

COMMIT;
