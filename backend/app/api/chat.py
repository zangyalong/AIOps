from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.dependencies import DBSession
from app.schemas.chat import ChatRequest, ChatRunOut, ResumeApprovalRequest
from app.services import (
    chat_run_record_service,
    chat_service,
    conversation_service,
    server_service,
)
from app.services.chat_run_service import ChatRun, ChatRunManager

router = APIRouter(tags=["chat"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _manager(request: Request) -> ChatRunManager:
    return request.app.state.chat_run_manager


def _run_out(run: ChatRun, *, title: str | None = None) -> ChatRunOut:
    return ChatRunOut(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        title=title,
    )


@router.post("/chat", response_model=ChatRunOut, status_code=status.HTTP_202_ACCEPTED)
async def chat(data: ChatRequest, request: Request, db: DBSession) -> ChatRunOut:
    """创建独立后台 Chat Run；返回后浏览器即可订阅事件，断开不会取消 Agent。"""
    new_title: str | None = None
    default_server = None
    if data.default_server_id is not None:
        default_server = await server_service.get_server(db, data.default_server_id)
        if default_server is None:
            raise HTTPException(
                status_code=422, detail="选择的默认服务器不存在或已删除"
            )

    if data.conversation_id is None:
        new_title = conversation_service.make_title(data.message)
        conversation = await conversation_service.create_conversation(
            db, title=new_title
        )
    else:
        conversation = await conversation_service.get_conversation(
            db, data.conversation_id
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

    graph = request.app.state.chat_graph
    manager = _manager(request)
    if manager.get_active(conversation.id) is not None:
        raise HTTPException(status_code=409, detail="该对话已有回复正在生成")

    prepared = await chat_service.prepare_chat_turn(
        conversation.id,
        data.message,
        default_server=default_server,
    )
    try:
        run = manager.start(
            conversation.id,
            lambda: chat_service.run_prepared_chat_turn(
                graph,
                conversation.id,
                prepared,
                new_title=new_title,
            ),
            run_id=str(prepared.run_id),
        )
    except ValueError as exc:
        chat_run = await chat_run_record_service.get_chat_run(db, prepared.run_id)
        if chat_run is not None:
            await chat_run_record_service.set_status(
                db,
                chat_run,
                "failed",
                error_message=str(exc),
            )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_out(run, title=new_title)


@router.get("/chat/runs/active/{conversation_id}", response_model=ChatRunOut)
async def active_chat_run(conversation_id: uuid.UUID, request: Request) -> ChatRunOut:
    run = _manager(request).get_active(conversation_id)
    if run is None:
        raise HTTPException(status_code=404, detail="active chat run not found")
    return _run_out(run)


@router.get("/chat/runs/{run_id}/events")
async def chat_run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    manager = _manager(request)
    if manager.get(run_id) is None:
        raise HTTPException(status_code=404, detail="chat run not found")

    async def event_stream() -> AsyncIterator[str]:
        async for event in manager.subscribe(run_id, after=after):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/chat/resume", response_model=ChatRunOut, status_code=status.HTTP_202_ACCEPTED
)
async def chat_resume(
    data: ResumeApprovalRequest, request: Request, db: DBSession
) -> ChatRunOut:
    """恢复一次挂在写操作审批门上的对话：带回 thread_id + 用户裁决，从挂起点接着把这轮跑完。

    恢复仍使用原 run_id；接口返回后，前端从原事件序号继续订阅该 Chat Run。
    """
    conversation = await conversation_service.get_conversation(db, data.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    manager = _manager(request)
    run = manager.get(data.run_id)
    if run is None or run.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="chat run not found")

    graph = request.app.state.chat_graph
    try:
        run = await manager.resume(
            data.run_id,
            thread_id=data.thread_id,
            decisions=[item.model_dump(mode="json") for item in data.decisions],
            producer=lambda: chat_service.resume_chat_turn(
                graph,
                conversation.id,
                uuid.UUID(run.id),
                data.thread_id,
                data.decisions,
            ),
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_out(run)
