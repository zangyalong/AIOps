from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_rule import AlertRule


async def list_all(db: AsyncSession) -> list[AlertRule]:
    result = await db.scalars(select(AlertRule).order_by(AlertRule.name))
    return list(result)


async def get(db: AsyncSession, rule_id: uuid.UUID) -> AlertRule | None:
    return await db.get(AlertRule, rule_id)


async def get_by_name(db: AsyncSession, name: str) -> AlertRule | None:
    return await db.scalar(select(AlertRule).where(AlertRule.name == name))


async def create(db: AsyncSession, rule: AlertRule) -> AlertRule:
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def save(db: AsyncSession, rule: AlertRule) -> AlertRule:
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete(db: AsyncSession, rule: AlertRule) -> None:
    await db.delete(rule)
    await db.commit()
