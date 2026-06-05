import logging
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Default per-call (connect, read) timeout for the file byte download. The voice
# hot path can pass a tighter value. pyTelegramBotAPI's download_file honours only
# the process-wide telebot.apihelper timeouts (90s read), so we fetch the bytes
# ourselves via requests to get a real per-call bound (B2).
_DEFAULT_TIMEOUT: Tuple[float, float] = (10.0, 30.0)


def download_telegram_file_bytes(
    telegram_bot,
    file_id: str,
    timeout: Optional[Tuple[float, float]] = None,
) -> Optional[Tuple[bytes, str]]:
    """Download Telegram file bytes. Returns (bytes, file_path) or None.

    `get_file` (metadata) is bounded by the global telebot.apihelper timeout;
    the byte transfer is fetched via requests so it honours a real per-call
    `timeout` instead of the process-wide 90s read timeout.
    """
    try:
        file_info = telegram_bot.get_file(file_id)
        file_path = file_info.file_path
        token = getattr(telegram_bot, "token", None)
        if token:
            url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            eff_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT
            resp = requests.get(url, timeout=eff_timeout)
            resp.raise_for_status()
            return resp.content, file_path
        # Fallback: library download (global timeout only) when the token isn't reachable.
        data = telegram_bot.download_file(file_path)
        return data, file_path
    except Exception as e:
        logger.error("[Telegram] file download failed: %s", e)
        return None
