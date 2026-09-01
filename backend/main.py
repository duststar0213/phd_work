"""FastAPI backend: OpenAI chat for the unused ChatPanel. Canvas UI does not call this yet."""

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

load_dotenv()

ALLOWED_ROLES = {"system", "user", "assistant"}
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant for an interactive research prototype."
)

app = FastAPI(title="PhD Work API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


def get_client() -> AsyncOpenAI:
    """Build an OpenAI client from backend/.env (OPENAI_API_KEY)."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-your-key"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not set. Copy backend/.env.example to backend/.env.",
        )
    return AsyncOpenAI(api_key=api_key)


@app.get("/api/health")
async def health():
    """Liveness check; also reports whether an API key is configured."""
    has_key = bool(os.getenv("OPENAI_API_KEY")) and not os.getenv(
        "OPENAI_API_KEY", ""
    ).startswith("sk-your-key")
    return {"status": "ok", "openai_configured": has_key}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Forward a message list to OpenAI chat completions."""
    for message in req.messages:
        if message.role not in ALLOWED_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role '{message.role}'. Use system, user, or assistant.",
            )

    payload = [message.model_dump() for message in req.messages]
    if not any(message["role"] == "system" for message in payload):
        payload.insert(0, {"role": "system", "content": DEFAULT_SYSTEM_PROMPT})

    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    content = response.choices[0].message.content
    if not content:
        raise HTTPException(status_code=502, detail="Empty response from OpenAI.")

    return {"content": content, "model": model}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
