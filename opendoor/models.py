"""Shared data shapes for Open Door. Field names are a contract between the Modal app, the classifier, the pipeline and the UI."""
import re
from typing import Literal, Optional
from pydantic import BaseModel, Field

NHS_GUIDANCE_URL = "https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/"
NHS_GUIDANCE_QUOTE = "You do not need ID, proof of address or proof of immigration status."

Category = Literal["demands_documents", "asks_softly", "says_not_needed", "no_mention", "unclear", "not_checked"]
COLOUR = {
    "demands_documents": "red",
    "asks_softly": "amber",
    "says_not_needed": "green",
    "no_mention": "green-outline",
    "unclear": "grey",
    "not_checked": "grey",
}

# Document phrases. Used as the pre-filter (no hit means no Gemini call) and for `doc_hits`.
DOC_RE = re.compile(
    r"passport|proof of (?:address|id|identity|identification|residence|residency)|photo(?:graphic)? ?(?:id|identification)"
    r"|utility bill|driving licen[cs]e|bank statement|tenancy agreement|council tax bill|immigration status|visa|biometric"
    r"|\bID\b|identification|birth certificate",
    re.I,
)


def norm(s: str) -> str:
    """Whitespace-normalise so quote checks do not fail on line breaks."""
    return re.sub(r"\s+", " ", s or "").strip()


class Finding(BaseModel):
    """What the classifier returns for one registration page."""
    category: Category
    quote: str = Field("", description="One sentence copied character for character from the page. Empty only for no_mention, unclear, not_checked.")
    documents: list[str] = Field(default_factory=list, description="Document types the page asks for, e.g. ['passport', 'proof of address'].")
    reason: str = Field("", description="One short plain-English sentence explaining the category.")
    retries: int = 0
    quote_verified: bool = False


class PracticeResult(BaseModel):
    code: str
    name: str
    postcode: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    site: Optional[str] = None
    reg_url: Optional[str] = None
    status: Literal["ok", "blocked", "no_site", "error"] = "ok"
    links_national_form: bool = False
    doc_hits: list[str] = Field(default_factory=list)
    category: Category = "not_checked"
    colour: str = "grey"
    quote: str = ""
    quote_verified: bool = False
    quote_box: Optional[dict] = None
    documents: list[str] = Field(default_factory=list)
    reason: str = ""
    self_contradiction: bool = False
    shot: Optional[str] = None  # path relative to the run folder, e.g. "shots/F84004.png"
    retries: int = 0
    secs: float = 0.0
    checked_at: str = ""
