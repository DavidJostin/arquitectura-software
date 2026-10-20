"""Configuración central del scraper: rutas, tiempos, navegador y stealth."""
from __future__ import annotations

from pathlib import Path

# --- Rutas -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
COOKIES_PATH = BASE_DIR / "cookies.json"
OUTPUT_PATH = BASE_DIR / "output.json"

DEBUG_DIR = BASE_DIR / "debug"  # capturas cuando Instagram pide login/checkpoint

# --- Objetivo ----------------------------------------------------------------
# Perfil a extraer cuando se ejecuta `python main.py` sin argumentos (o con
# "Run" del editor). Acepta "usuario", "@usuario" o la URL completa del perfil.
TARGET_PROFILE = "https://www.instagram.com/jessicapauchi099/"

# --- Instagram ---------------------------------------------------------------
# URL base del sitio: NO poner aquí un perfil (se usa para construir todas las URLs).
INSTAGRAM_URL = "https://www.instagram.com"
# App ID público que usa la web de Instagram en la cabecera X-IG-App-ID.
IG_APP_ID = "936619743392459"
MAX_POSTS = 200
MAX_PREVIEW_COMMENTS = 5  # comentarios de vista previa guardados por post

# Fragmentos de URL cuyas respuestas JSON se interceptan.
API_URL_PATTERNS = (
    "/api/v1/users/web_profile_info/",
    "/api/v1/feed/user/",
    "/graphql/query",
    "/api/graphql",
)

# --- Navegador ---------------------------------------------------------------
HEADLESS = False
# "chrome" usa el Google Chrome instalado (huella más realista). Si no está,
# se usa el Chromium que trae Playwright. None = usar siempre Chromium.
BROWSER_CHANNEL: str | None = "chrome"

# Si se deja vacío, el User-Agent se construye con la versión REAL del
# navegador lanzado, para que coincida con navigator.userAgentData.
USER_AGENT = ""
USER_AGENT_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1366, "height": 768}
LOCALE = "es-ES"
TIMEZONE_ID = "America/Guayaquil"
ACCEPT_LANGUAGE = "es-ES,es;q=0.9,en;q=0.8"

CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    f"--window-size={VIEWPORT['width']},{VIEWPORT['height']}",
]
# Evita la barra "Chrome está siendo controlado por software automatizado".
IGNORE_DEFAULT_ARGS = ["--enable-automation"]

# Script inyectado antes de cualquier script de la página.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => undefined });
if (!window.chrome) { window.chrome = { runtime: {} }; }
const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (_origQuery) {
  window.navigator.permissions.query = (params) =>
    params && params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _origQuery.call(window.navigator.permissions, params);
}
"""

# --- Tiempos -----------------------------------------------------------------
DEFAULT_TIMEOUT_MS = 30_000
NETWORK_IDLE_TIMEOUT_MS = 10_000
NAV_DELAY = (2.5, 5.0)       # pausa tras cada navegación (s)
SCROLL_DELAY = (2.5, 4.8)    # pausa entre scrolls (s)
MAX_SCROLL_ROUNDS = 20       # tope absoluto de scrolls
MAX_STAGNANT_ROUNDS = 4      # scrolls seguidos sin posts nuevos antes de parar
