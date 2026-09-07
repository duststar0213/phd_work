---
marp: true
paginate: true
title: Repertoire prototype(temporary name)
---

# Repertoire
## sticky-note ideation + rationale labels

- Research prototype for capturing **why** an idea exists, not a chatbot suggest what ideas they should make
- Designer writes a **reflection** → AI (or the person) turns it into **short labels** = surfacing rationale out from reflection (vebalized)
- Using reflection instead of rationale: **relationship between reflection and rationale? raionale is more structrued while reflection is more fluid and will give user more freedom to express**
- Labels can **recur** on the same note and **across notes** and defines a pattern that may be reusable for users 

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

# Function: get on the prototype

- Invitation code first (`STUDY_ACCESS_CODE`, currently `rep-c6e4`), need to host on website for sending to participants in evaluating 
NEED TO FIGURE OUT: where should i host this prototype? what are policy if I need to get json info of how participant interacting with the prototype
- Then email + password (or emailed one-time code)
- Same email **always** maps to the same canvas 
- Optional allowlist of emails (manually add participants email is possible)
- Cap on how many new accounts can join

---

# Function: infinite canvas
This part is basically fork what figjam or miro is doing....
- Dot grid stays locked to the world
- Place sticky notes with the note tool (or `N`)
- Select / drag notes
- Bottom bar: tools + zoom + sign out
Add:
- Pan (drag empty space) + zoom (wheel / ⌘+/−). I think I add this just for my habit, not a must on

---Below is core function----

# CORE FUNCTION: create & edit an idea

- Click canvas → new sticky note (use the bottom bar)
- Placeholder: “define idea here” - grey text
- Click again / empty note → type in the note
- Color wheel on the selected note
- Corner handles: **resizable**
- Short idea + large note → **type grows** so the note will not have a lot of empty space if user resize it larger
----
# CORE FUNCTRION: AI helps surfacing rationale from user verbalized reflection

- Open the chevron under a note (or relation), this part will always stay when user creating idea for **discoverities** and also engaging user to input and rrite rationale in **reflection**
THINK/NEED: writing is still little be disruptive? although this part is create that user can do it for **optinonal** but
will there be more engaging way? 
- **Enter** generates labels (Shift+Enter = new line)
- Blue chips = AI generated; yellow chips = human add or edited
THINK/NEED: I used the different label color to make AI and human generated, and also user can edit, delete or keep what ever AI generate
- Local gate first: empty / junk / near-copy of last generate **do not** call OpenAI. If user is trying to over-rely on AI instead of really inputing rationale, there is detect duplication check...and also this is prevent similar labels is over prducing by AI
- Hesitation (“I don’t know”…) → fixed chip `not sure`, no API call, allow user to just label vague or uncertainty. 
---

---
# Function: abandon / revive idea

- Delete on a **filled** note does not erase it
- Opens abandon reflection: “why abandon this idea?” --> define rationale
- Confirm → ghost note (locked, faded), still on the canvas
- Empty note → actually removed
- Right-click ghost → **revive**
- Same flow exists for relations
-The canvas auto-saves to your account. Come back later, same email, same board.

---
# CORE FUNCTION: Relate idea (WIP)

- Connector tool → drag from a mag-point on one note to another
- Curve sits between notes
- Mid-line **relation marker**: same `+` / tabs as a note
- Reflection on a relation is “why these two are linked”

---




# AI prompt 1: create labels

**When:** Enter on reflection  
**Model:** `gpt-4o-mini` · temperature 0.4 · JSON only

**System prompt (summary)**
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
This is created if user entering label that share meaning with the current existing labels and also potential offer for user to **merge** labels 
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
