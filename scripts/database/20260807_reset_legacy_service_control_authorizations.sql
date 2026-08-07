-- 新策略网关上线前打开的开关没有可靠的目标服务器同步记录。
-- 统一回到最安全的关闭状态，由用户在网关安装后重新明确授权。
UPDATE service_instances
SET control_enabled = false,
    updated_at = now()
WHERE control_enabled = true;
