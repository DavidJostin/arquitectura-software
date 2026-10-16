"""Jerarquía de errores del scraper (permite mensajes y códigos de salida claros)."""


class ScraperError(Exception):
    """Error genérico del scraper."""


class CookieError(ScraperError):
    """El archivo de cookies no existe, está mal formado o incompleto."""


class SessionExpiredError(ScraperError):
    """Las cookies ya no son válidas: Instagram pide iniciar sesión."""


class ChallengeRequiredError(SessionExpiredError):
    """Instagram exige un checkpoint/verificación manual de la cuenta."""


class RateLimitError(ScraperError):
    """Instagram respondió con HTTP 429 o con un aviso de 'espera unos minutos'."""


class ProfileNotFoundError(ScraperError):
    """El perfil no existe o no está disponible."""
