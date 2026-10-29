"""Every table in the platform, re-exported so Alembic can see all of them."""
from __future__ import annotations

from libs.db.base import Base
from libs.db.models.ai import AiRequest
from libs.db.models.ats import AtsEtag, AtsToken
from libs.db.models.auth import (
    MAIL_PURPOSES,
    TOKEN_PURPOSES,
    AuthToken,
    MailSend,
    UserSession,
)
from libs.db.models.built import BuiltDocument
from libs.db.models.corpus import CorpusJob
from libs.db.models.document import DOCUMENT_KINDS, UserDocument
from libs.db.models.notification import UserNotification
from libs.db.models.operator import OperatorKeyword
from libs.db.models.plan import FREE_PLAN, Plan
from libs.db.models.profile_file import (
    PROFILE_FILE_KINDS,
    SCORING_RELEVANT_FILES,
    UserProfileFile,
)
from libs.db.models.proxy import PROXY_STATUSES, ProxyCredential
from libs.db.models.queue import CLAIM_TIMEOUT_MIN, JOB_STATES, LANES, MAX_ATTEMPTS, QueueJob
from libs.db.models.settings import PlatformSetting
from libs.db.models.setup import (
    DEFAULT_TITLE,
    SETUP_AGENTS,
    SetupConversation,
    SetupMessage,
)
from libs.db.models.shadow import ShadowScore
from libs.db.models.tenant import LABEL_SOURCES, UserJobState, UserLabel, UserModel
from libs.db.models.user import (
    SECRET_KINDS,
    TELEGRAM_STATUSES,
    USER_STATUSES,
    User,
    UserConfig,
    UserSecret,
)

__all__ = [
    "CLAIM_TIMEOUT_MIN",
    "DEFAULT_TITLE",
    "DOCUMENT_KINDS",
    "FREE_PLAN",
    "JOB_STATES",
    "LABEL_SOURCES",
    "LANES",
    "MAIL_PURPOSES",
    "MAX_ATTEMPTS",
    "PROFILE_FILE_KINDS",
    "PROXY_STATUSES",
    "SCORING_RELEVANT_FILES",
    "SECRET_KINDS",
    "SETUP_AGENTS",
    "TELEGRAM_STATUSES",
    "TOKEN_PURPOSES",
    "USER_STATUSES",
    "AiRequest",
    "AtsEtag",
    "AtsToken",
    "AuthToken",
    "Base",
    "BuiltDocument",
    "CorpusJob",
    "MailSend",
    "OperatorKeyword",
    "Plan",
    "PlatformSetting",
    "ProxyCredential",
    "QueueJob",
    "SetupConversation",
    "SetupMessage",
    "ShadowScore",
    "User",
    "UserConfig",
    "UserDocument",
    "UserJobState",
    "UserLabel",
    "UserModel",
    "UserNotification",
    "UserProfileFile",
    "UserSecret",
    "UserSession",
]
