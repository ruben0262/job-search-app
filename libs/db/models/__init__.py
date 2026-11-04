"""Every collection in the platform. `MODELS` is the one list `ensure_schema` and `delete_account` read."""
from __future__ import annotations

from libs.db.models.ai import AiRequest
from libs.db.models.ats import AtsEtag, AtsToken
from libs.db.models.auth import MAIL_PURPOSES, TOKEN_PURPOSES, AuthToken, MailSend, UserSession
from libs.db.models.built import BuiltDocument
from libs.db.models.contact import ContactMessage
from libs.db.models.corpus import WORKPLACES, CorpusJob, SweptPlace, Word
from libs.db.models.document import DOCUMENT_KINDS, UserDocument
from libs.db.models.notification import PushSubscription, UserNotification
from libs.db.models.operator import OperatorKeyword
from libs.db.models.profile_file import PROFILE_FILE_KINDS, UserProfileFile
from libs.db.models.queue import (
    CLAIM_TIMEOUT_MIN,
    JOB_STATES,
    LANES,
    MAX_ATTEMPTS,
    LanePause,
    Lock,
    QueueJob,
)
from libs.db.models.settings import PlatformSetting
from libs.db.models.setup import DEFAULT_TITLE, MESSAGE_STATUSES, SetupConversation, SetupMessage
from libs.db.models.tenant import UserJobState
from libs.db.models.user import (
    SECRET_KINDS,
    TELEGRAM_STATUSES,
    USER_STATUSES,
    User,
    UserConfig,
    UserSecret,
)

MODELS = (User, UserConfig, UserSecret, UserSession, AuthToken, MailSend, CorpusJob, QueueJob,
          UserJobState, SetupConversation, SetupMessage, UserNotification, PushSubscription,
          UserDocument, UserProfileFile, PlatformSetting, AiRequest, AtsToken, AtsEtag,
          BuiltDocument, OperatorKeyword, LanePause, Lock, ContactMessage, Word, SweptPlace)

__all__ = [
    "CLAIM_TIMEOUT_MIN", "DEFAULT_TITLE", "DOCUMENT_KINDS", "JOB_STATES", "LANES", "MAIL_PURPOSES",
    "MAX_ATTEMPTS", "MESSAGE_STATUSES", "MODELS", "PROFILE_FILE_KINDS", "SECRET_KINDS",
    "TELEGRAM_STATUSES", "TOKEN_PURPOSES", "USER_STATUSES", "WORKPLACES",
    "AiRequest", "AtsEtag", "AtsToken", "AuthToken", "BuiltDocument", "ContactMessage", "CorpusJob",
    "LanePause", "Lock",
    "MailSend", "OperatorKeyword", "PlatformSetting", "PushSubscription", "QueueJob",
    "SetupConversation", "SetupMessage", "SweptPlace", "User", "UserConfig", "UserDocument", "UserJobState",
    "UserNotification", "UserProfileFile", "UserSecret", "UserSession", "Word",
]
