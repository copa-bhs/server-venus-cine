"""
Proxy reverso de streaming para o provedor IPTV.
- Esconde credenciais do provedor
- Suporta Range requests (seek em vídeo)
- Limita streams simultâneos (global + por IP)
- Streaming bidirecional chunk-a-chunk (não carrega vídeo em memória)
- Slug maps separados por tipo (filmes / series / canais)
"""
import asyncio
import json
import re
import unicodedata

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from config import (
    USER_AGENT,
    SLUG_MAP_FILMES_FILE, SLUG_MAP_SERIES_FILE, SLUG_MAP_CANAIS_FILE,
    PROXY_ENABLED, PROXY_MAX_CONCURRENT, PROXY_MAX_PER_IP,
    PROXY_CHUNK_SIZE, PROXY_CONNECT_TIMEOUT, PROXY_READ_TIMEOUT,
)


# ==========================================================
# CONTADORES DE STREAM ATIVO
# ==========================================================
_active_total: int = 0
_active_by_ip: dict = {}


def stats() -> dict:
    return {
        "ativo_total": _active_total,
        "limite_total": PROXY_MAX_CONCURRENT,
        "limite_por_ip": PROXY_MAX_PER_IP,
        "ips_conectados": len(_active_by_ip),
        "por_ip": dict(_active_by_ip),
    }


# ==========================================================
# SLUGIFY
# ==========================================================
def slugify(texto: str, ano=None) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^\w\s-]", "", texto.lower())
    texto = re.sub(r"[-\s]+", "-", texto).strip("-")
    texto = re.sub(r"^(filme|serie|série|movie|tv|vod)[-\s]+", "", texto)
    if ano:
        try:
            ano_int = int(ano)
            texto = f"{texto}-{ano_int}"
        except Exception:
            pass
    return texto or ""


# ==========================================================
# MAPAS EM MEMÓRIA
# ==========================================================
_SLUG_MAP: dict = {}
_ID_MAP: dict   = {}

_SLUG_MAP_FILMES: dict = {}
_SLUG_MAP_SERIES: dict = {}
_SLUG_MAP_CANAIS: dict = {}


def build_slug_map(index: dict) -> dict:
    global _SLUG_MAP, _ID_MAP
    global _SLUG_MAP_FILMES, _SLUG_MAP_SERIES, _SLUG_MAP_CANAIS

    smap_filmes = {}
    smap_series = {}
    smap_canais = {}
    imap = {}

    def _add(target: dict, slug: str, dados: dict):
        if not slug:
            return
        # Colisão entre tipos → adiciona sufixo numérico
        base = slug
        i = 2
        while (slug in smap_filmes or slug in smap_series or slug in smap_canais):
            slug = f"{base}-{i}"
            i += 1
        target[slug] = dados
        imap[str(dados.get("id"))] = dados

    # ---------- FILMES ----------
    for f in index.get("filmes", []):
        if not f.get("url"):
            continue
        slug = slugify(f.get("titulo") or "", f.get("ano"))
        dados = {
            "id": f.get("id"),
            "url": f.get("url"),
            "titulo": f.get("titulo"),
            "tipo": "filme",
            "slug": slug,
        }
        _add(smap_filmes, slug, dados)

    # ---------- SÉRIES ----------
    for s in index.get("series", []):
        titulo_base = slugify(s.get("titulo") or "")
        if not titulo_base:
            continue

        # Episódios (se já estiverem no JSON — normalmente só após Opção B)
        for ep in s.get("episodios", []):
            t = ep.get("temporada")
            e = ep.get("episodio")
            if not ep.get("url") or not t or not e:
                continue
            try:
                t_int = int(t)
                e_int = int(e)
            except Exception:
                continue
            slug = f"{titulo_base}-t{t_int:02d}-e{e_int:02d}"
            dados = {
                "id": ep.get("id"),
                "url": ep.get("url"),
                "titulo": f"{s.get('titulo')} T{t_int:02d}E{e_int:02d}",
                "tipo": "serie",
                "slug": slug,
            }
            _add(smap_series, slug, dados)

        # Salva SEMPRE o slug base da série (mesmo sem URL)
        # URL vazia → usado apenas pra lookup, o episódio é resolvido sob demanda
        dados_base = {
            "id": s.get("id"),
            "url": s.get("url") or "",     # pode ser vazio
            "titulo": s.get("titulo"),
            "tipo": "serie",
            "slug": titulo_base,
        }
        _add(smap_series, titulo_base, dados_base)

    # ---------- CANAIS ----------
    for c in index.get("canais", []):
        if not c.get("url"):
            continue
        slug = slugify(c.get("titulo") or "")
        dados = {
            "id": c.get("id"),
            "url": c.get("url"),
            "titulo": c.get("titulo"),
            "tipo": "canal",
            "slug": slug,
        }
        _add(smap_canais, slug, dados)

    # ---------- Consolidado em memória ----------
    smap_total = {}
    smap_total.update(smap_filmes)
    smap_total.update(smap_series)
    smap_total.update(smap_canais)

    _SLUG_MAP = smap_total
    _ID_MAP = imap
    _SLUG_MAP_FILMES = smap_filmes
    _SLUG_MAP_SERIES = smap_series
    _SLUG_MAP_CANAIS = smap_canais

    # ---------- Salva 3 arquivos ----------
    def _salvar(path, dados):
        try:
            path.write_text(
                json.dumps(dados, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Proxy] Erro ao salvar {path.name}: {e}")

    _salvar(SLUG_MAP_FILMES_FILE, smap_filmes)
    _salvar(SLUG_MAP_SERIES_FILE, smap_series)
    _salvar(SLUG_MAP_CANAIS_FILE, smap_canais)

    print(f"[Proxy] Slugs: {len(smap_filmes)} filmes · "
          f"{len(smap_series)} séries · {len(smap_canais)} canais")

    return smap_total


def get_by_slug(slug: str) -> dict | None:
    return _SLUG_MAP.get(slug)


def get_by_id(item_id: str) -> dict | None:
    return _ID_MAP.get(str(item_id))


def total_slugs() -> int:
    return len(_SLUG_MAP)


def total_slugs_por_tipo() -> dict:
    return {
        "filmes": len(_SLUG_MAP_FILMES),
        "series": len(_SLUG_MAP_SERIES),
        "canais": len(_SLUG_MAP_CANAIS),
        "total": len(_SLUG_MAP),
    }


def slug_for(item: dict, tipo: str | None = None) -> str:
    titulo = item.get("titulo") or ""
    ano = item.get("ano")
    t = tipo or item.get("tipo") or ""
    if t == "serie":
        base = slugify(titulo)
        t_num = item.get("temporada")
        e_num = item.get("episodio")
        if t_num and e_num:
            try:
                return f"{base}-t{int(t_num):02d}-e{int(e_num):02d}"
            except Exception:
                return base
        return base
    return slugify(titulo, ano)


# ==========================================================
# REGISTRO DINÂMICO (usado por info.py)
# ==========================================================
def register_item(item_id: str, url: str, titulo: str, tipo: str, slug: str = None):
    global _ID_MAP, _SLUG_MAP
    if not item_id or not url:
        return
    dados = {
        "id": str(item_id),
        "url": url,
        "titulo": titulo,
        "tipo": tipo,
        "slug": slug or slugify(titulo),
    }
    _ID_MAP[str(item_id)] = dados
    if slug:
        _SLUG_MAP[slug] = dados
    print(f"[Proxy] ID {item_id} registrado dinamicamente ({tipo})")


# ==========================================================
# STREAM PROXY
# ==========================================================
async def stream_proxy(url: str, request: Request) -> StreamingResponse:
    global _active_total

    if not PROXY_ENABLED:
        raise HTTPException(404, "Proxy desativado.")

    # Se a URL for vazia (série sem episódios resolvidos), recusa
    if not url or not url.strip():
        raise HTTPException(404, "Item sem URL de stream. Abra a série primeiro.")

    client_ip = request.client.host if request.client else "unknown"

    if _active_total >= PROXY_MAX_CONCURRENT:
        raise HTTPException(
            503,
            "Servidor no limite de streams. Tente novamente em alguns segundos."
        )

    if _active_by_ip.get(client_ip, 0) >= PROXY_MAX_PER_IP:
        raise HTTPException(
            429,
            f"Você atingiu o limite de {PROXY_MAX_PER_IP} streams "
            "simultâneos. Feche algum player e tente de novo."
        )

    headers = {"User-Agent": USER_AGENT}
    if "range" in request.headers:
        headers["Range"] = request.headers["range"]
    if "if-modified-since" in request.headers:
        headers["If-Modified-Since"] = request.headers["if-modified-since"]

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=PROXY_CONNECT_TIMEOUT,
            read=PROXY_READ_TIMEOUT,
            write=PROXY_READ_TIMEOUT,
            pool=None,
        ),
        limits=httpx.Limits(
            max_connections=PROXY_MAX_CONCURRENT + 50,
            max_keepalive_connections=PROXY_MAX_CONCURRENT,
        ),
        follow_redirects=True,
    )

    try:
        upstream = await client.send(
            client.build_request("GET", url, headers=headers),
            stream=True,
        )
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(502, f"Erro ao conectar no provedor: {e}")

    if upstream.status_code >= 400:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status, "Provedor recusou a requisição do stream.")

    _active_total += 1
    _active_by_ip[client_ip] = _active_by_ip.get(client_ip, 0) + 1

    async def _streamer():
        global _active_total
        try:
            async for chunk in upstream.aiter_bytes(PROXY_CHUNK_SIZE):
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            pass
        except Exception as e:
            print(f"[!] Stream erro ({client_ip}): {e}")
        finally:
            _active_total = max(0, _active_total - 1)
            if client_ip in _active_by_ip:
                _active_by_ip[client_ip] -= 1
                if _active_by_ip[client_ip] <= 0:
                    del _active_by_ip[client_ip]
            try:
                await upstream.aclose()
            except Exception:
                pass
            try:
                await client.aclose()
            except Exception:
                pass

    resp_headers = {}
    for h in (
        "content-type",
        "content-length",
        "content-range",
        "accept-ranges",
        "last-modified",
        "etag",
    ):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]

    resp_headers.setdefault("accept-ranges", "bytes")
    resp_headers["cache-control"] = "no-store"

    resp_headers["Access-Control-Allow-Origin"] = "*"
    resp_headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    resp_headers["Access-Control-Allow-Headers"] = "Range, Content-Type, Origin"
    resp_headers["Access-Control-Expose-Headers"] = (
        "Content-Length, Content-Range, Accept-Ranges, Content-Type"
    )

    media_type = upstream.headers.get("content-type", "video/mp4")

    return StreamingResponse(
        _streamer(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=media_type,
    )