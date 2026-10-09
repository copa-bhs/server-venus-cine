"""
Fanart.tv — busca de logos em tempo real (sem cache).
"""
import httpx

from config import (
    USER_AGENT,
    FANART_API_KEY, FANART_ENABLED, FANART_LANGUAGE,
)

FANART_BASE = "https://webservice.fanart.tv/v3"


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


async def buscar_logo_fanart_movie(tmdb_id) -> str | None:
    if not FANART_ENABLED or not tmdb_id:
        return None

    url = f"{FANART_BASE}/movies/{tmdb_id}?api_key={FANART_API_KEY}"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, headers={"User-Agent": USER_AGENT})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
        except Exception:
            return None

    return (
        _melhor_por_idioma(data.get("hdmovielogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("movielogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("hdmovieclearart") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("movieart") or [], FANART_LANGUAGE)
    )


async def buscar_logo_fanart_tv(tvdb_id) -> str | None:
    if not FANART_ENABLED or not tvdb_id:
        return None

    url = f"{FANART_BASE}/tv/{tvdb_id}?api_key={FANART_API_KEY}"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, headers={"User-Agent": USER_AGENT})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
        except Exception:
            return None

    return (
        _melhor_por_idioma(data.get("hdtvlogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("clearlogo") or [], FANART_LANGUAGE)
        or _melhor_por_idioma(data.get("tvthumb") or [], FANART_LANGUAGE)
    )


async def buscar_logo_filme(titulo: str, ano=None,
                            tmdb_id=None, logo_tmdb=None) -> str | None:
    if logo_tmdb:
        return logo_tmdb
    if FANART_ENABLED and tmdb_id:
        return await buscar_logo_fanart_movie(tmdb_id)
    return None


async def buscar_logo_serie(titulo: str, ano=None,
                            tmdb_id=None, tvdb_id=None,
                            logo_tmdb=None) -> str | None:
    if logo_tmdb:
        return logo_tmdb
    if FANART_ENABLED and tvdb_id:
        return await buscar_logo_fanart_tv(tvdb_id)
    return None