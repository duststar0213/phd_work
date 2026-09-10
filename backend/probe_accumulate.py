"""Throwaway check: labels for the added clause, and a calm verdict when nothing was added."""

import json
import urllib.error
import urllib.request

IDEA = "As a user I want to capture why I removed an idea so I can interpret its status later"
BASE = (
    "the rationale of removal captures why at that moment this idea doesnt work, "
    "but this may also need justification when I explain it to the others"
)
GROWN = BASE + ", and the abandoned idea may be reused later so marking it as a tried path saves time"
KNOWN = [
    {"ref": "r:1", "text": "Justification needed for decisions"},
    {"ref": "r:2", "text": "Capture rationale for later directions"},
    {"ref": "r:3", "text": "rationale of removal for later justification"},
]


def call(text, labelled):
    body = json.dumps(
        {"text": text, "idea": IDEA, "target": "note", "known": KNOWN, "labelled": labelled}
    ).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/rationale-labels",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"http_error": exc.code, "body": json.loads(exc.read() or b"{}")}


print("1. added a clause on top of labelled text")
data = call(GROWN, IDEA + "\n\n" + BASE)
print(json.dumps(data.get("labels", data), ensure_ascii=False, indent=2)[:600])
print("   reason:", data.get("reason"))

print("\n2. nothing added beyond the labelled text")
data = call(BASE + ".", IDEA + "\n\n" + BASE)
print("   reason:", data.get("reason"), "| labels:", len(data.get("labels", [])), "| err:", data.get("http_error"))

print("\n3. no labelled text at all (first generate, must still 502 on an empty model reply)")
data = call(GROWN, "")
print("   reason:", data.get("reason"), "| labels:", len(data.get("labels", [])), "| err:", data.get("http_error"))
