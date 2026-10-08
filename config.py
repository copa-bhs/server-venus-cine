import os
from pathlib import Path

# ==========================================================
# Diretório base
# ==========================================================
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


# ==========================================================
# Carregamento do .env
# ==========================================================
def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(
            f"[config] Variável obrigatória '{key}' não encontrada.\n"
            f"        Crie o arquivo .env em: {ENV_FILE}\n"
            f"        Modelo disponível em: .env.example"
        )
    return value.strip()


# ==========================================================
# IPTV (obrigatório)
# ==========================================================
IPTV_USERNAME = _require("IPTV_USERNAME")
IPTV_PASSWORD = _require("IPTV_PASSWORD")
IPTV_HOST     = _require("IPTV_HOST").rstrip("/")

IPTV_URL = os.getenv(
    "IPTV_URL",
    f"{IPTV_HOST}/get.php?username={IPTV_USERNAME}&password={IPTV_PASSWORD}&type=m3u_plus&output=ts",
)

USER_AGENT = os.getenv("USER_AGENT", "VLC/3.0.20 LibVLC/3.0.20")


# ==========================================================
# TMDB (opcional)
# ==========================================================
TMDB_API_KEY  = os.getenv("TMDB_API_KEY", "").strip()
TMDB_LANGUAGE = os.getenv("TMDB_LANGUAGE", "pt-BR")
TMDB_ENABLED  = bool(TMDB_API_KEY)
TMDB_RECENT_DAYS = int(os.getenv("TMDB_RECENT_DAYS", "90"))
TMDB_MAX_PER_CYCLE = int(os.getenv("TMDB_MAX_PER_CYCLE", "30"))


# ==========================================================
# Enricher (background)
# ==========================================================
ENRICH_BATCH_SIZE    = int(os.getenv("ENRICH_BATCH_SIZE", "20"))
ENRICH_INTERVAL      = int(os.getenv("ENRICH_INTERVAL", "60"))
ENRICH_STARTUP_DELAY = int(os.getenv("ENRICH_STARTUP_DELAY", "30"))


# ==========================================================
# Trailer checker
# ==========================================================
TRAILER_CHECK_INTERVAL      = int(os.getenv("TRAILER_CHECK_INTERVAL", "21600"))
TRAILER_CHECK_BATCH         = int(os.getenv("TRAILER_CHECK_BATCH", "25"))
TRAILER_CHECK_STARTUP_DELAY = int(os.getenv("TRAILER_CHECK_STARTUP_DELAY", "120"))


# ==========================================================
# Proxy reverso de streaming
# ==========================================================
PROXY_ENABLED          = os.getenv("PROXY_ENABLED", "true").lower() == "true"
PROXY_MAX_CONCURRENT   = int(os.getenv("PROXY_MAX_CONCURRENT", "100"))
PROXY_MAX_PER_IP       = int(os.getenv("PROXY_MAX_PER_IP", "3"))
PROXY_CHUNK_SIZE       = int(os.getenv("PROXY_CHUNK_SIZE", "65536"))
PROXY_CONNECT_TIMEOUT  = int(os.getenv("PROXY_CONNECT_TIMEOUT", "10"))
PROXY_READ_TIMEOUT     = int(os.getenv("PROXY_READ_TIMEOUT", "300"))

# Base pública pra montar URLs completas (opcional)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


# ==========================================================
# Gerais
# ==========================================================
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
PORT      = int(os.getenv("PORT", "8000"))

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE     = 200

DOWNLOAD_CHUNK_SIZE      = 1024 * 256
DOWNLOAD_WORKERS         = 8
DOWNLOAD_CONNECT_TIMEOUT = 15
DOWNLOAD_READ_TIMEOUT    = 60


# ==========================================================
# Cache
# ==========================================================
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

M3U_FILE       = CACHE_DIR / "playlist.m3u"
META_FILE      = CACHE_DIR / "meta.json"
INDEX_FILE     = CACHE_DIR / "index.json"
NOVIDADES_FILE = CACHE_DIR / "novidades.json"
SLUG_MAP_FILE  = CACHE_DIR / "slug_map.json"

TMDB_CACHE_DIR = CACHE_DIR / "tmdb"
TMDB_CACHE_DIR.mkdir(exist_ok=True)

INFO_CACHE_DIR = CACHE_DIR / "info"
INFO_CACHE_DIR.mkdir(exist_ok=True)


# ==========================================================
# Fallback de capa
# ==========================================================
DEFAULT_MOVIE_COVER  = "https://via.placeholder.com/300x450?text=Sem+Capa"
DEFAULT_SERIES_COVER = "https://via.placeholder.com/300x450?text=Serie"