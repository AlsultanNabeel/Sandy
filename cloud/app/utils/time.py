import os
from zoneinfo import ZoneInfo

USER_TIMEZONE = os.getenv("USER_TIMEZONE", "Africa/Cairo")
USER_TZ = ZoneInfo(USER_TIMEZONE)
