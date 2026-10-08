import asyncio
import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from config import (
    IPTV_URL, USER_AGENT, CACHE_TTL,
    M3U_FILE, META_FILE, INDEX_FILE, NOVIDADES_FILE,
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PORT,
    TMDB_ENABLED, TMDB_MAX_PER_CYCLE, PUBLIC_BASE_URL,
)
from downloader import parallel_download
from parser import parse_m3u, build_index
from info import fetch_movie_info, fetch_series_info
from enricher import enricher_loop
from trailer_checker import trailer_check_loop, checar_agora
from home import build_home
from proxy import (
    build_slug_map, get_by_slug, get_by_id,
    stream_proxy, stats as proxy_stats, total_slugs, slug_for,
)
import tmdb

app = FastAPI(title="IPTV Organizer Pro", version="2.6.0")

_INDEX: dict = {}
_AUTO_REFRESH_TASK = None
_HOME_CACHE: dict = {}
_HOME_CACHE_TS: float = 0
_HOME_CACHE_TTL = 300


# ==========================================================
# CACHE
# ==========================================================
def cache_valid() -> bool:
    if not M3U_FILE.exists() or not META_FILE.exists():
        return False
    try:
        meta = json.loads(META_FILE.read_text())
        return (time.time() - meta["timestamp"]) < CACHE_TTL
    except Exception:
        return False


def load_index_from_disk() -> bool:
    global _INDEX
    if INDEX_FILE.exists():
        try:
            _INDEX = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            return True
        except Exception:
            return False
    return False


def rebuild_index_from_m3u() -> dict:
    content = M3U_FILE.read_text(encoding="utf-8", errors="ignore")
    raw = parse_m3u(content)
    return build_index(raw)


def _salvar_index():
    INDEX_FILE.write_text(
        json.dumps(_INDEX, ensure_ascii=False), encoding="utf-8"
    )


def _rebuild_slugs():
    if _INDEX:
        build_slug_map(_INDEX)
        print(f"[✓] Slugs gerados: {total_slugs()}")


async def fetch_and_rebuild(force: bool = False):
    global _INDEX
    if not force and cache_valid() and load_index_from_disk():
        print("[✓] Cache válido — usando índice em disco.")
        _rebuild_slugs()
        return

    print("[•] Baixando lista IPTV em paralelo...")
    t0 = time.time()
    result = await parallel_download(IPTV_URL, M3U_FILE)
    print(f"[✓] Download: {result} em {time.time() - t0:.2f}s")

    META_FILE.write_text(json.dumps({"timestamp": time.time()}))

    print("[•] Processando M3U...")
    t1 = time.time()
    _INDEX = rebuild_index_from_m3u()
    _salvar_index()
    _rebuild_slugs()
    print(f"[✓] Índice pronto em {time.time() - t1:.2f}s")
    print(f"[✓] Stats: {_INDEX['stats']}")


# ==========================================================
# COMPARAÇÃO
# ==========================================================
def _chave_filme(item: dict) -> str:
    titulo = (item.get("titulo") or "").strip().lower()
    ano = item.get("ano") or ""
    return f"{titulo}|{ano}"


def _chave_serie(item: dict) -> str:
    return (item.get("titulo") or "").strip().lower()


def _chave_canal(item: dict) -> str:
    return (item.get("titulo") or "").strip().lower()


def _comparar_conjuntos(lista_antiga, lista_nova, fn_chave):
    chaves_antigas = {fn_chave(i) for i in lista_antiga if fn_chave(i)}
    return [i for i in lista_nova if fn_chave(i) and fn_chave(i) not in chaves_antigas]


# ==========================================================
# NOVIDADES
# ==========================================================
async def _enriquecer_lote_novidades(filmes, series):
    if not TMDB_ENABLED:
        for item in filmes + series:
            item["classificacao"] = "catalogo"
        return

    limite = TMDB_MAX_PER_CYCLE

    for item in filmes[:limite]:
        dados = await tmdb.buscar_filme(item["titulo"], item.get("ano"))
        if dados:
            item["tmdb"] = {
                "id": dados["tmdb_id"],
                "release_date": dados["release_date"],
                "ano_real": dados["ano"],
                "score": dados["score"],
                "sinopse": dados["sinopse"],
                "capa_oficial": dados["capa"],
                "banner_oficial": dados["banner"],
                "logo": dados.get("logo"),
                "trailer_url": dados.get("trailer_url"),
                "classificacao": dados.get("classificacao"),
                "generos": dados.get("generos"),
                "recente": dados["recente"],
            }
            item["classificacao"] = "lancamento" if dados["recente"] else "catalogo"
        else:
            item["classificacao"] = "catalogo"

    for item in series[:limite]:
        dados = await tmdb.buscar_serie(item["titulo"], item.get("ano"))
        if dados:
            item["tmdb"] = {
                "id": dados["tmdb_id"],
                "release_date": dados["release_date"],
                "ano_real": dados["ano"],
                "score": dados["score"],
                "sinopse": dados["sinopse"],
                "capa_oficial": dados["capa"],
                "banner_oficial": dados["banner"],
                "logo": dados.get("logo"),
                "trailer_url": dados.get("trailer_url"),
                "classificacao": dados.get("classificacao"),
                "generos": dados.get("generos"),
                "recente": dados["recente"],
            }
            item["classificacao"] = "lancamento" if dados["recente"] else "catalogo"
        else:
            item["classificacao"] = "catalogo"


async def detectar_novidades(index_antigo: dict, index_novo: dict) -> dict:
    filmes_novos = _comparar_conjuntos(
        index_antigo.get("filmes", []),
        index_novo.get("filmes", []),
        _chave_filme,
    )
    series_novas = _comparar_conjuntos(
        index_antigo.get("series", []),
        index_novo.get("series", []),
        _chave_serie,
    )
    canais_novos = _comparar_conjuntos(
        index_antigo.get("canais", []),
        index_novo.get("canais", []),
        _chave_canal,
    )

    await _enriquecer_lote_novidades(filmes_novos, series_novas)

    lancamentos = []
    catalogo = []

    for f in filmes_novos:
        item = {
            "id": f["id"],
            "titulo": f["titulo"],
            "ano": f.get("ano"),
            "capa": f.get("capa"),
            "categoria": f.get("categoria"),
            "tipo": "filme",
            "tmdb": f.get("tmdb"),
            "classificacao": f.get("classificacao", "catalogo"),
        }
        (lancamentos if item["classificacao"] == "lancamento" else catalogo).append(item)

    for s in series_novas:
        item = {
            "id": s["id"],
            "titulo": s["titulo"],
            "ano": s.get("ano"),
            "capa": s.get("capa"),
            "categoria": s.get("categoria"),
            "tipo": "serie",
            "total_episodios": s.get("total_episodios"),
            "tmdb": s.get("tmdb"),
            "classificacao": s.get("classificacao", "catalogo"),
        }
        (lancamentos if item["classificacao"] == "lancamento" else catalogo).append(item)

    return {
        "detectado_em": time.time(),
        "detectado_em_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tmdb_ativo": TMDB_ENABLED,
        "totais": {
            "filmes_novos": len(filmes_novos),
            "series_novas": len(series_novas),
            "canais_novos": len(canais_novos),
            "lancamentos": len(lancamentos),
            "catalogo": len(catalogo),
        },
        "lancamentos": lancamentos[:50],
        "catalogo": catalogo[:100],
        "canais": [
            {"id": c["id"], "titulo": c["titulo"], "categoria": c.get("categoria")}
            for c in canais_novos[:50]
        ],
    }


def logar_novidades(nov: dict):
    t = nov["totais"]
    total = t["filmes_novos"] + t["series_novas"] + t["canais_novos"]
    if total == 0:
        print("[•] Nenhuma novidade detectada.")
        return

    print("")
    print("=" * 60)
    print(f"  🎉 NOVIDADES — {nov['detectado_em_str']}")
    print("=" * 60)
    print(f"  🎬 Filmes novos:      {t['filmes_novos']}")
    print(f"  📺 Séries novas:      {t['series_novas']}")
    print(f"  📡 Canais novos:      {t['canais_novos']}")
    print(f"  🌟 Lançamentos:       {t['lancamentos']}")
    print(f"  📚 Catálogo novo:     {t['catalogo']}")
    print("-" * 60)
    if nov["lancamentos"]:
        print("  🌟 LANÇAMENTOS RECENTES:")
        for item in nov["lancamentos"][:10]:
            ano = item.get("ano") or "s/ano"
            print(f"     🆕 [{item['tipo'].upper()}] {item['titulo']} ({ano})")
    if nov["catalogo"]:
        print("  📚 NOVOS NO CATÁLOGO:")
        for item in nov["catalogo"][:10]:
            ano = item.get("ano") or "s/ano"
            print(f"     ➕ [{item['tipo'].upper()}] {item['titulo']} ({ano})")
    print("=" * 60)
    print("")


# ==========================================================
# AUTO-REFRESH
# ==========================================================
async def auto_refresh_loop():
    global _INDEX, _HOME_CACHE_TS
    print(f"[✓] Auto-refresh ativo (a cada {CACHE_TTL}s = {CACHE_TTL // 60}min)")

    while True:
        await asyncio.sleep(CACHE_TTL)
        print(f"\n[•] Auto-refresh disparado ({time.strftime('%H:%M:%S')})")

        tmp_m3u = M3U_FILE.with_suffix(".new")
        tmp_index = INDEX_FILE.with_suffix(".new")

        try:
            t0 = time.time()
            result = await parallel_download(IPTV_URL, tmp_m3u)
            print(f"[✓] Download: {result} em {time.time() - t0:.2f}s")

            content = tmp_m3u.read_text(encoding="utf-8", errors="ignore")
            raw = parse_m3u(content)
            novo_index = build_index(raw)

            index_antigo = _INDEX if _INDEX else {"filmes": [], "series": [], "canais": []}
            novidades = await detectar_novidades(index_antigo, novo_index)

            tmp_m3u.replace(M3U_FILE)
            tmp_index.write_text(json.dumps(novo_index, ensure_ascii=False), encoding="utf-8")
            tmp_index.replace(INDEX_FILE)
            META_FILE.write_text(json.dumps({"timestamp": time.time()}))
            _INDEX = novo_index
            _HOME_CACHE_TS = 0
            _rebuild_slugs()

            NOVIDADES_FILE.write_text(
                json.dumps(novidades, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logar_novidades(novidades)
            print(f"[✓] Stats: {novo_index['stats']}")

        except Exception as e:
            print(f"[!] Auto-refresh falhou: {e}")
            for p in (tmp_m3u, tmp_index):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass


# ==========================================================
# PAGINAÇÃO
# ==========================================================
def paginate(items: list, page: int, size: int) -> dict:
    total = len(items)
    total_pages = (total + size - 1) // size if size else 0
    start = (page - 1) * size
    end = start + size
    return {
        "page": page,
        "page_size": size,
        "total_items": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "items": items[start:end],
    }


def _add_slug_urls(item: dict, tipo: str | None = None):
    """Adiciona slug e URL de stream amigável sem expor credencial."""
    slug = slug_for(item, tipo)
    if slug:
        item["slug"] = slug
        item["url_stream"] = f"{PUBLIC_BASE_URL}/stream/{slug}" if PUBLIC_BASE_URL else f"/stream/{slug}"


# ==========================================================
# STARTUP / SHUTDOWN
# ==========================================================
@app.on_event("startup")
async def startup():
    global _AUTO_REFRESH_TASK
    print("[•] Startup: verificando cache...")
    try:
        await fetch_and_rebuild(force=False)
    except Exception as e:
        print(f"[!] Startup falhou: {e}")

    _AUTO_REFRESH_TASK = asyncio.create_task(auto_refresh_loop())
    asyncio.create_task(enricher_loop(_INDEX, _salvar_index))
    asyncio.create_task(trailer_check_loop())


@app.on_event("shutdown")
async def shutdown():
    global _AUTO_REFRESH_TASK
    if _AUTO_REFRESH_TASK:
        _AUTO_REFRESH_TASK.cancel()
        try:
            await _AUTO_REFRESH_TASK
        except asyncio.CancelledError:
            pass
    print("[•] Auto-refresh encerrado.")


# ==========================================================
# RAIZ
# ==========================================================
@app.get("/")
def root():
    return {
        "service": "IPTV Organizer Pro",
        "version": "2.6.0",
        "tmdb": TMDB_ENABLED,
        "proxy": True,
        "endpoints": {
            "GET  /home": "Dashboard 12 coleções + hero + trailers",
            "GET  /stream/{slug}": "Proxy de stream (URL amigável)",
            "GET  /stream/id/{id}": "Proxy de stream (via ID do painel)",
            "GET  /proxy/stats": "Monitor do proxy em tempo real",
            "POST /refresh": "Força atualização do M3U",
            "GET  /status": "Estado + stats",
            "GET  /novidades": "Lançamentos vs catálogo novo",
            "POST /novidades/verificar": "Força detecção de novidades",
            "POST /trailers/verificar?limit=50": "Re-verifica trailers",
            "GET  /filmes?page=1&size=30": "Filmes paginados",
            "GET  /series?page=1&size=30": "Séries paginadas",
            "GET  /canais?page=1&size=50": "Canais paginados",
            "GET  /buscar?q=&tipo=": "Busca paginada",
            "GET  /categorias": "Categorias",
            "GET  /info/filme/{id}": "Metadados ricos do filme",
            "GET  /info/serie/{id}": "Metadados ricos da série",
        },
    }


# ==========================================================
# HOME
# ==========================================================
@app.get("/home")
def home(force: bool = False):
    global _HOME_CACHE, _HOME_CACHE_TS
    if not _INDEX:
        raise HTTPException(503, "Índice não carregado. Chame /refresh.")
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < _HOME_CACHE_TTL:
        return _HOME_CACHE
    _HOME_CACHE = build_home(_INDEX)
    _HOME_CACHE_TS = time.time()
    return _HOME_CACHE


# ==========================================================
# STATUS
# ==========================================================
@app.get("/status")
async def status():
    meta = {}
    if META_FILE.exists():
        try:
            meta = json.loads(META_FILE.read_text())
        except Exception:
            meta = {}
    idade = time.time() - meta.get("timestamp", 0) if meta else 0

    filmes = _INDEX.get("filmes", [])
    series = _INDEX.get("series", [])
    enriq_f = sum(1 for f in filmes if f.get("tmdb_enriquecido"))
    enriq_s = sum(1 for s in series if s.get("tmdb_enriquecido"))

    return {
        "cached": bool(meta),
        "age_seconds": int(idade),
        "ttl_seconds": CACHE_TTL,
        "expires_in": max(0, int(CACHE_TTL - idade)),
        "valid": idade < CACHE_TTL,
        "auto_refresh_ativo": _AUTO_REFRESH_TASK is not None and not _AUTO_REFRESH_TASK.done(),
        "tmdb_ativo": TMDB_ENABLED,
        "enrichment": {
            "filmes_total": len(filmes),
            "filmes_enriquecidos": enriq_f,
            "series_total": len(series),
            "series_enriquecidas": enriq_s,
        },
        "proxy": proxy_stats(),
        "stats": _INDEX.get("stats", {}),
    }


@app.post("/refresh")
async def refresh():
    global _HOME_CACHE_TS
    await fetch_and_rebuild(force=True)
    _HOME_CACHE_TS = 0
    return {"ok": True, "stats": _INDEX.get("stats", {})}


# ==========================================================
# NOVIDADES
# ==========================================================
@app.get("/novidades")
def novidades(
    apenas_lancamentos: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    if not NOVIDADES_FILE.exists():
        return {"disponivel": False, "mensagem": "Nenhuma detecção ainda."}
    try:
        data = json.loads(NOVIDADES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"disponivel": False, "mensagem": "Erro ao ler arquivo."}
    data["disponivel"] = True
    if apenas_lancamentos:
        return {
            "detectado_em_str": data["detectado_em_str"],
            "total": len(data.get("lancamentos", [])),
            **paginate(data.get("lancamentos", []), page, size),
        }
    return data


@app.post("/novidades/verificar")
async def verificar_novidades():
    global _INDEX
    try:
        t0 = time.time()
        tmp_m3u = M3U_FILE.with_suffix(".check")
        await parallel_download(IPTV_URL, tmp_m3u)
        content = tmp_m3u.read_text(encoding="utf-8", errors="ignore")
        tmp_m3u.unlink(missing_ok=True)

        raw = parse_m3u(content)
        novo_index = build_index(raw)

        index_antigo = _INDEX if _INDEX else {"filmes": [], "series": [], "canais": []}
        novidades = await detectar_novidades(index_antigo, novo_index)

        NOVIDADES_FILE.write_text(
            json.dumps(novidades, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logar_novidades(novidades)

        return {
            "ok": True,
            "duracao_segundos": round(time.time() - t0, 2),
            "totais": novidades["totais"],
        }
    except Exception as e:
        raise HTTPException(500, f"Erro na verificação: {e}")


@app.post("/trailers/verificar")
async def verificar_trailers(limit: int = 50):
    return await checar_agora(limit=limit)


# ==========================================================
# LISTAGENS
# ==========================================================
@app.get("/filmes")
def listar_filmes(
    page: int = Query(1, ge=1),
    size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    categoria: str | None = None,
):
    if not _INDEX:
        raise HTTPException(503, "Índice não carregado. Chame /refresh.")
    filmes = _INDEX.get("filmes", [])
    if categoria:
        filmes = [f for f in filmes if f["categoria"].lower() == categoria.lower()]
    resp = paginate(filmes, page, size)
    for it in resp["items"]:
        _add_slug_urls(it, "filme")
    return resp


@app.get("/series")
def listar_series(
    page: int = Query(1, ge=1),
    size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    categoria: str | None = None,
):
    if not _INDEX:
        raise HTTPException(503, "Índice não carregado. Chame /refresh.")
    series = _INDEX.get("series", [])
    if categoria:
        series = [s for s in series if s["categoria"].lower() == categoria.lower()]
    resumo = [{k: v for k, v in s.items() if k != "episodios"} for s in series]
    resp = paginate(resumo, page, size)
    for it in resp["items"]:
        _add_slug_urls(it, "serie")
    return resp


@app.get("/canais")
def listar_canais(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    categoria: str | None = None,
):
    if not _INDEX:
        raise HTTPException(503, "Índice não carregado. Chame /refresh.")
    canais = _INDEX.get("canais", [])
    if categoria:
        canais = [c for c in canais if c["categoria"].lower() == categoria.lower()]
    resp = paginate(canais, page, size)
    for it in resp["items"]:
        _add_slug_urls(it, "canal")
    return resp


@app.get("/buscar")
def buscar(
    q: str = Query(..., min_length=1),
    tipo: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    if not _INDEX:
        raise HTTPException(503, "Índice não carregado. Chame /refresh.")
    q_low = q.lower()
    resultados = []

    if tipo in (None, "filme"):
        resultados += [
            f for f in _INDEX.get("filmes", [])
            if q_low in (f.get("titulo") or "").lower()
        ]
    if tipo in (None, "serie"):
        for s in _INDEX.get("series", []):
            if q_low in (s.get("titulo") or "").lower():
                resultados.append({k: v for k, v in s.items() if k != "episodios"})
    if tipo in (None, "canal"):
        resultados += [
            c for c in _INDEX.get("canais", [])
            if q_low in (c.get("titulo") or "").lower()
        ]
    resp = paginate(resultados, page, size)
    for it in resp["items"]:
        _add_slug_urls(it)
    return resp


@app.get("/categorias")
def categorias():
    cats = {"filmes": set(), "series": set(), "canais": set()}
    for f in _INDEX.get("filmes", []):
        cats["filmes"].add(f["categoria"])
    for s in _INDEX.get("series", []):
        cats["series"].add(s["categoria"])
    for c in _INDEX.get("canais", []):
        cats["canais"].add(c["categoria"])
    return {k: sorted(v) for k, v in cats.items()}


# ==========================================================
# INFO VIA ID
# ==========================================================
@app.get("/info/filme/{filme_id}")
async def info_filme(filme_id: str, force: bool = False):
    try:
        data = await fetch_movie_info(filme_id, force=force)
        _add_slug_urls(data, "filme")
        return data
    except ValueError as e:
        raise HTTPException(404, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Provedor recusou a requisição")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Falha ao contatar o provedor: {e}")


@app.get("/info/serie/{serie_id}")
async def info_serie(serie_id: str, force: bool = False):
    try:
        data = await fetch_series_info(serie_id, force=force)
        _add_slug_urls(data, "serie")
        # Adiciona slug em cada episódio
        for ep in data.get("episodios", []):
            t = ep.get("temporada")
            e = ep.get("episodio")
            if t and e:
                base = slug_for({"titulo": data.get("titulo"), "tipo": "serie"}, "serie")
                if base:
                    slug_ep = f"{base}-t{int(t):02d}-e{int(e):02d}"
                    ep["slug"] = slug_ep
                    ep["url_stream"] = f"{PUBLIC_BASE_URL}/stream/{slug_ep}" if PUBLIC_BASE_URL else f"/stream/{slug_ep}"
        return data
    except ValueError as e:
        raise HTTPException(404, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Provedor recusou a requisição")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Falha ao contatar o provedor: {e}")


# ==========================================================
# PROXY DE STREAM
# ==========================================================
@app.get("/stream/{slug}")
async def proxy_por_slug(slug: str, request: Request):
    """
    Proxy de stream via slug amigável.
    Ex: /stream/homem-aranha-de-volta-para-casa-2017
    """
    item = get_by_slug(slug)
    if not item:
        raise HTTPException(404, "Slug não encontrado.")
    return await stream_proxy(item["url"], request)


@app.get("/stream/id/{item_id}")
async def proxy_por_id(item_id: str, request: Request):
    """Proxy via ID do painel Xtream: /stream/id/2127"""
    item = get_by_id(item_id)
    if not item:
        raise HTTPException(404, "ID não encontrado.")
    return await stream_proxy(item["url"], request)


@app.get("/proxy/stats")
def proxy_monitor():
    """Monitor do proxy em tempo real."""
    return {
        "slugs_carregados": total_slugs(),
        **proxy_stats(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)