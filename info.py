import httpx

from config import (
    USER_AGENT,
    IPTV_HOST,
    IPTV_USERNAME,
    IPTV_PASSWORD,
    TMDB_ENABLED,
)
import tmdb
import logos

BASE_API = f"{IPTV_HOST}/player_api.php"
USERNAME = IPTV_USERNAME
PASSWORD = IPTV_PASSWORD

INFO_MOVIE_URL  = f"{BASE_API}?username={USERNAME}&password={PASSWORD}&action=get_vod_info&vod_id={{id}}"
INFO_SERIES_URL = f"{BASE_API}?username={USERNAME}&password={PASSWORD}&action=get_series_info&series_id={{id}}"


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


def _build_stream_url(kind: str, item_id: str) -> str:
    return f"{IPTV_HOST}/{kind}/{USERNAME}/{PASSWORD}/{item_id}.mp4"


def _build_episode_url(series_id: str, ep: dict) -> str:
    season = ep.get("season")
    episode = ep.get("episode_num")
    ext = ep.get("container_extension", "mp4")
    return f"{IPTV_HOST}/series/{USERNAME}/{PASSWORD}/{series_id}_{season}_{episode}.{ext}"


# ==========================================================
# FILME — pesquisa em tempo real
# ==========================================================
async def fetch_movie_info(item_id: str, force: bool = False) -> dict:
    # Consulta Xtream
    url = INFO_MOVIE_URL.format(id=item_id)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            raise ValueError("Provedor recusou")
        raw = r.json()

    info = raw.get("info") or {}
    movie_data = raw.get("movie_data") or {}

    titulo = _safe(info, "name", "o_name") or _safe(movie_data, "name")
    capa = _safe(info, "movie_image", "cover_big", "cover")

    banner = None
    backdrop_list = info.get("backdrop_path") or []
    if isinstance(backdrop_list, list) and backdrop_list:
        banner = backdrop_list[0]
    elif isinstance(backdrop_list, str):
        banner = backdrop_list

    trailer = _safe(info, "youtube_trailer", "trailer")

    payload = {
        "id": item_id,
        "tipo": "filme",
        "titulo": titulo,
        "titulo_original": _safe(info, "o_name"),
        "tagline": None,
        "capa": capa,
        "capa_grande": None,
        "banner": banner,
        "banner_4k": None,
        "logo": None,
        "logo_original": None,
        "ano": _safe(info, "releasedate", "releaseDate", "year"),
        "data_lancamento": _safe(info, "releasedate", "releaseDate"),
        "duracao": _safe(info, "duration"),
        "duracao_min": _safe(info, "episode_run_time"),
        "score": _safe(info, "rating", "rating_5based"),
        "sinopse": _safe(info, "plot", "description"),
        "generos": [],
        "genero": _safe(info, "genre"),
        "elenco": _safe(info, "cast", "actors"),
        "diretor": _safe(info, "director"),
        "pais": _safe(info, "country"),
        "trailer": trailer,
        "trailer_url": f"https://www.youtube.com/watch?v={trailer}" if trailer else None,
        "trailer_nome": None,
        "classificacao": _safe(info, "mpaa_rating", "age"),
        "container": _safe(movie_data, "container_extension"),
        "url_stream": _safe(movie_data, "stream_url") or _build_stream_url("movie", item_id),
        "categoria": _safe(info, "category", "genre"),
        "tmdb_id": None,
        "tmdb_recente": False,
        "campos_preenchidos_por_tmdb": [],
    }

    # Enriquece com TMDB em tempo real (sem cache)
    if TMDB_ENABLED:
        try:
            dados_tmdb = await tmdb.buscar_filme(titulo or "", payload.get("ano"))
            if dados_tmdb:
                if dados_tmdb.get("sinopse"):
                    payload["sinopse"] = dados_tmdb["sinopse"]
                if dados_tmdb.get("banner"):
                    payload["banner"] = dados_tmdb["banner"]
                    payload["banner_4k"] = dados_tmdb.get("banner_4k")
                if dados_tmdb.get("capa") and not payload["capa"]:
                    payload["capa"] = dados_tmdb["capa"]
                if dados_tmdb.get("generos"):
                    payload["generos"] = dados_tmdb["generos"]
                # Sempre sobrescreve classificação com o valor normalizado
                if dados_tmdb.get("classificacao"):
                    payload["classificacao"] = dados_tmdb["classificacao"]
                if dados_tmdb.get("trailer_url"):
                    payload["trailer_url"] = dados_tmdb["trailer_url"]
                if dados_tmdb.get("score") and not payload["score"]:
                    payload["score"] = dados_tmdb["score"]
                payload["tmdb_id"] = dados_tmdb.get("tmdb_id")

                # Logo TMDB ou Fanart
                if dados_tmdb.get("logo"):
                    payload["logo"] = dados_tmdb["logo"]
                    payload["logo_fonte"] = "tmdb"
                elif dados_tmdb.get("tmdb_id"):
                    logo_fanart = await logos.buscar_logo_fanart_movie(dados_tmdb["tmdb_id"])
                    if logo_fanart:
                        payload["logo"] = logo_fanart
                        payload["logo_fonte"] = "fanart"
        except Exception as e:
            print(f"[!] TMDB falhou filme {item_id}: {e}")

    return payload


# ==========================================================
# SÉRIE — pesquisa em tempo real
# ==========================================================
async def fetch_series_info(item_id: str, force: bool = False) -> dict:
    url = INFO_SERIES_URL.format(id=item_id)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            raise ValueError("Provedor recusou")
        raw = r.json()

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

    payload = {
        "id": item_id,
        "tipo": "serie",
        "titulo": titulo,
        "titulo_original": _safe(info, "original_name"),
        "tagline": None,
        "capa": capa,
        "capa_grande": None,
        "banner": banner,
        "banner_4k": None,
        "logo": None,
        "logo_original": None,
        "ano": _safe(info, "releaseDate", "releasedate", "year"),
        "data_lancamento": _safe(info, "releaseDate", "releasedate"),
        "score": _safe(info, "rating", "rating_5based"),
        "sinopse": _safe(info, "plot", "description"),
        "generos": [],
        "genero": _safe(info, "genre"),
        "elenco": _safe(info, "cast", "actors"),
        "diretor": _safe(info, "director"),
        "pais": _safe(info, "country"),
        "trailer": trailer,
        "trailer_url": f"https://www.youtube.com/watch?v={trailer}" if trailer else None,
        "trailer_nome": None,
        "classificacao": _safe(info, "mpaa_rating", "age"),
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
        "tmdb_id": None,
        "tmdb_recente": False,
        "campos_preenchidos_por_tmdb": [],
    }

    if TMDB_ENABLED:
        try:
            dados_tmdb = await tmdb.buscar_serie(titulo or "", payload.get("ano"))
            if dados_tmdb:
                if dados_tmdb.get("sinopse"):
                    payload["sinopse"] = dados_tmdb["sinopse"]
                if dados_tmdb.get("banner"):
                    payload["banner"] = dados_tmdb["banner"]
                    payload["banner_4k"] = dados_tmdb.get("banner_4k")
                if dados_tmdb.get("capa") and not payload["capa"]:
                    payload["capa"] = dados_tmdb["capa"]
                if dados_tmdb.get("generos"):
                    payload["generos"] = dados_tmdb["generos"]
                if dados_tmdb.get("classificacao"):
                    payload["classificacao"] = dados_tmdb["classificacao"]
                if dados_tmdb.get("trailer_url"):
                    payload["trailer_url"] = dados_tmdb["trailer_url"]
                if dados_tmdb.get("score") and not payload["score"]:
                    payload["score"] = dados_tmdb["score"]
                payload["tmdb_id"] = dados_tmdb.get("tmdb_id")

                if dados_tmdb.get("logo"):
                    payload["logo"] = dados_tmdb["logo"]
                    payload["logo_fonte"] = "tmdb"
                elif dados_tmdb.get("tvdb_id"):
                    logo_fanart = await logos.buscar_logo_fanart_tv(dados_tmdb["tvdb_id"])
                    if logo_fanart:
                        payload["logo"] = logo_fanart
                        payload["logo_fonte"] = "fanart"
        except Exception as e:
            print(f"[!] TMDB falhou série {item_id}: {e}")

    return payload