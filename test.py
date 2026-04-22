"""
Human-like test for the Email Sub-Agent Interceptor.
Simulates what happens when a user speaks to VoxKage naturally.
Tests: intent detection -> compose -> edit -> send -> session cleanup.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import automation.gmail_manager as gmail_mod
from automation.gmail_manager import (
    detect_email_intent, handle_compose, handle_edit, handle_send, 
    handle_cancel, get_session_active
)
import time

def sep(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def test_intent_detection():
    """Test that the regex patterns correctly identify email intents."""
    sep("PHASE 1: Intent Detection (no LLM involved)")
    
    test_cases = [
        # Compose intents
        ("send an email to ad860500@gmail.com with subject greeting", "compose"),
        ("email ad860500@gmail.com saying hello", "compose"),
        ("draft a mail to test@example.com about the project", "compose"),
        ("compose an email to user@domain.com with morning greetings", "compose"),
        # Check inbox intents
        ("check my emails", "check_inbox"),
        ("any new mails?", "check_inbox"),
        ("what's in my inbox", "check_inbox"),
        ("show me my gmail", "check_inbox"),
        # Non-email intents (should return None)
        ("play my usual songs", None),
        ("what's the weather today", None),
        ("search for python tutorials", None),
    ]
    
    passed = 0
    for prompt, expected_action in test_cases:
        result = detect_email_intent(prompt)
        actual = result["action"] if result else None
        status = "[PASS]" if actual == expected_action else "[FAIL]"
        if actual == expected_action:
            passed += 1
        extra = ""
        if result and result.get("recipient"):
            extra = f" → {result['recipient']}"
        print(f"  {status} \"{prompt}\"")
        print(f"       Expected: {expected_action} | Got: {actual}{extra}")
    
    print(f"\n  Score: {passed}/{len(test_cases)}")
    return passed == len(test_cases)


def test_full_compose_flow():
    """Simulate a real user conversation: compose → edit → send."""
    sep("PHASE 2: Full Compose → Edit → Send Flow")
    
    # Step 1: User says "send an email to ad860500@gmail.com saying morning greetings and VoxKage runs smoothly"
    user_prompt = "send an email to ad860500@gmail.com saying morning greetings from voxkage and tell them voxkage is running smoothly"
    print(f"  USER: {user_prompt}")
    
    intent = detect_email_intent(user_prompt)
    assert intent is not None, "Intent detection failed!"
    assert intent["action"] == "compose", f"Expected compose, got {intent['action']}"
    assert intent["recipient"] == "ad860500@gmail.com", f"Wrong recipient: {intent['recipient']}"
    print(f"  → Intent: {intent['action']} to {intent['recipient']}")
    
    print(f"\n  VOXKAGE: Drafting now, sir...")
    start = time.time()
    result = handle_compose(intent["recipient"], intent["instructions"])
    dur = time.time() - start
    print(f"  [Sub-agent completed in {dur:.1f}s]")
    print(f"  VOXKAGE: {result}")
    
    # Verify session is active
    assert get_session_active(), "Session should be active after compose!"
    print(f"\n  [PASS] Session active: recipient={gmail_mod._email_session['recipient']}, draft_id={gmail_mod._email_session['draft_id'][:20]}...")
    
    # Step 2: User wants to edit
    edit_prompt = "edit the subject to say Hello from VoxKage"
    print(f"\n  USER: {edit_prompt}")
    
    edit_intent = detect_email_intent(edit_prompt)
    assert edit_intent is not None, "Edit intent detection failed!"
    assert edit_intent["action"] == "edit", f"Expected edit, got {edit_intent['action']}"
    
    print(f"  VOXKAGE: Updating draft now, sir...")
    start = time.time()
    result = handle_edit(edit_intent["instructions"])
    dur = time.time() - start
    print(f"  [Sub-agent completed in {dur:.1f}s]")
    print(f"  VOXKAGE: {result}")
    
    # Step 3: User confirms send
    send_prompt = "yes send it"
    print(f"\n  USER: {send_prompt}")
    
    send_intent = detect_email_intent(send_prompt)
    assert send_intent is not None, "Send intent detection failed!"
    assert send_intent["action"] == "send", f"Expected send, got {send_intent['action']}"
    
    result = handle_send()
    print(f"  VOXKAGE: {result}")
    
    # Verify session is cleared
    assert not get_session_active(), "Session should be cleared after send!"
    print(f"\n  [PASS] Session cleared after send. Memory clean.")
    
    return True


def test_cancel_flow():
    """Test that cancelling clears the session."""
    sep("PHASE 3: Cancel Flow")
    
    user_prompt = "send an email to test@example.com saying hello"
    print(f"  USER: {user_prompt}")
    
    intent = detect_email_intent(user_prompt)
    print(f"  → Composing...")
    handle_compose(intent["recipient"], intent["instructions"])
    assert get_session_active(), "Session should be active!"
    
    cancel_prompt = "never mind, cancel it"
    print(f"  USER: {cancel_prompt}")
    
    cancel_intent = detect_email_intent(cancel_prompt)
    assert cancel_intent["action"] == "cancel"
    result = handle_cancel()
    print(f"  VOXKAGE: {result}")
    
    assert not get_session_active(), "Session should be cleared after cancel!"
    print(f"  [PASS] Session cleared after cancel.")
    return True


if __name__ == "__main__":
    print("\n" + "="*60)
    print("  VOXKAGE EMAIL SUB-AGENT INTERCEPTOR TEST")
    print("="*60)
    
    all_pass = True
    all_pass &= test_intent_detection()
    all_pass &= test_full_compose_flow()
    all_pass &= test_cancel_flow()
    
    sep("FINAL RESULT")
    if all_pass:
        print("  [PASS] ALL TESTS PASSED -- Email interceptor is fully operational.")
    else:
        print("  [FAIL] SOME TESTS FAILED -- Review output above.")
