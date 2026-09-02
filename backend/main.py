"""FastAPI backend: OpenAI for rationale labels (and unused ChatPanel)."""

import json
import logging
import os
import re

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, Field

load_dotenv()

log = logging.getLogger("repertoire")

ALLOWED_ROLES = {"system", "user", "assistant"}
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant for an interactive research prototype."
)
LABEL_KINDS = ("assumption", "constraint", "goal", "tension", "insight", "question")
LABEL_SYSTEM_PROMPT = """You turn a designer's rationale into short labels for a sticky-note canvas.

Return JSON only:
{"labels":[{"text":"...","kind":"assumption|constraint|goal|tension|insight|question"}]}

Rules:
- 3 to 6 labels
- each visible text is AT MOST 3 words, specific to THIS input
- kind is internal only (do not put kind words into text)
- no paragraphs, no chatbot tone, no greetings, no markdown
- kind must be one of the listed values
- match the language of the input
- if target is "relation", labels should name why two ideas are linked
- if target is "note", labels should name the idea's design rationale
"""
MIN_RATIONALE_CHARS = 8
OPENAI_TIMEOUT_S = 30.0
MAX_LABEL_WORDS = 3

app = FastAPI(title="PhD Work API")


def cors_origins() -> list[str]:
    """Local Vite ports plus optional FRONTEND_ORIGINS (comma-separated) for a public host."""
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]
    extra = os.getenv("FRONTEND_ORIGINS", "")
    origins.extend(item.strip() for item in extra.split(",") if item.strip())
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def api_error(status: int, code: str, message: str) -> HTTPException:
    """Public error body: { code, message }. Never include secrets."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, _exc: RequestValidationError):
    """Turn 422 validation into the same {code, message} shape the UI expects."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "code": "invalid_input",
                "message": "Type a short rationale first, then generate labels.",
            }
        },
    )



class ChatMessage(BaseModel):
    role: str
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


class RationaleRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    target: str = Field(default="generic")  # generic | note | relation


def get_client() -> AsyncOpenAI:
    """Build an OpenAI client from OPENAI_API_KEY. Raises a public error if missing."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-your-key"):
        raise api_error(
            503,
            "missing_key",
            "Label generation isn't set up on this server yet. Try again later.",
        )
    return AsyncOpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT_S)


def openai_http_error(exc: Exception) -> HTTPException:
    """Map OpenAI SDK errors to visitor-facing reminders (no raw SDK text)."""
    if isinstance(exc, AuthenticationError):
        return api_error(
            502,
            "invalid_key",
            "The AI key on this server is invalid. Ask the host to check it.",
        )
    if isinstance(exc, RateLimitError):
        return api_error(
            429,
            "rate_limit",
            "Too many requests. Wait a few seconds and try again.",
        )
    if isinstance(exc, APITimeoutError):
        return api_error(
            504,
            "timeout",
            "The AI took too long. Check your connection and try again.",
        )
    if isinstance(exc, APIConnectionError):
        return api_error(
            503,
            "upstream_offline",
            "Can't reach the AI service right now. Try again in a moment.",
        )
    if isinstance(exc, APIStatusError) and exc.status_code == 429:
        return api_error(
            429,
            "rate_limit",
            "Too many requests. Wait a few seconds and try again.",
        )
    log.warning("openai_call_failed: %s", type(exc).__name__)
    return api_error(
        502,
        "upstream_error",
        "Couldn't generate labels. Try again.",
    )


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
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    content = response.choices[0].message.content
    if not content:
        raise HTTPException(status_code=502, detail="Empty response from OpenAI.")

    return {"content": content, "model": model}


def clip_words(text: str, max_words: int = MAX_LABEL_WORDS) -> str:
    """Keep at most max_words so tags stay short."""
    parts = [part for part in re.split(r"\s+", text.strip()) if part]
    return " ".join(parts[:max_words])


def parse_label_payload(raw: str) -> list[dict]:
    """Parse model JSON into [{text, kind}, ...]. Strips markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise api_error(502, "bad_model_json", "The AI returned no usable labels. Try rephrasing.") from exc

    items = data.get("labels") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise api_error(502, "empty_labels", "The AI returned no labels. Try rephrasing and generate again.")

    labels = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label_text = str(item.get("text", "")).strip()
        kind = str(item.get("kind", "insight")).strip().lower()
        if not label_text:
            continue
        if kind not in LABEL_KINDS:
            kind = "insight"
        labels.append({"text": clip_words(label_text), "kind": kind})
    if not labels:
        raise api_error(502, "empty_labels", "The AI returned no labels. Try rephrasing and generate again.")
    return labels[:6]


@app.post("/api/rationale-labels")
async def rationale_labels(req: RationaleRequest):
    """Turn a rationale paragraph into short labels (not a chatbot reply)."""
    text = req.text.strip()
    if len(text) < MIN_RATIONALE_CHARS:
        raise api_error(
            400,
            "too_short",
            "Write a bit more rationale so the labels can be specific.",
        )

    target = req.target if req.target in {"generic", "note", "relation"} else "generic"
    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    user_content = f"target: {target}\n\n{text}"

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": LABEL_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=300,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    content = response.choices[0].message.content
    if not content:
        raise api_error(502, "empty_labels", "The AI returned no labels. Try rephrasing and generate again.")

    return {"labels": parse_label_payload(content), "model": model}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
