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
    _DOMAIN_SEMANTICS,
    _COMPLEXITY_HIGH,
    _MULTI_REQUIREMENT
)
from .storage import _load_dynamic_rules, _load_evolved_rules


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


_TYPO_MAP = {
    "soo": "so",
    "dont": "don't",
    "wanna": "want to",
    "gonna": "going to",
    "gotta": "got to",
}


def _preprocess_message(msg: str) -> str:
    """Preprocesses user messages to normalize spelling, repeated letters, and spacing."""
    if not msg:
        return ""
    # Normalize whitespace
    msg = re.sub(r"\s+", " ", msg)
    # Deduplicate 3+ consecutive identical characters (e.g. 'sooooo' -> 'so')
    msg = re.sub(r"([a-zA-Z])\1{2,}", r"\1", msg)
    
    words = msg.split()
    for i, w in enumerate(words):
        w_low = w.lower()
        clean_w = re.sub(r"^[^\w']+|[^\w']+$", "", w_low)
        if clean_w in _TYPO_MAP:
            words[i] = w_low.replace(clean_w, _TYPO_MAP[clean_w])
            
    return " ".join(words)


def _score_domains(msg: str) -> tuple:
    """
    Computes domain scores using semantic verb (5.0 weight) and noun (1.0 weight) matches.
    Applies active evolved rules, negations, verb-noun mappings, and example-based adjustments.
    Returns:
      (domain_scores, primary_domain, secondary_domains, reason_codes)
    """
    preprocessed_msg = _preprocess_message(msg)
    domain_scores = {}
    reason_codes = []
    
    # 1. Base keyword scoring
    for d, sem in _DOMAIN_SEMANTICS.items():
        verb_matches = len(sem["verbs"].findall(preprocessed_msg))
        noun_matches = len(sem["nouns"].findall(preprocessed_msg))
        score = (verb_matches * 5.0) + (noun_matches * 1.0)
        if score > 0:
            domain_scores[d] = score
            reason_codes.append(f"{d}_keywords: +{score:.1f}")

    # 2. Active Evolved rules adjustments
    try:
        evolved_rules = _load_evolved_rules().get("rules", [])
        for rule in evolved_rules:
            if rule.get("status") == "active" and rule.get("type") in ["domain_reclassification", "domain_rule"]:
                pattern = rule.get("pattern")
                if pattern:
                    if re.search(pattern, preprocessed_msg, re.I):
                        target_domain = rule.get("domain")
                        domain_scores[target_domain] = domain_scores.get(target_domain, 0.0) + 20.0
                        reason_codes.append(f"evolved_rule_{target_domain}: +20.0")
    except Exception:
        pass

    # 3. Verb-Noun mapping semantic boost
    from .constants import _VERB_NOUN_DOMAIN_MAP
    for verb_pat, noun_pat, target_domain in _VERB_NOUN_DOMAIN_MAP:
        if verb_pat.search(preprocessed_msg) and noun_pat.search(preprocessed_msg):
            domain_scores[target_domain] = domain_scores.get(target_domain, 0.0) + 8.0
            reason_codes.append(f"verb_noun_match_{target_domain}: +8.0")

    # 4. Domain negation pattern checks
    from .constants import _DOMAIN_NEGATION_PATTERNS
    if _DOMAIN_NEGATION_PATTERNS.search(preprocessed_msg):
        if "coding" in domain_scores:
            domain_scores["coding"] = max(0.0, domain_scores["coding"] - 20.0)
            reason_codes.append("coding_negation: -20.0")

    # 5. Context-aware overrides (e.g. report about coding is research)
    report_signals = re.compile(r"\b(report|summary|article|comparison|matrix|comparison matrix|review|paper|guide|tutorial|weather|news)\b", re.I)
    if report_signals.search(preprocessed_msg):
        if "research" in domain_scores:
            domain_scores["research"] = domain_scores.get("research", 0.0) + 8.0
            reason_codes.append("report_signals_research: +8.0")
        else:
            domain_scores["research"] = 8.0
            reason_codes.append("report_signals_research: +8.0")
            
        code_dev_signals = re.compile(r"\b(write\s+code|implement\s+function|write\s+function|write\s+class|refactor\s+code|debug\s+code|syntax\s+error)\b", re.I)
        if not code_dev_signals.search(preprocessed_msg) and "coding" in domain_scores:
            domain_scores["coding"] = max(0.0, domain_scores["coding"] - 12.0)
            reason_codes.append("coding_report_penalty: -12.0")

    # 5.5. Boost specialized domains when highly unique topic terms are present
    special_boosts = [
        # DevOps
        (re.compile(r"\b(github\s*action|workflow|pipeline|docker|kubernetes|terraform|ansible|nginx|reverse proxy|ssl|certbot|deploy)\b", re.I), "devops", 20.0),
        # Backend
        (re.compile(r"\b(postgres|mysql|sqlite|mongodb|redis|database|api|endpoint|controller|middleware|jwt|oauth|express|django|flask|fastapi)\b", re.I), "backend", 12.0),
        # Frontend
        (re.compile(r"\b(tailwind|flexbox|flex|grid|css|html|align|styling|style|modern look|responsive|modal|card|button|navbar)\b", re.I), "frontend", 12.0),
        # Data
        (re.compile(r"\b(pandas|dataframe|dataset|numpy|scikit-learn|machine learning|sklearn)\b", re.I), "data", 15.0),
        # Planning
        (re.compile(r"\b(roadmap|gantt|milestones|phases|todo list|todo|schema\s*design|map\s*out|architecture)\b", re.I), "planning", 15.0),
        # Creative
        (re.compile(r"\b(poem|lyrics|joke|story|recipe|welcome\s*email|caption|song)\b", re.I), "creative", 20.0),
        # Analysis
        (re.compile(r"\b(performance\s*metrics|metrics|logs|diagnostics|inspect\s*logs)\b", re.I), "analysis", 15.0)
    ]
    for pattern, target_domain, boost in special_boosts:
        if pattern.search(preprocessed_msg):
            domain_scores[target_domain] = domain_scores.get(target_domain, 0.0) + boost
            reason_codes.append(f"special_boost_{target_domain}: +{boost:.1f}")

    # 6. LAYER 1.5: Similarity-based adjustments
    try:
        from .classification_examples import _get_fingerprint, _find_similar_examples
        fingerprint = _get_fingerprint(preprocessed_msg)
        similar = _find_similar_examples(fingerprint, top_n=1)
        if similar:
            best_match = similar[0]
            if best_match["similarity"] >= 0.4:
                correct_d = best_match["correct_domain"]
                classifier_said = best_match["classifier_said"]
                was_corrected = best_match["was_corrected"]
                
                if was_corrected:
                    if classifier_said in domain_scores:
                        domain_scores[classifier_said] = max(0.0, domain_scores[classifier_said] - 15.0)
                        reason_codes.append(f"similarity_penalty_{classifier_said}: -15.0")
                    domain_scores[correct_d] = domain_scores.get(correct_d, 0.0) + 15.0
                    reason_codes.append(f"similarity_boost_{correct_d}: +15.0")
                else:
                    domain_scores[correct_d] = domain_scores.get(correct_d, 0.0) + 5.0
                    reason_codes.append(f"similarity_reinforce_{correct_d}: +5.0")
    except Exception:
        pass

    ranked_domains = sorted(
        [(d, s) for d, s in domain_scores.items()],
        key=lambda x: x[1], reverse=True
    )

    if domain_scores:
        domain = ranked_domains[0][0]
    else:
        domain = "general"

    secondary_domains = []
    for d, s in ranked_domains:
        if d != domain and s >= 1.0:
            secondary_domains.append(d)
            if len(secondary_domains) == 2:
                break

    return domain_scores, domain, secondary_domains, reason_codes


def _make_task_response(domain: str, secondary_domains: list, ranked_domains: list, tier: int, is_read_only: bool, reason_codes: list, msg_stripped: str) -> dict:
    """Helper to construct a standardized task response with confidence, reason codes, and similar examples."""
    if ranked_domains:
        top_score = ranked_domains[0][1]
        second_score = ranked_domains[1][1] if len(ranked_domains) > 1 else 0.0
        if top_score + second_score > 0.0:
            confidence = float(top_score / (top_score + second_score))
        else:
            confidence = 1.0
    else:
        confidence = 0.0

    similar_examples = []
    try:
        from .classification_examples import _get_fingerprint, _find_similar_examples
        fingerprint = _get_fingerprint(msg_stripped)
        similar_examples = _find_similar_examples(fingerprint, top_n=1)
    except Exception:
        pass

    return {
        "type": "task",
        "domain": domain,
        "secondary_domains": secondary_domains,
        "ranked_domains": ranked_domains,
        "tier": tier,
        "is_read_only": is_read_only,
        "confidence": confidence,
        "reason_codes": reason_codes,
        "similar_examples": similar_examples
    }


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
        dom_match = re.search(r"^\s*(?:Domains?|Categories|Domain):\s*([a-zA-Z\s,;➔→\-\(\)>]+)", line, re.I)
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
        
        tier_match = re.search(r"^\s*(?:Tiers?|Complexity):\s*([1-3])", line, re.I)
        if tier_match:
            declared_tier = int(tier_match.group(1))

    if declared_domains:
        primary_domain = declared_domains[0]
        secondary_domains = declared_domains[1:3]
        tier = declared_tier if declared_tier is not None else 1
        return _make_task_response(
            primary_domain,
            secondary_domains,
            [(d, 5.0) for d in declared_domains],
            tier,
            False,
            [f"declared_header: {primary_domain}"],
            msg_stripped
        )

    # v8: Structural task header detection — forces Tier 3 immediately
    # These patterns appear in well-specified task prompts and are unambiguous task signals
    _STRUCTURAL_TASK_HEADERS = re.compile(
        r"\b(task\s*breakdown|phase\s*\d|step\s*\d|requirements?:|phases:|steps:|breakdown:|"
        r"objective:|goal:|instructions?:|sub[\s-]?tasks?:|deliverables?:)\b",
        re.I
    )
    if _STRUCTURAL_TASK_HEADERS.search(msg_stripped):
        domain_scores, domain, secondary_domains, reason_codes = _score_domains(msg_stripped)
        ranked_domains = sorted(
            [(d, s) for d, s in domain_scores.items()], key=lambda x: x[1], reverse=True
        )
        return _make_task_response(domain, secondary_domains, ranked_domains, 3, False, reason_codes, msg_stripped)


    # Load dynamic rules
    rules = _load_dynamic_rules()

    # ── Force Tier 1 matching ──
    for pat in rules.get("force_tier_1_patterns", []):
        try:
            if re.search(pat, msg_stripped, re.I):
                domain_scores, domain, secondary_domains, reason_codes = _score_domains(msg)
                ranked_domains = sorted(
                    [(d, s) for d, s in domain_scores.items()],
                    key=lambda x: x[1], reverse=True
                )
                return _make_task_response(domain, secondary_domains, ranked_domains, 1, True, reason_codes, msg_stripped)
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
    # Questions asking for information/action (strip greetings first)
    msg_cleaned_prefix = re.sub(r"^(?:hey|hi|hello|yo|sup|jarvis|voxkage|sir|\s+)+\b\s*", "", msg_stripped, flags=re.I).strip()
    if re.match(r"^(what|how|where|when|why|can\s*you|could\s*you|please|show|tell\s*me)\b", msg_cleaned_prefix, re.I):
        task_score += 1

    # v8: Informal human speech task patterns
    # These fire regardless of message length — people speak to VoxKage this way naturally.
    # "i want you to", "can you just", "yo can you", "soo basically" wrapping a task request
    _INFORMAL_TASK_PATTERNS = [
        # Direct delegation patterns — human asking AI to do something
        (r"\bi\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\s+\w", 3),
        (r"\bcan\s+you\s+(?:please\s+|just\s+|quickly\s+|help\s+me\s+)?(?:do|go|check|find|get|look|run|write|build|make|create|test|research|compare|search|set up|pull|push|fix)\b", 3),
        (r"\bcould\s+you\s+(?:please\s+|just\s+)?(?:do|go|check|find|get|look|run|write|build|make|create|test|research|compare|search)\b", 2),
        (r"\b(?:yo|hey)\s+can\s+you\b", 2),
        # Informal openers that wrap a task
        (r"\bso+\s+(?:basically|i|can|could|let|like)\b", 1),
        (r"\balright\s+so+\b", 1),
        (r"\bokay\s+so+\b", 1),
        # Multi-step sequencing — strong signal that this is a task not a conversation
        (r"(?:once\s+you(?:'re|\s+are)?\s+done|after\s+(?:that|this|doing\s+that)|and\s+then\s+(?:also\s+)?(?:i\s+want|make sure|check))", 3),
        (r"\bthen\s+i\s+(?:want|need)\s+you\s+to\b", 2),
        (r"\bmake\s+sure\s+(?:the|it|that|you)\b", 1),
        (r"\band\s+also\s+(?:double\s+check|verify|make\s+sure|check)\b", 2),
    ]
    informal_task_boost = 0
    for pattern, weight in _INFORMAL_TASK_PATTERNS:
        if re.search(pattern, msg_stripped, re.I):
            informal_task_boost += weight
    # Cap informal boost at 5 so a casual message can't blow past a strong conv_score
    task_score += min(informal_task_boost, 5)

    # v8: Numbered list detection — multi-step task breakdown signal
    # Works on inline numbering ("1. do this 2. do that") and multi-line
    numbered_items = re.findall(r"(?:^|\n|\s)\d+[\.\.\)]\s+\w", msg_stripped)
    if len(numbered_items) >= 2:
        task_score += min(len(numbered_items), 4)  # cap at 4 bonus points

    # Task wins when both present (social wrapping rule)
    if task_score > 0 and conv_score > 0:
        task_score += 1  # bias toward task

    if task_score <= conv_score and conv_score > 0:
        return {"type": "conversation"}

    # ── Domain classification ──
    domain_scores, domain, secondary_domains, reason_codes = _score_domains(msg)
    ranked_domains = sorted(
        [(d, s) for d, s in domain_scores.items()],
        key=lambda x: x[1], reverse=True
    )

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

    # Apply active evolved tier rule overrides
    try:
        evolved_rules = _load_evolved_rules().get("rules", [])
        for rule in evolved_rules:
            if rule.get("status") == "active" and rule.get("type") in ["tier_rule", "tier_adjustment"]:
                pattern = rule.get("pattern")
                if pattern and re.search(pattern, msg, re.I):
                    rule_tier = rule.get("tier")
                    if rule_tier in [1, 2, 3]:
                        tier = rule_tier
    except Exception:
        pass

    return _make_task_response(
        domain,
        secondary_domains,
        ranked_domains,
        tier,
        (has_read_only and not has_state_change) or is_trivial,
        reason_codes,
        msg_stripped
    )


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
