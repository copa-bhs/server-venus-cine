from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

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

app = FastAPI(title="IPTV Organizer Pro", version="4.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_INDEX: dict = {}
_AUTO_REFRESH_TASK = None
_HOME_CACHE: dict = {}
_HOME_CACHE_TS: float = 0
_HOME_CACHE_TTL = 300


# ==========================================================
# CACHE / ÍNDICE
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


def _salvar_index():
    INDEX_FILE.write_text(json.dumps(_INDEX, ensure_ascii=False), encoding="utf-8")


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
    content = M3U_FILE.read_text(encoding="utf-8", errors="ignore")
    raw = parse_m3u(content)
    _INDEX = build_index(raw)
    _salvar_index()
    _rebuild_slugs()
    print(f"[✓] Índice pronto em {time.time() - t1:.2f}s")
    print(f"[✓] Stats: {_INDEX['stats']}")


# ==========================================================
# COMPARAÇÃO
# ==========================================================
def _chave_filme(item: dict) -> str:
    return f"{(item.get('titulo') or '').strip().lower()}|{item.get('ano') or ''}"


def _chave_serie(item: dict) -> str:
    return (item.get("titulo") or "").strip().lower()


def _comparar_conjuntos(lista_antiga, lista_nova, fn_chave):
    antigas = {fn_chave(i) for i in lista_antiga if fn_chave(i)}
    return [i for i in lista_nova if fn_chave(i) and fn_chave(i) not in antigas]


async def detectar_novidades(index_antigo: dict, index_novo: dict) -> dict:
    filmes_novos = _comparar_conjuntos(
        index_antigo.get("filmes", []), index_novo.get("filmes", []), _chave_filme
    )
    series_novas = _comparar_conjuntos(
        index_antigo.get("series", []), index_novo.get("series", []), _chave_serie
    )
    canais_novos = _comparar_conjuntos(
        index_antigo.get("canais", []), index_novo.get("canais", []), _chave_serie
    )

    limite = TMDB_MAX_PER_CYCLE

    for item in filmes_novos[:limite]:
        dados = await tmdb.buscar_filme(item.get("titulo") or "", item.get("ano"))
        item["classificacao"] = "lancamento" if (dados and dados.get("recente")) else "catalogo"

    for item in series_novas[:limite]:
        dados = await tmdb.buscar_serie(item.get("titulo") or "", item.get("ano"))
        item["classificacao"] = "lancamento" if (dados and dados.get("recente")) else "catalogo"

    lancamentos = [f for f in filmes_novos + series_novas if f.get("classificacao") == "lancamento"]
    catalogo = [f for f in filmes_novos + series_novas if f.get("classificacao") == "catalogo"]

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
        "canais": canais_novos[:50],
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
    print(f"  🎬 Filmes novos:  {t['filmes_novos']}")
    print(f"  📺 Séries novas:  {t['series_novas']}")
    print(f"  📡 Canais novos:  {t['canais_novos']}")
    print(f"  🌟 Lançamentos:   {t['lancamentos']}")
    print(f"  📚 Catálogo novo: {t['catalogo']}")
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
        try:
            index_antigo = _INDEX if _INDEX else {"filmes": [], "series": [], "canais": []}

            t0 = time.time()
            result = await parallel_download(IPTV_URL, tmp_m3u)
            print(f"[✓] Download: {result} em {time.time() - t0:.2f}s")

            content = tmp_m3u.read_text(encoding="utf-8", errors="ignore")
            tmp_m3u.replace(M3U_FILE)

            raw = parse_m3u(content)
            novo_index = build_index(raw)

            novidades = await detectar_novidades(index_antigo, novo_index)

            INDEX_FILE.write_text(json.dumps(novo_index, ensure_ascii=False), encoding="utf-8")
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
            try:
                tmp_m3u.unlink(missing_ok=True)
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


def _card(item: dict, tipo: str | None = None) -> dict:
    return {
        "id": item.get("id"),
        "tipo": tipo or item.get("tipo") or "filme",
        "titulo": item.get("titulo") or "",
        "capa": item.get("capa"),
        "ano": item.get("ano"),
        "classificacao": item.get("classificacao"),
    }


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
        "version": "4.1.0",
        "storage": "JSON",
        "tmdb": TMDB_ENABLED,
        "proxy": True,
        "endpoints": {
            "GET  /home": "Dashboard 12 coleções + hero + trailers",
            "GET  /stream/{slug}.mp4": "Proxy de stream",
            "GET  /stream/id/{id}.mp4": "Proxy via ID do painel",
            "GET  /proxy/stats": "Monitor do proxy",
            "POST /refresh": "Força atualização do M3U",
            "GET  /status": "Estado + stats",
            "GET  /novidades": "Lançamentos vs catálogo novo",
            "POST /novidades/verificar": "Força detecção de novidades",
            "POST /trailers/verificar?limit=50": "Re-verifica trailers",
            "GET  /filmes?page=1&size=30": "Lista filmes",
            "GET  /series?page=1&size=30": "Lista séries",
            "GET  /canais?page=1&size=50": "Lista canais",
            "GET  /buscar?q=&tipo=": "Busca paginada",
            "GET  /categorias": "Categorias",
            "GET  /info/filme/{id}": "Detalhes do filme",
            "GET  /info/serie/{id}": "Detalhes da série",
            "GET  /admin/sem-logo": "Itens sem logo",
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
        "storage": "JSON",
        "public_base_url": PUBLIC_BASE_URL,
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
    return data


@app.post("/novidades/verificar")
async def verificar_novidades():
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
    resp["items"] = [_card(it, "filme") for it in resp["items"]]
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
    resp = paginate(series, page, size)
    resp["items"] = [_card(it, "serie") for it in resp["items"]]
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
    resp["items"] = [_card(it, "canal") for it in resp["items"]]
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
        for f in _INDEX.get("filmes", []):
            if q_low in (f.get("titulo") or "").lower():
                resultados.append(_card(f, "filme"))
    if tipo in (None, "serie"):
        for s in _INDEX.get("series", []):
            if q_low in (s.get("titulo") or "").lower():
                resultados.append(_card(s, "serie"))
    if tipo in (None, "canal"):
        for c in _INDEX.get("canais", []):
            if q_low in (c.get("titulo") or "").lower():
                resultados.append(_card(c, "canal"))
    return paginate(resultados, page, size)


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
        return await fetch_movie_info(filme_id, force=force)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Provedor recusou")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Falha: {e}")


@app.get("/info/serie/{serie_id}")
async def info_serie(serie_id: str, force: bool = False):
    try:
        return await fetch_series_info(serie_id, force=force)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Provedor recusou")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Falha: {e}")


# ==========================================================
# ADMIN SEM LOGO
# ==========================================================
@app.get("/admin/sem-logo")
def listar_sem_logo(
    tipo: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    if not _INDEX:
        raise HTTPException(503, "Índice não carregado.")
    filmes_sem = []
    for f in _INDEX.get("filmes", []):
        if f.get("tmdb_enriquecido") and not f.get("logo"):
            filmes_sem.append({
                **_card(f, "filme"),
                "tmdb_id": f.get("tmdb_id"),
                "tmdb_url": f"https://www.themoviedb.org/movie/{f['tmdb_id']}" if f.get("tmdb_id") else None,
                "score": f.get("score"),
            })
    series_sem = []
    for s in _INDEX.get("series", []):
        if s.get("tmdb_enriquecido") and not s.get("logo"):
            series_sem.append({
                **_card(s, "serie"),
                "tmdb_id": s.get("tmdb_id"),
                "tmdb_url": f"https://www.themoviedb.org/tv/{s['tmdb_id']}" if s.get("tmdb_id") else None,
                "score": s.get("score"),
            })
    resultado = filmes_sem + series_sem if not tipo else (filmes_sem if tipo == "filme" else series_sem)
    resultado.sort(key=lambda x: (x.get("titulo") or "").lower())
    return {
        "total_filmes_sem_logo": len(filmes_sem),
        "total_series_sem_logo": len(series_sem),
        "total_geral": len(resultado),
        **paginate(resultado, page, size),
    }


# ==========================================================
# PROXY DE STREAM
# ==========================================================
@app.get("/stream/{slug_path:path}")
async def proxy_por_slug(slug_path: str, request: Request):
    slug = slug_path
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov", ".flv", ".webm"):
        if slug.lower().endswith(ext):
            slug = slug[:-len(ext)]
            break
    item = get_by_slug(slug)
    if not item:
        raise HTTPException(404, "Slug não encontrado.")
    return await stream_proxy(item["url"], request)


@app.get("/stream/id/{item_id}")
async def proxy_por_id(item_id: str, request: Request):
    clean_id = item_id
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov"):
        if clean_id.lower().endswith(ext):
            clean_id = clean_id[:-len(ext)]
            break
    item = get_by_id(clean_id)
    if not item:
        raise HTTPException(404, "ID não encontrado.")
    return await stream_proxy(item["url"], request)


@app.get("/proxy/stats")
def proxy_monitor():
    return {"slugs_carregados": total_slugs(), **proxy_stats()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)