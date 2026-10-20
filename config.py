import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

def load_env_file() -> None:
    if not ENV_FILE.exists(): return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ: os.environ[key] = value

load_env_file()

def _require(key: str) -> str:
    value = os.getenv(key)
    if not value: raise RuntimeError(f"Variável obrigatória '{key}' não encontrada no .env")
    return value.strip()

# ==========================================================
# IPTV PRINCIPAL
# ==========================================================
IPTV_USERNAME = _require("IPTV_USERNAME")
IPTV_PASSWORD = _require("IPTV_PASSWORD")
IPTV_HOST     = _require("IPTV_HOST").rstrip("/")

# ==========================================================
# IPTV BELLATRIX (SECUNDÁRIO)
# ==========================================================
BELLATRIX_USER = "206877981"
BELLATRIX_PASS = "501431350"
BELLATRIX_HOST = "http://bellatrix.live"

# Lista de provedores (facilita a expansão para 3 ou mais no futuro)
IPTV_PROVIDERS = [
    {
        "name": "principal",
        "host": IPTV_HOST,
        "user": IPTV_USERNAME,
        "pass": IPTV_PASSWORD,
        "prefix": "" # Sem prefixo para os itens originais
    },
    {
        "name": "bellatrix",
        "host": BELLATRIX_HOST,
        "user": BELLATRIX_USER,
        "pass": BELLATRIX_PASS,
        "prefix": "bellatrix_" # Itens do bellatrix terão esse prefixo
    }
]

USER_AGENT = os.getenv("USER_AGENT", "VLC/3.0.20 LibVLC/3.0.20")

# TMDB
TMDB_API_KEY  = os.getenv("TMDB_API_KEY", "").strip()
TMDB_LANGUAGE = os.getenv("TMDB_LANGUAGE", "pt-BR")
TMDB_ENABLED  = bool(TMDB_API_KEY)
TMDB_RECENT_DAYS = int(os.getenv("TMDB_RECENT_DAYS", "90"))
TMDB_MAX_PER_CYCLE = int(os.getenv("TMDB_MAX_PER_CYCLE", "30"))

# Fanart
FANART_API_KEY  = os.getenv("FANART_API_KEY", "").strip()
FANART_ENABLED  = bool(FANART_API_KEY)
FANART_LANGUAGE = os.getenv("FANART_LANGUAGE", "pt").lower()[:2]

# Proxy
PROXY_ENABLED          = os.getenv("PROXY_ENABLED", "true").lower() == "true"
PROXY_MAX_CONCURRENT   = int(os.getenv("PROXY_MAX_CONCURRENT", "100"))
PROXY_MAX_PER_IP       = int(os.getenv("PROXY_MAX_PER_IP", "3"))
PROXY_CHUNK_SIZE       = int(os.getenv("PROXY_CHUNK_SIZE", "262144"))
PROXY_CONNECT_TIMEOUT  = int(os.getenv("PROXY_CONNECT_TIMEOUT", "10"))
PROXY_READ_TIMEOUT     = int(os.getenv("PROXY_READ_TIMEOUT", "300"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Gerais
CACHE_TTL = int(os.getenv("CACHE_TTL", "21600"))
PORT      = int(os.getenv("PORT", "8000"))
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE     = 200

# Cache
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
FILMES_FILE = CACHE_DIR / "filmes.json"
SERIES_FILE = CACHE_DIR / "series.json"
CANAIS_FILE = CACHE_DIR / "canais.json"
META_FILE      = CACHE_DIR / "meta.json"
NOVIDADES_FILE = CACHE_DIR / "novidades.json"
SLUG_MAP_FILE  = CACHE_DIR / "slug_map.json"
SLUG_MAP_FILMES_FILE   = CACHE_DIR / "slug_map_filmes.json"
SLUG_MAP_SERIES_FILE   = CACHE_DIR / "slug_map_series.json"
SLUG_MAP_CANAIS_FILE   = CACHE_DIR / "slug_map_canais.json"

# Uploads
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
(UPLOAD_DIR / "capas").mkdir(exist_ok=True)
(UPLOAD_DIR / "banners").mkdir(exist_ok=True)
(UPLOAD_DIR / "logos").mkdir(exist_ok=True)