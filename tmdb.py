import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from config import (
    TMDB_API_KEY, TMDB_LANGUAGE, TMDB_ENABLED,
    TMDB_RECENT_DAYS, TMDB_CACHE_DIR, USER_AGENT,
)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG  = "https://image.tmdb.org/t/p"
TMDB_CACHE_TTL = 60 * 60 * 24 * 30


def _cache_path(kind: str, chave: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in chave)[:120]
    return TMDB_CACHE_DIR / f"{kind}_{safe}.json"


def _cache_get(kind: str, chave: str):
    p = _cache_path(kind, chave)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) < TMDB_CACHE_TTL:
            return data.get("payload")
    except Exception:
        pass
    return None


def _cache_put(kind: str, chave: str, payload):
    p = _cache_path(kind, chave)
    p.write_text(
        json.dumps({"_ts": time.time(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def _build_query(titulo: str, ano=None, extra: dict | None = None):
    params = {
        "api_key": TMDB_API_KEY,
        "language": TMDB_LANGUAGE,
        "query": titulo,
        "include_adult": "false",
    }
    if ano:
        try:
            params["year"] = str(int(ano))
        except Exception:
            pass
    if extra:
        params.update(extra)
    return params


def _pick_best(results: list, ano):
    if not results:
        return None
    if ano:
        try:
            ano_int = int(ano)
            for r in results:
                data = r.get("release_date") or r.get("first_air_date") or ""
                if data.startswith(str(ano_int)):
                    return r
        except Exception:
            pass
    return results[0]


def _is_recent(date_str: str) -> bool:
    if not date_str:
        return False
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return (datetime.now() - d) <= timedelta(days=TMDB_RECENT_DAYS)
    except Exception:
        return False


def _img(path: str, size: str = "w500"):
    if not path:
        return None
    return f"{TMDB_IMG}/{size}{path}"


def _melhor_logo(logos: list, lang: str = "pt"):
    if not logos:
        return None
    for pref in (lang, "en", None):
        for lg in logos:
            if lg.get("iso_639_1") == pref:
                return lg.get("file_path")
    return logos[0].get("file_path")


def _melhor_backdrop(backdrops: list, lang: str = "pt"):
    if not backdrops:
        return None
    for bd in backdrops:
        if bd.get("iso_639_1") == lang:
            return bd.get("file_path")
    for bd in backdrops:
        if bd.get("iso_639_1") is None:
            return bd.get("file_path")
    maior = max(backdrops, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0))
    return maior.get("file_path")


def _melhor_trailer(videos: list):
    if not videos:
        return None
    trailers = [v for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"]
    if not trailers:
        trailers = [v for v in videos if v.get("site") == "YouTube"]
    if not trailers:
        return None
    for pref in ("pt", "en"):
        for t in trailers:
            if t.get("iso_639_1") == pref and t.get("official"):
                return t
    for pref in ("pt", "en"):
        for t in trailers:
            if t.get("iso_639_1") == pref:
                return t
    return trailers[0]


def _classificacao_br_movie(release_dates: dict):
    resultados = (release_dates or {}).get("results") or []
    for pais in resultados:
        if pais.get("iso_3166_1") == "BR":
            for rd in pais.get("release_dates", []):
                cert = (rd.get("certification") or "").strip()
                if cert:
                    return cert
    for pais in resultados:
        if pais.get("iso_3166_1") == "US":
            for rd in pais.get("release_dates", []):
                cert = (rd.get("certification") or "").strip()
                if cert and cert not in ("NR", "UR"):
                    return cert
    return None


def _classificacao_br_tv(content_ratings: dict):
    resultados = (content_ratings or {}).get("results") or []
    for pais in resultados:
        if pais.get("iso_3166_1") == "BR":
            rating = (pais.get("rating") or "").strip()
            if rating:
                return rating
    for pais in resultados:
        if pais.get("iso_3166_1") == "US":
            rating = (pais.get("rating") or "").strip()
            if rating and rating not in ("NR", "TV-NR"):
                return rating
    return None


async def buscar_filme(titulo: str, ano=None):
    if not TMDB_ENABLED or not titulo:
        return None

    chave = f"{titulo}|{ano or ''}"
    cached = _cache_get("movie", chave)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(
                f"{TMDB_BASE}/search/movie",
                params=_build_query(titulo, ano),
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            _cache_put("movie", chave, None)
            return None

        melhor = _pick_best(data.get("results", []), ano)
        if not melhor:
            _cache_put("movie", chave, None)
            return None

        tmdb_id = melhor["id"]

        try:
            r = await client.get(
                f"{TMDB_BASE}/movie/{tmdb_id}",
                params={
                    "api_key": TMDB_API_KEY,
                    "language": TMDB_LANGUAGE,
                    "append_to_response": "images,videos,release_dates",
                    "include_image_language": f"{TMDB_LANGUAGE[:2]},en,null",
                },
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            full = r.json()
        except Exception:
            full = {}

    images = full.get("images") or {}
    videos = (full.get("videos") or {}).get("results") or []
    release = full.get("release_date", "")

    backdrop_path = _melhor_backdrop(images.get("backdrops") or [], TMDB_LANGUAGE[:2])
    logo_path = _melhor_logo(images.get("logos") or [], TMDB_LANGUAGE[:2])
    trailer = _melhor_trailer(videos)
    cert = _classificacao_br_movie(full.get("release_dates") or {})

    resultado = {
        "tmdb_id": tmdb_id,
        "titulo_tmdb": full.get("title") or melhor.get("title"),
        "titulo_original": full.get("original_title"),
        "tagline": full.get("tagline") or None,
        "release_date": release,
        "ano": int(release[:4]) if release else None,
        "duracao_min": full.get("runtime"),
        "sinopse": full.get("overview") or None,
        "score": round(full.get("vote_average") or 0, 1) or None,
        "votos": full.get("vote_count"),
        "capa": _img(full.get("poster_path"), "w500"),
        "capa_grande": _img(full.get("poster_path"), "w780"),
        "banner": _img(backdrop_path, "w1280"),
        "banner_4k": _img(backdrop_path, "original"),
        "logo": _img(logo_path, "w500"),
        "logo_original": _img(logo_path, "original"),
        "trailer": trailer.get("key") if trailer else None,
        "trailer_url": f"https://www.youtube.com/watch?v={trailer['key']}" if trailer else None,
        "trailer_nome": trailer.get("name") if trailer else None,
        "classificacao": cert,
        "generos": [g.get("name") for g in (full.get("genres") or [])],
        "popularidade": full.get("popularity"),
        "recente": _is_recent(release),
    }
    _cache_put("movie", chave, resultado)
    return resultado


async def buscar_serie(titulo: str, ano=None):
    if not TMDB_ENABLED or not titulo:
        return None

    chave = f"{titulo}|{ano or ''}"
    cached = _cache_get("series", chave)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(
                f"{TMDB_BASE}/search/tv",
                params=_build_query(titulo, ano),
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            _cache_put("series", chave, None)
            return None

        melhor = _pick_best(data.get("results", []), ano)
        if not melhor:
            _cache_put("series", chave, None)
            return None

        tmdb_id = melhor["id"]

        try:
            r = await client.get(
                f"{TMDB_BASE}/tv/{tmdb_id}",
                params={
                    "api_key": TMDB_API_KEY,
                    "language": TMDB_LANGUAGE,
                    "append_to_response": "images,videos,content_ratings",
                    "include_image_language": f"{TMDB_LANGUAGE[:2]},en,null",
                },
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            full = r.json()
        except Exception:
            full = {}

    images = full.get("images") or {}
    videos = (full.get("videos") or {}).get("results") or []
    release = full.get("first_air_date", "")

    backdrop_path = _melhor_backdrop(images.get("backdrops") or [], TMDB_LANGUAGE[:2])
    logo_path = _melhor_logo(images.get("logos") or [], TMDB_LANGUAGE[:2])
    trailer = _melhor_trailer(videos)
    cert = _classificacao_br_tv(full.get("content_ratings") or {})

    resultado = {
        "tmdb_id": tmdb_id,
        "titulo_tmdb": full.get("name") or melhor.get("name"),
        "titulo_original": full.get("original_name"),
        "tagline": full.get("tagline") or None,
        "release_date": release,
        "ano": int(release[:4]) if release else None,
        "sinopse": full.get("overview") or None,
        "score": round(full.get("vote_average") or 0, 1) or None,
        "votos": full.get("vote_count"),
        "capa": _img(full.get("poster_path"), "w500"),
        "capa_grande": _img(full.get("poster_path"), "w780"),
        "banner": _img(backdrop_path, "w1280"),
        "banner_4k": _img(backdrop_path, "original"),
        "logo": _img(logo_path, "w500"),
        "logo_original": _img(logo_path, "original"),
        "trailer": trailer.get("key") if trailer else None,
        "trailer_url": f"https://www.youtube.com/watch?v={trailer['key']}" if trailer else None,
        "trailer_nome": trailer.get("name") if trailer else None,
        "classificacao": cert,
        "generos": [g.get("name") for g in (full.get("genres") or [])],
        "total_temporadas": full.get("number_of_seasons"),
        "total_episodios": full.get("number_of_episodes"),
        "popularidade": full.get("popularity"),
        "recente": _is_recent(release),
    }
    _cache_put("series", chave, resultado)
    return resultado


def limpar_cache():
    import shutil
    if TMDB_CACHE_DIR.exists():
        shutil.rmtree(TMDB_CACHE_DIR)
        TMDB_CACHE_DIR.mkdir(exist_ok=True)
    print("[✓] Cache TMDB limpo.")