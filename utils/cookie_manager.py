"""Carga, valida y normaliza cookies exportadas del navegador para Playwright.

Formatos aceptados en cookies.json:
  * Lista de cookies exportada por extensiones (Cookie-Editor, EditThisCookie...).
  * Objeto {"cookies": [...]}.
  * Diccionario simple {"sessionid": "...", "csrftoken": "...", ...}.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from exceptions import CookieError, SessionExpiredError

log = logging.getLogger(__name__)

REQUIRED_COOKIES = ("sessionid", "ds_user_id", "csrftoken")
RECOMMENDED_COOKIES = ("mid",)
PLACEHOLDER_PREFIX = "PEGA_AQUI"
DEFAULT_DOMAIN = ".instagram.com"

# Valores de sameSite de Chrome/Firefox -> valores que acepta Playwright.
_SAMESITE_MAP = {
    "no_restriction": "None",
    "none": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "Lax",
}


def load_cookies(path: str | Path) -> list[dict[str, Any]]:
    """Lee el archivo y devuelve cookies listas para ``context.add_cookies()``."""
    path = Path(path)
    if not path.exists():
        raise CookieError(
            f"No se encontró {path}. Copia cookies.json.example a cookies.json "
            "y pega las cookies exportadas de tu navegador."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CookieError(f"{path.name} no es un JSON válido: {exc}") from exc

    cookies = [c for c in map(_normalize, _extract_list(raw)) if c is not None]
    _validate(cookies)
    log.info("Cargadas %d cookies desde %s", len(cookies), path.name)
    return cookies


def _extract_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        raw = raw["cookies"]
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        # Formato simple nombre -> valor.
        return [{"name": k, "value": v} for k, v in raw.items() if isinstance(v, str)]
    raise CookieError("Formato de cookies no reconocido (se esperaba lista u objeto).")


def _normalize(cookie: dict[str, Any]) -> dict[str, Any] | None:
    name, value = cookie.get("name"), cookie.get("value")
    if not isinstance(name, str) or not isinstance(value, str):
        return None

    domain = cookie.get("domain") or DEFAULT_DOMAIN
    if "instagram.com" not in domain:
        log.debug("Ignorando cookie %s de dominio %s", name, domain)
        return None

    result: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": cookie.get("path") or "/",
        "httpOnly": bool(cookie.get("httpOnly", False)),
        "secure": bool(cookie.get("secure", True)),
    }

    expires = cookie.get("expirationDate", cookie.get("expires"))
    if isinstance(expires, (int, float)) and expires > 0 and not cookie.get("session"):
        result["expires"] = float(expires)

    same_site = _SAMESITE_MAP.get(str(cookie.get("sameSite") or "unspecified").lower(), "Lax")
    result["sameSite"] = same_site
    if same_site == "None":
        result["secure"] = True  # Chrome exige Secure para SameSite=None.
    return result


def _validate(cookies: list[dict[str, Any]]) -> None:
    by_name = {c["name"]: c for c in cookies}

    missing = [n for n in REQUIRED_COOKIES if not by_name.get(n, {}).get("value")]
    if missing:
        raise CookieError(f"Faltan cookies obligatorias: {', '.join(missing)}")

    placeholders = [c["name"] for c in cookies if c["value"].startswith(PLACEHOLDER_PREFIX)]
    if placeholders:
        raise CookieError(
            "cookies.json aún contiene valores de plantilla en: " + ", ".join(placeholders)
        )

    session_expiry = by_name["sessionid"].get("expires", 0)
    if session_expiry and session_expiry < time.time():
        raise SessionExpiredError(
            "La cookie 'sessionid' está caducada. Inicia sesión en el navegador "
            "y vuelve a exportar las cookies."
        )

    for name in RECOMMENDED_COOKIES:
        if name not in by_name:
            log.warning("Cookie recomendada ausente: %s (puede aumentar los bloqueos)", name)
