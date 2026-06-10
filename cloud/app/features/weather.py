import time
import requests
from typing import Any, Dict, Optional
from urllib.parse import quote

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError

_cb = CircuitBreaker(name="weather", failure_threshold=5, recovery_timeout=60.0)
_RETRY_ATTEMPTS = 2
_RETRY_DELAY = 1.0


def _fetch_weather(url: str) -> Dict[str, Any]:
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.json()


def get_weather(city: str = "October City", **kwargs) -> Optional[Dict[str, Any]]:
    url = f"https://wttr.in/{quote(city)}+Egypt?format=j1"

    last_error: Exception = Exception("unknown")
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            data = _cb.call(_fetch_weather, url)
            break
        except CircuitOpenError:
            print("[Weather] circuit open, skipping weather fetch")
            return None
        except Exception as e:
            last_error = e
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY)
    else:
        print(f"[Weather] failed after {_RETRY_ATTEMPTS} attempts: {last_error}")
        return None

    try:

        current = data["current_condition"][0]
        today = data["weather"][0]
        astronomy = today["astronomy"][0]

        return {
            "temp_c": current.get("temp_C", ""),
            "feels_like_c": current.get("FeelsLikeC", ""),
            "humidity": current.get("humidity", ""),
            "description": current["weatherDesc"][0].get("value", ""),
            "max_temp_c": today.get("maxtempC", ""),
            "min_temp_c": today.get("mintempC", ""),
            "sunset": astronomy.get("sunset", ""),
            "city": city,
        }
    except Exception as e:
        print(f"[Weather] parse failed: {e}")
        return None


def format_weather_for_prompt(weather: Optional[Dict[str, Any]]) -> str:
    if not weather:
        return "الطقس: غير متوفر حالياً"

    return (
        f"الطقس في {weather['city']} اليوم: "
        f"{weather['description']}، "
        f"الحرارة {weather['temp_c']}°C (الشعور الفعلي {weather['feels_like_c']}°C)، "
        f"أعلى {weather['max_temp_c']}°C وأدنى {weather['min_temp_c']}°C، "
        f"الرطوبة {weather['humidity']}%، "
        f"الغروب الساعة {weather['sunset']}"
    )
