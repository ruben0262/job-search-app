"""Vocabulary the harvest and the admin panel both need.

Here rather than in `services/harvester` because a service may not import another service
(`CODE_STRUCTURE.md` §2), and the admin panel has to answer "how many queries does this source
have" with **the same arithmetic the harvest does** — not with an approximation of it. Two copies of
a normalisation rule is precisely the shape of defect this project keeps finding.
"""
from libs.harvest.keywords import distinct_keywords, normalise_keyword

__all__ = ["distinct_keywords", "normalise_keyword"]
