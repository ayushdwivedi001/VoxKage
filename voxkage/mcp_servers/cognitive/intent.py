import re

from .constants import (
    _GREETING_PATTERNS,
    _FAREWELL_PATTERNS,
    _THANKS_PATTERNS,
    _SOCIAL_PATTERNS,
    _ACTION_VERBS,
    _CODE_KEYWORDS,
    _PATH_PATTERN,
    _URL_PATTERN,
    _READ_ONLY_VERBS,
    _STATE_CHANGE_VERBS,
    _DOMAIN_KEYWORDS,
    _COMPLEXITY_HIGH,
    _MULTI_REQUIREMENT
)
from .storage import _load_dynamic_rules


def _is_trivial_task(msg: str) -> bool:
    """
    Checks if a message represents a trivial command that should bypass heavy ceremony.
    """
    msg_lower = msg.lower().strip()

    # If it is a direct shell command for git or package manager, it is ALWAYS trivial
    direct_commands = [
        r"^git\s+(commit|push|add|checkout|status|diff|log|branch|pull)\b",
        r"^(npm|pip|yarn|pnpm|cargo|pipenv|poetry)\s+(install|add|update|remove|uninstall)\b",
        r"^git\s+commit\s+-m\b",
    ]
    if any(re.search(pat, msg_lower) for pat in direct_commands):
        return True

    # Explicit implementation verbs (must NEVER bypass ceremony if any of these are present)
    impl_verbs = [
        r"\bcreate\b", r"\bwrite\b", r"\bdelete\b", r"\bmodify\b", r"\bfix\b", 
        r"\brefactor\b", r"\bbuild\b", r"\bdeploy\b", r"\bdesign\b", r"\badd\b", 
        r"\bremove\b", r"\bchange\b", r"\bupdate\b", r"\bupgrade\b", r"\bimplement\b", 
        r"\bbump\b", r"\brename\b", r"\bchore\b", r"\bmake\b", r"\bgenerate\b", 
        r"\bsetup\b", r"\bpatch\b", r"\bedit\b"
    ]
    if any(re.search(pat, msg_lower) for pat in impl_verbs):
        return False

    # Positive list of general trivial keywords / phrases
    trivial_patterns = [
        r"\b(commit|push|add|checkout|status|diff|log|branch|pull|install|uninstall)\b",
    ]
    return any(re.search(pat, msg_lower) for pat in trivial_patterns)


def _classify_intent(msg: str) -> dict:
    """
    Rule-based intent classification. Returns type, domain, tier, etc.
    v3: Risk-based tier classification — observation verbs cap tier at 1,
    state-change verbs escalate to Tier 2+. Domain includes creative/lifestyle.
    Pure computation — no LLM calls, no disk writes. <1ms.
    """
    msg_stripped = msg.strip()
    if not msg_stripped:
        return {"type": "conversation"}

    # NEW: Explicit Header & Metadata Parsing
    msg_lines = msg_stripped.split("\n")[:5]
    declared_domains = []
    declared_tier = None

    for line in msg_lines:
        dom_match = re.search(r"^(?:Domains?|Categories|Domain):\s*([a-zA-Z\s,;➔→\-\(\)>]+)", line, re.I)
        if dom_match:
            # Split on arrows and delimiters
            raw_doms = re.split(r"[,;➔→\-<>]+", dom_match.group(1))
            for rd in raw_doms:
                clean_d = re.sub(r"\(.*?\)", "", rd).strip().lower()
                if not clean_d:
                    continue
                for standard_domain in _DOMAIN_KEYWORDS.keys():
                    if standard_domain in clean_d or clean_d in standard_domain:
                        if standard_domain not in declared_domains:
                            declared_domains.append(standard_domain)
                            break
        
        tier_match = re.search(r"^(?:Tiers?|Complexity):\s*([1-3])", line, re.I)
        if tier_match:
            declared_tier = int(tier_match.group(1))

    if declared_domains:
        primary_domain = declared_domains[0]
        secondary_domains = declared_domains[1:3]
        tier = declared_tier if declared_tier is not None else 1
        return {
            "type": "task",
            "domain": primary_domain,
            "secondary_domains": secondary_domains,
            "ranked_domains": [(d, 5.0) for d in declared_domains],
            "tier": tier,
            "is_read_only": False,
        }

    # Load dynamic rules
    rules = _load_dynamic_rules()

    # ── Force Tier 1 matching ──
    for pat in rules.get("force_tier_1_patterns", []):
        try:
            if re.search(pat, msg_stripped, re.I):
                # Classify domain using the keywords (same as below)
                domain_scores = {}
                for d, pattern in _DOMAIN_KEYWORDS.items():
                    matches = len(pattern.findall(msg))
                    if matches > 0:
                        domain_scores[d] = matches
                domain = max(domain_scores, key=domain_scores.get) if domain_scores else "general"
                
                ranked_domains = sorted(
                    [(d, s) for d, s in domain_scores.items()],
                    key=lambda x: x[1], reverse=True
                )
                secondary_domains = []
                for d, s in ranked_domains:
                    if d != domain and s >= 1:
                        secondary_domains.append(d)
                        if len(secondary_domains) == 2:
                            break
                return {
                    "type": "task",
                    "domain": domain,
                    "secondary_domains": secondary_domains,
                    "ranked_domains": ranked_domains,
                    "tier": 1,
                    "is_read_only": True,
                }
        except Exception:
            pass

    # Score calculation
    conv_score = 0
    task_score = 0

    # Conversation signals
    if _GREETING_PATTERNS.search(msg):
        conv_score += 1
    if _FAREWELL_PATTERNS.search(msg):
        conv_score += 1
    if _THANKS_PATTERNS.search(msg):
        conv_score += 1
    if _SOCIAL_PATTERNS.search(msg):
        conv_score += 1
    # Very short messages with no action verbs lean conversational
    if len(msg_stripped) < 15 and not _ACTION_VERBS.search(msg):
        conv_score += 1

    # Task signals
    action_matches = len(_ACTION_VERBS.findall(msg))
    task_score += min(action_matches, 3)  # cap at 3 to avoid over-scoring
    if _CODE_KEYWORDS.search(msg):
        task_score += 1
    if _PATH_PATTERN.search(msg):
        task_score += 1
    if _URL_PATTERN.search(msg):
        task_score += 1
    # Questions asking for information/action
    if re.match(r"^(what|how|where|when|why|can\s*you|could\s*you|please|show|tell\s*me)\b", msg_stripped, re.I):
        task_score += 1

    # Task wins when both present (social wrapping rule)
    if task_score > 0 and conv_score > 0:
        task_score += 1  # bias toward task

    if task_score <= conv_score:
        return {"type": "conversation"}

    # ── Domain classification ──
    domain_scores = {}
    for d, pattern in _DOMAIN_KEYWORDS.items():
        matches = len(pattern.findall(msg))
        if matches > 0:
            domain_scores[d] = matches

    ranked_domains = sorted(
        [(d, s) for d, s in domain_scores.items()],
        key=lambda x: x[1], reverse=True
    )

    if domain_scores:
        max_score = max(domain_scores.values())
        if max_score <= 1:
            domain = "general"
        else:
            domain = max(domain_scores, key=domain_scores.get)
    else:
        domain = "general"

    secondary_domains = []
    for d, s in ranked_domains:
        if d != domain and s >= 1:
            secondary_domains.append(d)
            if len(secondary_domains) == 2:
                break

    # ── v3 Tier classification — ACTION RISK, not message length ──
    # Step 1: detect read-only (observation) vs state-change (mutation) intent
    
    # Check for negations of state-change verbs using dynamic rules
    has_state_change = False
    state_change_matches = list(_STATE_CHANGE_VERBS.finditer(msg))
    
    if state_change_matches:
        negation_rules = rules.get("negation_rules", [])
        for match in state_change_matches:
            verb = match.group(0).lower()
            start_idx = match.start()
            
            # Find the prefix string before the verb
            prefix_text = msg[max(0, start_idx - 50):start_idx].lower()
            prefix_words = re.findall(r"\b[\w']+\b", prefix_text)
            
            # Default window size
            window_size = 4
            for rule in negation_rules:
                if verb in rule.get("verbs", []):
                    window_size = rule.get("window_size", 4)
                    break
            
            preceding_words = prefix_words[-window_size:] if prefix_words else []
            
            # Check if negated
            negated = False
            for rule in negation_rules:
                if verb in rule.get("verbs", []):
                    for negator in rule.get("negators", []):
                        neg_words = negator.split()
                        if len(neg_words) == 1:
                            if neg_words[0] in preceding_words:
                                negated = True
                                break
                        else:
                            for i in range(len(preceding_words) - len(neg_words) + 1):
                                if preceding_words[i:i+len(neg_words)] == neg_words:
                                    negated = True
                                    break
                    if negated:
                        break
            
            if not negated:
                has_state_change = True
                break
    else:
        has_state_change = False

    has_read_only = bool(_READ_ONLY_VERBS.search(msg))
    has_complexity = bool(_COMPLEXITY_HIGH.search(msg))
    multi_req_count = len(_MULTI_REQUIREMENT.findall(msg))
    msg_len = len(msg_stripped)
    is_trivial = _is_trivial_task(msg_stripped)

    # Pure read-only observation → Tier 1, even if message is long
    if has_read_only and not has_state_change:
        tier = 1
    # Trivial task -> Tier 1
    elif is_trivial:
        tier = 1
    # State-change with high complexity signals → Tier 3
    elif has_state_change and (has_complexity or multi_req_count >= 3 or msg_len > 300):
        tier = 3
    # State-change with moderate complexity → Tier 2
    elif has_state_change or (has_complexity and not has_read_only):
        tier = 2
    # Fallback: use length-based heuristics for ambiguous cases
    elif msg_len > 200 or has_complexity:
        tier = 3
    elif msg_len > 80 or multi_req_count >= 2:
        tier = 2
    else:
        tier = 1

    return {
        "type": "task",
        "domain": domain,
        "secondary_domains": secondary_domains,
        "ranked_domains": ranked_domains,
        "tier": tier,
        "is_read_only": (has_read_only and not has_state_change) or is_trivial,
    }


def _detect_followup(msg: str, session: dict) -> bool:
    """Detect if this message is a follow-up to the previous task."""
    if not session.get("last_task"):
        return False
    # Pronouns referencing previous work
    followup_signals = re.compile(
        r"\b(it|this|that|those|these|the\s*(same|previous|last)|"
        r"also|too|as\s*well|instead|rather|but\s*(make|change|update)|"
        r"now\s*(make|change|add|remove|fix)|actually|wait)\b", re.I
    )
    if followup_signals.search(msg) and len(msg.strip()) < 100:
        return True
    return False


def _generate_task_plan(user_message: str, domain: str) -> list:
    """Extracts 3-5 specific requirements or actions from user_message to generate a dynamic plan."""
    # Split by newlines, semicolons, and periods (with space)
    raw_sentences = re.split(r'[\n;]|\.\s+', user_message)
    requirements = []
    
    # Simple clean up of bullet points and numbering
    bullet_cleanup = re.compile(r'^\s*([-*+•]|\d+\.?)\s*')
    
    for sentence in raw_sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # Remove leading bullets/numbers
        sentence = bullet_cleanup.sub('', sentence).strip()
        if len(sentence) < 12:
            continue
            
        # Ignore purely conversational greetings or thanks
        if _GREETING_PATTERNS.search(sentence) or _THANKS_PATTERNS.search(sentence) or _FAREWELL_PATTERNS.search(sentence):
            continue
            
        # Check if the sentence has action words or domain keywords to represent a real requirement
        if _ACTION_VERBS.search(sentence) or _CODE_KEYWORDS.search(sentence) or len(sentence) > 30:
            requirements.append(sentence)
            
    # If list is empty, split by logical connectors like 'and also', 'as well as', 'then'
    if not requirements and len(user_message) > 30:
        parts = re.split(r'\b(?:and\s+also|then|additionally|plus)\b', user_message, flags=re.I)
        for part in parts:
            part = bullet_cleanup.sub('', part.strip()).strip()
            if len(part) >= 12:
                requirements.append(part)
                
    # Format as checklist items
    plan_items = []
    for i, req in enumerate(requirements[:5]):
        # Capitalize first letter, ensure ends with punctuation
        req_clean = req[0].upper() + req[1:]
        if not req_clean.endswith(('.', '?', '!')):
            req_clean += '.'
        plan_items.append({
            "id": f"plan_{i+1}",
            "check": f"Dynamic Plan: {req_clean}",
            "severity": "high"
        })
        
    if not plan_items:
        plan_items.append({
            "id": "plan_1",
            "check": "Dynamic Plan: Address all implicit and explicit instructions in user request.",
            "severity": "high"
        })
        
    return plan_items


def _normalize_pattern(msg: str) -> str:
    """Normalizes a query message to extract its core intent pattern.
    - Strips articles (a, an, the).
    - Normalizes whitespace.
    - If < 5 tokens, requires exact match (with word boundaries).
    - If >= 5 tokens, falls to wildcard at the end.
    """
    if not msg:
        return ""
    line = msg.strip().split("\n")[0].lower()
    line = re.sub(r'\b(a|an|the)\b', '', line)
    line = re.sub(r'\s+', ' ', line).strip()
    words = line.split()
    if not words:
        return ""
    if len(words) < 5:
        escaped_words = [re.escape(w) for w in words]
        pattern = r"^\s*" + r"\s+".join(escaped_words) + r"\s*$"
    else:
        core_words = words[:5]
        escaped_words = [re.escape(w) for w in core_words]
        pattern = r"^\s*" + r"\s+".join(escaped_words) + r".*"
    return pattern
