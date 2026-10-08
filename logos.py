from __future__ import annotations

"""
Agregação multi-fonte de logos.
Prioridade: TMDB → Fanart.tv
Cache de 30 dias por item.
"""
import json
import time
from pathlib import Path

import httpx

from config import (
    USER_AGENT, LOGOS_CACHE_DIR,
    FANART_API_KEY, FANART_ENABLED, FANART_LANGUAGE,
)

FANART_BASE = "https://webservice.fanart.tv/v3"
LOGOS_CACHE_TTL = 60 * 60 * 24 * 30


# ==========================================================
# Cache
# ==========================================================
def _cache_path(kind: str, chave) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(chave))[:120]
    return LOGOS_CACHE_DIR / f"{kind}_{safe}.json"


def _cache_get(kind: str, chave):
    p = _cache_path(kind, chave)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) < LOGOS_CACHE_TTL:
            return data.get("payload")
    except Exception:
        pass
    return None


def _cache_put(kind: str, chave, payload):
    p = _cache_path(kind, chave)
    p.write_text(
        json.dumps({"_ts": time.time(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


# ==========================================================
# Helpers
# ==========================================================
def _melhor_por_idioma(itens: list, lang_pref: str = "pt"):
    if not itens:
        return None
    validos = [i for i in itens if i.get("url")]
    if not validos:
        return None

    def score(i):
        lang = (i.get("lang") or "").lower()
        if lang == lang_pref:
            lang_score = 0
        elif lang == "en":
            lang_score = 1
        elif lang == "":
            lang_score = 2
        else:
            lang_score = 3
        likes = -(i.get("likes") or 0)
        return (lang_score, likes)

    validos.sort(key=score)
    return validos[0]["url"]


# ==========================================================
# Fanart.tv — Filme
# ==========================================================
async def buscar_logo_fanart_movie(tmdb_id) -> str | None:
    if not FANART_ENABLED or not tmdb_id:
        return None

    chave = f"movie_{tmdb_id}"
    cached = _cache_get("fanart", chave)
    if cached is not None:
        return cached.get("logo") if isinstance(cached, dict) else None

    url = f"{FANART_BASE}/movies/{tmdb_id}?api_key={FANART_API_KEY}"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, headers={"User-Agent": USER_AGENT})
            if r.status_code == 404:
                _cache_put("fanart", chave, {"logo": None})
                return None
            r.raise_for_status()
            data = r.json()
        except Exception:
            _cache_put("fanart", chave, {"logo": None})
            return None

    logo = (
        _melhor_por_idioma(data.get("hdmovielogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("movielogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("hdmovieclearart") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("movieart") or [], FANART_LANGUAGE)
    )

    _cache_put("fanart", chave, {"logo": logo})
    return logo


# ==========================================================
# Fanart.tv — Série
# ==========================================================
async def buscar_logo_fanart_tv(tvdb_id) -> str | None:
    if not FANART_ENABLED or not tvdb_id:
        return None

    chave = f"tv_{tvdb_id}"
    cached = _cache_get("fanart", chave)
    if cached is not None:
        return cached.get("logo") if isinstance(cached, dict) else None

    url = f"{FANART_BASE}/tv/{tvdb_id}?api_key={FANART_API_KEY}"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, headers={"User-Agent": USER_AGENT})
            if r.status_code == 404:
                _cache_put("fanart", chave, {"logo": None})
                return None
            r.raise_for_status()
            data = r.json()
        except Exception:
            _cache_put("fanart", chave, {"logo": None})
            return None

    logo = (
        _melhor_por_idioma(data.get("hdtvlogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("clearlogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("tvthumb") or [], FANART_LANGUAGE)
    )

    _cache_put("fanart", chave, {"logo": logo})
    return logo


# ==========================================================
# APIs públicas agregadas
# ==========================================================
async def buscar_logo_filme(titulo: str, ano=None,
                            tmdb_id=None, logo_tmdb=None) -> str | None:
    """
    Prioridade: TMDB → Fanart.tv
    """
    if logo_tmdb:
        return logo_tmdb

    if FANART_ENABLED and tmdb_id:
        return await buscar_logo_fanart_movie(tmdb_id)

    return None


async def buscar_logo_serie(titulo: str, ano=None,
                            tmdb_id=None, tvdb_id=None,
                            logo_tmdb=None) -> str | None:
    """
    Prioridade: TMDB → Fanart.tv (via TVDB ID)
    """
    if logo_tmdb:
        return logo_tmdb

    if FANART_ENABLED and tvdb_id:
        return await buscar_logo_fanart_tv(tvdb_id)

    return None