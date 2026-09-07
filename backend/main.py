"""FastAPI backend: participant sessions, canvas records, OpenAI rationale labels."""

import hmac
import json
import logging
import math
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
- each visible text is AT MOST 10 words, specific to THIS input
- kind is internal only (do not put kind words into text)
- no paragraphs, no chatbot tone, no greetings, no markdown
- kind must be one of the listed values
- match the language of the input
- if target is "relation", labels should name why two ideas are linked
- if target is "note", labels should name the idea's design rationale

The user message may list labels the designer already has, as "ref | text".
Before writing a label, check that list. If one of them already expresses the SAME
reasoning (same point in different words, or in another language), do not invent a
new label: return {"same_as":"<ref>","phrasing":"<how you would have put it>"} instead.
Only reuse a ref when the reasoning genuinely matches; related-but-different reasons
stay separate. Never return the same ref twice in one response.
"""
MEANING_SYSTEM_PROMPT = """You decide whether a label names the SAME reason as other existing labels (AI or human).

Return JSON only:
{"matches":["<ref>",...]}

Treat paraphrase as a match: same intent in different words
(e.g. "allow input non-clear reason" vs "Allow vague inputs for user comfort").
Include a ref when both labels point at the same design reason, even if nouns differ.
Exclude only when the reasons are actually different claims.
Do not rewrite either label. Never invent refs.
"""
SHORTEN_SYSTEM_PROMPT = """You shorten a designer's own label for a small chip.

Return JSON only:
{"text":"..."}

Rules:
- keep THEIR words and order; only drop filler or repeated words
- do not add synonyms, new claims, or words they did not write
- at most 10 words
- same language as the input
- if it is already short enough, return it unchanged
"""
MAX_KNOWN_LABELS = 40  # caps prompt size; the newest labels are the ones worth matching
MIN_RATIONALE_CHARS = 1
OPENAI_TIMEOUT_S = 30.0
MAX_LABEL_WORDS = 10
EMBED_DIM = 256  # text-embedding-3-small matryoshka; small enough to persist on the canvas
MEANING_TOP_K = 3  # LLM only sees cosine neighbours, never the whole pool
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


class KnownLabel(BaseModel):
    """A label the designer already has, offered to the model so it can reuse instead of repeat."""

    ref: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=200)


class RationaleRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    target: str = Field(default="generic")  # generic | note | relation
    known: list[KnownLabel] = Field(default_factory=list, max_length=200)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=32)


class MeaningRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    candidates: list[KnownLabel] = Field(min_length=1, max_length=MEANING_TOP_K)


class ShortenRequest(BaseModel):
    text: str = Field(min_length=1, max_length=400)


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


def parse_label_payload(raw: str, known_refs: set[str] | None = None) -> list[dict]:
    """Parse model JSON into [{text, kind}, ...] plus {same_as, phrasing, kind} reuse hits.

    Strips markdown fences if present. A same_as pointing at a ref we never sent is a
    model slip, so it falls back to the phrasing as a plain new label.
    """
    refs = known_refs or set()
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
    seen_refs = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "insight")).strip().lower()
        if kind not in LABEL_KINDS:
            kind = "insight"
        label_text = str(item.get("text", "")).strip()
        same_as = str(item.get("same_as", "")).strip()
        phrasing = clip_words(str(item.get("phrasing", "")).strip())

        if same_as and same_as in refs and same_as not in seen_refs:
            seen_refs.add(same_as)
            labels.append({"same_as": same_as, "phrasing": phrasing, "kind": kind})
            continue
        if not label_text:
            label_text = phrasing  # unknown ref: keep the reasoning as a fresh label
        if not label_text:
            continue
        labels.append({"text": clip_words(label_text), "kind": kind})
    if not labels:
        raise api_error(502, "empty_labels", "The AI returned no labels. Try rephrasing and generate again.")
    return labels[:6]


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


async def embed_texts(client: AsyncOpenAI, texts: list[str]) -> list[list[float]]:
    """One embeddings call for the whole batch. Insert cost, not query cost."""
    model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    response = await client.embeddings.create(
        model=model,
        input=texts,
        dimensions=EMBED_DIM,
    )
    by_index = {item.index: item.embedding for item in response.data}
    return [l2_normalize(by_index[i]) for i in range(len(texts))]


async def attach_embeddings(client: AsyncOpenAI, labels: list[dict]) -> list[dict]:
    texts = [item["text"] for item in labels if item.get("text")]
    if not texts:
        return labels
    try:
        vectors = await embed_texts(client, texts)
    except Exception as exc:
        log.warning("embed_failed: %s", type(exc).__name__)
        return labels
    i = 0
    for item in labels:
        if not item.get("text"):
            continue
        item["embedding"] = vectors[i]
        i += 1
    return labels


@app.post("/api/embeddings")
async def embeddings(req: EmbedRequest):
    """Index texts: one OpenAI call for the batch, vectors stored by the client."""
    texts = [item.strip() for item in req.texts if item.strip()]
    if not texts:
        raise api_error(400, "invalid_input", "Nothing to embed.")
    client = get_client()
    try:
        vectors = await embed_texts(client, texts)
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc
    return {"vectors": vectors, "model": os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")}


@app.post("/api/label-meaning")
async def label_meaning(req: MeaningRequest):
    """Confirm whether query shares meaning with cosine neighbours only (k <= 3)."""
    query = req.query.strip()
    candidates = [item for item in req.candidates if item.ref and item.text.strip()][:MEANING_TOP_K]
    if not query or not candidates:
        return {"matches": []}

    listing = "\n".join(f"{item.ref} | {item.text.strip()}" for item in candidates)
    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MEANING_SYSTEM_PROMPT},
                {"role": "user", "content": f"new or edited label:\n{query}\n\nexisting labels:\n{listing}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=80,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    raw = (response.choices[0].message.content or "").strip()
    allowed = {item.ref for item in candidates}
    try:
        data = json.loads(raw)
        hits = data.get("matches") if isinstance(data, dict) else data
        if not isinstance(hits, list):
            hits = []
    except json.JSONDecodeError:
        hits = []
    matches = [str(item) for item in hits if str(item) in allowed]
    return {"matches": matches, "model": model}


def label_tokens(text: str) -> list[str]:
    return [part.lower() for part in re.split(r"\s+", text.strip()) if part]


def kept_wording(source: str, suggestion: str) -> bool:
    """Suggestion must be a shorter subsequence of the writer's own words."""
    src = label_tokens(source)
    sug = label_tokens(suggestion)
    if not sug or len(sug) >= len(src):
        return False
    i = 0
    for token in sug:
        while i < len(src) and src[i] != token:
            i += 1
        if i >= len(src):
            return False
        i += 1
    return True


@app.post("/api/shorten-label")
async def shorten_label(req: ShortenRequest):
    """Compress a human label using only the writer's words. Never auto-applies."""
    source = " ".join(part for part in re.split(r"\s+", req.text.strip()) if part)
    if not source:
        return {"text": ""}
    if len(label_tokens(source)) <= MAX_LABEL_WORDS:
        return {"text": ""}

    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SHORTEN_SYSTEM_PROMPT},
                {"role": "user", "content": source},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=60,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
        suggestion = clip_words(str(data.get("text", "") if isinstance(data, dict) else ""))
    except json.JSONDecodeError:
        suggestion = ""
    if not kept_wording(source, suggestion):
        suggestion = ""
    return {"text": suggestion, "model": model}


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

    known = req.known[-MAX_KNOWN_LABELS:]
    known_refs = {item.ref for item in known}
    user_content = f"target: {target}\n\n{text}"
    if known:
        listing = "\n".join(f"{item.ref} | {item.text}" for item in known)
        user_content = f"{user_content}\n\nlabels the designer already has:\n{listing}"

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

    labels = parse_label_payload(content, known_refs)
    labels = await attach_embeddings(client, labels)
    return {"labels": labels, "model": model}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
