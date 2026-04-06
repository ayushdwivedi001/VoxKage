# intent_engine.py
import re

# --- Canonical synonym pools (English + common Hinglish transliterations) ---
SYNONYMS = {
    "open": [
        "open","launch","start","run","fire up",
        "khol","kholo","khol do","kholo na","chalu karo","shuru karo","start karo","run karo","launch karo"
    ],
    "close": [
        "close","exit","shut","terminate","kill",
        "band","band karo","band kar do","close karo","band krdo","band kr de"
    ],
    "switch": [
        "switch","switch to","go to","bring up","focus on",
        "badlo","change to","le jao","pe jao","par jao"
    ],
    "search": [
        "search","look up","lookup","find","find out",
        "search karo","dhund","dhundo","dhoond","dhoondo","khoj","khojo","dekhna","dekho"
    ],
    "screenshot": ["screenshot","take picture","capture screen","photo lo","screen lo","ss le lo","ss"],
    "wallpaper": ["wallpaper","background","desktop picture","background badlo","wallpaper badlo"],
    "volume": ["volume","sound","audio","awaz"],
    "brightness": ["brightness","display light","screen light","roshni","brightnes","light"],
    "wifi": ["wifi","wi-fi","internet","vaifai"],
    "bluetooth": ["bluetooth","bt"],
    "shutdown": ["shutdown","power off","turn off pc","band karo pc","pc band karo","shut down"],
    "restart": ["restart","reboot","restart karo","pc restart"],
    "sleep": ["sleep","suspend","hibernate","so jao","pc sula do"]
}

# Prepositions/particles we see around services
IN_ON_WORDS = ["on","in","me","pe","par","per","ke upar"]

# Fillers we want to strip from queries
FILLERS = [
    "please","plz","pls","yaar","bro","bhai","abhi","now","zara","na","toh","ji","jee",
    "karo","kar do","kardo","kijiye","krdo","kr de","krde","krke","krke de","kindly"
]

def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"\s+", " ", t)
    # Collapse common multi-word phrases to single tokens to simplify matching
    replacements = {
        "look up": "lookup",
        "find out": "find",
        "you tube": "youtube",
        "wi-fi": "wifi",
        "kar do": "kardo",
        "band kar do": "band karo",
        "switch to": "switch",
        "bring up": "switch",
        "go to": "switch",
        "focus on": "switch",
        "search karo": "search",
        "start karo": "start",
        "run karo": "run",
        "launch karo": "launch",
        "open karo": "open",
    }
    for a, b in replacements.items():
        t = t.replace(a, b)
    return t

def _contains_any(t: str, words) -> bool:
    return any(w in t for w in words)

def _remove_fillers(t: str) -> str:
    # remove standalone fillers
    words = t.split()
    words = [w for w in words if w not in FILLERS]
    t = " ".join(words)
    # trim punctuation
    t = re.sub(r"[^\w\s\-\.\,']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _match_patterns(patterns, text):
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            q = (m.group("q") if "q" in m.groupdict() else "").strip()
            q = _remove_fillers(q)
            if q:
                return q
    return None

def _extract_service_query(text: str, service_words):
    """
    Robustly extract a query for searches like:
    - search youtube for X
    - search on youtube X
    - youtube me X search
    - X on youtube
    - youtube X (if strong signal of search verbs present)
    """
    s = _normalize(text)

    svc = r"(?:%s)" % "|".join(map(re.escape, service_words))
    prep = r"(?:%s)" % "|".join(map(re.escape, IN_ON_WORDS))
    verbs = r"(?:search|lookup|find|dhund\w*|dhoond\w*|khoj\w*|dekhna|dekho)"

    patterns = [
        # 1) search [on|in|me|pe] youtube [for] <q>
        rf"\b{verbs}\s+(?:{prep}\s+)?{svc}\s*(?:for\s+)?(?P<q>.+)$",
        # 2) youtube [on|in|me|pe] <q> (search|find|lookup ...)
        rf"\b{svc}\s+(?:{prep}\s+)?(?P<q>.+?)\s+{verbs}\b",
        # 3) <q> [on|in|me|pe] youtube
        rf"^(?P<q>.+?)\s+(?:{prep})\s+{svc}\b",
        # 4) youtube <q>  (if there's also a search verb anywhere)
        rf"\b{svc}\s+(?P<q>.+)$",
    ]

    # If no clear search verb exists, allow pattern 3 (X on youtube) or 4 only
    has_search_verb = _contains_any(s, ["search","lookup","find","dhund","dhoond","khoj","dekhna","dekho"])

    # Try with all patterns, but in case of pattern 4 require a search verb
    for i, pat in enumerate(patterns):
        q = _match_patterns([pat], s)
        if q:
            if i == 3 and not has_search_verb:
                # Pattern 4 needs a search signal; otherwise it's probably "open youtube <something>"
                continue
            return q
    return None

def detect_intent(text: str):
    """
    Turn freeform text into a structured {intent, ...} dict.
    Works for English + common Hinglish phrasing.
    """
    t = _normalize(text)

    # Hardcoded search heuristics removed to allow LLM to manage all search intent.

    # --- Volume / Brightness ---
    for key in ["volume", "brightness"]:
        if _contains_any(t, SYNONYMS[key]):
            lvl = None
            if "low" in t: lvl = 20
            elif "medium" in t: lvl = 50
            elif "high" in t: lvl = 80
            else:
                nums = re.findall(r"\d+", t)
                if nums:
                    lvl = int(nums[0])
                else:
                    # increase/decrease heuristics
                    if any(w in t for w in ["increase","up","zyada","badhao","badhaiye","raise","higher"]):
                        lvl = 70
                    if any(w in t for w in ["decrease","down","kam","ghatao","ghataiye","lower"]):
                        lvl = 30
            return {"intent": f"set_{key}", "level": lvl}

    # --- Wi-Fi ---
    if _contains_any(t, SYNONYMS["wifi"]):
        if "on" in t or "enable" in t or "chalu" in t: return {"intent": "wifi_on"}
        if "off" in t or "disable" in t or "band" in t: return {"intent": "wifi_off"}

    # --- Bluetooth ---
    if _contains_any(t, SYNONYMS["bluetooth"]):
        if "on" in t or "enable" in t or "chalu" in t: return {"intent": "bluetooth_on"}
        if "off" in t or "disable" in t or "band" in t: return {"intent": "bluetooth_off"}

    # --- Open apps/sites ---
    if _contains_any(t, SYNONYMS["open"]):
        name = t
        for word in SYNONYMS["open"]:
            name = name.replace(word, "")
        name = _remove_fillers(name).strip()
        return {"intent": "open", "target": name}

    # --- Close apps/sites ---
    if _contains_any(t, SYNONYMS["close"]):
        name = t
        for word in SYNONYMS["close"]:
            name = name.replace(word, "")
        name = _remove_fillers(name).strip()
        return {"intent": "close", "target": name}

    # --- Switch ---
    if _contains_any(t, SYNONYMS["switch"]):
        name = t
        for word in SYNONYMS["switch"]:
            name = name.replace(word, "")
        name = _remove_fillers(name).strip()
        return {"intent": "switch", "target": name}

    # --- System commands (Restrictive matching to avoid false positives) ---
    for sys_intent in ["shutdown", "restart", "sleep"]:
        # Only trigger if it starts with the command, or follows clear system verbs
        # e.g., "shutdown", "please shutdown", "put to sleep", "restart the pc"
        patterns = [
            rf"^{sys_intent}\b",
            rf"\b(?:put to|go to|set to|force)\s+{sys_intent}\b",
            rf"\b(?:pc|computer|system)\s+(?:to\s+)?{sys_intent}\b",
            rf"\b{sys_intent}\s+(?:the\s+)?(?:pc|computer|system)\b"
        ]
        if any(re.search(pat, t) for pat in patterns):
            return {"intent": sys_intent}

    # Screenshot intent removed for LLM management.

    # --- Wallpaper ---
    if _contains_any(t, SYNONYMS["wallpaper"]):
        return {"intent": "wallpaper"}

    return {"intent": "unknown", "text": t}
