"""Deterministic Unstuck Coach router.

Implements the routing layer of the canonical Unstuck Coach method (vendored
under vendor/, hash-pinned via method-pins.json): signal recognition from
reference/signal-map.md, stance selection from reference/mode-router.md, and
first-message routing plus protocol scripts from coach/rules.md,
coach/examples.md, and reference/safety-boundaries.md.

Honesty boundary: this is the method's DETERMINISTIC routing and script layer,
not a language model. Every reply below is assembled from the method's own
scripts and response shapes; nothing is improvised at runtime.

Routing precedence follows the method's own outranking rules (mode-router.md
"Routing Rules", signal-map.md "If a signal crosses surfaces"): crisis and
clinical boundaries first, then capture (capture can outrank categorization),
then hard anchors (live obligations outrank cleanup), body state (body state
can outrank planning), recovery, failed plans, threats, dumps, surfaces,
fuel, return, then first-contact runways.

Pure functions only: no I/O, no process spawning, no network, no globals.
"""

import re

# --- signal tables (each entry grounded in the vendored method files) -------

_CRISIS_SIGNALS = (
    "suicidal", "suicide", "kill myself", "ending my life", "end my life",
    "hurt myself", "harm myself", "self-harm", "self harm", "want to die",
    "better off dead", "can't stay safe", "cannot stay safe", "not stay safe",
    "hearing voices", "command hallucination", "lost hours of time",
)
_CAPTURE_PREFIXES = (
    "idea:", "todo:", "note to self:", "remind me", "bookmark this",
    "don't let me forget", "dont let me forget",
)
_MEDICATION_SIGNALS = (
    "medication", "medications", "adderall", "ritalin", "antidepressant",
    "antidepressants", "stimulant meds", "my meds", "change my dose",
    "stop taking my", "start taking", "should i take", "off my meds",
)
_THERAPY_SIGNALS = (
    "diagnose me", "do i have adhd", "do i have autism", "is this trauma",
    "interpret my trauma", "parts work", "emdr", "exposure therapy",
    "replace my therapist", "be my therapist",
)
_DEADLINE_SIGNALS = (
    "court", "irs", "visa", "eviction", "lawsuit", "tax", "taxes",
    "insurance form", "benefits", "immigration", "medical bill",
    "hospital bill", "lease", "landlord", "summons", "official letter",
)
_ANCHOR_RE = re.compile(
    r"\b(?:in|after|before|until|till)\s+\d{1,3}\s*(?:minutes?|mins?|hours?)\b"
    r"|\b\d{1,3}\s*(?:minutes?|mins?)\s+(?:before|until|till|to)\b",
    re.IGNORECASE,
)
_ANCHOR_NOUNS = (
    "meeting", "appointment", "pickup", "dropoff", "drop-off", "deadline",
    "call", "class", "train", "flight", "bus", "dentist", "doctor",
    "standup", "stand-up", "interview", "leave",
)
_RECOVERY_SIGNALS = (
    "cannot stop", "can't stop", "cant stop", "hyperfocus", "in the zone for",
    "locked in for", "done for tonight", "done for today", "shutting down for",
    "need to stop for", "wrap up for", "ending for the day", "end for the day",
)
_BODY_SIGNALS = (
    "frozen", "freeze", "fried", "exhausted", "starving", "haven't eaten",
    "havent eaten", "forgot to eat", "no food", "hungry", "thirsty",
    "shut down", "shut-down", "numb", "can't think", "cannot think",
    "cant think", "tired", "drained", "depleted", "dysregulated",
    "overwhelmed", "won't start", "wired", "jittery", "sensory",
)
_FAILED_PLAN_SIGNALS = (
    "three times", "3 times", "third time", "tried again", "keep failing",
    "still didn't work", "still did not work", "still doesn't work",
    "keep restarting", "every time i try",
)
_THREAT_PHRASES = (
    "we need to talk", "call me", "per my last email", "circling back",
    "as discussed", "let's touch base", "see my previous email",
)
_THREAT_CONTEXT = (
    "message", "text", "email", "dm", "comment", "review", "feedback",
    "critique", "reply", "notification", "boss", "manager", "client",
)
_THREAT_FEELINGS = (
    "did something wrong", "terrible", "awful", "fired", "mad at me",
    "angry at me", "in trouble", "anxious", "scared", "afraid", "dreading",
    "spiral", "hate opening",
)
_DUMP_PREFIXES = ("brain dump:", "braindump:", "everything in my head", "dump:")
_CALENDAR_SIGNALS = (
    "calendar", "schedule", "double-booked", "double booked", "missed",
    "appointment", "reschedule",
)
_INBOX_SIGNALS = (
    "inbox", "unread", "emails", "reply debt", "my messages pile",
    "notification stack",
)
_DOPAMINE_SIGNALS = (
    "dopamine", "stimulation", "understimulated", "need a spark",
    "nothing feels rewarding", "bored out of", "need fuel",
)
_RETURN_SIGNALS = (
    "i disappeared", "disappeared from", "haven't been back", "fell off",
    "i am behind", "i'm behind", "been avoiding this", "avoided this for",
    "returning after",
)
_START_HERE_PHRASES = (
    "i need a coach to get started", "help me get started",
    "i need help starting",
)
_VAGUE_SIGNALS = (
    "don't know where to start", "dont know where to start",
    "don't know what to do", "dont know what to do", "where do i start",
    "paralyzed", "procrastinating",
)
_OFFICIAL_DEADLINE_RE = re.compile(r"\b(deadline|due date|due by|notice)\b", re.IGNORECASE)

_MAX_SIGNALS = 12  # cap reported signal list; input itself is capped upstream

_LIFE_SURFACES = {
    "crisis": "safety",
    "clinical": "safety",
    "deadline": "home/admin loops",
    "capture": "capture and re-entry",
    "anchor": "calendar",
    "body": "food and body",
    "recovery": "closure and recovery",
    "plan": "any loop",
    "threat": "messages and shame",
    "dump": "working memory",
    "admin": "calendar and inbox",
    "fuel": "food and body",
    "return": "closure and recovery",
    "contact": "starting",
}


def _has(text, needles):
    return [n for n in needles if n in text]


def _split_items(text):
    """Split a raw dump into loose items without asking the user to format."""
    normalized = re.sub(r"^(?:\s*[-*•]\s*|\s*\d+[.)]\s*)", "", text.strip(), flags=re.MULTILINE)
    parts = re.split(r"[\n;]+|,(?=\s*\S)", normalized)
    items, seen = [], set()
    for part in parts:
        item = part.strip(" .-")
        if not item:
            continue
        key = item.lower()
        if key not in seen:  # deduplicate obvious repeats (rules.md)
            seen.add(key)
            items.append(item)
    return items


_TIME_ANCHOR_RE = re.compile(r"\bat\s+\d{1,2}\b|\b\d{1,2}\s*(?:am|pm)\b", re.IGNORECASE)


class CoachRouter:
    """Route one stuck point; assemble the method-grounded reply."""

    def route(self, stuck_point):
        text = stuck_point.strip()
        low = text.lower()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        signals = []
        prefixed_dump = low.startswith(_DUMP_PREFIXES)

        # 1. Crisis: stop normal coaching (reference/safety-boundaries.md).
        hits = _has(low, _CRISIS_SIGNALS)
        if hits:
            signals += ["crisis:" + h for h in hits[:3]]
            return self._out(
                stance="Ally / Stabilize",
                protocols=["Crisis Boundary"],
                signals=signals,
                life_surface=_LIFE_SURFACES["crisis"],
                reply=(
                    "This is bigger than executive-function coaching. "
                    "I am here with you, but I need you to reach a real person now.\n\n"
                    "If you are in the U.S. and might hurt yourself or cannot stay safe, "
                    "call or text 988 now. If there is immediate danger, call emergency "
                    "services or go to an ER.\n\n"
                    "Who can be with you in the next 10 minutes?"
                ),
                held=[],
                check="Who can be with you in the next 10 minutes?",
                method_refs=["vendor/reference/safety-boundaries.md"],
            )

        # 2. Capture first, coach later (coach/rules.md Natural Capture).
        for prefix in _CAPTURE_PREFIXES:
            if low.startswith(prefix):
                captured = text[len(prefix):].strip(" .")
                signals.append("capture:" + prefix.rstrip(":"))
                return self._out(
                    stance="Keeper / Remember",
                    protocols=["Natural Capture"],
                    signals=signals,
                    life_surface=_LIFE_SURFACES["capture"],
                    reply=(
                        f"Captured: {captured}.\n\n"
                        "I parked it as Later. Want me to route it now or leave it parked?"
                    ),
                    held=[captured],
                    check="Route it now, or leave it parked?",
                    method_refs=["vendor/coach/rules.md", "vendor/reference/signal-map.md"],
                )

        # 3. Clinical boundaries (reference/safety-boundaries.md scripts).
        med_hits = _has(low, _MEDICATION_SIGNALS)
        therapy_hits = _has(low, _THERAPY_SIGNALS)
        if med_hits or therapy_hits:
            signals += ["medication:" + h for h in med_hits[:2]]
            signals += ["therapy:" + h for h in therapy_hits[:2]]
            if med_hits:
                reply = (
                    "I cannot advise on medication. That is a prescriber question. "
                    "I can help you write down observations and questions to bring to them."
                )
                protocols = ["Medication Boundary"]
            else:
                reply = (
                    "I can help you notice the pattern and choose the next safe step. "
                    "The deeper processing belongs with a therapist or qualified clinician."
                )
                protocols = ["Therapy Boundary"]
            return self._out(
                stance="Ally / Stabilize",
                protocols=protocols,
                signals=signals,
                life_surface=_LIFE_SURFACES["clinical"],
                reply=reply,
                held=[],
                check="What one question do you want to hand the professional?",
                method_refs=["vendor/reference/safety-boundaries.md"],
            )

        # 4. Official deadline boundary (reference/safety-boundaries.md).
        deadline_hits = _has(low, _DEADLINE_SIGNALS)
        if deadline_hits and _OFFICIAL_DEADLINE_RE.search(low):
            signals += ["official:" + h for h in deadline_hits[:3]]
            return self._out(
                stance="Ally / Stabilize -> Strategist / Choose",
                protocols=["Official Deadline Boundary"],
                signals=signals,
                life_surface=_LIFE_SURFACES["deadline"],
                reply=(
                    "This is a real deadline, and I cannot be the professional authority "
                    "for it. We are making the next visible step smaller.\n\n"
                    "Copy one line first: the deadline line if it is visible, otherwise "
                    "the contact line. Then use that line with the qualified support "
                    "channel named on the notice or a local professional/help line."
                ),
                held=[],
                check="Copy the one line: deadline line, or contact line?",
                method_refs=["vendor/reference/safety-boundaries.md", "vendor/coach/rules.md"],
            )

        # 5. Imminent hard anchor (coach/rules.md; signal-map example row).
        anchor_nouns = [n for n in _ANCHOR_NOUNS if n in low]
        if _ANCHOR_RE.search(low) and anchor_nouns:
            signals += ["anchor:" + n for n in anchor_nouns[:2]]
            return self._out(
                stance="Engineer / Execute -> Ally / Stabilize",
                protocols=["Imminent Hard Anchor"],
                signals=signals,
                life_surface=_LIFE_SURFACES["anchor"],
                reply=(
                    "The meeting is the hard anchor. Everything else is parked for the "
                    "next few minutes. Open the meeting link now. If water is within "
                    "reach, put it beside you.\n\n"
                    "Reply: link open."
                ),
                held=[],
                check="Reply: link open.",
                method_refs=["vendor/coach/rules.md", "vendor/reference/mode-router.md"],
            )

        # 6. Explicit brain dump: sort it outside the head before any body or
        # planning read of the same text (coach/rules.md Brain Dump Protocol;
        # body-state inside the dump becomes the first MOVE, not the branch).
        dump = self._dump(text, lines, signals, forced=True) if prefixed_dump else None
        if dump:
            return dump

        # 7. Recovery / close (mode-router.md stance table).
        recovery_hits = _has(low, _RECOVERY_SIGNALS)
        if recovery_hits:
            signals += ["recovery:" + h for h in recovery_hits[:2]]
            return self._out(
                stance="Recovery / Close",
                protocols=["Hyperfocus Exit", "Finish Or Park"],
                signals=signals,
                life_surface=_LIFE_SURFACES["recovery"],
                reply=(
                    "That is a post-hyperfocus crash. We are not evaluating the work yet, "
                    "and I am not extracting one more task from you.\n\n"
                    "First recovery pass: water. Food or protein if available. Then one "
                    "sentence breadcrumb: \"Next time, start with ____.\""
                ),
                held=[],
                check="Reply with the breadcrumb only.",
                method_refs=["vendor/reference/mode-router.md", "vendor/coach/examples.md"],
            )

        # 7.5. Implicit multi-line pile sorts before any single-signal read of
        # the same text: body-state inside the pile becomes the first MOVE, not
        # the branch (rules.md Brain Dump Protocol; signal-map "a messy
        # multi-item dump" row). Mirrors the prefixed-dump treatment above.
        if len(lines) >= 3:
            pile = self._dump(text, lines, signals, forced=False)
            if pile:
                return pile

        # 8. Body state can outrank planning (mode-router.md Routing Rules).
        body_hits = _has(low, _BODY_SIGNALS)
        if body_hits:
            signals += ["body:" + h for h in body_hits[:3]]
            return self._out(
                stance="Ally / Stabilize",
                protocols=["Somatic Translator", "Body-First Routing"],
                signals=signals,
                life_surface=_LIFE_SURFACES["body"],
                reply=(
                    "I am holding the rest of it. Biology comes first, because planning "
                    "on empty is false data.\n\n"
                    "Eat or drink the smallest available thing — water counts. If choosing "
                    "is annoying, I choose: water first."
                ),
                held=[],
                check="Reply with one word: fed.",
                method_refs=["vendor/coach/rules.md", "vendor/coach/examples.md"],
            )

        # 9. Three-attempt escape (coach/rules.md; examples.md).
        plan_hits = _has(low, _FAILED_PLAN_SIGNALS)
        if plan_hits:
            signals += ["plan:" + h for h in plan_hits[:2]]
            return self._out(
                stance="Strategist / Choose",
                protocols=["Three-Attempt Escape"],
                signals=signals,
                life_surface=_LIFE_SURFACES["plan"],
                reply=(
                    "That is three attempts. We are not trying harder at the same plan. "
                    "The plan failed; you did not.\n\n"
                    "New shape: make the task 10 percent real. Open the surface and copy "
                    "the first field, sentence, or object you do not understand."
                ),
                held=[],
                check="Reply with only that piece.",
                method_refs=["vendor/coach/rules.md", "vendor/coach/examples.md"],
            )

        # 10. Communication threat armor (coach/rules.md; examples.md 5/5B).
        threat = self._threat(low, signals)
        if threat:
            return threat

        # 11. Implicit dump: unlabeled multi-item pile (>=3 lines or commas).
        dump = self._dump(text, lines, signals, forced=False)
        if dump:
            return dump

        # 12. Calendar reality before inbox triage (examples.md 14).
        cal_hits = _has(low, _CALENDAR_SIGNALS)
        inbox_hits = _has(low, _INBOX_SIGNALS)
        if cal_hits or inbox_hits:
            signals += ["calendar:" + h for h in cal_hits[:2]]
            signals += ["inbox:" + h for h in inbox_hits[:2]]
            if cal_hits:
                reply = (
                    "That is system overload, not a character problem. We are not "
                    "processing everything. We are rescuing live obligations.\n\n"
                    "First move: open the calendar and tell me the next hard anchor: "
                    "meeting, deadline, travel, pickup, sleep, or nothing today."
                )
                check = "Which hard anchor, or 'nothing today'?"
            else:
                reply = (
                    "We are not processing the whole inbox. We are rescuing live "
                    "obligations. Open the inbox and search 'due' first. If that finds "
                    "nothing live, search 'deadline'. Do not scroll the unread count."
                )
                check = "One item: sender, subject, date, or 'nothing live found'."
            return self._out(
                stance="Strategist / Choose -> Engineer / Execute",
                protocols=["Calendar Reality Check" if cal_hits else "Inbox Triage"],
                signals=signals,
                life_surface=_LIFE_SURFACES["admin"],
                reply=reply,
                held=[],
                check=check,
                method_refs=[
                    "vendor/reference/coaching-protocols.md",
                    "vendor/reference/admin-ops-playbooks.md",
                ],
            )

        # 13. Dopamine menu = activation fuel (coach/rules.md; examples.md 16).
        fuel_hits = _has(low, _DOPAMINE_SIGNALS)
        if fuel_hits:
            signals += ["fuel:" + h for h in fuel_hits[:2]]
            return self._out(
                stance="Ally / Stabilize -> Engineer / Execute",
                protocols=["Dopamine Menu"],
                signals=signals,
                life_surface=_LIFE_SURFACES["fuel"],
                reply=(
                    "You need activation fuel, not a lecture.\n\n"
                    "I am choosing one spark so the menu does not become the task: one "
                    "song while the stuck thing is open. Timebox: one song, 2-10 minutes. "
                    "Return target: the first visible piece of it."
                ),
                held=[],
                check="What is visible when the song ends?",
                method_refs=["vendor/coach/rules.md", "vendor/reference/signal-map.md"],
            )

        # 14. Return without shame (coaching-protocols.md Return Without Shame).
        return_hits = _has(low, _RETURN_SIGNALS)
        if return_hits:
            signals += ["return:" + h for h in return_hits[:2]]
            return self._out(
                stance="Recovery / Close",
                protocols=["Return Without Shame"],
                signals=signals,
                life_surface=_LIFE_SURFACES["return"],
                reply=(
                    "Welcome back. No penalty. We reconstruct from artifacts, not memory.\n\n"
                    "What is the last real artifact: file, email, note, branch, or "
                    "calendar item?"
                ),
                held=[],
                check="Name the last real artifact.",
                method_refs=["vendor/reference/coaching-protocols.md", "vendor/coach/examples.md"],
            )

        # 15. Start-here runway (coach/START_HERE.md shape; PROJECT_INSTRUCTIONS).
        if _has(low, _START_HERE_PHRASES):
            signals.append("contact:start-here")
            return self._out(
                stance="Ally / Stabilize -> Strategist / Choose",
                protocols=["First-Contact Runway"],
                signals=signals,
                life_surface=_LIFE_SURFACES["contact"],
                reply=(
                    "You do not need to make it clear first. Send the messy task pile "
                    "as-is, or any three items if the pile is too much. I will sort it "
                    "outside your head, hold the rest, and give back one next move."
                ),
                held=[],
                check="Send the pile as-is, or any three items.",
                method_refs=["vendor/coach/START_HERE.md", "vendor/coach/rules.md"],
            )

        # 16. Traffic-light opener ONLY for blank/vague/begin-only input
        # (rules.md First-Message Routing: "do not make the user pass through
        # a calibration ritual when they already gave a stuck signal" — a
        # concrete single-line stuck statement routes directly, below).
        vague = _has(low, _VAGUE_SIGNALS)
        bare = len(text.split()) <= 2  # nothing concrete named yet
        if vague or bare:
            signals += ["vague:" + v for v in vague[:2]] or ["vague:short-ambiguous"]
            return self._out(
                stance="Ally / Stabilize",
                protocols=["Traffic Light Check"],
                signals=signals,
                life_surface=_LIFE_SURFACES["contact"],
                reply=(
                    "Green, yellow, or red right now? If choosing is annoying, say "
                    "'yellow' and we will start there."
                ),
                held=[],
                check="Green, yellow, or red?",
                method_refs=["vendor/coach/rules.md", "vendor/reference/coaching-protocols.md"],
            )

        # 17. Default: start the contact, not the project (rules.md Task Initiation).
        signals.append("default:unrouted-concrete")
        return self._out(
            stance="Engineer / Execute",
            protocols=["First-Contact Runway", "Start In The Middle"],
            signals=signals,
            life_surface=_LIFE_SURFACES["contact"],
            reply=(
                "Do not start the whole thing. Start the contact.\n\n"
                "Open the thing — that counts. If the beginning is ugly, skip it: what "
                "part is least disgusting? I am holding the rest of the pile while you "
                "make one visible contact."
            ),
            held=[],
            check="What did you open?",
            method_refs=["vendor/coach/rules.md", "vendor/reference/coaching-protocols.md"],
        )

    def _threat(self, low, signals):
        phrase_hits = _has(low, _THREAT_PHRASES)
        feeling_hits = _has(low, _THREAT_FEELINGS)
        context_hits = _has(low, _THREAT_CONTEXT)
        if not (phrase_hits or (context_hits and feeling_hits)):
            return None
        signals += ["threat:" + p for p in phrase_hits[:2]]
        signals += ["threat-feeling:" + f for f in feeling_hits[:2]]
        signals += ["threat-context:" + c for c in context_hits[:2]]
        if phrase_hits:
            # Vague threat phrase: not a literal ask yet; do not draft
            # (PROJECT_INSTRUCTIONS.md; examples.md 5B).
            reply = (
                "We are not processing your worth through a few words. That sentence "
                "is not a verdict yet. Do not answer it.\n\n"
                "Copy the sentence that carries the threat, or tell me there is no "
                "context yet."
            )
        else:
            reply = (
                "We are not processing your worth through a notification. We are "
                "sorting signal before reply.\n\n"
                "Copy only the sentence that carries the threat — do not answer it, "
                "just quote it."
            )
        return self._out(
            stance="Ally / Stabilize -> Strategist / Choose",
            protocols=["Communication Threat Armor"],
            signals=signals[:_MAX_SIGNALS],
            life_surface=_LIFE_SURFACES["threat"],
            reply=reply,
            held=[],
            check="What is the sentence?",
            method_refs=["vendor/coach/rules.md", "vendor/coach/examples.md"],
        )

    def _dump(self, text, lines, signals, forced=False):
        multi = len(lines) >= 3 or (text.count(",") >= 2 and len(lines) >= 1)
        if not (forced or multi):
            return None
        body_words = ("eat", "food", "hungry", "starving", "shower", "sleep",
                      "tired", "meds", "medication", "bathroom", "panic",
                      "water", "fried", "exhausted", "thirsty")
        now_words = ("due", "today", "overdue", "deadline", "bill", "meeting",
                     "appointment", "late", "missed", "urgent", "pay",
                     "submit")
        waiting_words = ("waiting", "blocked", "waiting on", "waiting for")
        clean = text
        for prefix in _DUMP_PREFIXES:
            if clean.lower().startswith(prefix):
                clean = clean[len(prefix):]
                break
        items = _split_items(clean)
        body, now, later, waiting = [], [], [], []
        for item in items:
            ilow = item.lower()
            if any(w in ilow for w in body_words):
                body.append(item)
            elif any(w in ilow for w in now_words) or _TIME_ANCHOR_RE.search(ilow):
                now.append(item)
            elif any(w in ilow for w in waiting_words):
                waiting.append(item)
            else:
                later.append(item)
        if len(now) > 2:  # hold beyond two; one move only (rules.md)
            later = now[2:] + later
            now = now[:2]
        held = [f"Now: {', '.join(now)}"] if now else []
        held += [f"Next/Later: {', '.join(later)}"] if later else []
        held += [f"Waiting: {', '.join(waiting)}"] if waiting else []
        signals.append(f"dump:{len(items)}-items")
        if body:
            signals.append("dump:body-state-present")
            move = ("Eat or drink the smallest available food; I am holding the rest.")
            check = "Reply with one word: fed."
        else:
            first = now[0] if now else (later[0] if later else (items[0] if items else "the first thing"))
            move = f"Touch '{first}' — open it, do not finish it. I am holding the rest."
            check = "What is the smallest check this moved?"
        sections = [f"Body/State: {', '.join(body)}"] if body else []
        sections.append(f"Now: {', '.join(now) if now else '—'}")
        sections.append(f"Next/Later: {', '.join(later) if later else '—'}")
        if waiting:
            sections.append(f"Waiting: {', '.join(waiting)}")
        reply = (
            "I will sort it outside your head.\n\n"
            + "\n".join(sections)
            + "\n\nNext move: " + move
        )
        return self._out(
            stance="Strategist / Choose -> Engineer / Execute",
            protocols=["Brain Dump To One Action"],
            signals=signals,
            life_surface=_LIFE_SURFACES["dump"],
            reply=reply,
            held=held,
            check=check,
            method_refs=["vendor/coach/rules.md", "vendor/coach/examples.md"],
        )

    def _out(self, stance, protocols, signals, life_surface, reply, held, check, method_refs):
        return {
            "stance": stance,
            "protocols": protocols,
            "signals": signals[:_MAX_SIGNALS],
            "life_surface": life_surface,
            "reply": reply,
            "held": held,
            "check": check,
            "method_refs": method_refs,
        }
