"""Scraper de perfiles de Instagram basado en intercepción de red con Playwright.

Flujo:
  1. Lanza Chrome/Chromium con flags anti-automatización y un contexto realista.
  2. Inyecta las cookies de una sesión ya iniciada (no hay login automático).
  3. Abre el perfil y escucha las respuestas JSON de la API/GraphQL.
  4. Hace scroll humano hasta reunir ``max_posts`` publicaciones.
  5. Si la red no trae todo, completa con selectores DOM como respaldo.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

import config
from exceptions import (
    ChallengeRequiredError,
    ProfileNotFoundError,
    RateLimitError,
    ScraperError,
    SessionExpiredError,
)
from models import Comment, Post, PostType, Profile, ScrapeResult
from utils.humanize import human_scroll, jitter, random_mouse_move

log = logging.getLogger(__name__)

_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_SHORTCODE_RE = re.compile(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
# '#' precedido de inicio o de un carácter que no sea palabra/'&' (evita '&#39;').
_HASHTAG_RE = re.compile(r"(?<![\w&])#(\w+)")
_RATE_LIMIT_HINTS = ("wait a few minutes", "feedback_required", "rate limit", "try again later")
# Formulario de login que Instagram superpone a veces SIN cambiar la URL.
_LOGIN_FORM_SELECTOR = 'form#loginForm, input[name="username"][type="text"]'

# fetch ejecutado dentro de la página: usa las cookies y cabeceras del navegador.
_PROFILE_FETCH_JS = """
async ([username, appId]) => {
  const r = await fetch(
    `/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`,
    { headers: { 'X-IG-App-ID': appId, 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'include' });
  return { status: r.status, text: await r.text() };
}
"""

_DOM_POSTS_JS = """
els => els.map(a => {
  const img = a.querySelector('img');
  return { href: a.getAttribute('href'),
           img: img ? (img.currentSrc || img.src) : null,
           alt: img ? img.alt : null };
})
"""


class InstagramScraper:
    """Extrae perfil y publicaciones de un usuario. Úsese como context manager."""

    def __init__(
        self,
        cookies: list[dict[str, Any]],
        *,
        headless: bool = config.HEADLESS,
        max_posts: int = config.MAX_POSTS,
    ) -> None:
        self._cookies = cookies
        self.headless = headless
        self.max_posts = max_posts

        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

        self._reset_state("")

    # ------------------------------------------------------------------ ciclo de vida
    def __enter__(self) -> InstagramScraper:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def start(self) -> None:
        if urlparse(config.INSTAGRAM_URL).path.strip("/"):
            raise ScraperError(
                f"config.INSTAGRAM_URL debe ser la URL base 'https://www.instagram.com', "
                f"no un perfil ({config.INSTAGRAM_URL!r}). El perfil va en TARGET_PROFILE."
            )
        self._pw = sync_playwright().start()
        self._browser = self._launch_browser()
        user_agent = config.USER_AGENT or config.USER_AGENT_TEMPLATE.format(
            major=self._browser.version.split(".")[0]
        )
        log.info("Navegador %s | UA: %s", self._browser.version, user_agent)

        self._context = self._browser.new_context(
            user_agent=user_agent,
            viewport=config.VIEWPORT,
            locale=config.LOCALE,
            timezone_id=config.TIMEZONE_ID,
            extra_http_headers={"Accept-Language": config.ACCEPT_LANGUAGE},
        )
        self._context.set_default_timeout(config.DEFAULT_TIMEOUT_MS)
        self._context.add_init_script(config.STEALTH_INIT_SCRIPT)
        try:
            self._context.add_cookies(self._cookies)
        except PlaywrightError as exc:
            raise ScraperError(f"Playwright rechazó las cookies: {exc}") from exc

        self._page = self._context.new_page()
        self._apply_stealth_plugin(self._page)
        self._page.on("response", self._on_response)

    def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    closer.close()
                except PlaywrightError:
                    pass
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    def _launch_browser(self) -> Browser:
        assert self._pw is not None
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": config.CHROMIUM_ARGS,
            "ignore_default_args": config.IGNORE_DEFAULT_ARGS,
        }
        if config.BROWSER_CHANNEL:
            try:
                return self._pw.chromium.launch(channel=config.BROWSER_CHANNEL, **kwargs)
            except PlaywrightError:
                log.warning("Canal '%s' no disponible; usando Chromium de Playwright.",
                            config.BROWSER_CHANNEL)
        return self._pw.chromium.launch(**kwargs)

    @staticmethod
    def _apply_stealth_plugin(page: Page) -> None:
        """Aplica playwright-stealth si está instalado (v2.x o v1.x)."""
        try:
            from playwright_stealth import Stealth  # v2.x
        except ImportError:
            try:
                from playwright_stealth import stealth_sync  # v1.x
            except ImportError:
                log.info("playwright-stealth no instalado; solo se usa el script propio.")
                return
            stealth_sync(page)
            return
        try:
            stealth = Stealth(navigator_languages_override=("es-ES", "es"))
        except TypeError:
            stealth = Stealth()
        stealth.apply_stealth_sync(page)

    # ------------------------------------------------------------------ API pública
    def scrape(self, username: str) -> ScrapeResult:
        if self._page is None:
            raise ScraperError("Llama a start() o usa 'with InstagramScraper(...)'.")
        username = normalize_username(username)
        self._reset_state(username)

        self._ensure_logged_in()
        self._open_profile()
        profile = self._resolve_profile()

        if profile.is_private and not self._posts:
            log.warning("El perfil @%s es privado y no lo sigues: no hay posts visibles.",
                        username)
        else:
            self._collect_posts()

        posts = sorted(
            self._posts.values(),
            key=lambda p: p.timestamp or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[: self.max_posts]
        return ScrapeResult(profile=profile, posts=posts, total_posts=len(posts))

    # ------------------------------------------------------------------ navegación
    def _reset_state(self, username: str) -> None:
        self._target = username.lower()
        self._profile: Profile | None = None
        self._posts: dict[str, Post] = {}
        self._pending: list[Response] = []
        self._rate_limited = False
        self._unauthorized = False

    def _goto(self, url: str) -> Response | None:
        assert self._page is not None
        try:
            response = self._page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as exc:
            raise ScraperError(f"Timeout cargando {url}") from exc
        if response is not None and response.status == 429:
            raise RateLimitError(f"HTTP 429 al cargar {url}. Espera antes de reintentar.")
        jitter(*config.NAV_DELAY, page=self._page)
        return response

    def _ensure_logged_in(self) -> None:
        log.info("Abriendo Instagram con la sesión de cookies...")
        self._goto(f"{config.INSTAGRAM_URL}/")
        self._assert_session_alive()
        random_mouse_move(self._page)

    def _open_profile(self) -> None:
        assert self._page is not None
        log.info("Abriendo perfil @%s", self._target)
        response = self._goto(f"{config.INSTAGRAM_URL}/{self._target}/")
        # Primero la sesión: un login/checkpoint también puede llegar como 404.
        self._assert_session_alive()
        if response is not None and response.status == 404:
            self._screenshot("perfil_404")
            raise ProfileNotFoundError(
                f"El perfil @{self._target} no existe (HTTP 404 en {response.url})."
            )
        try:
            self._page.wait_for_load_state("networkidle", timeout=config.NETWORK_IDLE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass  # Instagram rara vez queda 100% inactivo; no es un error.
        self._process_pending()

    def _assert_session_alive(self) -> None:
        assert self._page is not None and self._context is not None
        url = self._page.url
        if "/challenge" in url or "/checkpoint" in url:
            shot = self._screenshot("checkpoint")
            raise ChallengeRequiredError(
                "Instagram pide verificar la cuenta (checkpoint). Resuélvelo manualmente "
                f"en el navegador y exporta de nuevo las cookies. Captura: {shot}"
            )
        if "/accounts/login" in url or self._login_form_visible():
            shot = self._screenshot("login")
            raise SessionExpiredError(
                "Instagram muestra el login: las cookies caducaron o fueron revocadas. "
                f"Vuelve a exportarlas desde un navegador con la sesión iniciada. Captura: {shot}"
            )
        session = next(
            (c for c in self._context.cookies(config.INSTAGRAM_URL) if c["name"] == "sessionid"),
            None,
        )
        if not session or not session["value"]:
            shot = self._screenshot("sin_sessionid")
            raise SessionExpiredError(
                f"Instagram eliminó la cookie 'sessionid': sesión inválida. Captura: {shot}"
            )
        self._raise_if_flagged()

    def _login_form_visible(self) -> bool:
        assert self._page is not None
        try:
            return self._page.locator(_LOGIN_FORM_SELECTOR).first.is_visible()
        except PlaywrightError:
            return False

    def _screenshot(self, reason: str) -> str | None:
        """Guarda una captura de página completa en config.DEBUG_DIR para diagnosticar."""
        if self._page is None:
            return None
        config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = config.DEBUG_DIR / f"{stamp}_{reason}.png"
        try:
            self._page.screenshot(path=str(path), full_page=True)
        except PlaywrightError as exc:
            log.warning("No se pudo guardar la captura (%s): %s", reason, exc)
            return None
        log.info("Captura guardada en %s (URL actual: %s)", path, self._page.url)
        return str(path)

    def _raise_if_flagged(self) -> None:
        if self._rate_limited:
            raise RateLimitError(
                "Instagram devolvió HTTP 429 (rate limit). Detén el scraper y espera "
                "al menos 15-30 minutos antes de reintentar."
            )
        if self._unauthorized:
            raise SessionExpiredError("La API respondió 401: la sesión ya no es válida.")

    # ------------------------------------------------------------------ intercepción de red
    def _on_response(self, response: Response) -> None:
        """Handler de ``page.on('response')``. Solo filtra y encola: el cuerpo se
        lee después en el flujo principal para no bloquear el bucle de eventos."""
        if not any(p in response.url for p in config.API_URL_PATTERNS):
            return
        status = response.status
        if status == 429:
            self._rate_limited = True
        elif status == 401:
            self._unauthorized = True
        elif status < 300:
            self._pending.append(response)
        else:
            log.debug("Respuesta %s ignorada: %s", status, response.url)

    def _process_pending(self) -> None:
        while self._pending:
            response = self._pending.pop(0)
            try:
                text = response.text()
            except PlaywrightError:
                continue  # cuerpo ya no disponible (redirección o navegación)
            for payload in parse_json_payloads(text):
                self._check_payload_errors(payload)
                self._ingest(payload)
        self._raise_if_flagged()

    @staticmethod
    def _check_payload_errors(payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        message = str(payload.get("message") or "").lower()
        if payload.get("require_login") or message == "login_required":
            raise SessionExpiredError("La API exige login: las cookies caducaron.")
        if "checkpoint" in message or payload.get("checkpoint_url"):
            raise ChallengeRequiredError("Instagram exige un checkpoint de seguridad.")
        if payload.get("spam") or any(h in message for h in _RATE_LIMIT_HINTS):
            raise RateLimitError(f"Instagram limitó las peticiones: {message or 'spam'}")

    def _ingest(self, node: Any) -> None:
        """Recorre el JSON buscando nodos de usuario objetivo y de publicaciones.

        Así no dependemos de la ruta exacta (``data.user...``,
        ``xdt_api__v1__feed__user_timeline_graphql_connection``...), que
        Instagram cambia a menudo."""
        if isinstance(node, list):
            for item in node:
                self._ingest(item)
            return
        if not isinstance(node, dict):
            return
        if self._try_parse_post(node):
            return  # no descender en hijos de carrusel
        self._try_parse_profile(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                self._ingest(value)

    def _try_parse_profile(self, node: dict[str, Any]) -> None:
        if str(node.get("username", "")).lower() != self._target:
            return
        if "edge_followed_by" not in node and "follower_count" not in node:
            return
        bio = node.get("biography")
        if bio is None and isinstance(node.get("biography_with_entities"), dict):
            bio = node["biography_with_entities"].get("raw_text")
        parsed = Profile(
            username=node["username"],
            user_id=_str_or_none(node.get("id") or node.get("pk")),
            full_name=node.get("full_name"),
            biography=bio,
            followers=_first(_count(node, "edge_followed_by"), node.get("follower_count")),
            following=_first(_count(node, "edge_follow"), node.get("following_count")),
            posts_count=_first(_count(node, "edge_owner_to_timeline_media"),
                               node.get("media_count")),
            is_private=node.get("is_private"),
            is_verified=node.get("is_verified"),
            external_url=node.get("external_url"),
            profile_pic_url=node.get("profile_pic_url_hd") or node.get("profile_pic_url"),
        )
        if self._profile is None:
            self._profile = parsed
            log.info("Perfil interceptado: %s seguidores, %s seguidos",
                     parsed.followers, parsed.following)
        else:
            updates = {k: v for k, v in parsed.model_dump().items() if v is not None}
            self._profile = self._profile.model_copy(update=updates)

    def _try_parse_post(self, node: dict[str, Any]) -> bool:
        shortcode = node.get("shortcode") or node.get("code")
        taken_at = _first(node.get("taken_at_timestamp"), node.get("taken_at"))
        if not isinstance(shortcode, str) or not isinstance(taken_at, (int, float)):
            return False
        if not self._belongs_to_target(node) or shortcode in self._posts:
            return True

        raw_id = node.get("pk") or str(node.get("id", "")).split("_")[0]
        caption = _caption(node)
        post_type, media_count = _post_type(node)
        self._posts[shortcode] = Post(
            post_id=str(raw_id or shortcode_to_media_id(shortcode)),
            shortcode=shortcode,
            url=f"{config.INSTAGRAM_URL}/p/{shortcode}/",
            caption=caption,
            hashtags=extract_hashtags(caption),
            post_type=post_type,
            media_count=media_count,
            top_comments=_preview_comments(node),
            timestamp=datetime.fromtimestamp(taken_at, tz=timezone.utc),
            like_count=_first(node.get("like_count"), _count(node, "edge_liked_by"),
                              _count(node, "edge_media_preview_like")),
            comment_count=_first(node.get("comment_count"), _count(node, "edge_media_to_comment"),
                                 _count(node, "edge_media_preview_comment")),
            image_url=_image_url(node),
            is_video=post_type == "Video",
        )
        return True

    def _belongs_to_target(self, node: dict[str, Any]) -> bool:
        owner = node.get("owner") or node.get("user") or {}
        name = str(owner.get("username", "")).lower() if isinstance(owner, dict) else ""
        if not name or name == self._target:
            return True
        # Publicaciones colaborativas: el dueño puede ser el otro coautor.
        coauthors = node.get("coauthor_producers") or []
        return any(str(c.get("username", "")).lower() == self._target
                   for c in coauthors if isinstance(c, dict))

    # ------------------------------------------------------------------ perfil
    def _resolve_profile(self) -> Profile:
        if self._profile is None:
            log.info("Perfil no interceptado pasivamente; consultando web_profile_info...")
            self._fetch_profile_api()
        if self._profile is None:
            log.warning("La API no devolvió el perfil; usando respaldo DOM (menos preciso).")
            self._profile = self._profile_from_dom()
        if self._profile is None:
            shot = self._screenshot("perfil_sin_datos")
            raise ProfileNotFoundError(
                f"No se pudo obtener información de @{self._target}. Captura: {shot}"
            )
        return self._profile

    def _fetch_profile_api(self) -> None:
        assert self._page is not None
        try:
            result = self._page.evaluate(_PROFILE_FETCH_JS, [self._target, config.IG_APP_ID])
        except PlaywrightError as exc:
            log.warning("Fallo el fetch de web_profile_info: %s", exc)
            return
        status = result.get("status")
        if status == 429:
            raise RateLimitError("web_profile_info respondió 429 (rate limit).")
        if status == 401:
            raise SessionExpiredError("web_profile_info respondió 401: sesión inválida.")
        if status == 404:
            self._screenshot("perfil_api_404")
            raise ProfileNotFoundError(f"El perfil @{self._target} no existe (web_profile_info 404).")
        if status != 200:
            log.warning("web_profile_info respondió HTTP %s", status)
            return
        for payload in parse_json_payloads(result.get("text", "")):
            self._check_payload_errors(payload)
            self._ingest(payload)
        self._pending.clear()  # la misma respuesta también pasó por el handler

    def _profile_from_dom(self) -> Profile | None:
        page = self._page
        assert page is not None

        def link_count(suffix: str) -> int | None:
            link = page.locator(f'a[href$="/{suffix}/"]').first
            if not link.count():
                return None
            titled = link.locator("span[title]").first
            raw = titled.get_attribute("title") if titled.count() else link.inner_text()
            return parse_human_count(raw)

        followers, following = link_count("followers"), link_count("following")
        if followers is None:
            meta = page.locator('meta[property="og:description"]').first
            content = meta.get_attribute("content") if meta.count() else None
            numbers = re.findall(r"([\d.,]+\s*[KkMm]?)", content or "")
            if len(numbers) >= 2:
                followers, following = parse_human_count(numbers[0]), parse_human_count(numbers[1])
        if followers is None and following is None:
            return None
        return Profile(username=self._target, followers=followers, following=following,
                       source="dom")

    # ------------------------------------------------------------------ publicaciones
    def _collect_posts(self) -> None:
        assert self._page is not None and self._profile is not None
        target = self.max_posts
        if self._profile.posts_count is not None:
            target = min(target, self._profile.posts_count)

        stagnant = 0
        for round_no in range(1, config.MAX_SCROLL_ROUNDS + 1):
            if len(self._posts) >= target:
                break
            before = len(self._posts)
            human_scroll(self._page)
            jitter(*config.SCROLL_DELAY, page=self._page)
            self._process_pending()
            log.info("Scroll %d: %d/%d publicaciones", round_no, len(self._posts), target)
            stagnant = stagnant + 1 if len(self._posts) == before else 0
            if stagnant >= config.MAX_STAGNANT_ROUNDS:
                log.info("Sin publicaciones nuevas tras %d scrolls; se detiene.", stagnant)
                break

        if len(self._posts) < target:
            self._collect_posts_from_dom(target)

    def _collect_posts_from_dom(self, target: int) -> None:
        """Respaldo: lee los enlaces del grid. Sin likes/comentarios ni fecha."""
        assert self._page is not None
        anchors = self._page.eval_on_selector_all(
            'a[href*="/p/"], a[href*="/reel/"]', _DOM_POSTS_JS
        )
        added = 0
        for anchor in anchors:
            if len(self._posts) >= target:
                break
            match = _SHORTCODE_RE.search(anchor.get("href") or "")
            if not match or match.group(1) in self._posts:
                continue
            shortcode = match.group(1)
            is_video = "/reel/" in (anchor.get("href") or "")
            caption = anchor.get("alt") or None
            self._posts[shortcode] = Post(
                post_id=shortcode_to_media_id(shortcode),
                shortcode=shortcode,
                url=f"{config.INSTAGRAM_URL}/p/{shortcode}/",
                caption=caption,
                hashtags=extract_hashtags(caption),
                post_type="Video" if is_video else "Image",
                image_url=anchor.get("img"),
                is_video=is_video,
                source="dom",
            )
            added += 1
        if added:
            log.info("Respaldo DOM añadió %d publicaciones (sin métricas).", added)


# ---------------------------------------------------------------------- helpers puros
def normalize_username(value: str) -> str:
    """Acepta 'usuario', '@usuario' o la URL completa del perfil."""
    value = value.strip()
    match = re.search(r"instagram\.com/([^/?#]+)", value)
    if match:
        value = match.group(1)
    value = value.lstrip("@").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", value):
        raise ValueError(f"Nombre de usuario inválido: {value!r}")
    return value


def parse_json_payloads(text: str) -> list[Any]:
    """Parsea respuestas JSON, incluidas las que traen el prefijo 'for (;;);' o
    varios objetos separados por saltos de línea (GraphQL con @defer)."""
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    if not text or text[0] not in "{[":
        return []
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        payloads = []
        for line in text.splitlines():
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return payloads


def extract_hashtags(text: str | None) -> list[str]:
    """Devuelve los '#hashtags' del texto, sin duplicados y en orden de aparición."""
    if not text:
        return []
    return list(dict.fromkeys(f"#{tag}" for tag in _HASHTAG_RE.findall(text)))


def shortcode_to_media_id(shortcode: str) -> str:
    """Decodifica el shortcode (base64 de Instagram) al ID numérico del medio."""
    media_id = 0
    for char in shortcode[:11]:
        media_id = media_id * 64 + _SHORTCODE_ALPHABET.index(char)
    return str(media_id)


def parse_human_count(raw: str | None) -> int | None:
    """Convierte '1,234', '10.5k', '1,2 M' o '3 mil' en entero (aproximado)."""
    if not raw:
        return None
    text = raw.strip().lower().replace(" ", " ")
    match = re.search(r"([\d.,]+)\s*(k|m|mil|mill)?", text)
    if not match:
        return None
    number, suffix = match.group(1), match.group(2)
    multiplier = {"k": 1_000, "mil": 1_000, "m": 1_000_000, "mill": 1_000_000}.get(suffix or "", 1)
    if multiplier == 1:
        digits = re.sub(r"[.,]", "", number)
        return int(digits) if digits else None
    return int(float(number.replace(",", ".")) * multiplier)


def _count(node: dict[str, Any], key: str) -> int | None:
    value = node.get(key)
    return value.get("count") if isinstance(value, dict) else None


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _caption(node: dict[str, Any]) -> str | None:
    caption = node.get("caption")
    if isinstance(caption, dict):
        return caption.get("text")
    if isinstance(caption, str):
        return caption
    edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
    return edges[0].get("node", {}).get("text") if edges else None


def _post_type(node: dict[str, Any]) -> tuple[PostType, int | None]:
    """Clasifica el post (API v1: media_type 1/2/8; GraphQL: __typename/sidecar)."""
    sidecar = (node.get("edge_sidecar_to_children") or {}).get("edges")
    children = node.get("carousel_media") or sidecar
    if (node.get("media_type") == 8 or children
            or str(node.get("__typename", "")).endswith("Sidecar")):
        count = _first(node.get("carousel_media_count"),
                       len(children) if isinstance(children, list) else None)
        return "Carousel", count
    if (node.get("is_video") or node.get("media_type") == 2
            or node.get("product_type") == "clips"):
        return "Video", None
    return "Image", None


def _preview_comments(node: dict[str, Any]) -> list[Comment]:
    """Comentarios de vista previa: 'preview_comments'/'comments' (API v1) o los
    edges de 'edge_media_to_parent_comment'/'edge_media_preview_comment' (GraphQL)."""
    raw: list[Any] = []
    for key in ("preview_comments", "comments"):
        if isinstance(node.get(key), list):
            raw = node[key]
            break
    else:
        for key in ("edge_media_to_parent_comment", "edge_media_preview_comment"):
            edges = (node.get(key) or {}).get("edges") or []
            if edges:
                raw = [e.get("node") for e in edges if isinstance(e, dict)]
                break

    comments: list[Comment] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        author = item.get("user") or item.get("owner") or {}
        username = author.get("username") if isinstance(author, dict) else None
        comments.append(Comment(username=username, text=item["text"]))
        if len(comments) >= config.MAX_PREVIEW_COMMENTS:
            break
    return comments


def _image_url(node: dict[str, Any]) -> str | None:
    if node.get("display_url") or node.get("thumbnail_src"):
        return node.get("display_url") or node.get("thumbnail_src")
    for source in (node, *(node.get("carousel_media") or [])[:1]):
        candidates = (source.get("image_versions2") or {}).get("candidates") or []
        if candidates:
            return candidates[0].get("url")
    return None
