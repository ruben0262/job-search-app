"""Whether a person agreed to the legal pages, and to which version. Light on purpose: the docgen worker asks
it, and its image installs nothing `libs.auth` needs. Who receives a person's data and what a device keeps are
in `libs.legal.disclosure`; the pages themselves in `libs.legal.documents`."""
from libs.legal.consent import LEGAL_VERSION, agreed, has_consented

__all__ = ["LEGAL_VERSION", "agreed", "has_consented"]
