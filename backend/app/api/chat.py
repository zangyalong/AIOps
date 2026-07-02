from __future__ import annotations

from fastapi import APIRouter

from app.agents.chat_agent import chat as run_chat
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(data: ChatRequest) -> ChatResponse:
    reply = await run_chat(data.message)
    return ChatResponse(reply=reply)
