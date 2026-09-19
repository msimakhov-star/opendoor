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


# Persona filter: the four things a person can tick as "I don't have". Code only, no LLM.
DOC_TYPES = {
    "passport": re.compile(r"passport", re.I),
    "photo_id": re.compile(r"photo(?:graphic)? ?(?:id|identification)|driving licen[cs]e|\bID\b|identification|identity", re.I),
    "proof_of_address": re.compile(r"proof of (?:address|residence|residency)|utility bill|bank statement|tenancy agreement|council tax bill", re.I),
    "immigration": re.compile(r"immigration|visa|biometric|residence permit|brp\b|home office", re.I),
}


def doc_types(texts: list[str]) -> list[str]:
    """Map free-text document names (from the quote and Finding.documents) to the four persona types."""
    joined = " | ".join(texts or [])
    return [k for k, rx in DOC_TYPES.items() if rx.search(joined)]


if __name__ == "__main__":
    assert doc_types(["Proof of address", "Photo ID"]) == ["photo_id", "proof_of_address"]
    assert doc_types(["passport or visa"]) == ["passport", "immigration"]
    assert doc_types([]) == []
    print("models ok")


class Finding(BaseModel):
    """What the classifier returns for one registration page."""
    category: Category
    quote: str = Field("", description="One sentence copied character for character from the page. Empty only for no_mention, unclear, not_checked.")
    documents: list[str] = Field(default_factory=list, description="Document types the page asks for, e.g. ['passport', 'proof of address'].")
    reason: str = Field("", description="One short plain-English sentence explaining the category.")
    retries: int = 0
    quote_verified: bool = False
    from_cache: bool = False


class SecondOpinion(BaseModel):
    """A separate model call that argues the surgery's side before a surgery is shown red."""
    verdict: Category
    agreed: bool = Field(description="True when the second reader also says demands_documents.")
    reason: str = Field("", description="One plain-English sentence, quoting the deciding words.")


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
    doc_types: list[str] = Field(default_factory=list, description="Subset of passport, photo_id, proof_of_address, immigration, from models.doc_types(). Only for red and amber.")
    distance_km: Optional[float] = None
    reason: str = ""
    self_contradiction: bool = False
    shot: Optional[str] = None  # path relative to the run folder, e.g. "shots/F84004.png"
    retries: int = 0
    secs: float = 0.0
    checked_at: str = ""
