"""The disclosures the cleared legal review depends on.

In ``libs`` because the consent screen, the privacy policy, and the record stored on
``users.processing_consent_version`` must all describe the same thing. A processor added to one and
not the others means the consent on file is to a disclosure that no longer matches reality — which
is the failure the review was meant to prevent, arriving quietly.
"""
from libs.legal.disclosure import (
    CONSENT_BODY,
    CONSENT_HEADLINE,
    DISCLOSURE_VERSION,
    PROCESSORS,
    Processor,
    disclosure,
)

__all__ = ["CONSENT_BODY", "CONSENT_HEADLINE", "DISCLOSURE_VERSION", "PROCESSORS", "Processor",
           "disclosure"]
