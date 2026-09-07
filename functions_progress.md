---
marp: true
paginate: true
title: Repertoire prototype — group meeting
---

# Repertoire
## sticky-note ideation + rationale labels

- Research prototype for capturing **why** an idea exists, not a chatbot
- Designer writes a **reflection** → AI (or the person) turns it into **short labels**
- Labels can **recur** on the same note and **across notes**

For the group meeting: what the prototype does, the stack, each function, and how AI is called.

---

# Research intent

- Sticky notes hold the **idea**
- Reflection holds the **rationale** (why this idea / why abandon / why two ideas link)
- Labels are compact, reusable **rationale identities** (`rid`)
- Repeating wording patterns may matter to users — same note **or** across notes

---

# Stack

**Frontend**
- Vue 3 (Composition API, SFC)
- Vite 8 (dev server + `/api` proxy)

**Backend**
- FastAPI (Python)
- SQLite (`backend/data/repertoire.db`)
- OpenAI API (`gpt-4o-mini` + `text-embedding-3-small`)

**Not used on the canvas:** chatbot panel (`ChatPanel`) — code exists, not wired in

---

# Request path

- Browser → Vite (`localhost:5173`)
- `/api/*` proxied to FastAPI (`127.0.0.1:8000`)
- Session cookie (httpOnly) identifies the participant
- Canvas snapshot saved with `PUT /api/canvas` (~1.4s debounce)
- Process log: `POST /api/events`

---

# Function: study door

- Invitation code first (`STUDY_ACCESS_CODE`, currently `rep-c6e4`)
- Then email + password (or emailed one-time code)
- Same email **always** maps to the same canvas
- Optional allowlist of emails
- Cap on how many new accounts can join

---

# Function: infinite canvas

- Pan (drag empty space) + zoom (wheel / ⌘+/−)
- Dot grid stays locked to the world
- Place sticky notes with the note tool (or `N`)
- Select / drag notes
- Bottom bar: tools + zoom + sign out

---

# Function: create & edit an idea

- Click canvas → new sticky note
- Placeholder: “define idea here”
- Click again / empty note → type in the note
- Color wheel on the selected note
- Corner handles: **resizable**
- Short idea + large note → **type grows** so the note is not empty purple
- Long idea stays 13px and the note can still grow downward

---

# Function: connect two ideas

- Connector tool → drag from a mag-point on one note to another
- Curve sits between notes
- Mid-line **relation marker**: same `+` / tabs as a note
- Reflection on a relation is “why these two are linked”

---

# Function: abandon / revive

- Delete on a **filled** note does not erase it
- Opens abandon reflection: “why abandon this idea?”
- Confirm → ghost note (locked, faded), still on the canvas
- Empty note → actually removed
- Right-click ghost → **revive**
- Same flow exists for relations

---

# Function: reflection → labels

This is the core AI loop.

- Open the chevron under a note (or relation)
- Write rationale in **reflection**
- **Enter** generates labels (Shift+Enter = new line)
- Blue chips = AI; yellow chips = human
- Local gate first: empty / junk / near-copy of last generate **do not** call OpenAI
- Hesitation (“I don’t know”…) → fixed chip `not sure`, no API call

---

# AI prompt 1: create labels

**When:** Enter on reflection  
**Model:** `gpt-4o-mini` · temperature 0.4 · JSON only

**System (summary)**
- Turn rationale into **3–6 short labels**
- Each label ≤ **10 words**, specific to this input
- Kinds (internal only): assumption / constraint / goal / tension / insight / question
- Match the input language
- `target: note` → idea’s design rationale
- `target: relation` → why two ideas are linked

**User message**
- The reflection text
- Plus existing canvas labels as `ref | text` (up to 40)

**Not in the prompt today:** the sticky-note idea body

---

# AI prompt 1: reuse instead of duplicating

Same generate call, extra rule:

- If an existing label already names the **same reason** (paraphrase / other language)
- Do **not** invent a new chip
- Return `{ "same_as": "<ref>", "phrasing": "..." }`
- UI asks: **same one** / **keep separate**
- Confirmed reuse shares one `rid` (rationale identity)
- Badge `×N` = this identity is behind N ideas

---

# Function: human labels + top 3

- `+` adds a **human** label (no generate call)
- Click chip to edit; yellow = human-authored
- Drag to **rank** in the pool
- Drag into **top 3 chosen** → pins onto the note as edge tabs
- `×` on a tab takes it off the note; it stays in the pool
- Max 3 pinned tabs per note / relation

---

# Function: live “close to an existing label”

While **typing or editing** a label (not after Enter):

- Instant lexical check vs existing chips
- After a short pause: **one embedding**, cosine vs others
- Matching chips **breathe** (AI stays blue, human stays yellow)
- Hint: “close to an existing label”
- **Enter / blur → breathing stops** (no full-canvas flashing)
- No GPT on every keystroke

---

# AI prompt 2: meaning check (rare)

**When:** confirm whether a new/edited label matches cosine neighbours  
**Endpoint:** `POST /api/label-meaning`  
**Model only sees k ≤ 3 neighbours**, never the whole pool

**System (summary)**
- Same reason = match, even if wording / nouns differ
- Different claims = not a match
- Do **not** rewrite either label
- JSON: `{ "matches": ["<ref>", ...] }`

Used to confirm meaning; the live highlight path prefers embeddings + lexical rules.

---

# Function: repeating patterns (same note + across notes)

**Same note**
- Similar labels sit **side by side**
- Hint: “these two are the same”

**Across notes**
- Same wording pattern on other ideas
- Hint: “this pattern appears on N ideas”
- Typing a label that already exists elsewhere: “this pattern already appears on another idea”

Original texts are kept. Nothing is rewritten into one chip.

---

# AI prompt 3: shorten a long human label

**When:** user-typed label is **> 10 words**, after a pause  
**Endpoint:** `POST /api/shorten-label`

**System (summary)**
- Keep **their** words and order
- Only drop filler / repeats — no new synonyms
- ≤ 10 words
- UI: **use** / **keep mine** (default is keep original)

AI labels from generate are already clipped to 10 words.

---

# Embeddings (no chat prompt)

**Model:** `text-embedding-3-small` · 256-d  
**When:** after generate; while typing a label (debounced)

- Vectors live on the canvas labels
- Cosine ~0.4 = “close in meaning” for highlight
- Lets the UI notice paraphrase without calling GPT every key

---

# Function: logging & export

- Interaction events stored per participant (`note_created`, generate, abandon, …)
- Canvas snapshot is the source of truth in SQLite
- `python export_study.py` writes:
  - `all.json`
  - `notes.csv` / `rationales.csv` / `events.csv`
  - `by_email/*.json`

`export/all.json` in the repo is an **old test dump**, not the live DB.

---

# Demo path (2 minutes)

1. Invite code → login
2. Place a note, write an idea, resize (watch type grow)
3. Open reflection, write why, Enter → blue labels
4. `+` a human label; drag top 3 onto the note
5. Second note with a **similar** reason → pair / “appears on N ideas”
6. Delete a filled note → abandon rationale → ghost → revive

---

# Honest limits (if asked)

- Label generate **does not** see the idea text, only reflection
- “Suggest relations” button currently **logs**, does not call a model
- Chat API exists, **not** on the canvas
- Meaning GPT is a backup; live UX is lexical + embeddings
- Study data is per email on this machine’s SQLite
