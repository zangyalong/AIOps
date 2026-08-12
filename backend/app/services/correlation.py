"""告警关联/归组规则引擎（方向二·任务①）。纯确定性，不用 LLM。

用 CEL 表达式写「匹配条件」（这条规则管哪些告警），group_by 指定按哪些标签归组。新告警进来时
按规则算出 group_key——同 group_key 的告警属于同一个「事件」，只触发一次自动诊断，避免同一台机
一批告警各查一遍。参考 survey 启发#1：业界最成熟的 Keep 这一层就是纯 CEL 规则，快 / 可解释 / 好测，
让 LLM 判断"这两条是不是一个事儿"反而是杀鸡用牛刀。

规则按顺序匹配，命中第一条就用它；都不命中则不参与关联（group_key=None，独立成组照常诊断）。
"""

from __future__ import annotations

import celpy

# 归组规则。match 是 CEL 表达式（对告警的 labels 求值），group_by 是归组用的标签键。
# 想加规则就往这个列表里加——这就是"规则引擎"，改规则不用改代码逻辑。
_RULES = [
    {
        "name": "by-instance",
        # 带 instance 的 critical/warning 告警，按实例归组（同一台机的这类告警算一个事件）
        "match": 'has(labels.instance) && labels.severity in ["critical", "warning"]',
        # 同一台机上「同一种」告警（含标签变体、重复推送）算一个事件、只诊断一次；
        # 「不同种」告警（CPU 高 vs 磁盘满）各成一个事件、各自诊断——避免不同问题被吞掉。
        "group_by": ["instance", "alertname"],
    },
]

# 预编译（编译一次，反复用）。每项：(规则名, 已编译 CEL 程序, group_by 标签列表)
_env = celpy.Environment()
_COMPILED = [
    (r["name"], _env.program(_env.compile(r["match"])), r["group_by"]) for r in _RULES
]


def compute_group_key(labels: dict | None) -> str | None:
    """按归组规则算这条告警的 group_key。

    命中第一条规则就返回 "规则名:标签=值,..."（如 "by-instance:instance=node-exporter:9100"）；
    没有规则命中返回 None（不参与关联，独立成组、照常单独诊断）。
    """
    labels = labels or {}
    activation = {"labels": celpy.json_to_cel(labels)}
    for name, prog, group_by in _COMPILED:
        try:
            matched = bool(prog.evaluate(activation))
        except celpy.CELEvalError:
            matched = (
                False  # 表达式对这条告警求值失败（如引用了不存在的字段），视为不匹配
            )
        if matched:
            parts = ",".join(f"{k}={labels.get(k, '')}" for k in group_by)
            return f"{name}:{parts}"
    return None
