"""What the platform's own AI key has paid for, one row per request: the daily allowance's count.

One source for every feature that spends it — AI-written documents now, the support chat next — so
`libs.llm.ai_requests_today` cannot disagree with itself. A request on the person's own key is not
written here: their own key is not the platform's allowance.
"""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class AiRequest(Base):
    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        # The count: one person, since their midnight.
        Index("ix_ai_requests_user_id_created_at", "user_id", "created_at"),
    )
