import json
import time
import httpx
from pathlib import Path

from config import (
    USER_AGENT,
    INFO_CACHE_DIR,
    IPTV_HOST,
    IPTV_USERNAME,
    IPTV_PASSWORD,
    TMDB_ENABLED,
)
import tmdb

BASE_API = f"{IPTV_HOST}/player_api.php"
USERNAME = IPTV_USERNAME
PASSWORD = IPTV_PASSWORD

INFO_MOVIE_URL  = f"{BASE_API}?username={USERNAME}&password={PASSWORD}&action=get_vod_info&vod_id={{id}}"
INFO_SERIES_URL = f"{BASE_API}?username={USERNAME}&password={PASSWORD}&action=get_series_info&series_id={{id}}"

INFO_TTL = 86400


def _cache_path(kind: str, item_id: str) -> Path:
    return INFO_CACHE_DIR / f"{kind}_{item_id}.json"


def _cache_get(kind: str, item_id: str):
    p = _cache_path(kind, item_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) < INFO_TTL:
            return data.get("payload")
    except Exception:
        pass
    return None


def _cache_put(kind: str, item_id: str, payload: dict):
    p = _cache_path(kind, item_id)
    p.write_text(
        json.dumps({"_ts": time.time(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def _safe(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, "", "0"):
            return d[k]
    return default


def _to_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _quality_from_name(name: str):
    if not name:
        return None
    n = name.upper()
    for tag in ("4K", "UHD", "FHD", "FULLHD", "HD", "SD", "CAM"):
        if tag in n:
            return tag
    return None


def _build_stream_url(kind: str, item_id: str) -> str:
    return f"{IPTV_HOST}/{kind}/{USERNAME}/{PASSWORD}/{item_id}.mp4"


def _build_episode_url(series_id: str, ep: dict) -> str:
    season = ep.get("season")
    episode = ep.get("episode_num")
    ext = ep.get("container_extension", "mp4")
    return f"{IPTV_HOST}/series/{USERNAME}/{PASSWORD}/{series_id}_{season}_{episode}.{ext}"


def _preencher_com_tmdb(payload: dict, dados_tmdb: dict) -> dict:
    if not dados_tmdb:
        return payload

    preenchidos = []

    for campo_local, campo_tmdb in [
        ("ano", "ano"),
        ("data_lancamento", "release_date"),
        ("sinopse", "sinopse"),
        ("score", "score"),
        ("titulo_original", "titulo_original"),
        ("duracao_min", "duracao_min"),
    ]:
        if not payload.get(campo_local) and dados_tmdb.get(campo_tmdb):
            payload[campo_local] = dados_tmdb[campo_tmdb]
            preenchidos.append(campo_local)

    if dados_tmdb.get("capa"):
        payload["capa_tmdb"] = dados_tmdb["capa"]
        payload["capa_grande"] = dados_tmdb.get("capa_grande")
        if not payload.get("capa"):
            payload["capa"] = dados_tmdb["capa"]
            preenchidos.append("capa")

    if dados_tmdb.get("banner"):
        payload["banner"] = dados_tmdb["banner"]
        payload["banner_4k"] = dados_tmdb.get("banner_4k")
        preenchidos.append("banner")

    if dados_tmdb.get("logo"):
        payload["logo"] = dados_tmdb["logo"]
        payload["logo_original"] = dados_tmdb.get("logo_original")

    if dados_tmdb.get("trailer"):
        payload["trailer"] = dados_tmdb["trailer"]
        payload["trailer_url"] = dados_tmdb["trailer_url"]
        payload["trailer_nome"] = dados_tmdb.get("trailer_nome")

    if dados_tmdb.get("classificacao"):
        payload["classificacao"] = dados_tmdb["classificacao"]

    payload["generos"] = dados_tmdb.get("generos") or []
    payload["tagline"] = dados_tmdb.get("tagline")
    payload["tmdb_id"] = dados_tmdb.get("tmdb_id")
    payload["tmdb_recente"] = dados_tmdb.get("recente", False)
    payload["campos_preenchidos_por_tmdb"] = preenchidos

    return payload


def _normalize_movie(raw: dict, item_id: str) -> dict:
    info = raw.get("info") or {}
    movie_data = raw.get("movie_data") or {}

    titulo = _safe(info, "name", "o_name") or _safe(movie_data, "name")
    capa = _safe(info, "movie_image", "cover_big", "cover")
    banner = _safe(info, "backdrop_path", "movie_image")
    backdrop_list = info.get("backdrop_path") or []
    if isinstance(backdrop_list, list) and backdrop_list:
        banner = backdrop_list[0]

    trailer = _safe(info, "youtube_trailer", "trailer")

    duracao = _safe(info, "duration", "duration_secs")
    if duracao and str(duracao).endswith("s"):
        try:
            duracao = int(str(duracao).rstrip("s"))
        except Exception:
            pass

    return {
        "id": item_id,
        "tipo": "filme",
        "titulo": titulo,
        "titulo_original": _safe(info, "o_name"),
        "capa": capa,
        "banner": banner,
        "ano": _safe(info, "releasedate", "releaseDate", "year"),
        "data_lancamento": _safe(info, "releasedate", "releaseDate"),
        "duracao": duracao,
        "score": _safe(info, "rating", "rating_5based"),
        "sinopse": _safe(info, "plot", "description"),
        "genero": _safe(info, "genre"),
        "elenco": _safe(info, "cast", "actors"),
        "diretor": _safe(info, "director"),
        "pais": _safe(info, "country"),
        "trailer": trailer,
        "trailer_url": f"https://www.youtube.com/watch?v={trailer}" if trailer else None,
        "qualidade": _quality_from_name(titulo or "") or _safe(info, "quality"),
        "container": _safe(movie_data, "container_extension"),
        "url_stream": _safe(movie_data, "stream_url") or _build_stream_url("movie", item_id),
        "categoria": _safe(info, "category", "genre"),
        "raw": raw,
    }


def _normalize_series(raw: dict, item_id: str) -> dict:
    info = raw.get("info") or {}
    seasons = raw.get("seasons") or []
    episodes_map = raw.get("episodes") or {}

    titulo = _safe(info, "name")
    capa = _safe(info, "cover", "movie_image")
    banner = _safe(info, "backdrop_path")
    if isinstance(banner, list) and banner:
        banner = banner[0]

    trailer = _safe(info, "youtube_trailer", "trailer")

    eps_normalizados = []
    for season_key, eps in (episodes_map or {}).items():
        for ep in eps:
            ep_info = ep.get("info") or {}
            eps_normalizados.append({
                "id": _safe(ep, "id"),
                "temporada": _to_int(_safe(ep, "season")),
                "episodio": _to_int(_safe(ep, "episode_num")),
                "titulo": _safe(ep_info, "name") or _safe(ep, "title"),
                "sinopse": _safe(ep_info, "plot"),
                "capa": _safe(ep_info, "movie_image"),
                "duracao": _safe(ep_info, "duration"),
                "score": _safe(ep_info, "rating"),
                "url_stream": _safe(ep, "stream_url") or _build_episode_url(item_id, ep),
            })

    eps_normalizados.sort(key=lambda e: (e["temporada"] or 0, e["episodio"] or 0))

    return {
        "id": item_id,
        "tipo": "serie",
        "titulo": titulo,
        "titulo_original": _safe(info, "original_name"),
        "capa": capa,
        "banner": banner,
        "ano": _safe(info, "releaseDate", "releasedate", "year"),
        "data_lancamento": _safe(info, "releaseDate", "releasedate"),
        "score": _safe(info, "rating", "rating_5based"),
        "sinopse": _safe(info, "plot", "description"),
        "genero": _safe(info, "genre"),
        "elenco": _safe(info, "cast", "actors"),
        "diretor": _safe(info, "director"),
        "pais": _safe(info, "country"),
        "trailer": trailer,
        "trailer_url": f"https://www.youtube.com/watch?v={trailer}" if trailer else None,
        "total_temporadas": len(seasons) or len({e["temporada"] for e in eps_normalizados}),
        "total_episodios": len(eps_normalizados),
        "temporadas": [
            {
                "numero": _to_int(_safe(s, "season_number")),
                "titulo": _safe(s, "name"),
                "capa": _safe(s, "cover"),
                "sinopse": _safe(s, "overview"),
            }
            for s in seasons
        ],
        "episodios": eps_normalizados,
        "raw": raw,
    }


async def fetch_movie_info(item_id: str, force: bool = False) -> dict:
    if not force:
        cached = _cache_get("movie", item_id)
        if cached:
            return cached

    url = INFO_MOVIE_URL.format(id=item_id)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        raw = r.json()

    if not raw or raw.get("info") is None:
        raise ValueError("Provedor não retornou informações para esse ID")

    normalized = _normalize_movie(raw, item_id)

    if TMDB_ENABLED:
        try:
            dados_tmdb = await tmdb.buscar_filme(
                normalized.get("titulo") or "",
                normalized.get("ano"),
            )
            if dados_tmdb:
                normalized = _preencher_com_tmdb(normalized, dados_tmdb)
        except Exception as e:
            print(f"[!] TMDB falhou pra filme {item_id}: {e}")

    _cache_put("movie", item_id, normalized)
    return normalized


async def fetch_series_info(item_id: str, force: bool = False) -> dict:
    if not force:
        cached = _cache_get("series", item_id)
        if cached:
            return cached

    url = INFO_SERIES_URL.format(id=item_id)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        raw = r.json()

    if not raw or raw.get("info") is None:
        raise ValueError("Provedor não retornou informações para esse ID")

    normalized = _normalize_series(raw, item_id)

    if TMDB_ENABLED:
        try:
            dados_tmdb = await tmdb.buscar_serie(
                normalized.get("titulo") or "",
                normalized.get("ano"),
            )
            if dados_tmdb:
                normalized = _preencher_com_tmdb(normalized, dados_tmdb)
        except Exception as e:
            print(f"[!] TMDB falhou pra série {item_id}: {e}")

    _cache_put("series", item_id, normalized)
    return normalized