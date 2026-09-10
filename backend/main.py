"""FastAPI backend: participant sessions, canvas records, OpenAI rationale labels."""

import hmac
import json
import logging
import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

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

load_dotenv(Path(__file__).resolve().parent / ".env")

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
- if target is "group", labels should name why these ideas belong together as one group
- Read the sticky-note idea FIRST: if it already contains a why (reason, constraint, who it is for, why it matters), use that.
- Then combine with the reflection field. If reflection is empty, label only from the idea's own why.
- If the idea is only a proposal with no why, label from the reflection field.
- Do not invent reasons that are not in the idea or the reflection.

The user message may list labels the designer already has, as "ref | text".
Before writing a label, check that list. If one of them already expresses the SAME
reasoning (same point in different words, or in another language), do not invent a
new label: return {"same_as":"<ref>","phrasing":"<how you would have put it>"} instead.
Only reuse a ref when the reasoning genuinely matches; related-but-different reasons
stay separate. Never return the same ref twice in one response.

The user message may also quote the reflection those labels were made from. People keep
writing on top of what they wrote before, so read that quoted part as background only and
label the reasoning the designer has ADDED since. Judge the addition on its own: a new
clause is worth a label even when it sits in a long paragraph you have already labelled.
Return an empty labels list only when the addition truly carries no reasoning of its own.
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
SUGGEST_LINKS_PROMPT = """You suggest which other sticky-note ideas should be linked to one source idea.

Return JSON only:
{"links":[{"id":"<candidate id>","why":"..."}]}

Rules:
- Judge from idea text AND the selected (pinned) rationale labels
- Suggest a link only when the two ideas share a meaningful design relation
  (same goal, complementary parts, tension, constraint, alternative, cause-effect, same user need)
- Prefer candidates whose selected labels overlap in meaning with the source labels
- Do not suggest weak, generic, or "everything is related" links
- 1 to 5 links, or {"links":[]} if none are warranted
- "id" must be one of the candidate ids; never invent ids; never repeat an id
- "why" is at most 12 words, same language as the ideas, naming the relation
- no chatbot tone, no markdown
"""
SUGGEST_GROUP_WHY_PROMPT = """You write one short reason why these sticky-note ideas belong in one suggested group.

Return JSON only:
{"why":"..."}

Rules:
- one sentence, at most 18 words
- name the shared concern, goal, constraint, tension, or who it is for
- use the idea texts and the shared rationale labels as clues
- write a reason, not a list of label names
- same language as the ideas
- no chatbot tone, no markdown, no quotation marks around the sentence
"""
DETECT_REFLECTION_PROMPT = """You decide whether a sticky-note IDEA already contains design reflection (the why), not only a proposal.

Reflection means a reason, motive, constraint, assumption, worry, tradeoff, who it is for, or why it matters.
A short title-like proposal with no why is NOT reflection.

Return JSON only:
{"has_reflection": true|false, "excerpt": "..."}

Rules:
- be conservative: true only if a reader could extract rationale labels from this text
- excerpt must be a contiguous phrase copied from the idea, not a rewrite; at most 40 words, or ""
- if you cannot copy a why phrase from the idea, return has_reflection false
- same language as the idea
- no chatbot tone, no markdown
"""
ASK_WHY_PROMPT = """You read one sticky-note idea from a designer and ask ONE question that pushes the design reasoning of that idea further.

Read the note before you ask. Return JSON only, with these seven keys in this order, every value written from the note in front of you:
ideas (number), proposals (list of strings), kind (one of idea, reflection, mixed), stated_why (string), settled (string), gap (string), question (string).
Never copy a placeholder or an example into a value.

The note may come with rationale the designer has already written, and with questions already asked. Both are settled ground: read them, then ask about what is still open.

ideas: how many proposals the note holds that could be built or dropped on their own. A wall plus a bot is 2. One proposal is still 1 when it merely names options, states, or steps.
proposals: when ideas is 2 or more, name each one as a short noun phrase of at most 4 words taken from the note, such as "colour coding" or "timeline view"; never a sentence or a verb phrase. Otherwise an empty list.
kind: "idea" when it proposes something to build or change, "reflection" when it only reports a situation, observation, or difficulty with nothing proposed, "mixed" when it does both.
stated_why: the reason already given, copied from the note or from the rationale written so far; "" when there is none.
settled: what else is already decided — the note's goal, any options it keeps open, any states it defines as different from each other, and anything the written rationale has now answered.
gap: one thing its reasoning has not decided yet. Pick the gap the note itself leans on, from: what it trades away, what it competes with, what changes downstream once it works, what it assumes about the situation, who it serves, when it should not apply, what decides which case applies.
question: ask about that gap, following the case that fits.

Cases, in priority order. Take the first that fits and ignore the rest:
1. ideas is 2 or more: ask why those proposals belong on one note, or which of them carries the intent. Do not ask about the workings of either one.
2. kind is "reflection": ask what that thinking should change in the design.
3. stated_why is not empty: that reason is settled, so never ask for it again; ask what it does not cover.
4. stated_why is empty: ask for the reasoning behind the most consequential choice the note makes.

Hard checks before you answer:
- the situation you ask about must be possible under the idea's own logic; if the idea defines two states as different, never ask about something being both
- do not invent an edge case the idea rules out
- if your question repeats anything in "stated_why" or "settled", pick another gap
- the asked list rules out a topic, not just a wording: if you already asked what is lost by filtering, asking what those ideas are worth is the same question again
- take the rationale written so far as answered and ask the next thing it opens up, never the thing it just settled
- only ask what defines or classifies something when telling two states apart is the point of the note; otherwise choose a different gap
- if ideas is 2 or more and your question is about how one of those proposals works, throw it away and ask why they belong together instead
- if nothing survives these checks, keep the reading fields and return "question":""

Rules for the question:
- stay on the idea as a design proposal; never ask about the person's feelings, memories, habits, or past projects
- the note is the subject. A design context block, when present, only tells you which project the note sits in: lean on it to make the question concrete, never treat the project's goals as this note's goals, and never ask about the context itself
- do not invent users, places, constraints, or motives that neither the note nor the context names
- never restate or summarise the idea
- name something concrete from the idea, so it could not be asked of any other note
- reject anything that would fit any idea, such as "what do you hope to achieve" or "why is this important"
- one question, at most 16 words, ending in a question mark
- same language as the idea
- no chatbot tone, no markdown, no preamble, no multiple questions

Examples of the judgement wanted:
idea: As a user, I want the system to distinguish between vaguely abandoned and explicitly rejected ideas so I can read their status differently.
bad: How would the system handle an idea that straddles both categories? (the idea defines them as different states, so that case cannot exist)
good: What tells the system an idea was vaguely abandoned rather than explicitly rejected?

idea: As Dennis, I want to attach a vague or specific reason when I abandon something, so I remember why I set it aside.
bad: Why choose a vague reason instead of a specific one? (the idea already allows both)
bad: What did a vague reason cost you months later? (asks about him, not about the design)
good: What should a vague reason still be good for when the idea returns?

idea: Sticky note wall in the lab entrance for weekly reading picks
bad: What do you hope to achieve with weekly picks? (fits any idea)
good: What makes the entrance beat the group chat for these picks?

idea: Weekly reading wall in the entrance, and a bot that posts the picks to Slack
reading: ideas 2, because the wall and the bot could each be built alone
bad: How would the wall and the bot stay in sync? (asks about plumbing, not intent)
good: Why should the wall and the Slack bot live on one note?

idea: Add a filter for abandoned ideas
written so far: The board fills up and dead ideas drown the live ones.
already asked: What is lost when abandoned ideas are hidden?
bad: What value might abandoned ideas still hold? (the asked question again, in new words)
good: What brings a hidden idea back onto the board?

idea: I keep reopening ideas I dropped months ago and cannot tell if I rejected them or ran out of time
reading: kind reflection, because it reports a difficulty and proposes nothing
bad: What criteria separate a rejected idea from a dropped one? (answers the reflection for them)
good: What should the canvas show differently once that difference is known?
"""
MAX_KNOWN_LABELS = 40  # caps prompt size; the newest labels are the ones worth matching
MAX_SUGGEST_LINKS = 5
MAX_SUGGEST_WHY_WORDS = 12
MAX_GROUP_WHY_WORDS = 18
MAX_WHY_QUESTION_WORDS = 16
MAX_CONTEXT_CHARS = 1500  # the standing brief is background, so it must not crowd out the idea
MIN_WHY_QUESTION_CHARS = 12
MIN_RATIONALE_CHARS = 1
OPENAI_TIMEOUT_S = 30.0
MAX_LABEL_WORDS = 10
EMBED_DIM = 256  # text-embedding-3-small matryoshka; small enough to persist on the canvas
MEANING_TOP_K = 3  # LLM only sees cosine neighbours, never the whole pool
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STUDY_CAP = int(os.getenv("STUDY_CAP", str(db.STUDY_CAP)) or db.STUDY_CAP)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    clear_local_proxies()
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
    text: str = Field(default="", max_length=4000)  # reflection field
    idea: str = Field(default="", max_length=4000)  # sticky-note / relation body
    target: str = Field(default="generic")  # generic | note | relation | group
    known: list[KnownLabel] = Field(default_factory=list, max_length=200)
    labelled: str = Field(default="", max_length=4000)  # reflection the known labels came from
    context: str = Field(default="", max_length=MAX_CONTEXT_CHARS)  # the designer's standing brief


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=32)


class MeaningRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    candidates: list[KnownLabel] = Field(min_length=1, max_length=MEANING_TOP_K)


class ShortenRequest(BaseModel):
    text: str = Field(min_length=1, max_length=400)


class SuggestIdea(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    text: str = Field(default="", max_length=2000)
    labels: list[str] = Field(default_factory=list, max_length=3)


class SuggestLinksRequest(BaseModel):
    source: SuggestIdea
    candidates: list[SuggestIdea] = Field(min_length=1, max_length=40)
    context: str = Field(default="", max_length=MAX_CONTEXT_CHARS)


class SuggestGroupWhyRequest(BaseModel):
    ideas: list[SuggestIdea] = Field(min_length=2, max_length=8)
    shared: list[str] = Field(default_factory=list, max_length=6)
    context: str = Field(default="", max_length=MAX_CONTEXT_CHARS)


class DetectReflectionRequest(BaseModel):
    idea: str = Field(min_length=1, max_length=4000)


class AskWhyRequest(BaseModel):
    idea: str = Field(min_length=1, max_length=4000)
    answer: str = Field(default="", max_length=4000)  # rationale written so far
    asked: list[str] = Field(default_factory=list, max_length=8)  # never ask these again
    context: str = Field(default="", max_length=MAX_CONTEXT_CHARS)


def clear_local_proxies() -> None:
    """Drop HTTP_PROXY aimed at 127.0.0.1 (Cursor sandbox). Those cannot reach api.openai.com."""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = os.environ.get(key, "")
        if "127.0.0.1" in value or "localhost" in value:
            os.environ.pop(key, None)


def get_client() -> AsyncOpenAI:
    """Build an OpenAI client from OPENAI_API_KEY. Raises a public error if missing."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-your-key"):
        raise api_error(
            503,
            "missing_key",
            "Label generation isn't set up on this server yet. Try again later.",
        )
    clear_local_proxies()
    proxy = os.getenv("OPENAI_PROXY", "").strip()
    kwargs = {"api_key": api_key, "timeout": OPENAI_TIMEOUT_S}
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"] = proxy
    return AsyncOpenAI(**kwargs)


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
        log.warning("openai_unreachable: %s", exc)
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
    """Study collection is paused: the browser keeps the board, the server does not store it."""
    return {"ok": True, "updated_at": db.utc_now()}


@app.post("/api/events")
def post_event(req: EventRequest, person=Depends(require_participant)):
    """Study collection is paused: interaction events are not stored."""
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


def parse_label_payload(
    raw: str,
    known_refs: set[str] | None = None,
    allow_empty: bool = False,
) -> list[dict]:
    """Parse model JSON into [{text, kind}, ...] plus {same_as, phrasing, kind} reuse hits.

    Strips markdown fences if present. A same_as pointing at a ref we never sent is a
    model slip, so it falls back to the phrasing as a plain new label. With `allow_empty`
    an empty list is a verdict on the addition, not a failure, so it comes back as [].
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
    if not isinstance(items, list):
        raise api_error(502, "empty_labels", "The AI returned no labels. Try rephrasing and generate again.")
    if not items:
        if allow_empty:
            return []
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
        labels.append({
            "text": clip_words(label_text),
            "kind": kind,
        })
    if not labels and not allow_empty:
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


def parse_suggest_links(raw: str, allowed_ids: set[str]) -> list[dict]:
    """Parse model JSON into [{id, why}, ...] using only candidate ids we sent."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise api_error(502, "bad_model_json", "The AI returned no usable links. Try again.") from exc

    items = data.get("links") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    links = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        link_id = str(item.get("id", "")).strip()
        if not link_id or link_id not in allowed_ids or link_id in seen:
            continue
        seen.add(link_id)
        why = clip_words(str(item.get("why", "")).strip(), MAX_SUGGEST_WHY_WORDS)
        links.append({"id": link_id, "why": why})
        if len(links) >= MAX_SUGGEST_LINKS:
            break
    return links


def idea_block(item: SuggestIdea) -> str:
    labels = [str(label).strip() for label in item.labels if str(label).strip()][:3]
    label_line = " | ".join(label[:200] for label in labels) if labels else "(none)"
    idea = str(item.text or "").strip() or "(empty idea)"
    return f"id: {item.id}\nidea: {idea}\nselected labels: {label_line}"


@app.post("/api/suggest-links")
async def suggest_links(req: SuggestLinksRequest):
    """Suggest dashed relations from one idea + its pinned labels to other ideas."""
    source_text = str(req.source.text or "").strip()
    source_labels = [str(label).strip() for label in req.source.labels if str(label).strip()]
    if not source_text and not source_labels:
        raise api_error(
            400,
            "too_short",
            "Write an idea or pin a label first so suggestions have something to go on.",
        )

    allowed_ids = {item.id for item in req.candidates if item.id != req.source.id}
    if not allowed_ids:
        return {"links": [], "model": ""}

    listing = "\n\n".join(idea_block(item) for item in req.candidates if item.id in allowed_ids)
    user_content = f"{context_block(req.context)}source idea\n{idea_block(req.source)}\n\ncandidate ideas\n{listing}"

    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUGGEST_LINKS_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=280,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    content = response.choices[0].message.content
    if not content:
        return {"links": [], "model": model}
    return {"links": parse_suggest_links(content, allowed_ids), "model": model}


def parse_group_why(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    return clip_words(str(data.get("why") or "").strip(), MAX_GROUP_WHY_WORDS)


@app.post("/api/group-why")
async def suggest_group_why(req: SuggestGroupWhyRequest):
    """One short narrative for why these ideas sit in a suggested group."""
    listing = "\n\n".join(idea_block(item) for item in req.ideas)
    shared = [str(item).strip() for item in req.shared if str(item).strip()][:6]
    shared_line = " | ".join(item[:200] for item in shared) if shared else "(none)"
    user_content = f"{context_block(req.context)}grouped ideas\n{listing}\n\nshared rationale labels: {shared_line}"

    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUGGEST_GROUP_WHY_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=80,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    content = response.choices[0].message.content
    why = parse_group_why(content or "")
    return {"why": why, "model": model}


def parse_detect_reflection(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"has_reflection": False, "excerpt": ""}
    if not isinstance(data, dict):
        return {"has_reflection": False, "excerpt": ""}
    excerpt = " ".join(str(data.get("excerpt") or "").split())
    words = excerpt.split()
    if len(words) > 40:
        excerpt = " ".join(words[:40])
    return {"has_reflection": bool(data.get("has_reflection")), "excerpt": excerpt}


def keep_idea_excerpt(idea: str, excerpt: str) -> str:
    """Only keep an excerpt that is actually copied from the idea."""
    hay = " ".join(idea.split()).lower()
    needle = " ".join(excerpt.split()).lower()
    if needle and needle in hay:
        return excerpt
    return ""


def context_block(context: str) -> str:
    """The designer's standing brief, as a labelled block the model reads as background only."""
    text = " ".join(str(context or "").split())[:MAX_CONTEXT_CHARS].strip()
    if not text:
        return ""
    return f"design context, written by the designer about the project as a whole (background only):\n{text}\n\n"


def already_labelled_text(labelled: str, idea: str, reflection: str) -> str:
    """The part of the reflection the existing labels came from, when this round only added to it.

    The client sends the idea and the reflection joined, so the idea is trimmed back off.
    A reflection that was rewritten rather than extended returns "", and gets read whole.
    """
    text = labelled.strip()
    if not text or not reflection:
        return ""
    if idea and text.startswith(idea):
        text = text[len(idea):].strip()
    if not text or text == reflection:
        return ""
    return text if reflection.startswith(text) else ""


def idea_has_why_context(idea: str) -> bool:
    """Skip the why-question model when the sticky note is empty or too thin."""
    text = idea.strip()
    if len(text) < MIN_WHY_QUESTION_CHARS:
        return False
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9']+", text)
    return len(tokens) >= 3


def lumped_ideas_question(proposals: list[str], idea: str) -> str:
    """A note holding several proposals always earns the same question, in the note's language."""
    # Clipping to 4 words can leave a dangling preposition, which reads badly inside the sentence.
    first, second = (
        re.sub(r"\s+(?:of|for|to|with|and|the|a|an)$", "", item.strip().strip(".,;"), flags=re.IGNORECASE)
        for item in proposals[:2]
    )
    if re.search(r"[\u4e00-\u9fff]", idea):
        return f"为什么「{first}」和「{second}」要放在同一张便签上？"
    return f"Why do “{first}” and “{second}” belong on one note?"


def ask_why_user_content(req: AskWhyRequest, idea: str) -> str:
    """Note first, then the ground already covered: the rationale written and the questions spent."""
    blocks = [f"{context_block(req.context)}sticky note:\n{idea}"]
    answer = req.answer.strip()
    if answer:
        blocks.append(f"rationale the designer has written so far:\n{answer}")
    asked = [item.strip() for item in req.asked if item.strip()][-8:]
    if asked:
        listing = "\n".join(f"- {item}" for item in asked)
        blocks.append(f"questions already asked here, never ask these again:\n{listing}")
    return "\n\n".join(blocks)


QUESTION_FILLER = {
    "what", "why", "how", "when", "where", "who", "which", "whose", "does", "did", "the",
    "and", "but", "for", "with", "that", "this", "these", "those", "you", "your", "their",
    "would", "should", "could", "might", "may", "can", "will", "are", "was", "were", "been",
    "have", "has", "had", "not", "from", "into", "than", "then", "some", "any", "one",
}


def question_topic(text: str) -> set[str]:
    """The content words a question turns on, blunted so tense and plurals do not hide a repeat."""
    words = re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]", text.lower())
    stems = set()
    for word in words:
        if word in QUESTION_FILLER:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(word) > len(suffix) + 2 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        stems.add(word)
    return stems


def same_question(a: str, b: str) -> bool:
    """Wording aside, is this the ground an earlier question already covered?"""
    if re.sub(r"[^\w\u4e00-\u9fff]+", " ", a.lower()).strip() == re.sub(r"[^\w\u4e00-\u9fff]+", " ", b.lower()).strip():
        return True
    left, right = question_topic(a), question_topic(b)
    if not left or not right:
        return False
    return len(left & right) / min(len(left), len(right)) >= 0.6


def parse_why_reply(raw: str, idea: str = "") -> dict:
    """The question, plus how the model read the note: one idea or several, idea or reflection, why already given."""
    empty = {"question": "", "ideas": 0, "kind": "", "stated_why": ""}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return empty
    if not isinstance(data, dict):
        return empty
    question = clip_words(str(data.get("question") or "").strip(), MAX_WHY_QUESTION_WORDS)
    try:
        ideas = int(data.get("ideas") or 0)
    except (TypeError, ValueError):
        ideas = 0
    # A statement means the model summarised instead of asking; drop it rather than show it.
    if not question.endswith(("?", "？")):
        question = ""
    raw_proposals = data.get("proposals")
    proposals = [
        clip_words(str(item).strip(), 4)
        for item in (raw_proposals if isinstance(raw_proposals, list) else [])
        if str(item).strip()
    ]
    # The model keeps drifting into how one proposal works, so a lumped note is questioned here instead.
    if ideas >= 2 and len(proposals) >= 2:
        question = lumped_ideas_question(proposals, idea)
    return {
        "question": question,
        "ideas": ideas,
        "kind": str(data.get("kind") or "").strip().lower(),
        "stated_why": str(data.get("stated_why") or "").strip(),
    }


@app.post("/api/ask-why")
async def ask_why(req: AskWhyRequest):
    """One probing question from sticky-note text only; empty when there is not enough to go on."""
    idea = req.idea.strip()
    if not idea_has_why_context(idea):
        return {"question": "", "ideas": 0, "kind": "", "stated_why": "", "model": ""}

    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": ASK_WHY_PROMPT},
                {"role": "user", "content": ask_why_user_content(req, idea)},
            ],
            response_format={"type": "json_object"},
            temperature=0.5,
            max_tokens=320,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    content = response.choices[0].message.content
    reply = parse_why_reply(content or "", idea)
    # The model still repeats itself now and then; an exact repeat is worse than no bubble.
    if reply["question"] and any(same_question(reply["question"], item) for item in req.asked):
        reply["question"] = ""
    return {**reply, "model": model}


@app.post("/api/detect-reflection")
async def detect_reflection(req: DetectReflectionRequest):
    """Does this idea already contain a why, so the reflection field need not be blank?"""
    idea = req.idea.strip()
    if len(idea) < 36:
        return {"has_reflection": False, "excerpt": "", "model": ""}

    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": DETECT_REFLECTION_PROMPT},
                {"role": "user", "content": idea},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=120,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    content = response.choices[0].message.content
    parsed = parse_detect_reflection(content or "")
    parsed["excerpt"] = keep_idea_excerpt(idea, parsed.get("excerpt") or "")
    parsed["model"] = model
    return parsed


@app.post("/api/rationale-labels")
async def rationale_labels(req: RationaleRequest):
    """Turn a rationale paragraph into short labels (not a chatbot reply)."""
    reflection = req.text.strip()
    idea = (req.idea or "").strip()
    if not reflection and not idea:
        raise api_error(
            400,
            "too_short",
            "Write a bit more rationale so the labels can be specific.",
        )
    packed = len(reflection) + len(idea)
    if packed < MIN_RATIONALE_CHARS:
        raise api_error(
            400,
            "too_short",
            "Write a bit more rationale so the labels can be specific.",
        )

    target = req.target if req.target in {"generic", "note", "relation", "group"} else "generic"
    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    known = req.known[-MAX_KNOWN_LABELS:]
    known_refs = {item.ref for item in known}
    blocks = [block for block in (context_block(req.context).strip(),) if block]
    blocks.append(f"target: {target}")
    if idea:
        idea_label = "grouped ideas" if target == "group" else "sticky-note idea"
        blocks.append(f"{idea_label}:\n{idea}")
    blocks.append(f"reflection:\n{reflection or '(empty)'}")
    user_content = "\n\n".join(blocks)
    if known:
        listing = "\n".join(f"{item.ref} | {item.text}" for item in known)
        user_content = f"{user_content}\n\nlabels the designer already has:\n{listing}"
    labelled = already_labelled_text(req.labelled, idea, reflection)
    if labelled:
        user_content = (
            f"{user_content}\n\nthe part of that reflection those labels already cover, "
            f"background only:\n{labelled}"
        )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": LABEL_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=280,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise openai_http_error(exc) from exc

    content = response.choices[0].message.content
    if not content:
        raise api_error(502, "empty_labels", "The AI returned no labels. Try rephrasing and generate again.")

    labels = parse_label_payload(content, known_refs, allow_empty=bool(labelled))
    if not labels:
        return {"labels": [], "model": model, "reason": "nothing_new"}
    labels = await attach_embeddings(client, labels)
    return {"labels": labels, "model": model, "reason": ""}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
