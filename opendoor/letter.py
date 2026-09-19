"""Draft letter to a practice manager. A plain code template, no LLM. A human reads, edits and sends it; this tool never sends anything."""
import string

from opendoor.models import NHS_GUIDANCE_QUOTE, NHS_GUIDANCE_URL

LETTER_CATEGORIES = ("demands_documents", "asks_softly")


def draft_letter(r: dict) -> str:
    """r is a PracticeResult dict. Returns plain text, or "" when there is nothing to write about (no verified quote, or green)."""
    if r.get("category") not in LETTER_CATEGORIES or not r.get("quote") or not r.get("quote_verified"):
        return ""
    page = r.get("reg_url") or r.get("site") or "your practice website"
    name = string.capwords(r.get("name") or "your practice")
    parts = [
        f"To: The Practice Manager, {name}" + (f", {r['postcode']}" if r.get("postcode") else ""),
        "Dear Practice Manager,",
        f"I am writing about the new patient registration page on your website:\n{page}",
        f"When it was read on {(r.get('checked_at') or '')[:10] or '[date]'}, the page said:\n\"{r['quote']}\"",
        f"The NHS guidance for patients on nhs.uk says:\n\"{NHS_GUIDANCE_QUOTE}\"\nSource: {NHS_GUIDANCE_URL}",
        "As written, the wording on your page contradicts the NHS guidance on nhs.uk. Someone who does not have these "
        "documents could read your page and decide not to try to register.",
    ]
    if r.get("self_contradiction"):
        parts.append("Your page also links to the national NHS registration form, which does not ask for these documents, "
                     "so the page currently gives new patients two different messages.")
    parts += [
        "Could you please update the page so that it matches the nhs.uk guidance? For example, it could say that "
        "documents are helpful if a patient has them, and that nobody needs them to register.",
        "I have only read the wording on your website. I have not looked at how your reception team handles "
        "registrations, and this letter says nothing about that.",
        "Thank you for your time.",
        "Yours faithfully,\n[Your name]",
    ]
    return "\n\n".join(parts) + "\n"
