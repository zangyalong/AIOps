from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.dependencies import DBSession
from app.schemas.chat import ChatRequest
from app.services import chat_service, conversation_service

router = APIRouter(tags=["chat"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(data: ChatRequest, request: Request, db: DBSession) -> StreamingResponse:
    new_title: str | None = None

    if data.conversation_id is None:
        new_title = conversation_service.make_title(data.message)
        conversation = await conversation_service.create_conversation(db, title=new_title)
    else:
        conversation = await conversation_service.get_conversation(db, data.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

    graph = request.app.state.chat_graph

    async def event_stream() -> AsyncIterator[str]:
        async for event in chat_service.stream_chat_turn(
            graph, db, conversation.id, data.message, new_title=new_title
        ):
            yield _sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
