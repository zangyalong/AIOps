from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from langchain_core.messages import HumanMessage

from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(data: ChatRequest, request: Request) -> ChatResponse:
    thread_id = data.thread_id or str(uuid.uuid4())
    graph = request.app.state.chat_graph

    result = await graph.ainvoke(
        {"messages": [HumanMessage(data.message)]},
        {"configurable": {"thread_id": thread_id}},
    )

    return ChatResponse(reply=str(result["messages"][-1].content), thread_id=thread_id)
