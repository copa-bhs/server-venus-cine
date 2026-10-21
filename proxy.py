import asyncio, json, re, unicodedata
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from config import (
    USER_AGENT, SLUG_MAP_FILMES_FILE, SLUG_MAP_SERIES_FILE, SLUG_MAP_CANAIS_FILE,
    PROXY_ENABLED, PROXY_MAX_CONCURRENT, PROXY_MAX_PER_IP,
    PROXY_CHUNK_SIZE, PROXY_CONNECT_TIMEOUT, PROXY_READ_TIMEOUT,
)

_active_total: int = 0
_active_by_ip: dict = {}

def stats() -> dict:
    return {"ativo_total": _active_total, "limite_total": PROXY_MAX_CONCURRENT, "limite_por_ip": PROXY_MAX_PER_IP, "ips_conectados": len(_active_by_ip), "por_ip": dict(_active_by_ip)}

def slugify(texto: str, ano=None) -> str:
    if not texto: return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^\w\s-]", "", texto.lower())
    texto = re.sub(r"[-\s]+", "-", texto).strip("-")
    texto = re.sub(r"^(filme|serie|série|movie|tv|vod)[-\s]+", "", texto)
    if ano:
        try: texto = f"{texto}-{int(ano)}"
        except Exception: pass
    return texto or ""

_SLUG_MAP: dict = {}
_ID_MAP: dict   = {}
_SLUG_MAP_FILMES: dict = {}
_SLUG_MAP_SERIES: dict = {}
_SLUG_MAP_CANAIS: dict = {}

def limpar_tudo():
    global _SLUG_MAP, _ID_MAP, _SLUG_MAP_FILMES, _SLUG_MAP_SERIES, _SLUG_MAP_CANAIS
    _SLUG_MAP.clear(); _ID_MAP.clear(); _SLUG_MAP_FILMES.clear(); _SLUG_MAP_SERIES.clear(); _SLUG_MAP_CANAIS.clear()

def build_slug_map(index: dict) -> dict:
    global _SLUG_MAP, _ID_MAP, _SLUG_MAP_FILMES, _SLUG_MAP_SERIES, _SLUG_MAP_CANAIS
    smap_filmes, smap_series, smap_canais, imap = {}, {}, {}, {}

    def _add(target: dict, slug: str, dados: dict):
        if not slug: return
        base = slug; i = 2
        while (slug in smap_filmes or slug in smap_series or slug in smap_canais):
            slug = f"{base}-{i}"; i += 1
        target[slug] = dados; imap[str(dados.get("id"))] = dados

    for f in index.get("filmes", []):
        if not f.get("url"): continue
        slug = slugify(f.get("titulo") or "", f.get("ano"))
        _add(smap_filmes, slug, {"id": f.get("id"), "url": f.get("url"), "titulo": f.get("titulo"), "tipo": "filme", "slug": slug})

    for s in index.get("series", []):
        titulo_base = slugify(s.get("titulo") or "")
        if not titulo_base: continue
        for ep in s.get("episodios", []):
            t, e = ep.get("temporada"), ep.get("episodio")
            if not ep.get("url") or not t or not e: continue
            try: t_int, e_int = int(t), int(e)
            except: continue
            slug = f"{titulo_base}-t{t_int:02d}-e{e_int:02d}"
            _add(smap_series, slug, {"id": ep.get("id"), "url": ep.get("url"), "titulo": f"{s.get('titulo')} T{t_int:02d}E{e_int:02d}", "tipo": "serie", "slug": slug})
        _add(smap_series, titulo_base, {"id": s.get("id"), "url": s.get("url") or "", "titulo": s.get("titulo"), "tipo": "serie", "slug": titulo_base})

    for c in index.get("canais", []):
        if not c.get("url"): continue
        slug = slugify(c.get("titulo") or "")
        _add(smap_canais, slug, {"id": c.get("id"), "url": c.get("url"), "titulo": c.get("titulo"), "tipo": "canal", "slug": slug})

    _SLUG_MAP.clear(); _SLUG_MAP.update(smap_filmes); _SLUG_MAP.update(smap_series); _SLUG_MAP.update(smap_canais)
    _ID_MAP.clear(); _ID_MAP.update(imap)
    _SLUG_MAP_FILMES.clear(); _SLUG_MAP_FILMES.update(smap_filmes)
    _SLUG_MAP_SERIES.clear(); _SLUG_MAP_SERIES.update(smap_series)
    _SLUG_MAP_CANAIS.clear(); _SLUG_MAP_CANAIS.update(smap_canais)

    def _salvar(path, dados):
        try: path.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        except Exception: pass

    _salvar(SLUG_MAP_FILMES_FILE, smap_filmes); _salvar(SLUG_MAP_SERIES_FILE, smap_series); _salvar(SLUG_MAP_CANAIS_FILE, smap_canais)
    return _SLUG_MAP

def get_by_slug(slug: str) -> dict | None: return _SLUG_MAP.get(slug)
def get_by_id(item_id: str) -> dict | None: return _ID_MAP.get(str(item_id))
def total_slugs() -> int: return len(_SLUG_MAP)

def register_item(item_id: str, url: str, titulo: str, tipo: str, slug: str = None):
    global _ID_MAP, _SLUG_MAP
    if not item_id or not url: return
    dados = {"id": str(item_id), "url": url, "titulo": titulo, "tipo": tipo, "slug": slug or slugify(titulo)}
    _ID_MAP[str(item_id)] = dados
    if slug: _SLUG_MAP[slug] = dados

async def stream_proxy(url: str, request: Request) -> StreamingResponse:
    global _active_total
    if not PROXY_ENABLED: raise HTTPException(404, "Proxy desativado.")
    if not url or not url.strip(): raise HTTPException(404, "Item sem URL.")

    # ⚡ 1. Tratamento de OPTIONS (Preflight CORS) para o Canvas
    if request.method == "OPTIONS":
        return StreamingResponse(
            iter([]), 
            status_code=200, 
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "3600",
            }
        )

    client_ip = request.client.host if request.client else "unknown"
    if _active_total >= PROXY_MAX_CONCURRENT: raise HTTPException(503, "Servidor no limite.")
    if _active_by_ip.get(client_ip, 0) >= PROXY_MAX_PER_IP: raise HTTPException(429, "Limite por IP.")

    headers = {"User-Agent": USER_AGENT}
    if "range" in request.headers: headers["Range"] = request.headers["range"]
    if "if-modified-since" in request.headers: headers["If-Modified-Since"] = request.headers["if-modified-since"]

    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=PROXY_CONNECT_TIMEOUT, read=PROXY_READ_TIMEOUT, write=PROXY_READ_TIMEOUT, pool=None), limits=httpx.Limits(max_connections=PROXY_MAX_CONCURRENT + 50, max_keepalive_connections=PROXY_MAX_CONCURRENT), follow_redirects=True)

    try: upstream = await client.send(client.build_request("GET", url, headers=headers), stream=True)
    except httpx.RequestError as e:
        await client.aclose(); raise HTTPException(502, f"Erro: {e}")

    if upstream.status_code >= 400:
        status = upstream.status_code
        await upstream.aclose(); await client.aclose()
        raise HTTPException(status, "Provedor recusou.")

    _active_total += 1
    _active_by_ip[client_ip] = _active_by_ip.get(client_ip, 0) + 1

    async def _streamer():
        global _active_total
        try:
            async for chunk in upstream.aiter_bytes(PROXY_CHUNK_SIZE): yield chunk
        except: pass
        finally:
            _active_total = max(0, _active_total - 1)
            if client_ip in _active_by_ip:
                _active_by_ip[client_ip] -= 1
                if _active_by_ip[client_ip] <= 0: del _active_by_ip[client_ip]
            try: await upstream.aclose()
            except: pass
            try: await client.aclose()
            except: pass

    resp_headers = {}
    for h in ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag"):
        if h in upstream.headers: resp_headers[h] = upstream.headers[h]
    
    # ⚡ 2. Headers de CORS liberados para o Canvas (Ambient Light)
    resp_headers["accept-ranges"] = "bytes"
    resp_headers["cache-control"] = "no-store"
    resp_headers["Access-Control-Allow-Origin"] = "*"
    resp_headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp_headers["Access-Control-Allow-Headers"] = "*"
    resp_headers["Access-Control-Expose-Headers"] = "*" 
    resp_headers["Cross-Origin-Resource-Policy"] = "cross-origin" # Permite ao Chrome ler os pixels do vídeo

    media_type = upstream.headers.get("content-type", "video/mp4")
    return StreamingResponse(_streamer(), status_code=upstream.status_code, headers=resp_headers, media_type=media_type)