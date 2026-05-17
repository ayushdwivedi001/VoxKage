"""
MCP Server: Gmail — Full Capability
Provides email reading, searching, composing, sending, deleting,
labelling, and folder management via Gmail OAuth.

Standalone — run directly:
    python mcp_servers/email_server.py
"""

import base64
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-email")


# ── Gmail service helper ───────────────────────────────────────────────────────
def _svc():
    from voxkage.automation.gmail_manager import _get_gmail_service
    return _get_gmail_service()


# ═══════════════════════════════════════════════════════════════════════════════
# READ / SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def check_email(
    query: str = "",
    label: str = "INBOX",
    max_results: int = 5,
) -> str:
    """
    Read the Gmail inbox or any folder/label, with optional search.

    label examples:
      "INBOX"      - normal inbox
      "UNREAD"     - unread messages
      "SENT"       - sent mail
      "SPAM"       - spam folder
      "TRASH"      - deleted items
      "CATEGORY_PROMOTIONS" - promotions tab
      "CATEGORY_SOCIAL"     - social tab
      "CATEGORY_UPDATES"    - updates tab

    query examples (Gmail search syntax):
      "from:boss@company.com"
      "subject:invoice"
      "is:unread"
      "has:attachment"
      "after:2024/01/01"
    """
    from voxkage.automation.gmail_manager import check_gmail
    return check_gmail(query=query, label=label, max_results=max_results)


@mcp.tool()
def read_email(email_id: str) -> str:
    """
    Get the full text body of a specific email by its ID.
    Email IDs are returned by check_email.
    """
    from voxkage.automation.gmail_manager import get_email_summary
    return get_email_summary(email_id)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSE & SEND
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
) -> str:
    """
    Compose and IMMEDIATELY send an email via Gmail.

    Use this when the user asks to send an email and provides:
    - recipient address (to)
    - subject line
    - body text

    If the user only says "send this to X" after VoxKage drafted content in the
    main chat, pass that drafted text as the body.

    Parameters:
      to      : Recipient email address (e.g. "friend@gmail.com")
      subject : Email subject line
      body    : Full email body text (plain text, can include line breaks)
      cc      : Optional CC addresses (comma-separated)
      bcc     : Optional BCC addresses (comma-separated)
    """
    try:
        service = _svc()
        message = MIMEMultipart()
        message["to"]      = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc
        message.attach(MIMEText(body, "plain"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return (
            f"✅ Email sent successfully, sir.\n"
            f"   To      : {to}\n"
            f"   Subject : {subject}"
        )
    except Exception as e:
        return f"❌ Failed to send email: {e}"


@mcp.tool()
def save_draft(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
) -> str:
    """
    Save an email as a Gmail draft WITHOUT sending it.
    Use when user says "save as draft" or "prepare a draft for me to review".

    Parameters:
      to      : Recipient email address
      subject : Email subject line
      body    : Email body text
      cc      : Optional CC addresses
    """
    try:
        service = _svc()
        message = MIMEMultipart()
        message["to"]      = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        message.attach(MIMEText(body, "plain"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        return (
            f"📝 Draft saved, sir.\n"
            f"   Draft ID : {draft['id']}\n"
            f"   To       : {to}\n"
            f"   Subject  : {subject}"
        )
    except Exception as e:
        return f"❌ Failed to save draft: {e}"


@mcp.tool()
def reply_to_email(
    email_id: str,
    body: str,
) -> str:
    """
    Reply to an existing email thread.
    Fetches the original email's subject and recipient automatically.

    Parameters:
      email_id : The message ID to reply to (from check_email)
      body     : Your reply text
    """
    try:
        service = _svc()
        original = service.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()
        headers = original.get("payload", {}).get("headers", [])
        subject = next(
            (h["value"] for h in headers if h["name"].lower() == "subject"),
            "Re: (no subject)"
        )
        sender = next(
            (h["value"] for h in headers if h["name"].lower() == "from"),
            ""
        )
        thread_id = original.get("threadId", "")

        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        message = MIMEText(body)
        message["to"]      = sender
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id}
        ).execute()
        return f"✅ Reply sent to {sender}."
    except Exception as e:
        return f"❌ Failed to send reply: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# DELETE / MANAGE
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def delete_email(email_id: str) -> str:
    """
    Move a specific email to Trash (recoverable delete).
    Use when user says "delete this email" / "trash that message".

    Parameters:
      email_id : The message ID to delete (from check_email)
    """
    try:
        service = _svc()
        service.users().messages().trash(userId="me", id=email_id).execute()
        return f"🗑️  Email {email_id} moved to Trash."
    except Exception as e:
        return f"❌ Failed to delete email: {e}"


@mcp.tool()
def delete_emails_bulk(
    query: str,
    max_delete: int = 20,
) -> str:
    """
    Delete multiple emails matching a Gmail search query (moves to Trash).
    Use for "delete all promotion emails", "clean spam", "delete newsletters", etc.

    Parameters:
      query      : Gmail search query (e.g. "category:promotions", "in:spam", "from:newsletter@example.com")
      max_delete : Maximum number of emails to delete (default 20, max 50)
    """
    try:
        max_delete = min(max_delete, 50)
        service = _svc()
        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_delete
        ).execute()
        messages = results.get("messages", [])
        if not messages:
            return f"No emails found matching '{query}'."

        ids = [m["id"] for m in messages]
        service.users().messages().batchModify(
            userId="me",
            body={"ids": ids, "addLabelIds": ["TRASH"]}
        ).execute()
        return f"🗑️  {len(ids)} email(s) moved to Trash (query: '{query}')."
    except Exception as e:
        return f"❌ Failed to bulk delete: {e}"


@mcp.tool()
def mark_email_read(email_id: str) -> str:
    """Mark a specific email as read."""
    try:
        service = _svc()
        service.users().messages().modify(
            userId="me", id=email_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return f"✅ Email {email_id} marked as read."
    except Exception as e:
        return f"❌ Failed to mark as read: {e}"


@mcp.tool()
def mark_email_unread(email_id: str) -> str:
    """Mark a specific email as unread."""
    try:
        service = _svc()
        service.users().messages().modify(
            userId="me", id=email_id,
            body={"addLabelIds": ["UNREAD"]}
        ).execute()
        return f"✅ Email {email_id} marked as unread."
    except Exception as e:
        return f"❌ Failed to mark as unread: {e}"


@mcp.tool()
def archive_email(email_id: str) -> str:
    """
    Archive an email (removes from Inbox but keeps in All Mail).
    """
    try:
        service = _svc()
        service.users().messages().modify(
            userId="me", id=email_id,
            body={"removeLabelIds": ["INBOX"]}
        ).execute()
        return f"📦 Email {email_id} archived."
    except Exception as e:
        return f"❌ Failed to archive: {e}"


@mcp.tool()
def get_email_stats() -> str:
    """
    Get a quick summary of inbox stats: unread count, promotions, spam, etc.
    Use when user asks 'how many unread emails do I have?' or 'summarize my inbox'.
    """
    try:
        service = _svc()
        labels_to_check = {
            "INBOX":                "Inbox total",
            "UNREAD":               "Unread",
            "SPAM":                 "Spam",
            "CATEGORY_PROMOTIONS":  "Promotions",
            "CATEGORY_SOCIAL":      "Social",
            "CATEGORY_UPDATES":     "Updates",
        }
        lines = ["📊 Gmail Stats:"]
        for label_id, label_name in labels_to_check.items():
            try:
                result = service.users().messages().list(
                    userId="me", labelIds=[label_id], maxResults=1
                ).execute()
                count = result.get("resultSizeEstimate", 0)
                lines.append(f"   {label_name:20s}: ~{count}")
            except Exception:
                pass
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Failed to get stats: {e}"


if __name__ == "__main__":
    mcp.run()
