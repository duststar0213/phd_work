"""FastAPI backend: participant sessions, canvas records, OpenAI rationale labels."""

import hmac
import json
import logging
import os
import re
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
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

import auth
import db

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
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STUDY_CAP = int(os.getenv("STUDY_CAP", str(db.STUDY_CAP)) or db.STUDY_CAP)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="PhD Work API", lifespan=lifespan)


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
async def validation_handler(_request, _exc: RequestValidationError):
    """Turn 422 validation into the same {code, message} shape the UI expects."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "code": "invalid_input",
                "message": "Check the form and try again.",
            }
        },
    )



class ChatMessage(BaseModel):
    role: str
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


class GateRequest(BaseModel):
    access: str = Field(min_length=1, max_length=64)


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=120)


class LoginRequest(EmailRequest):
    password: str = Field(min_length=1, max_length=128)


class CanvasRequest(BaseModel):
    notes: list = Field(default_factory=list)
    connections: list = Field(default_factory=list)
    pan: dict = Field(default_factory=dict)
    nextId: int = 1
    nextConnId: int = 1
    scale: float = Field(default=1, ge=0.1, le=4)


class EventRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


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


def cookie_token(repertoire_session: str | None = Cookie(default=None)) -> str | None:
    return repertoire_session


def study_access_code() -> str:
    return os.getenv("STUDY_ACCESS_CODE", "").strip()


def require_study_access(given: str) -> None:
    expected = study_access_code()
    if not expected:
        return
    got = (given or "").strip().casefold()
    expected = expected.casefold()
    if len(got) != len(expected) or not hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")):
        raise api_error(401, "bad_access", "That study access code is not right.")


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email):
        raise api_error(400, "invalid_email", "Please enter a valid email address.")
    return email


def require_participant(token: str | None = Depends(cookie_token)):
    person = db.participant_for_token(token or "")
    if not person:
        raise api_error(401, "auth_required", "Please sign in with your email.")
    db.touch_participant(person["id"])
    return person


def cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=db.SESSION_DAYS * 86400,
        path="/",
        secure=cookie_secure(),
    )


def set_gate_cookie(response: Response) -> None:
    expected = study_access_code()
    if not expected:
        return
    response.set_cookie(
        key=auth.GATE_COOKIE,
        value=auth.gate_token(expected),
        httponly=True,
        samesite="lax",
        max_age=db.SESSION_DAYS * 86400,
        path="/",
        secure=cookie_secure(),
    )


def gate_unlocked(repertoire_gate: str | None) -> bool:
    expected = study_access_code()
    if not expected:
        return True
    want = auth.gate_token(expected)
    got = (repertoire_gate or "").strip()
    return len(got) == len(want) and hmac.compare_digest(got, want)


def require_gate(repertoire_gate: str | None = Cookie(default=None)):
    if not gate_unlocked(repertoire_gate):
        raise api_error(401, "gate_required", "Enter the invitation code first.")


@app.get("/api/auth/config")
def auth_config(repertoire_gate: str | None = Cookie(default=None)):
    return {
        "access_required": bool(study_access_code()),
        "gate_unlocked": gate_unlocked(repertoire_gate),
        "invite_only": db.invited_emails() is not None,
    }


@app.post("/api/auth/gate")
def open_gate(req: GateRequest, response: Response):
    """Door 1: invitation code. Unlocks the login screen."""
    require_study_access(req.access)
    if not study_access_code():
        raise api_error(401, "bad_access", "That study access code is not right.")
    set_gate_cookie(response)
    return {"ok": True}


def admit_email(raw_email: str, *, create: bool):
    """Allowlist, then load or create the participant row. Invitation code is door 1 (cookie)."""
    email = normalize_email(raw_email)
    try:
        db.require_invited(email)
        if create:
            person = db.get_or_create_participant(email, cap=STUDY_CAP)
        else:
            person = db.get_participant_by_username(email)
            if not person:
                raise api_error(401, "unknown_email", "No study space for this email yet. Start from the email step.")
    except db.NotInvitedError:
        raise api_error(
            403,
            "not_invited",
            "This email is not on the study list. Use the address from your invitation.",
        ) from None
    except db.StudyFullError:
        raise api_error(
            403,
            "study_full",
            "This study is full. If you already joined, use the same email.",
        ) from None
    return email, person


def open_session(person, response: Response, event_type: str) -> None:
    db.delete_sessions_for(person["id"])
    token = auth.new_token()
    db.create_session(person["id"], token)
    db.touch_participant(person["id"])
    db.add_event(person["id"], event_type, {})
    set_session_cookie(response, token)


def issue_password(person) -> str:
    password = auth.random_password()
    db.set_password(person["id"], auth.hash_otp(password))
    return password


@app.post("/api/auth/start")
def start(req: EmailRequest, response: Response, _gate=Depends(require_gate)):
    """New email → create a space and show a password once. Existing email → ask for password."""
    email, person = admit_email(req.email, create=True)
    if db.has_password(person):
        return {"email": email, "is_new": False}
    password = issue_password(person)
    open_session(person, response, "register")
    return {"email": email, "is_new": True, "password": password}


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response, _gate=Depends(require_gate)):
    _email, person = admit_email(req.email, create=False)
    if not db.has_password(person) or not auth.verify_otp(req.password.strip(), person["password_hash"]):
        raise api_error(401, "bad_credentials", "That password is not right.")
    open_session(person, response, "login")
    return {"email": person["username"]}


@app.post("/api/auth/reset")
def reset(req: EmailRequest, response: Response, _gate=Depends(require_gate)):
    """Forgot password: issue a new one. Door 1 (invitation code) must already be open."""
    email, person = admit_email(req.email, create=False)
    password = issue_password(person)
    open_session(person, response, "password_reset")
    return {"email": email, "password": password}


@app.post("/api/auth/logout")
def logout(response: Response, token: str | None = Depends(cookie_token)):
    person = db.participant_for_token(token or "")
    if person:
        db.add_event(person["id"], "logout", {})
    if token:
        db.delete_session(token)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(person=Depends(require_participant)):
    return {"email": person["username"]}


@app.get("/api/canvas")
def get_canvas(person=Depends(require_participant)):
    data = db.load_canvas(person["id"])
    if not data:
        return {"notes": [], "connections": [], "pan": {"x": 0, "y": 0}, "nextId": 1, "nextConnId": 1, "scale": 1}
    return data


@app.put("/api/canvas")
def put_canvas(req: CanvasRequest, person=Depends(require_participant)):
    payload = {
        "notes": req.notes,
        "connections": req.connections,
        "pan": req.pan,
        "nextId": req.nextId,
        "nextConnId": req.nextConnId,
        "scale": req.scale,
    }
    db.save_canvas(person["id"], payload)
    return {"ok": True, "updated_at": db.utc_now()}


@app.post("/api/events")
def post_event(req: EventRequest, person=Depends(require_participant)):
    db.add_event(person["id"], req.type.strip()[:64], req.payload)
    return {"ok": True}


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
