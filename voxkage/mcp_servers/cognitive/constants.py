import os
import re
import sys

# Path resolution relative to this file:
# This file is in voxkage/mcp_servers/cognitive/constants.py
# _ROOT should point to the voxkage root directory (voxkage/)
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PARENT = os.path.dirname(_ROOT)

if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Storage paths
_COG_DIR = os.path.join(os.path.expanduser("~"), ".voxkage", "cognitive")
_PROFILE_FILE = os.path.join(_COG_DIR, "profile.json")
_ANTI_PATTERNS_FILE = os.path.join(_COG_DIR, "anti_patterns.json")
_CALIBRATION_FILE = os.path.join(_COG_DIR, "calibration.json")
_SESSION_FILE = os.path.join(_COG_DIR, "session_state.json")
_HISTORY_DIR = os.path.join(_COG_DIR, "task_history")
_HISTORY_FILE = os.path.join(_HISTORY_DIR, "recent.jsonl")
_DYNAMIC_RULES_FILE = os.path.join(_COG_DIR, "dynamic_rules.json")
# _DYNAMIC_RULES_TEMPLATE: data directory is at voxkage/data
_DYNAMIC_RULES_TEMPLATE = os.path.join(_ROOT, "data", "dynamic_rules_template.json")

# Checklists shipped with the package (read-only)
_CHECKLISTS_DIR = os.path.join(_ROOT, "data", "checklists")
_WRITABLE_CHECKLISTS_DIR = os.path.join(_COG_DIR, "checklists")
_LOCK_FILE = os.path.join(_COG_DIR, "soul.lock")
_ARCHIVED_FILE = os.path.join(_COG_DIR, "anti_patterns_archived.json")
_DOMAIN_MISMATCHES_FILE = os.path.join(_COG_DIR, "domain_mismatches.json")
_EVOLVED_RULES_FILE = os.path.join(_COG_DIR, "evolved_rules.json")
_EVOLVED_RULES_PENDING_FILE = os.path.join(_COG_DIR, "evolved_rules_pending.json")
_CHECKLISTS_PENDING_FILE = os.path.join(_COG_DIR, "checklists_pending.json")

# Create necessary directories
os.makedirs(_COG_DIR, exist_ok=True)
os.makedirs(_HISTORY_DIR, exist_ok=True)
os.makedirs(_WRITABLE_CHECKLISTS_DIR, exist_ok=True)

# Guard windows for start_turn timing
_GUARD_WINDOWS = {1: 120, 2: 300, 3: 600}

# Conversation patterns (score -1 each)
_GREETING_PATTERNS = re.compile(
    r"\b(hey|hi|hello|howdy|yo|sup|good\s*(morning|evening|night|afternoon)|"
    r"what'?s?\s*up|how\s*(are|r)\s*(you|u)|how'?s?\s*it\s*going)\b", re.I
)
_FAREWELL_PATTERNS = re.compile(
    r"\b(bye|goodbye|good\s*night|see\s*(you|ya)|later|ciao|peace\s*out|"
    r"take\s*care|gn|night)\b", re.I
)
_THANKS_PATTERNS = re.compile(
    r"\b(thanks?|thank\s*you|thx|ty|appreciated|cheers)\b", re.I
)
_SOCIAL_PATTERNS = re.compile(
    r"\b(what\s*do\s*you\s*think|how\s*are\s*you|tell\s*me\s*about\s*yourself|"
    r"who\s*are\s*you|what'?s?\s*your\s*(name|opinion)|you'?re?\s*(cool|awesome|great|funny))\b", re.I
)

# Task patterns (score +1 each)
_ACTION_VERBS = re.compile(
    r"\b(build|create|make|fix|search|find|open|play|send|check|run|write|edit|"
    r"delete|download|install|deploy|analyze|debug|test|design|implement|update|"
    r"configure|move|copy|rename|show|get|fetch|read|list|set|change|modify|"
    r"add|remove|start|stop|restart|kill|close|browse|scrape|scan|compress|"
    r"extract|convert|generate|explain|research|compare|summarize|refactor|"
    r"optimize|sort|clean|format|index|commit|push|pull|merge|clone|log|"
    r"schedule|connect|disconnect|ping|monitor|backup|restore)\b", re.I
)
_CODE_KEYWORDS = re.compile(
    r"\b(function|class|method|api|endpoint|database|server|frontend|backend|"
    r"css|html|javascript|python|typescript|react|node|express|django|flask|"
    r"sql|mongodb|redis|docker|git|npm|pip|webpack|vite|component|module|"
    r"import|export|variable|constant|error|bug|exception|crash|test|lint|"
    r"type|interface|struct|enum|async|await|promise|callback|hook|state|"
    r"route|middleware|controller|model|view|template|schema|migration|"
    r"script|package|dependency|config|env|token|auth|login|signup|"
    r"password|session|cookie|jwt|oauth|websocket|rest|graphql|grpc)\b", re.I
)
_PATH_PATTERN = re.compile(r"[A-Za-z]:\\|/[a-z]+/|~/|\.\./|\./|\\\\")
_URL_PATTERN = re.compile(r"https?://|www\.", re.I)

# Risk-based tier signals (v3)
# Pure observation verbs → read-only, cap tier at 1 regardless of message length
_READ_ONLY_VERBS = re.compile(
    r"\b(tell\s*me|show\s*me|what\s*is|what\s*are|what\s*('?s|was|were)|explain|describe|"
    r"how\s*(does|do|did)|how\s*(many|much|long|far|old)|summarize|check|status|list|"
    r"view|look\s*at|inspect|see|monitor|report|display|output|find|which|review|audit|"
    r"what\s*changed|diff|log|history|who\s*(is|are|was)|where\s*(is|are|was|can)|"
    r"when\s*(is|was|did|does)|translate|convert\s*(this|that)|read\s*(this|that|out)|show\s*me)\b",
    re.I
)
# State-change verbs → mutations, minimum Tier 2
_STATE_CHANGE_VERBS = re.compile(
    r"\b(build|create|make|write|deploy|install|push|commit|delete|modify|update|"
    r"configure|set\s*up|initialize|init|migrate|refactor|implement|add|remove|change|"
    r"move|rename|copy|edit|fix|patch|upgrade|rollback|reset|format|clean|compress|"
    r"extract|generate|send|download|backup|restore|kill|restart|shutdown|start|stop)\b",
    re.I
)

# Domain classification keywords
_DOMAIN_KEYWORDS = {
    "frontend": re.compile(
        r"\b(frontend|front-end|ui|ux|css|html|react|vue|angular|svelte|"
        r"responsive|layout|style|animation|component|page|button|form|"
        r"modal|navbar|sidebar|footer|header|card|grid|flex|tailwind|"
        r"bootstrap|design|color|font|theme|dark\s*mode|mobile|tablet|"
        r"web\s*app|website|landing\s*page|dashboard)\b", re.I
    ),
    "backend": re.compile(
        r"\b(backend|back-end|api|server|endpoint|database|sql|mongo|"
        r"redis|express|django|flask|fastapi|node|rest|graphql|grpc|"
        r"auth|authentication|middleware|route|controller|model|schema|"
        r"migration|orm|query|table|index|cache|session|jwt|oauth|"
        r"microservice|docker|deploy|nginx|kubernetes|lambda|serverless)\b", re.I
    ),
    "research": re.compile(
        r"\b(search|research|find\s*(out|info|about)|look\s*up|what\s*is|"
        r"who\s*is|when\s*(did|was|is)|where\s*(is|can)|how\s*(to|does|do)|"
        r"latest|news|weather|price|stock|compare|review|summarize|"
        r"explain|article|paper|blog|documentation|wiki|learn|guide|tutorial)\b", re.I
    ),
    "system": re.compile(
        r"\b(system|os|windows|process|kill|restart|shutdown|sleep|"
        r"battery|disk|memory|cpu|ram|wifi|bluetooth|network|volume|"
        r"brightness|wallpaper|screenshot|clipboard|notification|"
        r"install|uninstall|update|driver|registry|service|startup|"
        r"firewall|antivirus|recycle\s*bin|task\s*manager|terminal|"
        r"command|powershell|bash|shell|folder|directory|file|"
        r"git\s*status|git\s*log|git\s*diff|uncommitted|staged|branch)\b", re.I
    ),
    "coding": re.compile(
        r"\b(code|coding|program|script|function|class|module|package|"
        r"debug|refactor|optimize|test|lint|compile|build|fix\s*(the\s*)?bug|"
        r"implement|write\s*(a\s*)?(function|script|class|code|program)|"
        r"git|commit|branch|merge|pull\s*request|review|pr|repo|"
        r"algorithm|data\s*structure|pattern|architecture|dependency|"
        r"import|export|type|interface|generic|inheritance)\b", re.I
    ),
    "creative": re.compile(
        r"\b(cook|cooking|recipe|bake|baking|food|meal|dish|ingredient|prepare|how\s*to\s*make|"
        r"write\s*(a\s*)?(story|poem|essay|letter|email|blog|caption|joke|song|script|speech)|"
        r"creative|brainstorm|idea|suggest|recommend|design\s*idea|draw|paint|compose|"
        r"lyrics|fiction|narrative|character|plot|describe\s*(how|what)|craft|art)\b", re.I
    ),
    "analysis": re.compile(
        r"\b(analysis|analyze|investigate|evaluate|review|inspect|audit|"
        r"profile|diagnostic|metrics|log|logs|chart|plot|graph|report|trend)\b", re.I
    ),
    "planning": re.compile(
        r"\b(planning|plan|design|architecture|roadmap|todo|task\s*list|"
        r"strategy|milestone|phases|step-by-step|diagram|flowchart|outline)\b", re.I
    ),
    "general": re.compile(
        r"\b(general|chitchat|hello|help|info|status|version|plugins|about|"
        r"system\s*info|uptime|date|time|volume|brightness|media|spotify)\b", re.I
    ),
    "data": re.compile(
        r"\b(data|database|dataset|sql|json|csv|xml|parquet|dataframe|pandas|"
        r"numpy|ml|machine\s*learning|train|predict|model|numpy|scipy|tensor|"
        r"torch|sklearn|cleaning|preprocess|ingest|etl|analytics)\b", re.I
    ),
    "devops": re.compile(
        r"\b(devops|deploy|ci|cd|pipeline|github\s*action|docker|kubernetes|"
        r"aws|gcp|azure|terraform|ansible|secret|env|environment|nginx|"
        r"server\s*config|ssh|ssl|cert|build|compile|package|publish)\b", re.I
    ),
}

# Complexity signals for tiering
_COMPLEXITY_HIGH = re.compile(
    r"\b(make\s*sure|perfect|thorough|comprehensive|complete|detailed|"
    r"production|robust|scalable|enterprise|full\s*stack|from\s*scratch|"
    r"end\s*to\s*end|step\s*by\s*step|multiple|several|all|every|entire)\b", re.I
)
_MULTI_REQUIREMENT = re.compile(r"\b(and|also|plus|additionally|then|after\s*that|next)\b", re.I)

# Code-specific checklist noise keywords
_CODE_CHECKLIST_KEYWORDS = re.compile(
    r"\b(sql|injection|database|query|parameterized|api|endpoint|http|cors|"
    r"migration|auth|jwt|hardcoded|secrets|lint|imports|typescript|node\b)\b", re.I
)

OUTPUT_TYPE_FILTERS = {
    "markdown": {
        "exclude":   ["imports_correct", "types_correct", "lint_clean", "tests_exist"],
        "downgrade": {"security": "low", "error_handling": "low"},
    },
    "command": {
        "exclude":   ["imports_correct", "types_correct", "responsive", "accessible"],
        "downgrade": {},
    },
    "research": {
        "exclude":   ["imports_correct", "types_correct", "lint_clean", "tests_exist"],
        "downgrade": {"security": "low", "error_handling": "low"},
    },
    "general": {
        "exclude":   ["imports_correct", "types_correct", "lint_clean", "tests_exist"],
        "downgrade": {"security": "low", "error_handling": "low"},
    },
}
