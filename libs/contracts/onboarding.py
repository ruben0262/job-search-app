"""Page 1 of onboarding: who the user is, as a recruiter would read it."""
from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Enough for the links a person has; every one of them ends up in the capture conversation.
MAX_LINKS = 10


class LinkPlatform(StrEnum):
    """What kind of link it is. `OTHER` is the escape hatch, not a failure."""

    LINKEDIN = "linkedin"
    GITHUB = "github"
    GITLAB = "gitlab"
    PORTFOLIO = "portfolio"
    STACKOVERFLOW = "stackoverflow"
    OTHER = "other"


# Host fragment -> platform.
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
        """`https://` prefixed if missing, then required to have a host."""
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
    """Full name and links."""

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
    """Exported like `libs.contracts.config.json_schema`: the form is generated from it."""
    return IdentitySchema.model_json_schema()
