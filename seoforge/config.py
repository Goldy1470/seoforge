from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file()


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    site_url: str = os.getenv("SEOFORGE_SITE_URL", "https://example.com")
    brand_name: str = os.getenv("SEOFORGE_BRAND_NAME", "Brand")
    brand_tagline: str = os.getenv("SEOFORGE_BRAND_TAGLINE", "")
    db_path: str = os.getenv("SEOFORGE_DB", str(ROOT / "data" / "seoforge.db"))
    exports_dir: str = os.getenv("SEOFORGE_EXPORTS", str(ROOT / "exports"))
    directories_csv: str = os.getenv(
        "SEOFORGE_DIRECTORIES_CSV", str(ROOT / "data" / "directories_73.csv")
    )

    user_agent: str = os.getenv(
        "SEOFORGE_UA",
        "Mozilla/5.0 (compatible; SEOForgeBot/1.0; +https://github.com/seoforge)",
    )
    max_pages: int = int(os.getenv("SEOFORGE_MAX_PAGES", "500"))
    max_depth: int = int(os.getenv("SEOFORGE_MAX_DEPTH", "8"))
    max_concurrency: int = int(os.getenv("SEOFORGE_MAX_CONCURRENCY", "8"))
    request_timeout: int = int(os.getenv("SEOFORGE_TIMEOUT", "25"))
    respect_robots: bool = _bool(os.getenv("SEOFORGE_RESPECT_ROBOTS"), True)

    gsc_credentials: str = os.getenv(
        "GSC_CREDENTIALS_PATH", str(ROOT / "data" / "gsc_client_secret.json")
    )
    gsc_property: str = os.getenv("GSC_PROPERTY", "")
    gsc_days: int = int(os.getenv("GSC_DAYS", "90"))

    dataforseo_login: str = os.getenv("DATAFORSEO_LOGIN", "")
    dataforseo_password: str = os.getenv("DATAFORSEO_PASSWORD", "")

    serp_method: str = os.getenv("SEOFORGE_SERP_METHOD", "auto")
    serpdev_api_key: str = os.getenv("SERPDEV_API_KEY", "")

    openseo_url: str = os.getenv("OPENSEO_URL", "")
    openseo_api_key: str = os.getenv("OPENSEO_API_KEY", "")

    browser_agent: str = os.getenv("SEOFORGE_BROWSER_AGENT", "browseruse")
    confirm_submissions: bool = _bool(os.getenv("SEOFORGE_CONFIRM_SUBMISSIONS"), True)
    headless_browser: bool = _bool(os.getenv("SEOFORGE_HEADLESS"), True)

    default_geo: str = os.getenv("SEOFORGE_GEO", "United States")
    default_language: str = os.getenv("SEOFORGE_LANGUAGE", "en")

    def ensure_dirs(self) -> None:
        Path(self.exports_dir).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
