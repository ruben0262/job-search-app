"""The disclosures the cleared legal review depends on, and whether a person agreed to them."""
from libs.legal.consent import has_consented
from libs.legal.disclosure import (
    CONSENT_BODY,
    CONSENT_HEADLINE,
    DISCLOSURE_VERSION,
    PROCESSORS,
    Processor,
    disclosure,
)

__all__ = ["CONSENT_BODY", "CONSENT_HEADLINE", "DISCLOSURE_VERSION", "PROCESSORS", "Processor",
           "disclosure", "has_consented"]
