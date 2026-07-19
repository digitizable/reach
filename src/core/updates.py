"""Check GitHub releases for Reach updates."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app_config import APPLICATION_VERSION

# Official desktop repository
GITHUB_OWNER = "digitizable"
GITHUB_REPO = "reach"
GITHUB_RELEASES_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
GITHUB_RELEASES_PAGE = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
)
GITHUB_REPO_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"

DEFAULT_CHECK_INTERVAL_HOURS = 24
_USER_AGENT = f"Reach/{APPLICATION_VERSION} (+{GITHUB_REPO_PAGE})"


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of a single update check."""

    ok: bool
    current_version: str
    latest_version: str = ""
    tag_name: str = ""
    release_name: str = ""
    release_url: str = ""
    published_at: str = ""
    update_available: bool = False
    message: str = ""
    error: str = ""

    @property
    def summary(self) -> str:
        if self.error:
            return self.error
        if self.message:
            return self.message
        if self.update_available:
            return f"Update available: {self.latest_version} (you have {self.current_version})"
        return f"Up to date ({self.current_version})"


def parse_version(text: str) -> tuple[int, ...]:
    """Parse a version/tag into comparable numeric parts.

    Accepts ``0.1.0``, ``v0.1.0``, ``0.1.0-beta.1`` (pre-release suffix ignored
    for ordering of the numeric core).
    """
    s = (text or "").strip()
    if s.lower().startswith("v"):
        s = s[1:]
    # Drop build metadata / pre-release for core comparison
    s = s.split("+", 1)[0]
    core = s.split("-", 1)[0]
    parts: list[int] = []
    for piece in core.split("."):
        m = re.match(r"(\d+)", piece)
        if m:
            parts.append(int(m.group(1)))
        else:
            break
    return tuple(parts) if parts else (0,)


def version_is_newer(latest: str, current: str) -> bool:
    """True if *latest* is strictly newer than *current*."""
    a = parse_version(latest)
    b = parse_version(current)
    # Pad to same length
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def should_check_now(
    *,
    enabled: bool,
    last_check_iso: str,
    interval_hours: int = DEFAULT_CHECK_INTERVAL_HOURS,
    now: datetime | None = None,
) -> bool:
    """Whether an automatic check is due."""
    if not enabled:
        return False
    if interval_hours <= 0:
        return True
    if not (last_check_iso or "").strip():
        return True
    try:
        last = datetime.fromisoformat(last_check_iso.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= last + timedelta(hours=interval_hours)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check_for_updates(
    *,
    current_version: str | None = None,
    timeout_sec: float = 12.0,
) -> UpdateResult:
    """Query GitHub for the latest release and compare to *current_version*."""
    current = (current_version or APPLICATION_VERSION).strip() or "0.0.0"
    req = urllib.request.Request(
        GITHUB_RELEASES_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            raw = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return UpdateResult(
                ok=True,
                current_version=current,
                release_url=GITHUB_RELEASES_PAGE,
                message=f"No published releases yet (running {current})",
            )
        return UpdateResult(
            ok=False,
            current_version=current,
            error=f"GitHub returned HTTP {exc.code}",
            release_url=GITHUB_RELEASES_PAGE,
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return UpdateResult(
            ok=False,
            current_version=current,
            error=f"Could not reach GitHub: {reason}",
            release_url=GITHUB_RELEASES_PAGE,
        )
    except TimeoutError:
        return UpdateResult(
            ok=False,
            current_version=current,
            error="Update check timed out",
            release_url=GITHUB_RELEASES_PAGE,
        )
    except OSError as exc:
        return UpdateResult(
            ok=False,
            current_version=current,
            error=f"Update check failed: {exc}",
            release_url=GITHUB_RELEASES_PAGE,
        )

    if status and int(status) >= 400:
        return UpdateResult(
            ok=False,
            current_version=current,
            error=f"GitHub returned HTTP {status}",
            release_url=GITHUB_RELEASES_PAGE,
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return UpdateResult(
            ok=False,
            current_version=current,
            error=f"Invalid GitHub response: {exc}",
        )

    if not isinstance(data, dict):
        return UpdateResult(
            ok=False,
            current_version=current,
            error="Unexpected GitHub response",
        )

    tag = str(data.get("tag_name") or "").strip()
    latest = tag[1:] if tag.lower().startswith("v") else tag
    if not latest:
        return UpdateResult(
            ok=False,
            current_version=current,
            error="Latest release has no tag name",
            release_url=GITHUB_RELEASES_PAGE,
        )

    html_url = str(data.get("html_url") or "").strip() or GITHUB_RELEASES_PAGE
    name = str(data.get("name") or "").strip() or tag
    published = str(data.get("published_at") or "").strip()
    newer = version_is_newer(latest, current)

    if newer:
        msg = f"Update available: {latest} (you have {current})"
    else:
        msg = f"Up to date ({current})"

    return UpdateResult(
        ok=True,
        current_version=current,
        latest_version=latest,
        tag_name=tag or f"v{latest}",
        release_name=name,
        release_url=html_url,
        published_at=published,
        update_available=newer,
        message=msg,
    )


def check_for_updates_async(
    on_done: Callable[[UpdateResult], None],
    *,
    current_version: str | None = None,
    timeout_sec: float = 12.0,
) -> None:
    """Run :func:`check_for_updates` on a daemon thread; call *on_done* with the result.

    *on_done* is invoked from the worker thread — marshal to the GTK main loop
    with ``GLib.idle_add`` if you touch UI.
    """
    import threading

    def worker() -> None:
        result = check_for_updates(
            current_version=current_version,
            timeout_sec=timeout_sec,
        )
        try:
            on_done(result)
        except Exception:
            pass

    threading.Thread(target=worker, name="spectre-update-check", daemon=True).start()
