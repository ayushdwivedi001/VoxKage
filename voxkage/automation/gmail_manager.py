import os
import json
import logging
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import base64
from email.mime.text import MIMEText
import httpx

logger = logging.getLogger(__name__)

from voxkage.paths import data_dir

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
DATA_DIR = data_dir()
CREDENTIALS_PATH = os.path.join(DATA_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(DATA_DIR, 'gmail_token.json')

# ═══════════════════════════════════════════════════════════════════
# IN-MEMORY STATE — cleared automatically when VoxKage session ends
# ═══════════════════════════════════════════════════════════════════
_email_cache = {}          # Cached inbox emails for instant re-read
_email_session = None      # Active compose/send session state

# ═══════════════════════════════════════════════════════════════════
# GMAIL SERVICE (Auth)
# ═══════════════════════════════════════════════════════════════════
def _get_gmail_service():
    """Authenticates and returns the Gmail API service."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            logger.error(f"Error loading gmail token: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Failed to refresh Gmail token: {e}")
                creds = None
                
        if not creds:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    "Missing 'data/credentials.json'. Download from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)

# ═══════════════════════════════════════════════════════════════════
# INTENT DETECTION — runs in Python BEFORE the LLM ever sees the prompt
# ═══════════════════════════════════════════════════════════════════
_COMPOSE_PATTERNS = [
    r'(?:send|compose|draft|write|mail|email)\s+(?:an?\s+)?(?:email|mail|message)\s+to\s+([^\s,]+@[^\s,]+)',
    r'(?:send|compose|draft|write|mail|email)\s+to\s+([^\s,]+@[^\s,]+)',
    r'(?:email|mail)\s+([^\s,]+@[^\s,]+)',
]

_CHECK_PATTERNS = [
    r'\b(?:check|read|open|show|any new|what.?s in)\b.*\b(?:email|mail|inbox|gmail)\b',
    r'\b(?:email|mail|inbox|gmail)\b.*\b(?:check|read|open|show)\b',
    r'\bcheck\b.*\b(?:email|mail)s?\b',
    r'\bany\s+new\s+(?:email|mail)s?\b',
]

_SEND_CONFIRM_PATTERNS = [
    r'\b(?:yes|yeah|yep|yup|sure|confirm|go ahead|do it|perfect|looks good|send it|send the|send this|send that|fire it|ship it)\b',
]

_EDIT_PATTERNS = [
    r'\b(?:edit|change|modify|update|fix|rewrite|redo)\b.*\b(?:mail|email|draft|subject|body|message)\b',
    r'\b(?:mail|email|draft|subject|body|message)\b.*\b(?:edit|change|modify|update|fix)\b',
    r'\b(?:make it|make the)\b',
]

_CANCEL_PATTERNS = [
    r'\b(?:cancel|discard|forget it|never ?mind|drop it|stop|don.?t send|abort)\b',
]

def detect_email_intent(prompt: str) -> dict | None:
    """
    Pure Python intent detector. Returns an intent dict or None.
    Runs BEFORE the LLM — the LLM never sees email prompts.
    """
    prompt_lower = prompt.lower().strip()
    
    # 1. Active session actions take priority
    if _email_session and _email_session.get("status") == "ready":
        # Cancel?
        for pat in _CANCEL_PATTERNS:
            if re.search(pat, prompt_lower):
                return {"action": "cancel"}
        
        # Edit?
        for pat in _EDIT_PATTERNS:
            if re.search(pat, prompt_lower):
                return {"action": "edit", "instructions": prompt}
        
        # Send confirmation?
        for pat in _SEND_CONFIRM_PATTERNS:
            if re.search(pat, prompt_lower):
                return {"action": "send"}
    
    # 2. New compose request — extract email address
    for pat in _COMPOSE_PATTERNS:
        m = re.search(pat, prompt_lower)
        if m:
            recipient = m.group(1).strip().rstrip('.')
            # Everything after the email address (or the full prompt) is the instruction
            instructions = prompt  # Pass the full original prompt as instructions
            return {"action": "compose", "recipient": recipient, "instructions": instructions}
    
    # 3. Check inbox
    for pat in _CHECK_PATTERNS:
        if re.search(pat, prompt_lower):
            return {"action": "check_inbox", "query": prompt}
    
    return None

# ═══════════════════════════════════════════════════════════════════
# SUB-AGENT — isolated LLM call for email body generation
# ═══════════════════════════════════════════════════════════════════
def _generate_email_json(context: str, existing_subject: str = None, existing_body: str = None) -> dict:
    """Isolated LLM sub-agent for generating email JSON. No tools, no schemas — just JSON."""
    import asyncio
    from voxkage.llm.gemini_engine import ask_voxkage_brain, clean_cli_json
    
    if existing_subject and existing_body:
        sys_prompt = "You are an email assistant editing an existing draft. Output ONLY valid JSON."
        usr_prompt = (
            f"Current draft:\nSubject: {existing_subject}\nBody: {existing_body}\n\n"
            f"Edit instructions: {context}\n\n"
            "Respond with ONLY a JSON object: {\"subject\": \"...\", \"body\": \"...\"}"
        )
    else:
        sys_prompt = "You are an email assistant. Output ONLY valid JSON."
        usr_prompt = (
            f"Write a professional concise email based on: {context}\n\n"
            "Respond with ONLY a JSON object: {\"subject\": \"...\", \"body\": \"...\"}"
        )

    try:
        full_prompt = f"{sys_prompt}\n\n{usr_prompt}"
        
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import threading
            result = [""]
            def _thread_target():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                result[0] = new_loop.run_until_complete(ask_voxkage_brain(full_prompt))
                new_loop.close()
            t = threading.Thread(target=_thread_target)
            t.start()
            t.join()
            raw_response = result[0]
        else:
            raw_response = asyncio.run(ask_voxkage_brain(full_prompt))

        parsed = clean_cli_json(raw_response)
        if isinstance(parsed, dict) and "subject" in parsed and "body" in parsed:
            return parsed
        return {"subject": "VoxKage Draft", "body": f"(Sub-agent failed to parse JSON. Instructions: {context})"}
    except Exception as e:
        logger.error(f"Email Sub-Agent Error: {e}")
        return {"subject": "VoxKage Draft", "body": f"(Sub-agent failed. Instructions: {context})"}

# ═══════════════════════════════════════════════════════════════════
# SESSION HANDLERS — called from llm_client.py, NOT from the LLM
# ═══════════════════════════════════════════════════════════════════
def handle_compose(recipient: str, instructions: str) -> str:
    """Full compose pipeline: sub-agent → Gmail API draft → HUD output."""
    global _email_session
    try:
        service = _get_gmail_service()
        
        # Generate content via sub-agent
        gen_data = _generate_email_json(instructions)
        subject = gen_data.get("subject", "VoxKage Draft")
        body = gen_data.get("body", "...")
        
        # Cleanup any previous draft
        if _email_session and _email_session.get("draft_id"):
            try:
                service.users().drafts().delete(userId='me', id=_email_session['draft_id']).execute()
            except Exception:
                pass
        
        # Create the Gmail draft
        message = MIMEText(body)
        message['to'] = recipient
        message['subject'] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = service.users().drafts().create(userId='me', body={'message': {'raw': raw}}).execute()
        
        # Store session
        _email_session = {
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "draft_id": draft['id'],
            "status": "ready"
        }
        
        # Push to HUD (silent — not spoken)
        try:
            from voxkage.llm.helpers import log_to_hud
            hud_msg = f"📝 DRAFT READY\nTo: {recipient}\nSubject: {subject}\n\n{body}"
            log_to_hud("VoxKage", hud_msg)
        except Exception:
            pass

        return f"I have drafted this mail for you. Please review the draft and confirm if it is ready to send."

    except Exception as e:
        logger.error(f"Compose error: {e}")
        _email_session = None
        return f"Failed to create draft: {str(e)}"

def handle_edit(instructions: str) -> str:
    """Edit the active draft using the sub-agent."""
    global _email_session
    if not _email_session:
        return "No active email draft to edit."
    
    try:
        service = _get_gmail_service()
        
        gen_data = _generate_email_json(
            instructions, _email_session['subject'], _email_session['body']
        )
        new_subject = gen_data.get("subject", _email_session['subject'])
        new_body = gen_data.get("body", _email_session['body'])
        
        # Delete old draft
        try:
            service.users().drafts().delete(userId='me', id=_email_session['draft_id']).execute()
        except Exception:
            pass
        
        # Create updated draft
        message = MIMEText(new_body)
        message['to'] = _email_session['recipient']
        message['subject'] = new_subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = service.users().drafts().create(userId='me', body={'message': {'raw': raw}}).execute()
        
        _email_session['subject'] = new_subject
        _email_session['body'] = new_body
        _email_session['draft_id'] = draft['id']
        
        try:
            from voxkage.llm.helpers import log_to_hud
            hud_msg = f"📝 DRAFT UPDATED\nTo: {_email_session['recipient']}\nSubject: {new_subject}\n\n{new_body}"
            log_to_hud("VoxKage", hud_msg)
        except Exception:
            pass

        return "Draft has been updated. Please review the draft."

    except Exception as e:
        logger.error(f"Edit draft error: {e}")
        return f"Failed to edit draft: {str(e)}"

def handle_send() -> str:
    """Send the active draft. All state is in _email_session — no LLM memory needed."""
    global _email_session
    if not _email_session:
        return "No active email draft to send."
    
    try:
        service = _get_gmail_service()
        service.users().drafts().send(userId='me', body={'id': _email_session['draft_id']}).execute()
        
        recipient = _email_session['recipient']
        _email_session = None  # Clear session
        return f"Done, the mail has been successfully sent to {recipient}."
    except Exception as e:
        logger.error(f"Send draft error: {e}")
        return f"Failed to send draft: {str(e)}"

def handle_cancel() -> str:
    """Cancel and discard the active draft."""
    global _email_session
    if not _email_session:
        return "Nothing to cancel."
    
    try:
        service = _get_gmail_service()
        service.users().drafts().delete(userId='me', id=_email_session['draft_id']).execute()
    except Exception:
        pass
    
    _email_session = None
    return "Draft discarded."

def get_session_active() -> bool:
    """Check if an email session is active."""
    return _email_session is not None and _email_session.get("status") == "ready"

# ═══════════════════════════════════════════════════════════════════
# INBOX TOOLS — these are safe for the LLM to invoke via tool schemas
# ═══════════════════════════════════════════════════════════════════
def check_gmail(query: str = "", label: str = "INBOX", max_results: int = 5) -> str:
    """Reads inbox or searches, returning concise metadata and caching the rest."""
    global _email_cache
    try:
        service = _get_gmail_service()
        
        q = query
        if label.upper() == "UNREAD":
            q = f"is:unread {query}".strip()
            label_ids = ["INBOX"]
        elif label.upper() == "INBOX":
            label_ids = ["INBOX"]
        else:
            label_ids = [label.upper()]

        results = service.users().messages().list(userId='me', labelIds=label_ids, q=q, maxResults=max_results).execute()
        messages = results.get('messages', [])

        if not messages:
            return f"No emails found matching query '{query}' in {label}."

        output = []
        for i, msg in enumerate(messages):
            msg_id = msg['id']
            full_msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            
            headers = full_msg.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
            snippet = full_msg.get('snippet', '')
            
            body_text = ""
            payload = full_msg.get('payload', {})
            def _extract_text(parts):
                text = ""
                for part in parts:
                    if part.get('mimeType') == 'text/plain':
                        data = part.get('body', {}).get('data')
                        if data:
                            text += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    elif part.get('parts'):
                        text += _extract_text(part.get('parts'))
                return text
            
            if payload.get('mimeType') == 'text/plain':
                data = payload.get('body', {}).get('data')
                if data:
                    body_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            else:
                body_text = _extract_text(payload.get('parts', []))
            
            _email_cache[msg_id] = {
                "subject": subject,
                "sender": sender,
                "snippet": snippet,
                "body": body_text or snippet,
            }
            
            output.append(f"[{i+1}] ID: {msg_id} | From: {sender} | Subj: {subject} | Snippet: {snippet}")
            
        return "Found Emails:\n" + "\n".join(output)

    except Exception as e:
        logger.error(f"Gmail read error: {e}")
        return f"Failed to check Gmail: {str(e)}"

def get_email_summary(email_id: str) -> str:
    """Retrieves full text from cache or API."""
    global _email_cache
    if email_id in _email_cache:
        data = _email_cache[email_id]
        return f"From: {data['sender']}\nSubject: {data['subject']}\n\n{data['body']}"
    
    try:
        service = _get_gmail_service()
        msg = service.users().messages().get(userId='me', id=email_id, format='full').execute()
        snippet = msg.get('snippet', '')
        return f"Cache miss for {email_id}. Snippet: {snippet}"
    except Exception as e:
        return f"Failed to get email summary: {str(e)}"
