"""Page 1 of onboarding: who the user is, as a recruiter would read it.

Small, but not trivial — **the name here is not the account email**. The email is how someone signs
in; this is what goes at the top of a generated CV, and getting it from the address (`r.lekli`,
`andraslekli`) would put that on a document sent to an employer.

Links are (URL, platform) pairs rather than free URLs so the generator can say "GitHub:" instead of
printing a bare address, and so the profile step knows which link is worth reading. The platform is
inferred from the host when the user does not pick one, because asking someone to classify their own
GitHub URL is a question with one possible answer.
"""
from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Enough for the links a person actually has; a ceiling exists because every one of them is text
# that ends up in the capture conversation.
MAX_LINKS = 10


class LinkPlatform(StrEnum):
    """What kind of link it is. `OTHER` is the escape hatch, not a failure."""

    LINKEDIN = "linkedin"
    GITHUB = "github"
    GITLAB = "gitlab"
    PORTFOLIO = "portfolio"
    STACKOVERFLOW = "stackoverflow"
    OTHER = "other"


# Host fragment -> platform. Deliberately a fragment match rather than an exact host: `github.com`,
# `www.github.com` and `gist.github.com` are all GitHub, and a user pasting any of them is right.
_HOSTS = (
    ("linkedin.", LinkPlatform.LINKEDIN),
    ("github.", LinkPlatform.GITHUB),
    ("gitlab.", LinkPlatform.GITLAB),
    ("stackoverflow.", LinkPlatform.STACKOVERFLOW),
)


class ProfileLink(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    url: str = Field(min_length=4, max_length=500)
    platform: LinkPlatform | None = None

    @field_validator("url")
    @classmethod
    def _require_a_usable_url(cls, url: str) -> str:
        """`https://` prefixed if missing, then required to have a host.

        People paste `github.com/name` constantly. Rejecting that would be technically correct and
        would make the form feel broken, so it is repaired rather than refused — but a string with
        no host at all is a typo, and storing it would put a dead link on a CV.
        """
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        parsed = urlparse(url)
        if not parsed.netloc or "." not in parsed.netloc:
            raise ValueError(f"{url!r} is not a usable web address")
        return url

    @model_validator(mode="after")
    def _infer_platform(self) -> ProfileLink:
        if self.platform is None:
            host = urlparse(self.url).netloc.lower()
            match = next((value for fragment, value in _HOSTS if fragment in host), None)
            self.platform = (match or LinkPlatform.OTHER).value
        return self


class IdentitySchema(BaseModel):
    """Full name and links. Both are page 1; only the name is required.

    Links are optional because plenty of good candidates have none, and a required field somebody
    cannot satisfy is a wall rather than a prompt.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    full_name: str = Field(min_length=2, max_length=120)
    links: list[ProfileLink] = Field(default_factory=list, max_length=MAX_LINKS)

    @field_validator("full_name")
    @classmethod
    def _collapse_whitespace(cls, name: str) -> str:
        collapsed = " ".join(name.split())
        if len(collapsed) < 2:
            raise ValueError("your name is required — it is what appears on a generated CV")
        return collapsed

    @field_validator("links")
    @classmethod
    def _one_row_per_url(cls, links: list[ProfileLink]) -> list[ProfileLink]:
        """The same address twice is a paste accident, and it would be read twice at capture."""
        seen, unique = set(), []
        for link in links:
            key = link.url.rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                unique.append(link)
        return unique


def json_schema() -> dict:
    """Exported for the same reason `libs.contracts.config.json_schema` is: the form is generated
    from it, so a field cannot exist on one side and not the other."""
    return IdentitySchema.model_json_schema()
