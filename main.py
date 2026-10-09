import asyncio
import json
import time

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from config import (
    CACHE_TTL,
    FILMES_FILE, SERIES_FILE, CANAIS_FILE,
    META_FILE, NOVIDADES_FILE,
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PORT,
    TMDB_ENABLED, TMDB_MAX_PER_CYCLE, PUBLIC_BASE_URL,
)
from parser import fetch_filmes, fetch_series, fetch_canais
from info import fetch_movie_info, fetch_series_info
from home import build_home_async
from proxy import (
    build_slug_map, get_by_slug, get_by_id,
    stream_proxy, stats as proxy_stats, total_slugs,
)
import tmdb

app = FastAPI(title="IPTV Organizer Pro", version="7.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# ÍNDICES EM MEMÓRIA (3 listas separadas)
# ==========================================================
_FILMES: list = []
_SERIES: list = []
_CANAIS: list = []

_AUTO_REFRESH_TASK = None
_HOME_CACHE: dict = {}
_HOME_CACHE_TS: float = 0
_HOME_CACHE_TTL = 300


def _stats() -> dict:
    return {
        "total_filmes": len(_FILMES),
        "total_series": len(_SERIES),
        "total_canais": len(_CANAIS),
    }


# ==========================================================
# CACHE / ÍNDICE
# ==========================================================
def cache_valid() -> bool:
    if not META_FILE.exists():
        return False
    try:
        meta = json.loads(META_FILE.read_text())
        return (time.time() - meta["timestamp"]) < CACHE_TTL
    except Exception:
        return False


def _load_json(path, default=None):
    if not path.exists():
        return default if default is not None else []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else []


def load_index_from_disk() -> bool:
    global _FILMES, _SERIES, _CANAIS
    if not (FILMES_FILE.exists() and SERIES_FILE.exists() and CANAIS_FILE.exists()):
        return False
    try:
        _FILMES = _load_json(FILMES_FILE)
        _SERIES = _load_json(SERIES_FILE)
        _CANAIS = _load_json(CANAIS_FILE)
        return True
    except Exception:
        return False


def _save_all():
    FILMES_FILE.write_text(json.dumps(_FILMES, ensure_ascii=False), encoding="utf-8")
    SERIES_FILE.write_text(json.dumps(_SERIES, ensure_ascii=False), encoding="utf-8")
    CANAIS_FILE.write_text(json.dumps(_CANAIS, ensure_ascii=False), encoding="utf-8")


def _rebuild_slugs():
    build_slug_map({
        "filmes": _FILMES,
        "series": _SERIES,
        "canais": _CANAIS,
    })
    print(f"[✓] Slugs gerados: {total_slugs()}")


async def fetch_and_rebuild(force: bool = False):
    global _FILMES, _SERIES, _CANAIS

    if not force and cache_valid() and load_index_from_disk():
        print("[✓] Cache válido — usando arquivos em disco.")
        _rebuild_slugs()
        return

    print("[•] Atualizando catálogo via API Xtream...")
    t0 = time.time()

    # Baixa os 3 em paralelo — cada um salva seu arquivo
    filmes, series, canais = await asyncio.gather(
        fetch_filmes(),
        fetch_series(),
        fetch_canais(),
    )

    _FILMES = filmes
    _SERIES = series
    _CANAIS = canais

    _save_all()
    META_FILE.write_text(json.dumps({"timestamp": time.time()}))
    _rebuild_slugs()

    print(f"[✓] Catálogo atualizado em {time.time() - t0:.2f}s")
    print(f"[✓] Stats: {_stats()}")


# ==========================================================
# COMPARAÇÃO DE NOVIDADES
# ==========================================================
def _chave_filme(item: dict) -> str:
    return f"{(item.get('titulo') or '').strip().lower()}|{item.get('ano') or ''}"


def _chave_serie(item: dict) -> str:
    return (item.get("titulo") or "").strip().lower()


def _comparar(lista_antiga, lista_nova, fn_chave):
    antigas = {fn_chave(i) for i in lista_antiga if fn_chave(i)}
    return [i for i in lista_nova if fn_chave(i) and fn_chave(i) not in antigas]


async def detectar_novidades(filmes_ant, series_ant, canais_ant) -> dict:
    filmes_novos = _comparar(filmes_ant, _FILMES, _chave_filme)
    series_novas = _comparar(series_ant, _SERIES, _chave_serie)
    canais_novos = _comparar(canais_ant, _CANAIS, _chave_serie)

    limite = TMDB_MAX_PER_CYCLE

    for item in filmes_novos[:limite]:
        dados = await tmdb.buscar_filme(item.get("titulo") or "", item.get("ano"))
        item["classificacao_tipo"] = "lancamento" if (dados and dados.get("recente")) else "catalogo"

    for item in series_novas[:limite]:
        dados = await tmdb.buscar_serie(item.get("titulo") or "", item.get("ano"))
        item["classificacao_tipo"] = "lancamento" if (dados and dados.get("recente")) else "catalogo"

    lancamentos = [f for f in filmes_novos + series_novas if f.get("classificacao_tipo") == "lancamento"]
    catalogo = [f for f in filmes_novos + series_novas if f.get("classificacao_tipo") == "catalogo"]

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
        print("[•] Nenhuma novidade.")
        return
    print("")
    print("=" * 60)
    print(f"  🎉 NOVIDADES — {nov['detectado_em_str']}")
    print("=" * 60)
    print(f"  🎬 Filmes novos:  {t['filmes_novos']}")
    print(f"  📺 Séries novas:  {t['series_novas']}")
    print(f"  📡 Canais novos:  {t['canais_novos']}")
    print("=" * 60)
    print("")


# ==========================================================
# AUTO-REFRESH
# ==========================================================
async def auto_refresh_loop():
    global _FILMES, _SERIES, _CANAIS, _HOME_CACHE_TS
    print(f"[✓] Auto-refresh ativo (a cada {CACHE_TTL}s = {CACHE_TTL // 60}min)")

    while True:
        await asyncio.sleep(CACHE_TTL)
        print(f"\n[•] Auto-refresh disparado ({time.strftime('%H:%M:%S')})")

        try:
            # Snapshots antigos
            filmes_ant = list(_FILMES)
            series_ant = list(_SERIES)
            canais_ant = list(_CANAIS)

            # Baixa tudo de novo
            t0 = time.time()
            filmes, series, canais = await asyncio.gather(
                fetch_filmes(),
                fetch_series(),
                fetch_canais(),
            )
            print(f"[✓] Baixado em {time.time() - t0:.2f}s")

            _FILMES = filmes
            _SERIES = series
            _CANAIS = canais

            # Novidades
            novidades = await detectar_novidades(filmes_ant, series_ant, canais_ant)

            # Salva tudo
            _save_all()
            META_FILE.write_text(json.dumps({"timestamp": time.time()}))
            _HOME_CACHE_TS = 0
            _rebuild_slugs()

            NOVIDADES_FILE.write_text(
                json.dumps(novidades, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logar_novidades(novidades)
            print(f"[✓] Stats: {_stats()}")

        except Exception as e:
            print(f"[!] Auto-refresh falhou: {e}")


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
        "version": "7.0.0",
        "storage": "3 arquivos JSON (filmes / series / canais)",
        "tmdb": TMDB_ENABLED,
        "proxy": True,
    }


# ==========================================================
# HOME
# ==========================================================
@app.get("/home")
async def home(force: bool = False):
    global _HOME_CACHE, _HOME_CACHE_TS
    if not _FILMES and not _SERIES:
        raise HTTPException(503, "Catálogo não carregado. Chame /refresh.")
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < _HOME_CACHE_TTL:
        return _HOME_CACHE
    _HOME_CACHE = await build_home_async({
        "filmes": _FILMES,
        "series": _SERIES,
        "stats": _stats(),
    })
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

    return {
        "cached": bool(meta),
        "age_seconds": int(idade),
        "ttl_seconds": CACHE_TTL,
        "expires_in": max(0, int(CACHE_TTL - idade)),
        "valid": idade < CACHE_TTL,
        "auto_refresh_ativo": _AUTO_REFRESH_TASK is not None and not _AUTO_REFRESH_TASK.done(),
        "tmdb_ativo": TMDB_ENABLED,
        "storage": "3 JSONs (filmes / series / canais)",
        "arquivos": {
            "filmes": str(FILMES_FILE.name),
            "series": str(SERIES_FILE.name),
            "canais": str(CANAIS_FILE.name),
        },
        "public_base_url": PUBLIC_BASE_URL,
        "proxy": proxy_stats(),
        "stats": _stats(),
    }


@app.post("/refresh")
async def refresh():
    global _HOME_CACHE_TS
    await fetch_and_rebuild(force=True)
    _HOME_CACHE_TS = 0
    return {"ok": True, "stats": _stats()}


# ==========================================================
# NOVIDADES
# ==========================================================
@app.get("/novidades")
def novidades():
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
    global _FILMES, _SERIES, _CANAIS
    try:
        t0 = time.time()
        filmes_ant = list(_FILMES)
        series_ant = list(_SERIES)
        canais_ant = list(_CANAIS)

        filmes, series, canais = await asyncio.gather(
            fetch_filmes(),
            fetch_series(),
            fetch_canais(),
        )
        _FILMES = filmes
        _SERIES = series
        _CANAIS = canais

        novidades = await detectar_novidades(filmes_ant, series_ant, canais_ant)

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
        raise HTTPException(500, f"Erro: {e}")


# ==========================================================
# LISTAGENS
# ==========================================================
@app.get("/filmes")
def listar_filmes(
    page: int = Query(1, ge=1),
    size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    categoria: str | None = None,
):
    if not _FILMES:
        raise HTTPException(503, "Filmes não carregados.")
    filmes = _FILMES
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
    if not _SERIES:
        raise HTTPException(503, "Séries não carregadas.")
    series = _SERIES
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
    if not _CANAIS:
        raise HTTPException(503, "Canais não carregados.")
    canais = _CANAIS
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
    q_low = q.lower()
    resultados = []
    if tipo in (None, "filme"):
        for f in _FILMES:
            if q_low in (f.get("titulo") or "").lower():
                resultados.append(_card(f, "filme"))
    if tipo in (None, "serie"):
        for s in _SERIES:
            if q_low in (s.get("titulo") or "").lower():
                resultados.append(_card(s, "serie"))
    if tipo in (None, "canal"):
        for c in _CANAIS:
            if q_low in (c.get("titulo") or "").lower():
                resultados.append(_card(c, "canal"))
    return paginate(resultados, page, size)


@app.get("/categorias")
def categorias():
    cats = {"filmes": set(), "series": set(), "canais": set()}
    for f in _FILMES:
        cats["filmes"].add(f["categoria"])
    for s in _SERIES:
        cats["series"].add(s["categoria"])
    for c in _CANAIS:
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