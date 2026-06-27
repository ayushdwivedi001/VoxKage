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
_CLASSIFICATION_EXAMPLES_FILE = os.path.join(_COG_DIR, "classification_examples.json")

# Negation of domains to avoid false positives (e.g. 'coding no', 'dont code')
_DOMAIN_NEGATION_PATTERNS = re.compile(
    r"\b(coding\s+no|not\s+coding|no\s+code|dont\s+code|don't\s+code|code\s+not|no\s+coding|skip\s+code|without\s+code)\b",
    re.I
)

# Semantic verb-noun pair mappings to handle fallback domain classification
_VERB_NOUN_DOMAIN_MAP = [
    (re.compile(r"\b(build|write|create|make|generate)\b", re.I), re.compile(r"\b(report|summary|article|matrix|comparison|doc|document|text|notes)\b", re.I), "research"),
    (re.compile(r"\b(build|write|create|make|generate|implement)\b", re.I), re.compile(r"\b(code|function|class|script|program|app|application|server|mcp|tool|backend|frontend)\b", re.I), "coding"),
    (re.compile(r"\b(search|find|look\s+up|research|fetch|get|scrape|query)\b", re.I), 
     re.compile(r"^(?!.*\b(code|function|class|script|program|app|application|server|mcp|tool|backend|frontend|api|route|database|postgres|redis|mongodb|docker|github|workflow|pipeline|pandas|dataframe|dataset|logs|trace|leak|wallpaper|microphone|volume|brightness|nginx|reverse proxy|ssl|certbot|recipe|welcome email|email|poem|story|names|names for|brainstorm|typescript|javascript|python|js|ts|rust|golang|go|java|c\+\+|algorithm|binary)\b).*$", re.I), 
     "research"),
    (re.compile(r"\b(analyze|audit|check|evaluate|inspect)\b", re.I), re.compile(r"\b(codebase|repo|file|quality|bug|error|issue|warning|lint|mismatch|performance|trace)\b", re.I), "analysis"),
]

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
    r"schedule|connect|disconnect|ping|monitor|backup|restore|look|preprocess|"
    r"mute|brainstorm|inspect)\b", re.I
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

# Domain classification keywords (separated into intent verbs and topic nouns)
_DOMAIN_SEMANTICS_RAW = {
    "frontend": {
        "verbs": ["render", "style", "animate", "align", "format", "layout", "design", "paint", "draw", "theme"],
        "nouns": ["frontend", "front-end", "ui", "ux", "css", "html", "react", "vue", "angular", "svelte", "responsive", "component", "page", "button", "form", "modal", "navbar", "sidebar", "footer", "header", "card", "grid", "flex", "tailwind", "bootstrap", "color", "font", "web\\s*app", "website", "landing\\s*page", "dashboard"]
    },
    "backend": {
        "verbs": ["serve", "route", "authenticate", "cache", "query", "connect", "listen"],
        "nouns": ["backend", "back-end", "api", "server", "endpoint", "database", "express", "django", "flask", "fastapi", "node", "rest", "graphql", "grpc", "auth", "middleware", "session", "jwt", "oauth", "microservice", "docker", "nginx", "kubernetes", "lambda", "serverless", "controller", "upload", "uploading"]
    },
    "research": {
        "verbs": ["search", "research", "find\\s*(out|info|about)", "look\\s*up", "compare", "review", "summarize", "explain", "learn", "investigate", "browse", "scrape", "fetch", "query", "check\\s*out"],
        "nouns": ["latest", "news", "weather", "price", "stock", "article", "paper", "blog", "documentation", "wiki", "guide", "tutorial", "comparison", "difference", "information"]
    },
    "system": {
        "verbs": ["restart", "shutdown", "sleep", "kill", "install", "uninstall", "ping", "backup", "restore", "set\\s*wallpaper", "set\\s*volume", "set\\s*brightness"],
        "nouns": ["system", "os", "windows", "process", "battery", "disk", "memory", "cpu", "ram", "wifi", "bluetooth", "network", "volume", "brightness", "wallpaper", "screenshot", "clipboard", "registry", "driver", "uncommitted", "staged", "terminal", "powershell", "bash", "shell", "folder", "directory", "file"]
    },
    "coding": {
        "verbs": ["code", "program", "script", "debug", "refactor", "optimize", "test", "lint", "compile", "build", "fix\\s*(the\\s*)?bug", "implement", "write\\s*(a\\s*)?(function|script|class|code|program)", "develop", "commit", "push", "pull", "merge", "clone"],
        "nouns": ["variable", "constant", "class", "function", "method", "interface", "generic", "inheritance", "algorithm", "data\\s*structure", "bug", "error", "exception", "crash", "syntax", "repo", "pr", "pull\\s*request", "dependency", "import", "export", "loop", "looping"]
    },
    "creative": {
        "verbs": ["cook", "bake", "write\\s+(?:[a-zA-Z0-9'\"\\s]+\\s+)?(story|poem|essay|letter|email|blog|caption|joke|song|script|speech|recipe|welcome)", "brainstorm", "draft", "imagine", "compose"],
        "nouns": ["recipe", "food", "meal", "dish", "ingredient", "story", "poem", "essay", "song", "lyrics", "art", "craft", "creative", "idea", "fiction", "narrative", "character", "plot", "names"]
    },
    "analysis": {
        "verbs": ["analyze", "evaluate", "inspect", "audit", "profile", "diagnose", "graph", "plot", "measure"],
        "nouns": ["analysis", "metrics", "logs", "diagnostics", "profile", "report", "trend", "performance", "chart", "plot", "graph", "leak", "trace", "traces"]
    },
    "planning": {
        "verbs": ["plan", "design", "outline", "map", "schedule", "roadmap"],
        "nouns": ["planning", "milestones", "phases", "todo", "task\\s*list", "strategy", "flowchart", "architecture", "plan"]
    },
    "general": {
        "verbs": ["help", "greet", "chat", "socialize"],
        "nouns": ["general", "chitchat", "info", "status", "version", "plugins", "about", "uptime", "date", "time", "spotify", "media", "mute"]
    },
    "data": {
        "verbs": ["clean", "preprocess", "predict", "train", "etl", "ingest"],
        "nouns": ["data", "dataset", "dataframe", "pandas", "numpy", "ml", "machine\\s*learning", "sql", "json", "csv", "xml", "parquet", "tensor", "torch", "sklearn"]
    },
    "devops": {
        "verbs": ["deploy", "build", "publish", "ssh", "ssl"],
        "nouns": ["devops", "ci", "cd", "pipeline", "github\\s*action", "docker", "kubernetes", "aws", "gcp", "azure", "terraform", "ansible", "nginx", "server\\s*config"]
    }
}

_DOMAIN_SEMANTICS = {
    d: {
        "verbs": re.compile(r"\b(" + "|".join(s["verbs"]) + r")\b", re.I),
        "nouns": re.compile(r"\b(" + "|".join(s["nouns"]) + r")\b", re.I)
    }
    for d, s in _DOMAIN_SEMANTICS_RAW.items()
}

_DOMAIN_KEYWORDS = {
    d: re.compile(r"\b(" + "|".join(s["verbs"] + s["nouns"]) + r")\b", re.I)
    for d, s in _DOMAIN_SEMANTICS_RAW.items()
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
        "exclude": [
            "imports_correct", "types_correct", "lint_clean", "tests_exist",
            "syntax_valid", "database", "tests", "input_validation",
            "env_config", "security", "error_handling", "edge_cases",
            "no_duplication", "codebase_style", "responsive", "accessible"
        ],
        "exclude_prefixes": ["corr_"],
        "downgrade": {}
    },
    "command": {
        "exclude": [
            "imports_correct", "types_correct", "responsive", "accessible",
            "database", "tests", "security", "error_handling"
        ],
        "exclude_prefixes": [],
        "downgrade": {},
    },
    "research": {
        "exclude": [
            "imports_correct", "types_correct", "lint_clean", "tests_exist",
            "syntax_valid", "database", "tests", "input_validation",
            "env_config", "security", "error_handling", "edge_cases",
            "no_duplication", "codebase_style", "responsive", "accessible"
        ],
        "exclude_prefixes": ["corr_"],
        "downgrade": {},
    },
    "general": {
        "exclude": [
            "imports_correct", "types_correct", "lint_clean", "tests_exist",
            "syntax_valid", "database", "tests", "input_validation",
            "env_config", "security", "error_handling", "edge_cases",
            "no_duplication", "codebase_style", "responsive", "accessible"
        ],
        "exclude_prefixes": ["corr_"],
        "downgrade": {},
    },
    "code": {
        "exclude": [],
        "exclude_prefixes": [],
        "downgrade": {}
    },
    "config": {
        "exclude": ["tests", "database", "imports_correct", "types_correct"],
        "exclude_prefixes": [],
        "downgrade": {}
    }
}
