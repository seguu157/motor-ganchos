"""Respaldo con Instaloader y una cuenta secundaria de Instagram.

No guarda la contraseña. El login interactivo lo hace la librería con getpass
y la sesión queda en un fichero. Entre cuentas espera 20-60 s al azar. Un
401, un 429, un checkpoint o un login requerido paran la tanda: no hay
reintentos.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Pausas deliberadamente lentas. Instagram corta las cuentas que piden en ráfaga.
ACCOUNT_DELAY_SECONDS = (20, 60)
POST_DELAY_SECONDS = (2, 5)

_BLOCK_TYPES = {
    "LoginRequiredException",
    "TooManyRequestsException",
    "LoginException",
    "BadCredentialsException",
    "TwoFactorAuthRequiredException",
}


class InstaloaderNotInstalled(RuntimeError):
    """Falta el paquete instaloader."""


class InstagramNotConfigured(RuntimeError):
    """No hay usuario o fichero de sesión. No se ha llamado a Instagram."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "Falta la sesión de Instaloader. Define IG_USERNAME e IG_SESSION_FILE "
                "y ejecuta `radar ig-login`. No se ha hecho ninguna petición."
            )
        )


class InstagramBlocked(RuntimeError):
    """401, 429, checkpoint o login requerido. La tanda se detiene."""

    def __init__(self, message: str, partial: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.partial = list(partial or [])


def load_instaloader_module() -> Any:
    try:
        import instaloader
    except ImportError as exc:
        raise InstaloaderNotInstalled(
            "Falta el paquete instaloader. Instálalo con el resto de dependencias "
            "(está en requirements.txt) antes de usar esta fuente."
        ) from exc
    return instaloader


def build_loader(module: Any | None = None) -> Any:
    """Una sola tentativa de conexión. El valor por defecto de la librería reintenta."""
    instaloader = module or load_instaloader_module()
    return instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        max_connection_attempts=1,
    )


def login_secondary_account(username: str, session_file: Path, *, module: Any | None = None) -> None:
    """Pide la contraseña a Instaloader (getpass) y guarda solo la sesión.

    Esta función no recibe la contraseña y no la escribe en la base ni en un log.
    """
    instaloader = module or load_instaloader_module()
    loader = build_loader(instaloader)
    loader.interactive_login(username)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    loader.save_session_to_file(str(session_file))
    try:
        session_file.chmod(0o600)
    except OSError:
        pass


def post_to_item(post: Any, fallback_owner: str) -> dict[str, Any]:
    """Pasa un Post de Instaloader (o un objeto con los mismos atributos) al dict del parser.

    Si no hay video_play_count, se usa video_view_count como reproducciones.
    Sin eso la mediana se quedaría vacía: Instaloader a menudo solo trae vistas.
    """
    shortcode = _text(_read(post, "shortcode", "shortCode"))
    media_id = _read(post, "mediaid", "id")
    play_count = _int(_read(post, "video_play_count", "play_count"))
    view_count = _int(_read(post, "video_view_count", "view_count"))
    if play_count is None:
        play_count = view_count
    owner = _text(_read(post, "owner_username", "ownerUsername")) or fallback_owner
    url = f"https://www.instagram.com/reel/{shortcode}/" if shortcode else _text(_read(post, "url"))
    if not media_id and not shortcode and not url:
        raise ValueError("El reel no trae shortcode ni id.")
    return {
        "id": str(media_id or shortcode or url),
        "shortCode": shortcode,
        "caption": _text(_read(post, "caption")) or "",
        "url": url,
        "likesCount": _int(_read(post, "likes", "likes_count")),
        "commentsCount": _int(_read(post, "comments", "comments_count")),
        "videoPlayCount": play_count,
        "videoViewCount": view_count,
        "timestamp": _iso(_read(post, "date_utc", "date", "timestamp")),
        "videoUrl": _text(_read(post, "video_url", "videoUrl")),
        "ownerUsername": owner,
        "videoDuration": _float(_read(post, "video_duration", "videoDuration")),
        "productType": "clips",
    }


def as_instagram_block(exc: BaseException) -> InstagramBlocked | None:
    if isinstance(exc, InstagramBlocked):
        return exc
    name = type(exc).__name__
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    markers = (
        "401",
        "429",
        "checkpoint",
        "checkpoint_required",
        "challenge_required",
        "login required",
        "login_required",
        "please wait a few minutes",
    )
    if name in _BLOCK_TYPES or status in (401, 429) or any(marker in text for marker in markers):
        return InstagramBlocked(
            "Instagram pidió parar "
            f"({name}). No se reintenta. Usa una cuenta secundaria y, si sigue el bloqueo, "
            f"una IP residencial en vez del VPS. Detalle: {exc}"
        )
    return None


class InstaloaderSource:
    name = "instaloader"

    def __init__(
        self,
        username: str | None,
        session_file: Path,
        *,
        max_accounts: int = 10,
        sleeper: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
        profile_lookup: Callable[[str], Any] | None = None,
        module: Any | None = None,
        account_delay: tuple[float, float] = ACCOUNT_DELAY_SECONDS,
        post_delay: tuple[float, float] = POST_DELAY_SECONDS,
    ) -> None:
        self.username = username
        self.session_file = Path(session_file)
        self.max_accounts = max_accounts
        self._sleeper = sleeper
        self._uniform = uniform
        self._profile_lookup = profile_lookup
        self._module = module
        self.account_delay = account_delay
        self.post_delay = post_delay
        self.warnings: list[str] = []
        self.skipped: list[str] = []
        self._loader: Any = None

    def fetch_reels(self, usernames: list[str], results_limit: int) -> list[dict[str, Any]]:
        if self._profile_lookup is None:
            self._ensure_session()
        chosen = list(usernames[: self.max_accounts])
        self.skipped = list(usernames[self.max_accounts :])
        items: list[dict[str, Any]] = []
        for index, account in enumerate(chosen):
            if index:
                self._pause("account")
            try:
                profile = self._profile(account)
                count = 0
                for post in profile.get_reels():
                    if count >= results_limit:
                        break
                    if count:
                        self._pause("post")
                    try:
                        items.append(post_to_item(post, account))
                    except Exception as exc:
                        self._raise_if_blocked(exc, items)
                        self.warnings.append(f"@{account}: reel ilegible ({exc}).")
                        continue
                    count += 1
            except InstagramBlocked:
                raise
            except Exception as exc:
                self._raise_if_blocked(exc, items)
                self.warnings.append(f"@{account}: {exc}")
        return items

    def close(self) -> None:
        context = getattr(self._loader, "context", None)
        close = getattr(context, "close", None)
        if close:
            close()

    def _profile(self, account: str) -> Any:
        if self._profile_lookup is not None:
            return self._profile_lookup(account)
        if self._loader is None:
            module = self._module or load_instaloader_module()
            self._loader = build_loader(module)
            self._loader.load_session_from_file(self.username, str(self.session_file))
        return module_profile(self._module or load_instaloader_module(), self._loader, account)

    def _ensure_session(self) -> None:
        if not self.username or not self.session_file.is_file():
            raise InstagramNotConfigured()

    def _pause(self, kind: str) -> None:
        low, high = self.account_delay if kind == "account" else self.post_delay
        self._sleeper(self._uniform(low, high))

    def _raise_if_blocked(self, exc: BaseException, items: list[dict[str, Any]]) -> None:
        if isinstance(exc, InstagramBlocked):
            if not exc.partial:
                exc.partial = list(items)
            raise exc
        blocked = as_instagram_block(exc)
        if blocked is not None:
            blocked.partial = list(items)
            raise blocked from exc


def module_profile(module: Any, loader: Any, account: str) -> Any:
    return module.Profile.from_username(loader.context, account)


def _read(post: Any, *names: str) -> Any:
    if isinstance(post, dict):
        for name in names:
            if name in post and post[name] is not None:
                return post[name]
        return None
    for name in names:
        value = getattr(post, name, None)
        if value is not None:
            return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text = str(value).strip()
    return text or None
