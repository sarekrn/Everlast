"""Custom triggers: "when a log line looks like THIS, show THAT and/or say THAT".

The format is EQTool's. A trigger has an id, a name, a search pattern (a regular
expression), and what to show and say. {name} placeholders in the pattern capture text
and can be used in the shown and spoken text:

    ^{backstabber} backstabs {target} for {damage} points of damage.
    -> shows "{backstabber} backstabs {target} for {damage}"

EQTool's own trigger file is plain text, one trigger per line, ';' between fields:

    triggerID;triggerEnabled;triggerName;searchText;textEnabled;displayText;audioEnabled;audioText

`parse_file` reads that format and `format_file` writes it, so triggers can be moved
between the two programs.
"""
import logging
import re

log = logging.getLogger(__name__)

FIELDS = ("id", "enabled", "name", "search", "text_on", "text", "audio_on", "audio")

# What a {placeholder} matches. Anything not listed here matches any text.
_PLACEHOLDER = {
    "damage": r"\d+",
    "direction": r"[A-Za-z]+",
}
_BRACES = re.compile(r"\{([A-Za-z_]\w*)\}")     # not {2} or {1,3}, which are regex counts


def _t(i, name, search, text, audio, enabled=1, text_on=1, audio_on=0):
    return {"id": i, "enabled": bool(enabled), "name": name, "search": search,
            "text_on": bool(text_on), "text": text, "audio_on": bool(audio_on), "audio": audio}


# EQTool 5.25's built-in triggers (ids 100-114), as it ships them: text on, voice off.
# 100 and 101 are switched off here because Everlast already announces fizzles and
# interrupted spells itself, and names the spell; with both on you would get two banners.
DEFAULT_TRIGGERS = [
    _t(100, "Spell Interrupted", r"^Your spell is interrupted.", "Spell Interrupted",
       "Interrupted", enabled=0),
    _t(101, "Spell Fizzle", r"^Your spell fizzles!", "Spell Fizzles", "Fizzle", enabled=0),
    _t(102, "Backstabber", r"^{backstabber} backstabs {target} for {damage} points of damage.",
       "{backstabber} backstabs {target} for {damage}", "Backstabber"),
    _t(103, "Corpse Need Consent", r"^You do not have consent to summon that corpse",
       "Need Consent", "Need Consent"),
    _t(104, "Corpse Out of Range", r"^The corpse is too far away to summon", "Corpse OOR",
       "Corpse out of range"),
    _t(105, "Select a Target",
       r"^(You must first select a target for this spell)|"
       r"(You must first click on the being you wish to attack)",
       "Select a target", "Select a target"),
    _t(106, "Insufficient Mana", r"^Insufficient Mana to cast this spell!", "OOM",
       "out of mana"),
    _t(107, "Target Out of Range", r"^Your target is out of range", "Target out of range",
       "Out of range"),
    _t(108, "Spell Did Not Take Hold", r"^Your spell did not take hold",
       "Spell did not take hold", "Spell did not take hold"),
    _t(109, "Must be standing to cast",
       r"^(You must be standing)|(You are too distracted to cast a spell now)",
       "Stand up!", "stand up"),
    _t(110, "Dispelled", r"^You feel a bit dispelled", "You have been dispelled", "dispelled"),
    _t(111, "Regen Faded", r"^You have stopped regenerating", "===== Regen faded =====",
       "re-gen faded"),
    _t(112, "Can't See Target", r"^You can't see your target", "Can't see target",
       "Can't see target"),
    _t(113, "Sense Heading", r"^You think you are heading {direction}", "Direction = {direction}",
       "{direction}"),
    _t(114, "Sense Heading Failed", r"^You have no idea what direction you are facing",
       "No idea", "no idea"),
]


def default_triggers():
    return [dict(t) for t in DEFAULT_TRIGGERS]


def compile_pattern(search):
    """Turn a search pattern with {placeholders} into a compiled regex, or None if the
    pattern is not valid."""
    seen = set()

    def group(m):
        name = m.group(1)
        if name in seen:                       # a name used twice: match the same text again
            return f"(?P={name})"
        seen.add(name)
        return f"(?P<{name}>{_PLACEHOLDER.get(name, '.+?')})"

    try:
        return re.compile(_BRACES.sub(group, search), re.IGNORECASE)
    except re.error:
        return None


def fill(template, values):
    """Put captured text into a {placeholder} template."""
    return _BRACES.sub(lambda m: str(values.get(m.group(1), m.group(0))), template or "")


def pattern_error(search):
    """Why a pattern does not work, or "" if it does."""
    if not (search or "").strip():
        return "The search text is empty."
    try:
        re.compile(_BRACES.sub(lambda m: f"(?P<{m.group(1)}>.+?)", search))
    except re.error as e:
        return f"Not a valid pattern: {e}"
    return ""


class TriggerSet:
    """The compiled triggers; rebuilt whenever the settings list changes."""

    def __init__(self, triggers=None):
        self.set(triggers)

    def set(self, triggers):
        self.items = []
        for t in (default_triggers() if triggers is None else triggers):
            try:
                if not t.get("enabled", True) or not (t.get("text_on") or t.get("audio_on")):
                    continue
                rx = compile_pattern(t.get("search", ""))
                if rx is None:
                    log.warning("trigger %r has a bad pattern", t.get("name"))
                    continue
                self.items.append((t, rx))
            except (AttributeError, TypeError):
                continue

    def match(self, msg):
        """[(trigger, shown text or "", spoken text or "")] for every trigger that fits."""
        out = []
        for t, rx in self.items:
            m = rx.search(msg)
            if not m:
                continue
            vals = {k: v for k, v in m.groupdict().items() if v is not None}
            shown = fill(t.get("text"), vals).strip() if t.get("text_on") else ""
            spoken = fill(t.get("audio"), vals).strip() if t.get("audio_on") else ""
            if shown or spoken:
                out.append((t, shown, spoken))
        return out


def next_id(triggers):
    return max([int(t.get("id", 0)) for t in triggers] + [999]) + 1


def parse_file(text):
    """Read EQTool's trigger file format. Returns (triggers, number of bad lines)."""
    out, bad = [], 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split(";")
        if len(p) < 8:
            bad += 1
            continue
        if len(p) > 8:                          # a ';' inside the text: keep the last 4 fields
            p = p[:3] + [";".join(p[3:-4])] + p[-4:]
        try:
            out.append({"id": int(p[0]), "enabled": p[1].strip() == "1", "name": p[2],
                        "search": p[3], "text_on": p[4].strip() == "1", "text": p[5],
                        "audio_on": p[6].strip() == "1", "audio": p[7]})
        except ValueError:
            bad += 1
    return out, bad


def format_file(triggers):
    head = ("# Triggers for Everlast (same format as EQTool)\n"
            "# triggerID;triggerEnabled;triggerName;searchText;textEnabled;displayText;"
            "audioEnabled;audioText\n")
    rows = []
    for t in triggers:
        rows.append(";".join([
            str(t["id"]), "1" if t.get("enabled", True) else "0", t.get("name", ""),
            t.get("search", ""), "1" if t.get("text_on") else "0", t.get("text", ""),
            "1" if t.get("audio_on") else "0", t.get("audio", "")]))
    return head + "\n".join(rows) + "\n"
