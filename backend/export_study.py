"""
Export all participant ideation data for analysis.

From backend/:
  .venv/bin/python export_study.py

Writes (gitignored):
  data/export/all.json          full dump (canvas + events)
  data/export/notes.csv         one row per sticky note (Excel / Numbers)
  data/export/rationales.csv    one row per rationale identity, with recurrence
  data/export/events.csv        process log
  data/export/by_email/*.json   one file per participant
"""

import csv
import json
import re
from pathlib import Path

import db

EXPORT_DIR = Path(__file__).resolve().parent / "data" / "export"

NOTE_FIELDS = [
    "email",
    "last_seen_at",
    "canvas_updated_at",
    "note_id",
    "text",
    "color",
    "x",
    "y",
    "width",
    "height",
    "rationale",
    "labels",
    "pinned",
    "abandoned",
    "abandon_rationale",
    "abandon_labels",
    "connection_count",
]
EVENT_FIELDS = ["email", "ts", "type", "payload"]
RATIONALE_FIELDS = [
    "email",
    "rid",
    "text",
    "source",
    "idea_count",  # distinct notes / relations invoking this rationale
    "occurrence_count",  # times it resurfaced, including repeats on one idea
    "owners",
    "phrasings",  # wordings the AI proposed that were merged into this one
]


def safe_name(email: str) -> str:
    return re.sub(r"[^a-z0-9._+-]+", "_", email.lower()) or "unknown"


def label_text(items) -> str:
    if not isinstance(items, list):
        return ""
    return " | ".join(
        str(item.get("text", "")).strip()
        for item in items
        if isinstance(item, dict) and item.get("text")
    )


def note_rows(people: list[dict]) -> list[dict]:
    rows = []
    for person in people:
        canvas = person.get("canvas") or {}
        notes = canvas.get("notes") or []
        connections = canvas.get("connections") or []
        for note in notes:
            if not isinstance(note, dict):
                continue
            rows.append(
                {
                    "email": person["email"],
                    "last_seen_at": person.get("last_seen_at") or "",
                    "canvas_updated_at": person.get("canvas_updated_at") or "",
                    "note_id": note.get("id", ""),
                    "text": note.get("text") or "",
                    "color": note.get("color") or "",
                    "x": note.get("x", ""),
                    "y": note.get("y", ""),
                    "width": note.get("width", ""),
                    "height": note.get("height", ""),
                    "rationale": note.get("rationaleText") or "",
                    "labels": label_text(note.get("rationaleLabels")),
                    "pinned": label_text(note.get("pinnedLabels")),
                    "abandoned": "1" if note.get("abandoned") else "",
                    "abandon_rationale": note.get("abandonText") or "",
                    "abandon_labels": label_text(note.get("abandonLabels")),
                    "connection_count": sum(
                        1
                        for link in connections
                        if isinstance(link, dict)
                        and (
                            link.get("fromId") == note.get("id")
                            or link.get("toId") == note.get("id")
                        )
                    ),
                }
            )
    return rows


def rationale_rows(people: list[dict]) -> list[dict]:
    """One row per rationale identity: what recurred, where, and in whose words."""
    rows = []
    for person in people:
        canvas = person.get("canvas") or {}
        by_rid: dict[str, dict] = {}
        owners = [("note", item) for item in canvas.get("notes") or []]
        owners += [("relation", item) for item in canvas.get("connections") or []]
        for kind, owner in owners:
            if not isinstance(owner, dict):
                continue
            key = f"{kind}:{owner.get('id', '')}"
            for label in owner.get("rationaleLabels") or []:
                if not isinstance(label, dict) or not label.get("rid"):
                    continue
                entry = by_rid.setdefault(
                    label["rid"],
                    {"text": "", "source": "", "owners": [], "occurrences": 0, "phrasings": []},
                )
                entry["text"] = entry["text"] or str(label.get("text") or "")
                entry["source"] = entry["source"] or str(label.get("source") or "")
                entry["owners"].append(key)
                entry["occurrences"] += max(1, int(label.get("count") or 1))
                for hit in label.get("occurrences") or []:
                    phrasing = str((hit or {}).get("phrasing") or "").strip()
                    if phrasing:
                        entry["phrasings"].append(phrasing)
        for rid, entry in by_rid.items():
            rows.append(
                {
                    "email": person["email"],
                    "rid": rid,
                    "text": entry["text"],
                    "source": entry["source"],
                    "idea_count": len(set(entry["owners"])),
                    "occurrence_count": entry["occurrences"],
                    "owners": " | ".join(sorted(set(entry["owners"]))),
                    "phrasings": " | ".join(entry["phrasings"]),
                }
            )
    return rows


def event_rows(people: list[dict]) -> list[dict]:
    rows = []
    for person in people:
        for item in person.get("events") or []:
            payload = item.get("payload") if isinstance(item, dict) else {}
            rows.append(
                {
                    "email": person["email"],
                    "ts": item.get("ts") or "",
                    "type": item.get("type") or "",
                    "payload": json.dumps(payload, ensure_ascii=False) if payload else "",
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    db.init_db()
    people = db.list_export()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    by_email = EXPORT_DIR / "by_email"
    by_email.mkdir(parents=True, exist_ok=True)

    (EXPORT_DIR / "all.json").write_text(
        json.dumps({"participants": people}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(EXPORT_DIR / "notes.csv", note_rows(people), NOTE_FIELDS)
    write_csv(EXPORT_DIR / "events.csv", event_rows(people), EVENT_FIELDS)
    write_csv(EXPORT_DIR / "rationales.csv", rationale_rows(people), RATIONALE_FIELDS)
    for person in people:
        name = safe_name(person.get("email") or "unknown")
        (by_email / f"{name}.json").write_text(
            json.dumps(person, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"{len(people)} participants → {EXPORT_DIR}")
    print(f"  notes table: {EXPORT_DIR / 'notes.csv'}")
    print(f"  rationales:  {EXPORT_DIR / 'rationales.csv'}")
    print(f"  events log:  {EXPORT_DIR / 'events.csv'}")
    print(f"  full dump:   {EXPORT_DIR / 'all.json'}")
    print(f"  per person:  {by_email}")


if __name__ == "__main__":
    main()
