import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from config import (
    TMDB_API_KEY, TMDB_LANGUAGE, TMDB_ENABLED,
    TMDB_RECENT_DAYS, USER_AGENT,
)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG  = "https://image.tmdb.org/t/p"


# ==========================================================
# NORMALIZAÇÃO DE CLASSIFICAÇÃO ETÁRIA
# ==========================================================
_MAPA_CLASSIFICACAO = {
    "L": "L", "10": "10", "12": "12", "14": "14", "16": "16", "18": "18",
    "G": "L", "PG": "10", "PG-13": "14", "R": "16", "NC-17": "18",
    "NR": None, "UR": None,
    "TV-G": "L", "TV-Y": "L", "TV-Y7": "10", "TV-PG": "10",
    "TV-14": "14", "TV-MA": "18",
    "U": "L", "12A": "12", "15": "16",
    "APTA": "L", "7": "10", "13": "14",
    "FSK 0": "L", "FSK 6": "10", "FSK 12": "12", "FSK 16": "16", "FSK 18": "18",
    "Tout public": "L", "-10": "10", "-12": "12", "-16": "16", "-18": "18",
    "M": "16", "MA15+": "16", "R18+": "18", "PG-13+": "14",
    "14+": "14", "16+": "16", "18+": "18", "12+": "12", "10+": "10",
    "ALL": "L", "T": "L", "PG-12": "12",
}


def normalizar_classificacao(bruto):
    if not bruto:
        return None
    c = str(bruto).strip().upper()
    if not c:
        return None

    if c in _MAPA_CLASSIFICACAO:
        return _MAPA_CLASSIFICACAO[c]

    if c.isdigit():
        n = int(c)
        if n <= 9:
            return "L"
        if n <= 11:
            return "10"
        if n <= 13:
            return "12"
        if n <= 15:
            return "14"
        if n <= 17:
            return "16"
        return "18"

    m = re.search(r'\d+', c)
    if m:
        return normalizar_classificacao(m.group())

    if "LIVRE" in c or "FREE" in c or "ALL" in c:
        return "L"

    return None


# ==========================================================
# HELPERS
# ==========================================================
def _build_query(titulo: str, ano=None):
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


# ==========================================================
# BACKDROP — prioriza textless (sem texto embutido)
# ==========================================================
def _melhor_backdrop(backdrops: list, lang: str = "pt"):
    """
    Escolhe o melhor backdrop (imagem de fundo 16:9) para o hero.

    IMPORTANTE: o hero sobrepõe a LOGO do filme em cima do backdrop.
    Se usarmos um backdrop COM TEXTO, o título do filme fica duplicado
    (uma vez na imagem, outra vez na logo).

    Por isso a ordem de prioridade é:
      1. Textless (iso_639_1 == None) com resolução >= 1280px
      2. Qualquer textless (mesmo pequeno)
      3. Backdrop no idioma preferido (mesmo com texto)
      4. O maior disponível (qualquer idioma)
    """
    if not backdrops:
        return None

    # 1. Textless + boa resolução
    limpos_grandes = [
        bd for bd in backdrops
        if bd.get("iso_639_1") is None
        and (bd.get("width") or 0) >= 1280
    ]
    if limpos_grandes:
        melhor = max(
            limpos_grandes,
            key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)
        )
        print(f"[TMDB] backdrop textless {melhor.get('width')}x{melhor.get('height')}")
        return melhor.get("file_path")

    # 2. Qualquer textless
    limpos = [bd for bd in backdrops if bd.get("iso_639_1") is None]
    if limpos:
        melhor = max(
            limpos,
            key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)
        )
        print(f"[TMDB] backdrop textless (resolução menor)")
        return melhor.get("file_path")

    # 3. Idioma preferido
    for bd in backdrops:
        if bd.get("iso_639_1") == lang:
            print(f"[TMDB] backdrop em {lang} (COM texto embutido)")
            return bd.get("file_path")

    # 4. O maior disponível
    maior = max(
        backdrops,
        key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)
    )
    print(f"[TMDB] backdrop fallback (maior disponível)")
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


# ==========================================================
# CLASSIFICAÇÃO — tenta BR → PT → US → ES → qualquer
# ==========================================================
_PAISES_PREFERIDOS_MOVIE = ["BR", "PT", "US", "ES", "AR", "MX", "GB", "FR", "DE", "IT"]


def _classificacao_movie(release_dates: dict):
    resultados = (release_dates or {}).get("results") or []
    if not resultados:
        return None

    for codigo in _PAISES_PREFERIDOS_MOVIE:
        for pais in resultados:
            if pais.get("iso_3166_1") == codigo:
                for rd in pais.get("release_dates", []):
                    cert = (rd.get("certification") or "").strip()
                    if cert and cert not in ("NR", "UR", "N/A"):
                        norm = normalizar_classificacao(cert)
                        if norm:
                            print(f"[TMDB] Classificação '{cert}' ({codigo}) → '{norm}'")
                            return norm

    for pais in resultados:
        for rd in pais.get("release_dates", []):
            cert = (rd.get("certification") or "").strip()
            if cert and cert not in ("NR", "UR", "N/A"):
                norm = normalizar_classificacao(cert)
                if norm:
                    return norm
    return None


_PAISES_PREFERIDOS_TV = ["BR", "PT", "US", "ES", "AR", "MX", "GB", "FR", "DE", "IT"]


def _classificacao_tv(content_ratings: dict):
    resultados = (content_ratings or {}).get("results") or []
    if not resultados:
        return None

    for codigo in _PAISES_PREFERIDOS_TV:
        for pais in resultados:
            if pais.get("iso_3166_1") == codigo:
                rating = (pais.get("rating") or "").strip()
                if rating and rating not in ("NR", "TV-NR", "N/A"):
                    norm = normalizar_classificacao(rating)
                    if norm:
                        print(f"[TMDB] Classificação '{rating}' ({codigo}) → '{norm}'")
                        return norm

    for pais in resultados:
        rating = (pais.get("rating") or "").strip()
        if rating and rating not in ("NR", "TV-NR", "N/A"):
            norm = normalizar_classificacao(rating)
            if norm:
                return norm
    return None


# ==========================================================
# BUSCA POR ID — SEM CACHE
# ==========================================================
async def buscar_filme_por_tmdb_id(tmdb_id: int):
    if not TMDB_ENABLED or not tmdb_id:
        return None

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(
                f"{TMDB_BASE}/movie/{tmdb_id}",
                params={
                    "api_key": TMDB_API_KEY,
                    "language": TMDB_LANGUAGE,
                    "append_to_response": "images,videos,release_dates,external_ids",
                    "include_image_language": f"null,{TMDB_LANGUAGE[:2]},en",
                },
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            full = r.json()
        except Exception as e:
            print(f"[TMDB] Erro filme {tmdb_id}: {e}")
            return None

    images = full.get("images") or {}
    videos = (full.get("videos") or {}).get("results") or []
    release = full.get("release_date", "")
    external = full.get("external_ids") or {}

    backdrop_path = _melhor_backdrop(images.get("backdrops") or [], TMDB_LANGUAGE[:2])
    logo_path = _melhor_logo(images.get("logos") or [], TMDB_LANGUAGE[:2])
    trailer = _melhor_trailer(videos)
    cert = _classificacao_movie(full.get("release_dates") or {})

    return {
        "tmdb_id": tmdb_id,
        "imdb_id": external.get("imdb_id"),
        "titulo_tmdb": full.get("title"),
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


async def buscar_serie_por_tmdb_id(tmdb_id: int):
    if not TMDB_ENABLED or not tmdb_id:
        return None

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(
                f"{TMDB_BASE}/tv/{tmdb_id}",
                params={
                    "api_key": TMDB_API_KEY,
                    "language": TMDB_LANGUAGE,
                    "append_to_response": "images,videos,content_ratings,external_ids",
                    "include_image_language": f"null,{TMDB_LANGUAGE[:2]},en",
                },
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            full = r.json()
        except Exception as e:
            print(f"[TMDB] Erro série {tmdb_id}: {e}")
            return None

    images = full.get("images") or {}
    videos = (full.get("videos") or {}).get("results") or []
    release = full.get("first_air_date", "")
    external = full.get("external_ids") or {}

    backdrop_path = _melhor_backdrop(images.get("backdrops") or [], TMDB_LANGUAGE[:2])
    logo_path = _melhor_logo(images.get("logos") or [], TMDB_LANGUAGE[:2])
    trailer = _melhor_trailer(videos)
    cert = _classificacao_tv(full.get("content_ratings") or {})

    return {
        "tmdb_id": tmdb_id,
        "tvdb_id": external.get("tvdb_id"),
        "imdb_id": external.get("imdb_id"),
        "titulo_tmdb": full.get("name"),
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


# ==========================================================
# BUSCA POR TÍTULO — SEM CACHE
# ==========================================================
async def buscar_filme(titulo: str, ano=None):
    if not TMDB_ENABLED or not titulo:
        return None

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
            return None

        melhor = _pick_best(data.get("results", []), ano)
        if not melhor:
            return None

        tmdb_id = melhor["id"]

    return await buscar_filme_por_tmdb_id(tmdb_id)


async def buscar_serie(titulo: str, ano=None):
    if not TMDB_ENABLED or not titulo:
        return None

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
            return None

        melhor = _pick_best(data.get("results", []), ano)
        if not melhor:
            return None

        tmdb_id = melhor["id"]

    return await buscar_serie_por_tmdb_id(tmdb_id)