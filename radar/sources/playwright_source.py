"""Fuente por defecto: Chromium con una sesión que Antonio abre a mano.

No guarda la contraseña. El perfil de navegador vive en disco y el marcador
`perfil.listo` solo anota el usuario, si se indicó. Ante 401, 429, checkpoint
o challenge la tanda se para y no se reintenta. Solo se leen las cuentas de
la watchlist.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ACCOUNT_DELAY_SECONDS = (25.0, 70.0)
POST_DELAY_SECONDS = (3.0, 8.0)

_CLAVES_DE_MEDIO = (
    "__typename",
    "product_type",
    "productType",
    "video_view_count",
    "video_play_count",
    "play_count",
    "like_count",
    "edge_liked_by",
    "edge_sidecar_to_children",
    "carousel_media",
)
_NO_BAJAR_A = {"edge_sidecar_to_children", "carousel_media"}


class PlaywrightNotInstalled(RuntimeError):
    """Falta el paquete o el navegador. No se ha abierto Chromium."""

    def __init__(self) -> None:
        super().__init__(
            "Falta Playwright. Con el entorno activo: pip install playwright "
            "y después playwright install chromium. No se ha abierto el navegador."
        )


class BrowserNotConfigured(RuntimeError):
    """No hay sesión de navegador. No se ha abierto Instagram."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "Falta la sesión del navegador. Ejecuta `radar login` una vez "
                "e inicia sesión a mano con la cuenta secundaria. "
                "No se ha hecho ninguna petición."
            )
        )


class BrowserBlocked(RuntimeError):
    """401, 429, checkpoint, challenge o login. La tanda se detiene."""

    def __init__(self, message: str, partial: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.partial = list(partial or [])


@dataclass
class PageHit:
    status: int | None = 200
    url: str = ""
    text: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)


def classify_page(*, status: int | None, url: str, text: str) -> str | None:
    """None si la página se puede leer. `privada` si se omite la cuenta.

    Cualquier otro valor para la tanda entera: no hay reintento.
    """
    url_l = (url or "").lower()
    text_l = (text or "").lower()
    if status in (401, 429):
        return str(status)
    if "checkpoint" in url_l or "checkpoint_required" in text_l:
        return "checkpoint"
    if "/challenge" in url_l or "challenge_required" in text_l:
        return "challenge"
    if "/accounts/login" in url_l or "login_required" in text_l:
        return "login"
    if "esta cuenta es privada" in text_l or "this account is private" in text_l:
        return "privada"
    return None


def mensaje_bloqueo(codigo: str) -> str:
    textos = {
        "401": (
            "Instagram respondió 401 y la sesión no sirve. Paro la tanda y no reintento. "
            "Vuelve a ejecutar `radar login` con la cuenta secundaria."
        ),
        "429": (
            "Instagram respondió 429 (demasiadas peticiones). Paro la tanda y no reintento. "
            "Espera antes de lanzar otra."
        ),
        "checkpoint": (
            "Instagram pidió un checkpoint. Paro la tanda y no reintento. "
            "Resuélvelo a mano con `radar login`."
        ),
        "challenge": (
            "Instagram pidió un challenge. Paro la tanda y no reintento. "
            "Resuélvelo a mano con `radar login`."
        ),
        "login": (
            "La sesión del navegador ha caducado. Paro la tanda y no reintento. "
            "Vuelve a ejecutar `radar login`."
        ),
    }
    return textos.get(codigo, "Instagram pidió parar. No se reintenta.")


def login_browser(
    profile_dir: Path,
    *,
    username: str | None = None,
    starter: Callable[[Path], None] | None = None,
) -> None:
    """Abre Chromium para que Antonio entre a mano. No recibe la contraseña."""
    profile_dir = Path(profile_dir)
    abrir = starter or _abrir_chromium
    abrir(profile_dir)
    if not _hay_cookies(profile_dir):
        raise BrowserNotConfigured(
            "No ha quedado sesión en el perfil del navegador. "
            "No se marca como lista. Repite `radar login` cuando veas el inicio de Instagram."
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    marker = profile_dir / "perfil.listo"
    etiqueta = (username or "").strip().lstrip("@")
    marker.write_text(etiqueta + "\n", encoding="utf-8")
    try:
        marker.chmod(0o600)
    except OSError:
        pass


def _hay_cookies(profile_dir: Path) -> bool:
    candidatos = (profile_dir / "Default" / "Cookies", profile_dir / "Cookies")
    for path in candidatos:
        if path.is_file() and path.stat().st_size > 0:
            return True
    return False


def _abrir_chromium(profile_dir: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightNotInstalled() from exc
    import sys

    if not sys.stdin.isatty():
        raise BrowserNotConfigured(
            "`radar login` necesita una terminal interactiva. No se ha abierto el navegador."
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    print("Se abre Chromium. Entra en Instagram con la cuenta secundaria.")
    print("La contraseña la escribes tú en la ventana: Radar no la ve ni la guarda.")
    print("Cuando veas el inicio, vuelve aquí y pulsa Enter.")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            locale="es-ES",
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=60_000)
        input()
        context.close()


def nodos_desde_json(blob: Any, owner: str) -> list[dict[str, Any]]:
    encontrados: list[dict[str, Any]] = []
    vistos: set[str] = set()

    def walk(valor: Any) -> None:
        if isinstance(valor, dict):
            if _parece_medio(valor):
                item = nodo_a_item(valor, owner)
                codigo = item.get("shortCode") if item else None
                if item and codigo and codigo not in vistos:
                    vistos.add(codigo)
                    encontrados.append(item)
                for clave, hijo in valor.items():
                    if clave in _NO_BAJAR_A:
                        continue
                    walk(hijo)
                return
            for hijo in valor.values():
                walk(hijo)
        elif isinstance(valor, list):
            for hijo in valor:
                walk(hijo)

    walk(blob)
    return encontrados


def nodo_a_item(node: dict[str, Any], owner: str) -> dict[str, Any] | None:
    short = _texto(node.get("shortcode") or node.get("shortCode") or node.get("code"))
    if not short:
        return None
    nodo_owner = owner
    if isinstance(node.get("owner"), dict):
        nodo_owner = _texto(node["owner"].get("username")) or owner
    else:
        nodo_owner = _texto(node.get("ownerUsername") or node.get("owner_username")) or owner
    nodo_owner = nodo_owner.lstrip("@").lower()
    if nodo_owner != owner.lstrip("@").lower():
        return None
    product = _texto(node.get("product_type") or node.get("productType")).lower()
    typename = _texto(node.get("__typename")).lower()
    sidecar = node.get("edge_sidecar_to_children")
    carrusel_raw = node.get("carousel_media")
    es_carrusel = (
        typename == "graphsidecar"
        or product in {"carousel_container", "sidecar", "carrusel"}
        or isinstance(sidecar, dict)
        or isinstance(carrusel_raw, list)
    )
    likes = node.get("like_count")
    if likes is None and isinstance(node.get("edge_liked_by"), dict):
        likes = node["edge_liked_by"].get("count")
    comments = node.get("comment_count")
    if comments is None and isinstance(node.get("edge_media_to_comment"), dict):
        comments = node["edge_media_to_comment"].get("count")
    if comments is None and isinstance(node.get("edge_media_to_parent_comment"), dict):
        comments = node["edge_media_to_parent_comment"].get("count")
    plays = node.get("video_play_count")
    if plays is None:
        plays = node.get("play_count")
    if plays is None:
        plays = node.get("video_view_count")
    slides = _slides_de_nodo(node) if es_carrusel else []
    return {
        "id": str(node.get("id") or short),
        "shortCode": short,
        "caption": _pie(node),
        "url": f"https://www.instagram.com/{'p' if es_carrusel else 'reel'}/{short}/",
        "likesCount": likes,
        "commentsCount": comments,
        "videoPlayCount": None if es_carrusel else plays,
        "videoViewCount": None if es_carrusel else node.get("video_view_count"),
        "timestamp": _iso(node.get("taken_at_timestamp") or node.get("taken_at") or node.get("timestamp")),
        "ownerUsername": nodo_owner,
        "productType": "carousel_container" if es_carrusel else (product or "clips"),
        "videoUrl": None if es_carrusel else _texto(node.get("video_url") or node.get("videoUrl")),
        "slides": slides,
    }


class PlaywrightNavigator:
    """Abre el perfil público. Una página cada vez, sin reintentos."""

    def __init__(self, profile_dir: Path, *, headless: bool = True) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self._playwright: Any = None
        self._context: Any = None

    def open_page(self, username: str, kind: str) -> PageHit:
        self._ensure()
        ruta = "reels/" if kind == "reels" else ""
        url = f"https://www.instagram.com/{username}/{ruta}"
        page = self._context.new_page()
        try:
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as exc:  # noqa: BLE001 - el navegador falla de muchas formas
                raise BrowserBlocked(
                    "La página no respondió. Paro la tanda y no reintento. "
                    f"({type(exc).__name__})"
                ) from exc
            status = response.status if response is not None else None
            final_url = page.url or url
            try:
                text = page.inner_text("body")[:4000]
            except Exception:  # noqa: BLE001
                text = ""
            items: list[dict[str, Any]] = []
            try:
                blobs = page.eval_on_selector_all(
                    "script[type='application/json']",
                    "els => els.map(e => e.textContent || '')",
                )
            except Exception:  # noqa: BLE001
                blobs = []
            for blob in blobs or []:
                items.extend(_items_de_texto(blob, username))
            return PageHit(status=status, url=final_url, text=text, items=items)
        finally:
            page.close()

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _ensure(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlaywrightNotInstalled() from exc
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            locale="es-ES",
            viewport={"width": 1280, "height": 900},
        )


class PlaywrightSource:
    name = "playwright"

    def __init__(
        self,
        profile_dir: Path,
        *,
        max_accounts: int = 5,
        max_posts: int = 20,
        sleeper: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
        navigator: Any | None = None,
        account_delay: tuple[float, float] = ACCOUNT_DELAY_SECONDS,
        post_delay: tuple[float, float] = POST_DELAY_SECONDS,
        headless: bool = True,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.max_accounts = max_accounts
        self.max_posts = max_posts
        self._sleeper = sleeper
        self._uniform = uniform
        self._navigator = navigator
        self.account_delay = account_delay
        self.post_delay = post_delay
        self.headless = headless
        self.warnings: list[str] = []
        self.skipped: list[str] = []
        self._owns_navigator = False

    def fetch_reels(self, usernames: list[str], results_limit: int) -> list[dict[str, Any]]:
        if self._navigator is None and not (self.profile_dir / "perfil.listo").is_file():
            raise BrowserNotConfigured()
        limite = max(1, min(results_limit, self.max_posts))
        elegidas = list(usernames[: self.max_accounts])
        self.skipped = list(usernames[self.max_accounts :])
        navigator = self._navigator
        if navigator is None:
            navigator = PlaywrightNavigator(self.profile_dir, headless=self.headless)
            self._navigator = navigator
            self._owns_navigator = True
        items: list[dict[str, Any]] = []
        try:
            for index, account in enumerate(elegidas):
                if index:
                    self._pause("account")
                try:
                    batch = self._leer_cuenta(navigator, account, limite)
                except BrowserBlocked as exc:
                    raise BrowserBlocked(str(exc), partial=items + list(exc.partial)) from exc
                items.extend(batch)
            return items
        finally:
            if self._owns_navigator:
                navigator.close()
                self._navigator = None
                self._owns_navigator = False

    def close(self) -> None:
        if self._owns_navigator and self._navigator is not None:
            self._navigator.close()
            self._navigator = None
            self._owns_navigator = False

    def _leer_cuenta(self, navigator: Any, account: str, limite: int) -> list[dict[str, Any]]:
        recogidos: list[dict[str, Any]] = []
        vistos: set[str] = set()
        for indice, kind in enumerate(("reels", "posts")):
            if len(recogidos) >= limite:
                break
            if indice:
                self._pause("post")
            hit = navigator.open_page(account, kind)
            codigo = classify_page(status=hit.status, url=hit.url, text=hit.text)
            if codigo == "privada":
                self.warnings.append(f"@{account} es privada. Solo se leen cuentas públicas. Se omite.")
                return recogidos
            if codigo:
                raise BrowserBlocked(mensaje_bloqueo(codigo), partial=recogidos)
            for raw in hit.items:
                item = raw if _ya_es_payload(raw) else nodo_a_item(raw, account)
                if not item:
                    continue
                codigo_item = str(item.get("shortCode") or "")
                if not codigo_item or codigo_item in vistos:
                    continue
                owner = str(item.get("ownerUsername") or account).lstrip("@").lower()
                if owner != account.lstrip("@").lower():
                    continue
                vistos.add(codigo_item)
                recogidos.append(item)
                if len(recogidos) >= limite:
                    break
        return recogidos

    def _pause(self, kind: str) -> None:
        low, high = self.account_delay if kind == "account" else self.post_delay
        self._sleeper(self._uniform(low, high))


def _parece_medio(node: dict[str, Any]) -> bool:
    if not (node.get("shortcode") or node.get("shortCode") or node.get("code")):
        return False
    return any(clave in node for clave in _CLAVES_DE_MEDIO)


def _slides_de_nodo(node: dict[str, Any]) -> list[dict[str, Any]]:
    hijos: list[Any] = []
    sidecar = node.get("edge_sidecar_to_children")
    if isinstance(sidecar, dict):
        for edge in sidecar.get("edges") or []:
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                hijos.append(edge["node"])
    if isinstance(node.get("carousel_media"), list):
        hijos.extend(node["carousel_media"])
    if isinstance(node.get("slides"), list):
        hijos.extend(node["slides"])
    slides = []
    for child in hijos:
        if not isinstance(child, dict):
            continue
        slides.append(
            {
                "texto": _texto_slide(child),
                "display_url": _texto(child.get("display_url") or child.get("image_url") or child.get("imagen")),
            }
        )
    return slides


def _texto_slide(child: dict[str, Any]) -> str:
    texto = _texto(child.get("texto") or child.get("text") or child.get("accessibility_caption"))
    if texto:
        return texto
    caption = child.get("edge_media_to_caption")
    if isinstance(caption, dict):
        edges = caption.get("edges") or []
        if edges and isinstance(edges[0], dict):
            return _texto((edges[0].get("node") or {}).get("text"))
    return ""


def _pie(node: dict[str, Any]) -> str:
    caption = node.get("caption")
    if isinstance(caption, dict):
        return _texto(caption.get("text"))
    if isinstance(caption, str) and caption.strip():
        return caption.strip()
    edge = node.get("edge_media_to_caption")
    if isinstance(edge, dict):
        edges = edge.get("edges") or []
        if edges and isinstance(edges[0], dict):
            return _texto((edges[0].get("node") or {}).get("text"))
    return ""


def _ya_es_payload(raw: dict[str, Any]) -> bool:
    return "shortCode" in raw and ("ownerUsername" in raw or "videoPlayCount" in raw or "productType" in raw)


def _items_de_texto(blob: str, owner: str) -> list[dict[str, Any]]:
    texto = (blob or "").strip()
    if not texto or texto[0] not in "{[":
        return []
    try:
        data = json.loads(texto)
    except json.JSONDecodeError:
        return []
    return nodos_desde_json(data, owner)


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def _iso(valor: Any) -> str | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return datetime.fromtimestamp(valor, timezone.utc).replace(microsecond=0).isoformat()
    return str(valor)
