import asyncio
import json
import secrets
import time
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import (
    FastAPI, HTTPException, Query, Request,
    UploadFile, File, Form, WebSocket, WebSocketDisconnect, Depends,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from config import (
    CACHE_TTL, BASE_DIR,
    FILMES_FILE, SERIES_FILE, CANAIS_FILE,
    META_FILE, NOVIDADES_FILE,
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PORT,
    TMDB_ENABLED, TMDB_MAX_PER_CYCLE, PUBLIC_BASE_URL,
)
from parser import fetch_filmes, fetch_series, fetch_canais
from info import fetch_movie_info, fetch_series_info
from home import build_home_async, _slugify
from proxy import (
    build_slug_map, get_by_slug, get_by_id,
    stream_proxy, stats as proxy_stats, total_slugs,
)
import tmdb

from db import (
    criar_tabelas, sincronizar, SessionLocal, get_db,
    Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Slide as SlideDB,
)
from websocket_manager import manager
from notificacoes import router as notificacoes_router

app = FastAPI(title="IPTV Organizer Pro", version="9.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
    max_age=3600,
)

app.include_router(notificacoes_router)

UPLOAD_DIR = BASE_DIR / "uploads"
for sub in ("capas", "banners", "logos"):
    (UPLOAD_DIR / sub).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


_FILMES: list = []
_SERIES: list = []
_CANAIS: list = []

_AUTO_REFRESH_TASK = None
_HOME_CACHE: dict = {}
_HOME_CACHE_TS: float = 0
_HOME_CACHE_TTL = 300

# Variável para controlar se uma sincronização forçada está rodando
_SYNC_ATIVO = False


def _stats() -> dict:
    return {
        "total_filmes": len(_FILMES),
        "total_series": len(_SERIES),
        "total_canais": len(_CANAIS),
    }


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
    build_slug_map({"filmes": _FILMES, "series": _SERIES, "canais": _CANAIS})
    print(f"[✓] Slugs gerados: {total_slugs()}")


async def fetch_and_rebuild(force: bool = False):
    global _FILMES, _SERIES, _CANAIS

    if not force and cache_valid() and load_index_from_disk():
        print("[✓] Cache válido — usando arquivos em disco.")
        _rebuild_slugs()
        sincronizar(_FILMES, _SERIES, _CANAIS)
        await manager.broadcast("catalogo_sincronizado", _stats())
        return

    print("[•] Atualizando catálogo via API Xtream...")
    t0 = time.time()
    filmes, series, canais = await asyncio.gather(
        fetch_filmes(), fetch_series(), fetch_canais(),
    )
    _FILMES = filmes
    _SERIES = series
    _CANAIS = canais
    _save_all()
    META_FILE.write_text(json.dumps({"timestamp": time.time()}))
    _rebuild_slugs()
    sincronizar(_FILMES, _SERIES, _CANAIS)
    await manager.broadcast("catalogo_sincronizado", _stats())
    print(f"[✓] Catálogo atualizado em {time.time() - t0:.2f}s · {_stats()}")


def _chave_filme(item: dict) -> str:
    return f"{(item.get('titulo') or '').strip().lower()}|{item.get('ano') or ''}"


def _chave_serie(item: dict) -> str:
    return (item.get("titulo") or "").strip().lower()


def _comparar(ant, novo, fn):
    s = {fn(i) for i in ant if fn(i)}
    return [i for i in novo if fn(i) and fn(i) not in s]


async def detectar_novidades(filmes_ant, series_ant, canais_ant) -> dict:
    fn = _comparar(filmes_ant, _FILMES, _chave_filme)
    sn = _comparar(series_ant, _SERIES, _chave_serie)
    cn = _comparar(canais_ant, _CANAIS, _chave_serie)
    lim = TMDB_MAX_PER_CYCLE
    for item in fn[:lim]:
        d = await tmdb.buscar_filme(item.get("titulo") or "", item.get("ano"))
        item["classificacao_tipo"] = "lancamento" if (d and d.get("recente")) else "catalogo"
    for item in sn[:lim]:
        d = await tmdb.buscar_serie(item.get("titulo") or "", item.get("ano"))
        item["classificacao_tipo"] = "lancamento" if (d and d.get("recente")) else "catalogo"
    return {
        "detectado_em": time.time(),
        "detectado_em_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tmdb_ativo": TMDB_ENABLED,
        "totais": {
            "filmes_novos": len(fn), "series_novas": len(sn), "canais_novos": len(cn),
            "lancamentos": len([x for x in fn + sn if x.get("classificacao_tipo") == "lancamento"]),
            "catalogo": len([x for x in fn + sn if x.get("classificacao_tipo") == "catalogo"]),
        },
        "lancamentos": [x for x in fn + sn if x.get("classificacao_tipo") == "lancamento"][:50],
        "catalogo": [x for x in fn + sn if x.get("classificacao_tipo") == "catalogo"][:50],
        "canais": cn[:50],
    }


def logar_novidades(nov: dict):
    t = nov["totais"]
    total = t["filmes_novos"] + t["series_novas"] + t["canais_novos"]
    if total == 0:
        print("[•] Nenhuma novidade.")
        return
    print("=" * 60)
    print(f"  🎉 NOVIDADES — {nov['detectado_em_str']}")
    print("=" * 60)
    print(f"  🎬 Filmes:  {t['filmes_novos']}")
    print(f"  📺 Séries:  {t['series_novas']}")
    print(f"  📡 Canais:  {t['canais_novos']}")
    print("=" * 60)


async def auto_refresh_loop():
    global _FILMES, _SERIES, _CANAIS, _HOME_CACHE_TS
    print(f"[✓] Auto-refresh ativo (a cada {CACHE_TTL}s = {CACHE_TTL // 60}min)")
    while True:
        await asyncio.sleep(CACHE_TTL)
        print(f"\n[•] Auto-refresh ({time.strftime('%H:%M:%S')})")
        try:
            fa, sa, ca = list(_FILMES), list(_SERIES), list(_CANAIS)
            filmes, series, canais = await asyncio.gather(
                fetch_filmes(), fetch_series(), fetch_canais(),
            )
            _FILMES, _SERIES, _CANAIS = filmes, series, canais
            nov = await detectar_novidades(fa, sa, ca)
            _save_all()
            META_FILE.write_text(json.dumps({"timestamp": time.time()}))
            _HOME_CACHE_TS = 0
            _rebuild_slugs()
            sincronizar(_FILMES, _SERIES, _CANAIS)
            NOVIDADES_FILE.write_text(
                json.dumps(nov, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logar_novidades(nov)
            await manager.broadcast("catalogo_sincronizado", _stats())
            if nov["totais"]["filmes_novos"] or nov["totais"]["series_novas"]:
                await manager.broadcast("novidades", nov["totais"])
        except Exception as e:
            print(f"[!] Auto-refresh falhou: {e}")


def paginate(items, page, size):
    total = len(items)
    tp = (total + size - 1) // size if size else 0
    st = (page - 1) * size
    return {
        "page": page, "page_size": size, "total_items": total,
        "total_pages": tp, "has_next": page < tp, "has_prev": page > 1,
        "items": items[st:st + size],
    }


def _card(item, tipo=None):
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
    print("[•] Startup...")
    criar_tabelas()
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


# ==========================================================
# WEBSOCKET
# ==========================================================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps({
            "evento": "conectado",
            "dados": {"stats": _stats(), "total_clientes": manager.total()},
        }, ensure_ascii=False))
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception:
        await manager.disconnect(ws)


# ==========================================================
# RAIZ E STATUS
# ==========================================================
@app.get("/")
def root():
    idx = BASE_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"service": "IPTV Organizer Pro", "version": "9.5.0"}

@app.get("/status")
async def status():
    meta = {}
    if META_FILE.exists():
        try:
            meta = json.loads(META_FILE.read_text())
        except Exception:
            pass
    idade = time.time() - meta.get("timestamp", 0) if meta else 0
    return {
        "cached": bool(meta), "age_seconds": int(idade),
        "ttl_seconds": CACHE_TTL, "valid": idade < CACHE_TTL,
        "auto_refresh_ativo": _AUTO_REFRESH_TASK is not None and not _AUTO_REFRESH_TASK.done(),
        "sync_ativo": _SYNC_ATIVO,
        "tmdb_ativo": TMDB_ENABLED,
        "storage": "3 JSONs + SQLite",
        "public_base_url": PUBLIC_BASE_URL,
        "proxy": {"slugs_carregados": total_slugs(), **proxy_stats()},
        "stats": _stats(),
        "ws_clientes": manager.total(),
    }

# ==========================================================
# FORÇAR SINCRONIZAÇÃO TOTAL (BACKGROUND)
# ==========================================================
async def _rodar_sync_bg():
    global _SYNC_ATIVO
    _SYNC_ATIVO = True
    print("\n[•] Sincronização forçada solicitada pelo painel...")
    try:
        # Força a limpeza do cache em disco e baixa tudo de novo
        if META_FILE.exists(): META_FILE.unlink()
        await fetch_and_rebuild(force=True)
        print("[✓] Sincronização forçada concluída com sucesso!")
    except Exception as e:
        print(f"[!] Erro na sincronização forçada: {e}")
    finally:
        _SYNC_ATIVO = False

@app.post("/admin/force-sync")
async def force_sync():
    if _SYNC_ATIVO:
        return {"ok": False, "msg": "Já existe uma sincronização em andamento."}
    asyncio.create_task(_rodar_sync_bg())
    return {"ok": True, "msg": "Sincronização total iniciada em segundo plano."}

@app.get("/admin/force-sync/status")
async def force_sync_status():
    return {"ativo": _SYNC_ATIVO, "stats": _stats()}


# ==========================================================
# LISTAGENS
# ==========================================================
@app.get("/home")
async def home(force: bool = False):
    global _HOME_CACHE, _HOME_CACHE_TS
    if not _FILMES and not _SERIES:
        raise HTTPException(503, "Catálogo vazio.")
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < _HOME_CACHE_TTL:
        return _HOME_CACHE
    _HOME_CACHE = await build_home_async(
        {"filmes": _FILMES, "series": _SERIES, "stats": _stats()},
        force_hero=force,
    )
    _HOME_CACHE_TS = time.time()
    return _HOME_CACHE

@app.get("/filmes")
def listar_filmes(page: int = Query(1, ge=1),
                  size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
                  categoria: str | None = None):
    filmes = _FILMES
    if categoria:
        filmes = [f for f in filmes if f["categoria"].lower() == categoria.lower()]
    resp = paginate(filmes, page, size)
    resp["items"] = [_card(it, "filme") for it in resp["items"]]
    return resp

@app.get("/series")
def listar_series(page: int = Query(1, ge=1),
                  size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
                  categoria: str | None = None):
    series = _SERIES
    if categoria:
        series = [s for s in series if s["categoria"].lower() == categoria.lower()]
    resp = paginate(series, page, size)
    resp["items"] = [_card(it, "serie") for it in resp["items"]]
    return resp

@app.get("/canais")
def listar_canais(page: int = Query(1, ge=1),
                  size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
                  categoria: str | None = None):
    canais = _CANAIS
    if categoria:
        canais = [c for c in canais if c["categoria"].lower() == categoria.lower()]
    resp = paginate(canais, page, size)
    resp["items"] = [_card(it, "canal") for it in resp["items"]]
    return resp

@app.get("/buscar")
def buscar(q: str = Query(..., min_length=1), tipo: str | None = None,
           page: int = Query(1, ge=1),
           size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)):
    ql = q.lower()
    r = []
    if tipo in (None, "filme"):
        r += [_card(f, "filme") for f in _FILMES if ql in (f.get("titulo") or "").lower()]
    if tipo in (None, "serie"):
        r += [_card(s, "serie") for s in _SERIES if ql in (s.get("titulo") or "").lower()]
    if tipo in (None, "canal"):
        r += [_card(c, "canal") for c in _CANAIS if ql in (c.get("titulo") or "").lower()]
    return paginate(r, page, size)

@app.get("/categorias")
def categorias():
    return {
        "filmes": sorted({f["categoria"] for f in _FILMES if f.get("categoria")}),
        "series": sorted({s["categoria"] for s in _SERIES if s.get("categoria")}),
        "canais": sorted({c["categoria"] for c in _CANAIS if c.get("categoria")}),
    }

@app.get("/novidades")
def novidades():
    if not NOVIDADES_FILE.exists():
        return {"disponivel": False, "mensagem": "Nenhuma detecção ainda."}
    try:
        data = json.loads(NOVIDADES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"disponivel": False, "mensagem": "Erro ao ler."}
    data["disponivel"] = True
    return data

@app.post("/novidades/verificar")
async def verificar_novidades():
    global _FILMES, _SERIES, _CANAIS
    try:
        t0 = time.time()
        fa, sa, ca = list(_FILMES), list(_SERIES), list(_CANAIS)
        filmes, series, canais = await asyncio.gather(
            fetch_filmes(), fetch_series(), fetch_canais(),
        )
        _FILMES, _SERIES, _CANAIS = filmes, series, canais
        nov = await detectar_novidades(fa, sa, ca)
        NOVIDADES_FILE.write_text(
            json.dumps(nov, ensure_ascii=False, indent=2),
            encoding="utf-8")
        logar_novidades(nov)
        await manager.broadcast("novidades", nov["totais"])
        return {"ok": True, "duracao_segundos": round(time.time() - t0, 2), "totais": nov["totais"]}
    except Exception as e:
        raise HTTPException(500, f"Erro: {e}")

# ==========================================================
# INFO E PROXY
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
# ADMIN (EDITOR E SLIDER)
# ==========================================================
@app.get("/admin/filmes")
def admin_filmes(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(FilmeDB)
    if q: query = query.filter(FilmeDB.titulo.ilike(f"%{q}%"))
    total = query.count()
    itens = query.order_by(FilmeDB.titulo).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [{"id": f.id, "iptv_id": f.iptv_id, "titulo": f.titulo, "capa": f.capa, "banner": f.banner, "logo": f.logo, "ano": f.ano, "categoria": f.categoria, "sinopse": f.sinopse, "url": f.url, "editado": f.editado} for f in itens]}

@app.get("/admin/series")
def admin_series(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(SerieDB)
    if q: query = query.filter(SerieDB.titulo.ilike(f"%{q}%"))
    total = query.count()
    itens = query.order_by(SerieDB.titulo).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [{"id": s.id, "iptv_id": s.iptv_id, "titulo": s.titulo, "capa": s.capa, "banner": s.banner, "logo": s.logo, "ano": s.ano, "categoria": s.categoria, "sinopse": s.sinopse, "editado": s.editado} for s in itens]}

@app.get("/admin/canais")
def admin_canais(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(CanalDB)
    if q: query = query.filter(CanalDB.titulo.ilike(f"%{q}%"))
    total = query.count()
    itens = query.order_by(CanalDB.titulo).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [{"id": c.id, "iptv_id": c.iptv_id, "titulo": c.titulo, "capa": c.capa, "categoria": c.categoria, "url": c.url, "editado": c.editado} for c in itens]}

@app.get("/admin/item/{tipo}/{item_id}")
def admin_item(tipo: str, item_id: int, db: Session = Depends(get_db)):
    if tipo == "filme": it = db.query(FilmeDB).get(item_id)
    elif tipo == "serie": it = db.query(SerieDB).get(item_id)
    elif tipo == "canal": it = db.query(CanalDB).get(item_id)
    else: raise HTTPException(400, "tipo inválido")
    if not it: raise HTTPException(404, "não encontrado")
    return {c.name: getattr(it, c.name) for c in it.__table__.columns}

async def _salvar_upload(arq: Optional[UploadFile], sub: str) -> Optional[str]:
    if not arq or not arq.filename: return None
    ext = Path(arq.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"): raise HTTPException(400, "Extensão inválida")
    nome = f"{secrets.token_hex(8)}{ext}"
    destino = UPLOAD_DIR / sub / nome
    with destino.open("wb") as f:
        while chunk := await arq.read(1024 * 1024): f.write(chunk)
    return f"/uploads/{sub}/{nome}"

@app.put("/admin/filme/{item_id}")
async def editar_filme(item_id: int, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), url: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    it = db.query(FilmeDB).get(item_id)
    if not it: raise HTTPException(404, "Filme não encontrado")
    if titulo is not None: it.titulo = titulo
    if ano is not None: it.ano = ano
    if categoria is not None: it.categoria = categoria
    if sinopse is not None: it.sinopse = sinopse
    if url is not None: it.url = url
    if capa: it.capa = await _salvar_upload(capa, "capas")
    if banner: it.banner = await _salvar_upload(banner, "banners")
    if logo: it.logo = await _salvar_upload(logo, "logos")
    it.editado = True
    db.commit(); db.refresh(it)
    await manager.broadcast("item_editado", {"tipo": "filme", "id": it.id, "titulo": it.titulo})
    return {"ok": True, "id": it.id, "titulo": it.titulo}

@app.put("/admin/serie/{item_id}")
async def editar_serie(item_id: int, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    it = db.query(SerieDB).get(item_id)
    if not it: raise HTTPException(404, "Série não encontrada")
    if titulo is not None: it.titulo = titulo
    if ano is not None: it.ano = ano
    if categoria is not None: it.categoria = categoria
    if sinopse is not None: it.sinopse = sinopse
    if capa: it.capa = await _salvar_upload(capa, "capas")
    if banner: it.banner = await _salvar_upload(banner, "banners")
    if logo: it.logo = await _salvar_upload(logo, "logos")
    it.editado = True
    db.commit(); db.refresh(it)
    await manager.broadcast("item_editado", {"tipo": "serie", "id": it.id, "titulo": it.titulo})
    return {"ok": True, "id": it.id, "titulo": it.titulo}

@app.put("/admin/canal/{item_id}")
async def editar_canal(item_id: int, titulo: Optional[str] = Form(None), categoria: Optional[str] = Form(None), url: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    it = db.query(CanalDB).get(item_id)
    if not it: raise HTTPException(404, "Canal não encontrado")
    if titulo is not None: it.titulo = titulo
    if categoria is not None: it.categoria = categoria
    if url is not None: it.url = url
    if capa: it.capa = await _salvar_upload(capa, "capas")
    it.editado = True
    db.commit(); db.refresh(it)
    await manager.broadcast("item_editado", {"tipo": "canal", "id": it.id, "titulo": it.titulo})
    return {"ok": True, "id": it.id, "titulo": it.titulo}

@app.get("/admin/slider")
def admin_slider_listar(db: Session = Depends(get_db)):
    slides = db.query(SlideDB).order_by(SlideDB.ordem).all()
    out = []
    for s in slides:
        ref = None
        if s.ref_tipo == "filme":
            r = db.query(FilmeDB).get(s.ref_id)
            ref = {"titulo": r.titulo, "capa": r.capa, "ano": r.ano} if r else None
        elif s.ref_tipo == "serie":
            r = db.query(SerieDB).get(s.ref_id)
            ref = {"titulo": r.titulo, "capa": r.capa, "ano": r.ano} if r else None
        out.append({"id": s.id, "ordem": s.ordem, "ref_tipo": s.ref_tipo, "ref_id": s.ref_id, "titulo": s.titulo or (ref["titulo"] if ref else "?"), "banner": s.banner or (ref.get("capa") if ref else None), "logo": s.logo, "sinopse": s.sinopse, "ref": ref, "ativo": s.ativo})
    return {"total": len(out), "items": out}

@app.post("/admin/slider")
async def admin_slider_adicionar(ref_tipo: str = Form(...), ref_id: int = Form(...), db: Session = Depends(get_db)):
    global _HOME_CACHE_TS
    if ref_tipo not in ("filme", "serie"): raise HTTPException(400, "inválido")
    ultimo = db.query(SlideDB).order_by(SlideDB.ordem.desc()).first()
    proxima = (ultimo.ordem + 1) if ultimo else 0
    novo = SlideDB(ordem=proxima, ref_tipo=ref_tipo, ref_id=ref_id, ativo=True)
    db.add(novo); db.commit(); db.refresh(novo)
    _HOME_CACHE_TS = 0
    await manager.broadcast("slider_atualizado", {"acao": "adicionar", "id": novo.id})
    return {"ok": True, "id": novo.id}

@app.put("/admin/slider/{slide_id}")
async def admin_slider_editar(slide_id: int, titulo: Optional[str] = Form(None), banner: Optional[str] = Form(None), logo: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), db: Session = Depends(get_db)):
    global _HOME_CACHE_TS
    s = db.query(SlideDB).get(slide_id)
    if not s: raise HTTPException(404, "não encontrado")
    if titulo is not None: s.titulo = titulo or None
    if banner is not None: s.banner = banner or None
    if logo is not None: s.logo = logo or None
    if sinopse is not None: s.sinopse = sinopse or None
    db.commit(); _HOME_CACHE_TS = 0
    await manager.broadcast("slider_atualizado", {"acao": "editar", "id": slide_id})
    return {"ok": True}

@app.delete("/admin/slider/{slide_id}")
async def admin_slider_remover(slide_id: int, db: Session = Depends(get_db)):
    global _HOME_CACHE_TS
    s = db.query(SlideDB).get(slide_id)
    if not s: raise HTTPException(404, "não encontrado")
    db.delete(s); db.commit(); _HOME_CACHE_TS = 0
    await manager.broadcast("slider_atualizado", {"acao": "remover", "id": slide_id})
    return {"ok": True}

@app.post("/admin/slider/reordenar")
async def admin_slider_reordenar(ordem: str = Form(...), db: Session = Depends(get_db)):
    global _HOME_CACHE_TS
    try: ids = [int(x.strip()) for x in ordem.split(",") if x.strip()]
    except: raise HTTPException(400, "Formato inválido")
    for i, sid in enumerate(ids):
        s = db.query(SlideDB).get(sid)
        if s: s.ordem = i
    db.commit(); _HOME_CACHE_TS = 0
    await manager.broadcast("slider_atualizado", {"acao": "reordenar"})
    return {"ok": True}

# ==========================================================
# RESOLUÇÃO E PROXY
# ==========================================================
_EPISODIO_RE = re.compile(r'^(.+?)-t(\d+)-e(\d+)$')

async def _resolver_episodio_sob_demanda(slug: str):
    m = _EPISODIO_RE.match(slug)
    if not m: return None
    base = m.group(1)
    serie_encontrada = None
    for s in _SERIES:
        if _slugify(s.get("titulo") or "") == base:
            serie_encontrada = s; break
    if not serie_encontrada: return None
    serie_id = str(serie_encontrada.get("id") or "")
    if not serie_id: return None
    try: await fetch_series_info(serie_id)
    except: return None
    return get_by_slug(slug)

@app.get("/stream/id/{item_id}")
async def proxy_por_id(item_id: str, request: Request):
    cid = item_id
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov"):
        if cid.lower().endswith(ext): cid = cid[:-len(ext)]; break
    item = get_by_id(cid)
    if not item: raise HTTPException(404, "ID não encontrado.")
    return await stream_proxy(item["url"], request)

@app.get("/stream/{slug_path:path}")
async def proxy_por_slug(slug_path: str, request: Request):
    slug = slug_path
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov", ".flv", ".webm"):
        if slug.lower().endswith(ext): slug = slug[:-len(ext)]; break
    item = get_by_slug(slug)
    if not item: item = await _resolver_episodio_sob_demanda(slug)
    if not item: raise HTTPException(404, "Slug não encontrado.")
    return await stream_proxy(item["url"], request)

@app.get("/proxy/stats")
def proxy_monitor():
    return {"slugs_carregados": total_slugs(), **proxy_stats()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)