import re
import traceback
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


# ==========================================================
# LIMPEZA DE TÍTULOS DE EPISÓDIO
# ==========================================================
_EPISODE_PATTERNS = [
    re.compile(r'\s*[-–—]\s*S\d+\s*[E\xD7x]\s*\d+.*$', re.I),
    re.compile(r'\s*S\d+\s*[E\xD7x]\s*\d+\s*[-–—]?\s*', re.I),
    re.compile(r'\s*[Tt]emporada\s*\d+\s*[Ee]pis[oó]dio\s*\d+.*$', re.I),
    re.compile(r'\s*[Tt]\d+\s*[Ee]\d+.*$', re.I),
    re.compile(r'\s*[Ee]pis[oó]dio\s*\d+\s*[-–—]?\s*$', re.I),
    re.compile(r'^\s*[Ee]pis[oó]dio\s*\d+\s*[-–—]?\s*', re.I),
]


def _clean_episode_title(titulo_bruto: str, titulo_serie: str = "") -> str:
    if not titulo_bruto:
        return ""
    t = titulo_bruto.strip()
    if titulo_serie:
        serie_esc = re.escape(titulo_serie.strip())
        t = re.sub(rf'^\s*{serie_esc}\s*[-–—:]?\s*', '', t, flags=re.I)
    t = re.sub(r'#\S+', ' ', t)
    t = re.sub(r'\s*(HD|SD|FHD|4K|UHD|DUB|LEG)\s*$', '', t, flags=re.I)
    m = re.search(r'S(\d+)\s*[E\xD7x]\s*(\d+)\s*[-–—]?\s*(.+)?$', t, re.I)
    if m:
        depois = (m.group(3) or "").strip(" -–—:|")
        if depois and not re.match(r'^epis[oó]dio\s*\d+$', depois, re.I):
            return depois.strip()
        return f"Episódio {int(m.group(2))}"
    for pat in _EPISODE_PATTERNS:
        t = pat.sub(" ", t)
    t = re.sub(r'\s+', ' ', t).strip(" -–—:.,;|")
    return t


def _limpar_titulo(titulo: str) -> str:
    if not titulo:
        return ""
    t = titulo
    t = re.sub(r'[\(\[]\s*(19|20)\d{2}\s*[\)\]]', ' ', t)
    t = re.sub(r'#\S+', ' ', t)
    t = re.sub(r'\s*(HD|SD|FHD|4K|UHD|DUB|LEG)\s*$', ' ', t, flags=re.I)
    t = re.sub(r'\(\s*\)', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip(" -–—_.,;:()[]{}")
    return t


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


def _build_episode_url(series_id: str, season, ep: dict) -> str:
    episode = ep.get("episode_num")
    ext = ep.get("container_extension", "mp4")
    return f"{IPTV_HOST}/series/{USERNAME}/{PASSWORD}/{series_id}_{season}_{episode}.{ext}"


async def _buscar_tmdb_filme(info: dict, titulo: str):
    tmdb_id = _safe(info, "tmdb_id")
    if tmdb_id:
        try:
            tmdb_id_int = int(tmdb_id)
            print(f"[TMDB] Filme por ID: {tmdb_id_int}")
            dados = await tmdb.buscar_filme_por_tmdb_id(tmdb_id_int)
            if dados:
                print(f"[TMDB] ✅ Achou por ID")
                return dados
        except Exception:
            pass

    titulo_limpo = _limpar_titulo(titulo)
    ano = _safe(info, "releasedate", "releaseDate")
    if ano:
        try:
            ano = int(str(ano)[:4])
        except Exception:
            ano = None

    print(f"[TMDB] Filme por título: '{titulo_limpo}' ({ano})")
    dados = await tmdb.buscar_filme(titulo_limpo, ano)
    if dados:
        print(f"[TMDB] ✅ Achou por título + ano")
        return dados

    dados = await tmdb.buscar_filme(titulo_limpo, None)
    if dados:
        print(f"[TMDB] ✅ Achou sem ano")
        return dados

    print(f"[TMDB] ❌ Nada encontrado")
    return None


async def _buscar_tmdb_serie(info: dict, titulo: str):
    tmdb_id = _safe(info, "tmdb_id")
    if tmdb_id:
        try:
            tmdb_id_int = int(tmdb_id)
            print(f"[TMDB] Série por ID: {tmdb_id_int}")
            dados = await tmdb.buscar_serie_por_tmdb_id(tmdb_id_int)
            if dados:
                print(f"[TMDB] ✅ Achou por ID")
                return dados
        except Exception:
            pass

    titulo_limpo = _limpar_titulo(titulo)
    ano = _safe(info, "releaseDate", "releasedate")
    if ano:
        try:
            ano = int(str(ano)[:4])
        except Exception:
            ano = None

    print(f"[TMDB] Série por título: '{titulo_limpo}' ({ano})")
    dados = await tmdb.buscar_serie(titulo_limpo, ano)
    if dados:
        print(f"[TMDB] ✅ Achou por título + ano")
        return dados

    dados = await tmdb.buscar_serie(titulo_limpo, None)
    if dados:
        print(f"[TMDB] ✅ Achou sem ano")
        return dados

    print(f"[TMDB] ❌ Nada encontrado")
    return None


# ==========================================================
# FILME — SEM CACHE
# ==========================================================
async def fetch_movie_info(item_id: str, force: bool = False) -> dict:
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
        "tmdb_id": _to_int(_safe(info, "tmdb_id")),
        "tmdb_recente": False,
        "campos_preenchidos_por_tmdb": [],
    }

    if not TMDB_ENABLED:
        return payload

    try:
        dados_tmdb = await _buscar_tmdb_filme(info, titulo or "")
        if not dados_tmdb:
            return payload

        preenchidos = []

        if dados_tmdb.get("sinopse"):
            payload["sinopse"] = dados_tmdb["sinopse"]
            preenchidos.append("sinopse")
        if dados_tmdb.get("banner"):
            payload["banner"] = dados_tmdb["banner"]
            payload["banner_4k"] = dados_tmdb.get("banner_4k")
            preenchidos.append("banner")
        if dados_tmdb.get("capa") and not payload["capa"]:
            payload["capa"] = dados_tmdb["capa"]
            preenchidos.append("capa")
        if dados_tmdb.get("generos"):
            payload["generos"] = dados_tmdb["generos"]
            preenchidos.append("generos")
        if dados_tmdb.get("classificacao"):
            payload["classificacao"] = dados_tmdb["classificacao"]
            preenchidos.append("classificacao")
        if dados_tmdb.get("trailer_url"):
            payload["trailer_url"] = dados_tmdb["trailer_url"]
            preenchidos.append("trailer")
        if dados_tmdb.get("score") and not payload["score"]:
            payload["score"] = dados_tmdb["score"]
            preenchidos.append("score")
        if dados_tmdb.get("tagline"):
            payload["tagline"] = dados_tmdb["tagline"]
            preenchidos.append("tagline")

        payload["tmdb_id"] = dados_tmdb.get("tmdb_id")
        payload["tmdb_recente"] = dados_tmdb.get("recente", False)

        if dados_tmdb.get("logo"):
            payload["logo"] = dados_tmdb["logo"]
            payload["logo_original"] = dados_tmdb.get("logo_original")
            payload["logo_fonte"] = "tmdb"
            preenchidos.append("logo")
        elif dados_tmdb.get("tmdb_id"):
            logo_fanart = await logos.buscar_logo_fanart_movie(dados_tmdb["tmdb_id"])
            if logo_fanart:
                payload["logo"] = logo_fanart
                payload["logo_fonte"] = "fanart"
                preenchidos.append("logo")

        payload["campos_preenchidos_por_tmdb"] = preenchidos
        print(f"[TMDB] Preencheu filme: {preenchidos}")

    except Exception as e:
        print(f"[!] TMDB falhou filme {item_id}: {e}")
        traceback.print_exc()

    return payload


# ==========================================================
# SÉRIE — SEM CACHE
# ==========================================================
async def fetch_series_info(item_id: str, force: bool = False) -> dict:
    url = INFO_SERIES_URL.format(id=item_id)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            raise ValueError(f"Provedor recusou (HTTP {r.status_code})")
        raw = r.json()

    if not raw or not isinstance(raw, dict):
        raise ValueError("Resposta inválida do provedor")

    info = raw.get("info") or {}
    seasons = raw.get("seasons") or []
    episodes_map = raw.get("episodes") or {}

    titulo_serie = _safe(info, "name") or ""
    capa = _safe(info, "cover", "movie_image")
    banner = _safe(info, "backdrop_path")
    if isinstance(banner, list) and banner:
        banner = banner[0]

    trailer = _safe(info, "youtube_trailer", "trailer")

    eps_normalizados = []
    if isinstance(episodes_map, dict):
        for season_key, eps_list in episodes_map.items():
            season_num = _to_int(season_key)
            if not isinstance(eps_list, list):
                continue
            for ep in eps_list:
                if not isinstance(ep, dict):
                    continue
                ep_info = ep.get("info") or {}
                ep_id = _safe(ep, "id")
                ep_num = _to_int(_safe(ep, "episode_num"))
                titulo_bruto = _safe(ep_info, "name") or _safe(ep, "title") or ""
                titulo_limpo = _clean_episode_title(titulo_bruto, titulo_serie)
                if not titulo_limpo:
                    titulo_limpo = f"Episódio {ep_num}" if ep_num else "Episódio"

                url_stream = _safe(ep, "stream_url")
                if not url_stream:
                    try:
                        url_stream = _build_episode_url(item_id, season_num, ep)
                    except Exception:
                        url_stream = None

                eps_normalizados.append({
                    "id": str(ep_id) if ep_id else None,
                    "temporada": season_num,
                    "episodio": ep_num,
                    "titulo": titulo_limpo,
                    "sinopse": _safe(ep_info, "plot"),
                    "capa": _safe(ep_info, "movie_image"),
                    "duracao": _safe(ep_info, "duration"),
                    "score": _safe(ep_info, "rating"),
                    "url_stream": url_stream,
                })

    eps_normalizados.sort(key=lambda e: (e["temporada"] or 0, e["episodio"] or 0))
    temporadas_unicas = sorted({e["temporada"] for e in eps_normalizados if e["temporada"]})

    payload = {
        "id": item_id,
        "tipo": "serie",
        "titulo": titulo_serie,
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
        "total_temporadas": len(temporadas_unicas) or len(seasons),
        "total_episodios": len(eps_normalizados),
        "temporadas": [
            {
                "numero": _to_int(_safe(s, "season_number")),
                "titulo": _safe(s, "name") or f"Temporada {_safe(s, 'season_number')}",
                "capa": _safe(s, "cover"),
                "sinopse": _safe(s, "overview"),
            }
            for s in seasons
        ] if isinstance(seasons, list) else [],
        "episodios": eps_normalizados,
        "tmdb_id": _to_int(_safe(info, "tmdb_id")),
        "tmdb_recente": False,
        "campos_preenchidos_por_tmdb": [],
    }

    if not TMDB_ENABLED:
        return payload

    try:
        dados_tmdb = await _buscar_tmdb_serie(info, titulo_serie)
        if not dados_tmdb:
            return payload

        preenchidos = []

        if dados_tmdb.get("sinopse"):
            payload["sinopse"] = dados_tmdb["sinopse"]
            preenchidos.append("sinopse")
        if dados_tmdb.get("banner"):
            payload["banner"] = dados_tmdb["banner"]
            payload["banner_4k"] = dados_tmdb.get("banner_4k")
            preenchidos.append("banner")
        if dados_tmdb.get("capa") and not payload["capa"]:
            payload["capa"] = dados_tmdb["capa"]
            preenchidos.append("capa")
        if dados_tmdb.get("generos"):
            payload["generos"] = dados_tmdb["generos"]
            preenchidos.append("generos")
        if dados_tmdb.get("classificacao"):
            payload["classificacao"] = dados_tmdb["classificacao"]
            preenchidos.append("classificacao")
        if dados_tmdb.get("trailer_url"):
            payload["trailer_url"] = dados_tmdb["trailer_url"]
            preenchidos.append("trailer")
        if dados_tmdb.get("score") and not payload["score"]:
            payload["score"] = dados_tmdb["score"]
            preenchidos.append("score")
        if dados_tmdb.get("tagline"):
            payload["tagline"] = dados_tmdb["tagline"]
            preenchidos.append("tagline")

        payload["tmdb_id"] = dados_tmdb.get("tmdb_id")
        payload["tmdb_recente"] = dados_tmdb.get("recente", False)

        if dados_tmdb.get("logo"):
            payload["logo"] = dados_tmdb["logo"]
            payload["logo_original"] = dados_tmdb.get("logo_original")
            payload["logo_fonte"] = "tmdb"
            preenchidos.append("logo")
        elif dados_tmdb.get("tvdb_id"):
            logo_fanart = await logos.buscar_logo_fanart_tv(dados_tmdb["tvdb_id"])
            if logo_fanart:
                payload["logo"] = logo_fanart
                payload["logo_fonte"] = "fanart"
                preenchidos.append("logo")

        payload["campos_preenchidos_por_tmdb"] = preenchidos
        print(f"[TMDB] Preencheu série: {preenchidos}")

    except Exception as e:
        print(f"[!] TMDB falhou série {item_id}: {e}")
        traceback.print_exc()

    return payload