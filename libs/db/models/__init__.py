"""Every collection in the platform. `MODELS` is the one list `ensure_schema` and `delete_account` read."""
from __future__ import annotations

from libs.db.models.ai import AiRequest
from libs.db.models.alert import CHANNELS, Alert, AlertCursor
from libs.db.models.ats import AtsEtag, AtsToken, CompanyLookup
from libs.db.models.auth import MAIL_PURPOSES, TOKEN_PURPOSES, AuthToken, MailSend, RateCount, UserSession
from libs.db.models.built import BuiltDocument
from libs.db.models.company import Company
from libs.db.models.contact import ContactMessage
from libs.db.models.corpus import WORKPLACES, CorpusFit, CorpusJob, CorpusText, CountryCount, SweptPlace, Word
from libs.db.models.document import DOCUMENT_KINDS, UploadedFile, UserDocument
from libs.db.models.fit import FitScore
from libs.db.models.notification import PushSubscription, UserNotification, VapidKey
from libs.db.models.page import DrawnPage
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

MODELS = (User, UserConfig, UserSecret, UserSession, AuthToken, MailSend, CorpusJob, CorpusText, CorpusFit,
          QueueJob,
          UserJobState, SetupConversation, SetupMessage, UserNotification, PushSubscription,
          UserDocument, UserProfileFile, PlatformSetting, AiRequest, AtsToken, AtsEtag,
          BuiltDocument, LanePause, Lock, ContactMessage, Word, SweptPlace,
          CountryCount, CompanyLookup, UploadedFile, FitScore, Alert, AlertCursor, VapidKey, RateCount,
          DrawnPage, Company)

__all__ = [
    "CHANNELS", "CLAIM_TIMEOUT_MIN", "DEFAULT_TITLE", "DOCUMENT_KINDS", "JOB_STATES", "LANES",
    "MAIL_PURPOSES",
    "MAX_ATTEMPTS", "MESSAGE_STATUSES", "MODELS", "PROFILE_FILE_KINDS", "SECRET_KINDS",
    "TELEGRAM_STATUSES", "TOKEN_PURPOSES", "USER_STATUSES", "WORKPLACES",
    "AiRequest", "Alert", "AlertCursor", "AtsEtag", "AtsToken", "AuthToken", "BuiltDocument", "Company",
    "CompanyLookup",
    "ContactMessage",
    "CorpusFit", "CorpusJob", "CorpusText", "CountryCount", "DrawnPage", "FitScore",
    "LanePause", "Lock",
    "MailSend", "PlatformSetting", "PushSubscription", "QueueJob", "RateCount",
    "SetupConversation", "SetupMessage", "SweptPlace", "UploadedFile", "User", "UserConfig", "UserDocument",
    "UserJobState",
    "UserNotification", "UserProfileFile", "UserSecret", "UserSession", "VapidKey", "Word",
]
