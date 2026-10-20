import re
import traceback
import httpx

from config import (
    USER_AGENT, IPTV_PROVIDERS, TMDB_ENABLED, PUBLIC_BASE_URL,
)
import tmdb
import logos
import proxy

# ==========================================================
# URL DO PROXY
# ==========================================================
def _url_proxy(item_id: str) -> str:
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    if base: return f"{base}/stream/id/{item_id}.mp4"
    return f"/stream/id/{item_id}.mp4"

# ==========================================================
# ENCONTRAR O PROVEDOR
# ==========================================================
def _get_provider(item_id: str):
    """Descobre a qual provedor o ID pertence baseado no prefixo."""
    for p in IPTV_PROVIDERS:
        if p["prefix"] and str(item_id).startswith(p["prefix"]):
            clean_id = str(item_id).replace(p["prefix"], "")
            return p, clean_id
    # Se não achou prefixo, é o principal
    return IPTV_PROVIDERS[0], str(item_id)

# ==========================================================
# LIMPEZA
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
    if not titulo_bruto: return ""
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
    if not titulo: return ""
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
    try: return int(v)
    except: return None

async def _buscar_tmdb_filme(info: dict, titulo: str):
    tmdb_id = _safe(info, "tmdb_id")
    if tmdb_id:
        try:
            dados = await tmdb.buscar_filme_por_tmdb_id(int(tmdb_id))
            if dados: return dados
        except: pass
    titulo_limpo = _limpar_titulo(titulo)
    ano = _safe(info, "releasedate", "releaseDate")
    if ano:
        try: ano = int(str(ano)[:4])
        except: ano = None
    dados = await tmdb.buscar_filme(titulo_limpo, ano)
    if dados: return dados
    return await tmdb.buscar_filme(titulo_limpo, None)

async def _buscar_tmdb_serie(info: dict, titulo: str):
    tmdb_id = _safe(info, "tmdb_id")
    if tmdb_id:
        try:
            dados = await tmdb.buscar_serie_por_tmdb_id(int(tmdb_id))
            if dados: return dados
        except: pass
    titulo_limpo = _limpar_titulo(titulo)
    ano = _safe(info, "releaseDate", "releasedate")
    if ano:
        try: ano = int(str(ano)[:4])
        except: ano = None
    dados = await tmdb.buscar_serie(titulo_limpo, ano)
    if dados: return dados
    return await tmdb.buscar_serie(titulo_limpo, None)

# ==========================================================
# FILME
# ==========================================================
async def fetch_movie_info(item_id: str, force: bool = False) -> dict:
    provider, clean_id = _get_provider(item_id)
    url = f"{provider['host']}/player_api.php?username={provider['user']}&password={provider['pass']}&action=get_vod_info&vod_id={clean_id}"
    
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200: raise ValueError("Provedor recusou")
        raw = r.json()

    info = raw.get("info") or {}
    movie_data = raw.get("movie_data") or {}
    titulo = _safe(info, "name", "o_name") or _safe(movie_data, "name")
    capa = _safe(info, "movie_image", "cover_big", "cover")
    banner = None
    backdrop_list = info.get("backdrop_path") or []
    if isinstance(backdrop_list, list) and backdrop_list: banner = backdrop_list[0]
    elif isinstance(backdrop_list, str): banner = backdrop_list

    trailer = _safe(info, "youtube_trailer", "trailer")
    url_stream_xtream = _safe(movie_data, "stream_url") or f"{provider['host']}/movie/{provider['user']}/{provider['pass']}/{clean_id}.mp4"

    payload = {
        "id": item_id, "tipo": "filme", "titulo": titulo, "titulo_original": _safe(info, "o_name"),
        "capa": capa, "banner": banner, "logo": None, "ano": _safe(info, "releasedate", "year"),
        "score": _safe(info, "rating"), "sinopse": _safe(info, "plot"), "generos": [],
        "trailer_url": f"https://www.youtube.com/watch?v={trailer}" if trailer else None,
        "classificacao": _safe(info, "mpaa_rating"), "url_stream": _url_proxy(item_id),
        "url_stream_direto": url_stream_xtream, "categoria": _safe(info, "genre"),
        "tmdb_id": _to_int(_safe(info, "tmdb_id")), "elenco": [], "diretores": [],
        "elenco_texto": _safe(info, "cast"), "diretor_texto": _safe(info, "director"),
    }

    proxy.register_item(item_id, url_stream_xtream, titulo, "filme")

    if not TMDB_ENABLED: return payload
    try:
        dados_tmdb = await _buscar_tmdb_filme(info, titulo or "")
        if not dados_tmdb: return payload
        
        if dados_tmdb.get("sinopse"): payload["sinopse"] = dados_tmdb["sinopse"]
        if dados_tmdb.get("banner"): payload["banner"] = dados_tmdb["banner"]
        if dados_tmdb.get("capa") and not payload["capa"]: payload["capa"] = dados_tmdb["capa"]
        if dados_tmdb.get("generos"): payload["generos"] = dados_tmdb["generos"]
        if dados_tmdb.get("classificacao"): payload["classificacao"] = dados_tmdb["classificacao"]
        if dados_tmdb.get("trailer_url"): payload["trailer_url"] = dados_tmdb["trailer_url"]
        if dados_tmdb.get("score") and not payload["score"]: payload["score"] = dados_tmdb["score"]
        if dados_tmdb.get("elenco"): payload["elenco"] = dados_tmdb["elenco"]
        if dados_tmdb.get("diretores"): payload["diretores"] = dados_tmdb["diretores"]

        if dados_tmdb.get("logo"):
            payload["logo"] = dados_tmdb["logo"]
        elif dados_tmdb.get("tmdb_id"):
            logo_fanart = await logos.buscar_logo_fanart_movie(dados_tmdb["tmdb_id"])
            if logo_fanart: payload["logo"] = logo_fanart
    except Exception as e:
        print(f"[!] TMDB falhou filme {item_id}: {e}")
    return payload

# ==========================================================
# SÉRIE
# ==========================================================
async def fetch_series_info(item_id: str, force: bool = False) -> dict:
    provider, clean_id = _get_provider(item_id)
    url = f"{provider['host']}/player_api.php?username={provider['user']}&password={provider['pass']}&action=get_series_info&series_id={clean_id}"
    
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200: raise ValueError(f"Provedor recusou (HTTP {r.status_code})")
        raw = r.json()

    if not raw or not isinstance(raw, dict): raise ValueError("Resposta inválida")

    info = raw.get("info") or {}
    seasons = raw.get("seasons") or []
    episodes_map = raw.get("episodes") or {}

    titulo_serie = _safe(info, "name") or ""
    capa = _safe(info, "cover", "movie_image")
    banner = _safe(info, "backdrop_path")
    if isinstance(banner, list) and banner: banner = banner[0]
    trailer = _safe(info, "youtube_trailer", "trailer")

    eps_normalizados = []
    if isinstance(episodes_map, dict):
        for season_key, eps_list in episodes_map.items():
            season_num = _to_int(season_key)
            if not isinstance(eps_list, list): continue
            for ep in eps_list:
                if not isinstance(ep, dict): continue
                ep_info = ep.get("info") or {}
                ep_id = _safe(ep, "id")
                ep_num = _to_int(_safe(ep, "episode_num"))
                titulo_bruto = _safe(ep_info, "name") or _safe(ep, "title") or ""
                titulo_limpo = _clean_episode_title(titulo_bruto, titulo_serie)
                if not titulo_limpo: titulo_limpo = f"Episódio {ep_num}" if ep_num else "Episódio"

                url_xtream = _safe(ep, "stream_url")
                if not url_xtream and ep_id:
                    ext = _safe(ep, "container_extension", default="mp4")
                    url_xtream = f"{provider['host']}/series/{provider['user']}/{provider['pass']}/{ep_id}.{ext}"

                # ⚡ Registra o episódio com o prefixo do provedor no ID
                full_ep_id = f"{provider['prefix']}{ep_id}" if ep_id else None
                if full_ep_id:
                    proxy.register_item(full_ep_id, url_xtream, titulo_limpo, "serie")

                eps_normalizados.append({
                    "id": full_ep_id, "temporada": season_num, "episodio": ep_num,
                    "titulo": titulo_limpo, "sinopse": _safe(ep_info, "plot"),
                    "capa": _safe(ep_info, "movie_image"), "duracao": _safe(ep_info, "duration"),
                    "score": _safe(ep_info, "rating"),
                    "url_stream": _url_proxy(full_ep_id) if full_ep_id else None,
                    "url_stream_direto": url_xtream,
                })

    eps_normalizados.sort(key=lambda e: (e["temporada"] or 0, e["episodio"] or 0))
    temporadas_unicas = sorted({e["temporada"] for e in eps_normalizados if e["temporada"]})

    payload = {
        "id": item_id, "tipo": "serie", "titulo": titulo_serie, "titulo_original": _safe(info, "original_name"),
        "capa": capa, "banner": banner, "logo": None, "ano": _safe(info, "releaseDate", "year"),
        "score": _safe(info, "rating"), "sinopse": _safe(info, "plot"), "generos": [],
        "trailer_url": f"https://www.youtube.com/watch?v={trailer}" if trailer else None,
        "classificacao": _safe(info, "mpaa_rating"), "total_temporadas": len(temporadas_unicas) or len(seasons),
        "total_episodios": len(eps_normalizados),
        "temporadas": [{"numero": _to_int(_safe(s, "season_number")), "titulo": _safe(s, "name"), "capa": _safe(s, "cover")} for s in seasons] if isinstance(seasons, list) else [],
        "episodios": eps_normalizados, "tmdb_id": _to_int(_safe(info, "tmdb_id")),
        "elenco": [], "diretores": [], "elenco_texto": _safe(info, "cast"), "diretor_texto": _safe(info, "director"),
    }

    if not TMDB_ENABLED: return payload
    try:
        dados_tmdb = await _buscar_tmdb_serie(info, titulo_serie)
        if not dados_tmdb: return payload
        
        if dados_tmdb.get("sinopse"): payload["sinopse"] = dados_tmdb["sinopse"]
        if dados_tmdb.get("banner"): payload["banner"] = dados_tmdb["banner"]
        if dados_tmdb.get("capa") and not payload["capa"]: payload["capa"] = dados_tmdb["capa"]
        if dados_tmdb.get("generos"): payload["generos"] = dados_tmdb["generos"]
        if dados_tmdb.get("classificacao"): payload["classificacao"] = dados_tmdb["classificacao"]
        if dados_tmdb.get("trailer_url"): payload["trailer_url"] = dados_tmdb["trailer_url"]
        if dados_tmdb.get("score") and not payload["score"]: payload["score"] = dados_tmdb["score"]
        if dados_tmdb.get("elenco"): payload["elenco"] = dados_tmdb["elenco"]
        if dados_tmdb.get("criadores"): payload["criadores"] = dados_tmdb["criadores"]

        if dados_tmdb.get("logo"):
            payload["logo"] = dados_tmdb["logo"]
        elif dados_tmdb.get("tvdb_id"):
            logo_fanart = await logos.buscar_logo_fanart_tv(dados_tmdb["tvdb_id"])
            if logo_fanart: payload["logo"] = logo_fanart
    except Exception as e:
        print(f"[!] TMDB falhou série {item_id}: {e}")
    return payload