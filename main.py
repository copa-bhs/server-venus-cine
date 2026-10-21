import asyncio
import json
import secrets
import time
import re
import unicodedata
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
from pydantic import BaseModel

from config import (
    CACHE_TTL, BASE_DIR, CACHE_DIR, FILMES_FILE, SERIES_FILE, CANAIS_FILE,
    META_FILE, NOVIDADES_FILE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PORT,
    TMDB_ENABLED, TMDB_MAX_PER_CYCLE, PUBLIC_BASE_URL, IPTV_HOST, IPTV_USERNAME, IPTV_PASSWORD
)
from parser import _clean_name, _extract_year, _clean_category
from info import fetch_movie_info, fetch_series_info
from home import build_home_async, _slugify
from proxy import (
    build_slug_map, get_by_slug, get_by_id, stream_proxy, stats as proxy_stats, 
    total_slugs, limpar_tudo as limpar_proxy, _SLUG_MAP, _ID_MAP
)
import tmdb
from db import (
    criar_tabelas, sincronizar, SessionLocal, get_db, engine, Base,
    Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Slide as SlideDB, Episodio as EpisodioDB
)
from websocket_manager import manager
from notificacoes import router as notificacoes_router

app = FastAPI(title="IPTV Organizer Pro", version="12.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(notificacoes_router)

UPLOAD_DIR = BASE_DIR / "uploads"
for sub in ("capas", "banners", "logos"):
    (UPLOAD_DIR / sub).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

_FILMES, _SERIES, _CANAIS = [], [], []
_HOME_CACHE, _HOME_CACHE_TS = {}, 0.0
_SYNC_ATIVO, _SYNC_LOGS = False, []
_IMPORT_ATIVO, _IMPORT_LOGS = False, []

def _stats() -> dict: return {"total_filmes": len(_FILMES), "total_series": len(_SERIES), "total_canais": len(_CANAIS)}
def _load_json(path, default=None):
    if not path.exists(): return default if default is not None else []
    try: return json.loads(path.read_text(encoding="utf-8"))
    except: return default if default is not None else []

def load_index_from_disk() -> bool:
    global _FILMES, _SERIES, _CANAIS
    if not (FILMES_FILE.exists() and SERIES_FILE.exists() and CANAIS_FILE.exists()): return False
    try:
        _FILMES = _load_json(FILMES_FILE); _SERIES = _load_json(SERIES_FILE); _CANAIS = _load_json(CANAIS_FILE)
        return True
    except: return False

def _save_all():
    FILMES_FILE.write_text(json.dumps(_FILMES, ensure_ascii=False), encoding="utf-8")
    SERIES_FILE.write_text(json.dumps(_SERIES, ensure_ascii=False), encoding="utf-8")
    CANAIS_FILE.write_text(json.dumps(_CANAIS, ensure_ascii=False), encoding="utf-8")

def _rebuild_slugs():
    build_slug_map({"filmes": _FILMES, "series": _SERIES, "canais": _CANAIS})

def paginate(items, page, size):
    total = len(items); tp = (total + size - 1) // size if size else 0; st = (page - 1) * size
    return {"page": page, "page_size": size, "total_items": total, "total_pages": tp, "has_next": page < tp, "has_prev": page > 1, "items": items[st:st + size]}

def _normalizar_busca(texto: str) -> str:
    if not texto: return ""
    t = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii').lower()
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    t = re.sub(r'\b(the|os|as|o|a|um|uma|de|do|da)\b', ' ', t)
    return " ".join(t.split())

def _card_app(item, tipo=None):
    t = tipo or item.get("tipo") or "filme"
    tipo_app = "movie" if t == "filme" else ("series" if t == "serie" else "channel")
    return {
        "id": str(item.get("id")), "title": item.get("titulo") or "",
        "poster": item.get("capa"), "type": tipo_app, "year": item.get("ano"),
        "slug": _slugify(item.get("titulo") or "", item.get("ano")),
        "titulo": item.get("titulo"), "capa": item.get("capa"), "tipo": t, "ano": item.get("ano"),
        "added_by_name": item.get("added_by_name"), "added_by_uid": item.get("added_by_uid")
    }

@app.on_event("startup")
async def startup():
    criar_tabelas()
    if load_index_from_disk():
        _rebuild_slugs()
        sincronizar(_FILMES, _SERIES, _CANAIS)
        print(f"[✓] Carregado e Sincronizado: {_stats()}")

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps({"evento": "conectado", "dados": {"stats": _stats()}}))
        while True:
            data = await ws.receive_text()
            if data == "ping": await ws.send_text("pong")
    except WebSocketDisconnect: await manager.disconnect(ws)
    except Exception: await manager.disconnect(ws)

@app.get("/")
def root():
    idx = BASE_DIR / "index.html"
    if idx.exists(): return FileResponse(idx)
    return {"service": "IPTV Organizer Pro"}

@app.get("/status")
async def status():
    return {"stats": _stats(), "sync_ativo": _SYNC_ATIVO, "proxy": {"slugs_carregados": total_slugs(), **proxy_stats()}}

@app.get("/home")
async def home(force: bool = False):
    global _HOME_CACHE, _HOME_CACHE_TS
    if not _FILMES and not _SERIES: return {"hero": [], "colecoes": [], "trailers": [], "stats": _stats()}
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < 300: return _HOME_CACHE
    _HOME_CACHE = await build_home_async({"filmes": _FILMES, "series": _SERIES, "stats": _stats()}, force_hero=force)
    _HOME_CACHE_TS = time.time()
    return _HOME_CACHE

@app.get("/filmes")
def listar_filmes(page: int = Query(1), size: int = Query(30), categoria: str | None = None):
    filmes = _FILMES
    if categoria: filmes = [f for f in filmes if f["categoria"].lower() == categoria.lower()]
    resp = paginate(filmes, page, size); resp["items"] = [_card_app(it, "filme") for it in resp["items"]]; return resp

@app.get("/series")
def listar_series(page: int = Query(1), size: int = Query(30), categoria: str | None = None):
    series = _SERIES
    if categoria: series = [s for s in series if s["categoria"].lower() == categoria.lower()]
    resp = paginate(series, page, size); resp["items"] = [_card_app(it, "serie") for it in resp["items"]]; return resp

@app.get("/canais")
def listar_canais(page: int = Query(1), size: int = Query(50), categoria: str | None = None):
    canais = _CANAIS
    if categoria: canais = [c for c in canais if c["categoria"].lower() == categoria.lower()]
    resp = paginate(canais, page, size); resp["items"] = [_card_app(it, "canal") for it in resp["items"]]; return resp

@app.get("/buscar")
def buscar(q: str, tipo: str | None = None, page: int = Query(1), size: int = Query(30)):
    q_raw = q.lower().strip(); q_norm = _normalizar_busca(q); resultados = []
    def match(item):
        tit = item.get("titulo") or ""
        return q_raw in tit.lower() or (q_norm and q_norm in _normalizar_busca(tit))
    if tipo in (None, "filme", "movie"): resultados += [_card_app(f, "filme") for f in _FILMES if match(f)]
    if tipo in (None, "serie", "series"): resultados += [_card_app(s, "serie") for s in _SERIES if match(s)]
    if tipo in (None, "canal", "channel"): resultados += [_card_app(c, "canal") for c in _CANAIS if match(c)]
    return paginate(resultados, page, size)

@app.get("/categorias")
def categorias():
    return {"filmes": sorted({f["categoria"] for f in _FILMES if f.get("categoria")}), "series": sorted({s["categoria"] for s in _SERIES if s.get("categoria")}), "canais": sorted({c["categoria"] for c in _CANAIS if c.get("categoria")})}

@app.get("/info/filme/{filme_id}")
async def info_filme(filme_id: str): return await fetch_movie_info(filme_id)

@app.get("/info/serie/{serie_id}")
async def info_serie(serie_id: str): return await fetch_series_info(serie_id)

# ==========================================================
# UPLOAD MANUAL (NOVO)
# ==========================================================
async def _salvar_upload(arq: Optional[UploadFile], sub: str) -> Optional[str]:
    if not arq or not arq.filename: return None
    ext = Path(arq.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"): raise HTTPException(400, "inválido")
    nome = f"{secrets.token_hex(8)}{ext}"; destino = UPLOAD_DIR / sub / nome
    with destino.open("wb") as f:
        while chunk := await arq.read(1024 * 1024): f.write(chunk)
    return f"/uploads/{sub}/{nome}"

@app.post("/admin/manual/filme")
async def upload_manual_filme(
    titulo: str = Form(...), ano: Optional[int] = Form(None), categoria: str = Form("Manual"),
    sinopse: Optional[str] = Form(None), url: str = Form(...),
    capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    global _FILMES, _FILMES
    manual_id = f"m_{secrets.token_hex(4)}"
    capa_url = await _salvar_upload(capa, "capas")
    banner_url = await _salvar_upload(banner, "banners")
    logo_url = await _salvar_upload(logo, "logos")

    novo_db = FilmeDB(iptv_id=manual_id, titulo=titulo, ano=ano, categoria=categoria, sinopse=sinopse, url=url, capa=capa_url, banner=banner_url, logo=logo_url)
    db.add(novo_db); db.commit(); db.refresh(novo_db)

    _FILMES.append({"id": manual_id, "tipo": "filme", "titulo": titulo, "capa": capa_url, "ano": ano, "categoria": categoria, "url": url})
    _rebuild_slugs(); _save_all()
    await manager.broadcast("catalogo_sincronizado", _stats())
    return {"ok": True, "id": manual_id}

@app.post("/admin/manual/serie")
async def upload_manual_serie(
    titulo: str = Form(...), ano: Optional[int] = Form(None), categoria: str = Form("Manual"),
    sinopse: Optional[str] = Form(None),
    capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    global _SERIES
    manual_id = f"m_{secrets.token_hex(4)}"
    capa_url = await _salvar_upload(capa, "capas")
    banner_url = await _salvar_upload(banner, "banners")
    logo_url = await _salvar_upload(logo, "logos")

    nova_db = SerieDB(iptv_id=manual_id, titulo=titulo, ano=ano, categoria=categoria, sinopse=sinopse, capa=capa_url, banner=banner_url, logo=logo_url)
    db.add(nova_db); db.commit(); db.refresh(nova_db)

    _SERIES.append({"id": manual_id, "tipo": "serie", "titulo": titulo, "capa": capa_url, "ano": ano, "categoria": categoria})
    _rebuild_slugs(); _save_all()
    await manager.broadcast("catalogo_sincronizado", _stats())
    return {"ok": True, "id": manual_id, "db_id": nova_db.id}

@app.post("/admin/manual/serie/{db_id}/episodio")
async def upload_manual_episodio(
    db_id: int, temporada: int = Form(...), episodio: int = Form(...), titulo: str = Form(...), url: str = Form(...),
    db: Session = Depends(get_db)
):
    ep = EpisodioDB(serie_id=db_id, temporada=temporada, episodio=episodio, titulo=titulo, url=url)
    db.add(ep); db.commit()
    
    # Atualiza o proxy imediatamente para o episódio funcionar
    serie = db.query(SerieDB).get(db_id)
    if serie:
        slug = _slugify(serie.titulo) + f"-t{temporada:02d}-e{episodio:02d}"
        register_item(str(ep.id), url, titulo, "serie", slug)
        
    return {"ok": True}

# ==========================================================
# GERENCIAMENTO ADMIN
# ==========================================================
@app.get("/admin/filmes")
def admin_filmes(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    filmes = _FILMES
    if q: filmes = [f for f in filmes if q.lower() in (f.get("titulo") or "").lower()]
    resp = paginate(filmes, page, size)
    itens_db = {f.iptv_id: f.id for f in db.query(FilmeDB).all()}
    for it in resp["items"]: it["db_id"] = itens_db.get(str(it.get("id")), 0)
    return resp

@app.get("/admin/series")
def admin_series(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    series = _SERIES
    if q: series = [s for s in series if q.lower() in (s.get("titulo") or "").lower()]
    resp = paginate(series, page, size)
    itens_db = {s.iptv_id: s.id for s in db.query(SerieDB).all()}
    for it in resp["items"]: it["db_id"] = itens_db.get(str(it.get("id")), 0)
    return resp

@app.get("/admin/canais")
def admin_canais(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    canais = _CANAIS
    if q: canais = [c for c in canais if q.lower() in (c.get("titulo") or "").lower()]
    resp = paginate(canais, page, size)
    itens_db = {c.iptv_id: c.id for c in db.query(CanalDB).all()}
    for it in resp["items"]: it["db_id"] = itens_db.get(str(it.get("id")), 0)
    return resp

@app.get("/admin/item/{tipo}/{item_id}")
def admin_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    if tipo == "filme": it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    elif tipo == "serie": it = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    elif tipo == "canal": it = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()
    else: raise HTTPException(400, "inválido")
    if it: return {c.name: getattr(it, c.name) for c in it.__table__.columns}
    lista = _FILMES if tipo == "filme" else (_SERIES if tipo == "serie" else _CANAIS)
    mem_item = next((x for x in lista if str(x.get("id")) == str(item_id)), None)
    if not mem_item: raise HTTPException(404, "Item não encontrado")
    return mem_item

@app.delete("/admin/item/{tipo}/{item_id}")
def deletar_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    global _FILMES, _SERIES, _CANAIS
    if tipo == "filme":
        it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
        if it: db.delete(it); db.commit()
        _FILMES[:] = [f for f in _FILMES if str(f.get("id")) != str(item_id)]
    elif tipo == "serie":
        it = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
        if it: db.delete(it); db.commit()
        _SERIES[:] = [s for s in _SERIES if str(s.get("id")) != str(item_id)]
    elif tipo == "canal":
        it = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()
        if it: db.delete(it); db.commit()
        _CANAIS[:] = [c for c in _CANAIS if str(c.get("id")) != str(item_id)]
    _rebuild_slugs(); _save_all()
    return {"ok": True}

def _upsert_db_item(db, tipo, item_id, form_data, uploads):
    global _FILMES, _SERIES, _CANAIS
    obj = None
    if tipo == "filme": obj = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    elif tipo == "serie": obj = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    elif tipo == "canal": obj = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()

    if not obj:
        if tipo == "filme": obj = FilmeDB(iptv_id=item_id, url="")
        elif tipo == "serie": obj = SerieDB(iptv_id=item_id)
        elif tipo == "canal": obj = CanalDB(iptv_id=item_id, url="")
        db.add(obj)

    for k, v in form_data.items():
        if v is not None: setattr(obj, k, v)
    for k, v in uploads.items():
        if v: setattr(obj, k, v)
    
    if hasattr(obj, 'editado'): obj.editado = True
    db.commit(); db.refresh(obj)
    
    lista = _FILMES if tipo == "filme" else (_SERIES if tipo == "serie" else _CANAIS)
    for it in lista:
        if str(it.get("id")) == str(item_id):
            for k, v in form_data.items():
                if v is not None: it[k] = v
            for k, v in uploads.items():
                if v: it[k] = v
            break
    _save_all(); _rebuild_slugs()
    return obj

@app.put("/admin/filme/{item_id}")
async def editar_filme(item_id: str, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), url: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    uploads = {"capa": await _salvar_upload(capa, "capas"), "banner": await _salvar_upload(banner, "banners"), "logo": await _salvar_upload(logo, "logos")}
    form = {"titulo": titulo, "ano": ano, "categoria": categoria, "sinopse": sinopse, "url": url}
    obj = _upsert_db_item(db, "filme", item_id, form, uploads)
    await manager.broadcast("item_editado", {"tipo": "filme", "id": obj.id, "titulo": obj.titulo})
    return {"ok": True, "id": obj.id, "titulo": obj.titulo}

@app.put("/admin/serie/{item_id}")
async def editar_serie(item_id: str, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    uploads = {"capa": await _salvar_upload(capa, "capas"), "banner": await _salvar_upload(banner, "banners"), "logo": await _salvar_upload(logo, "logos")}
    form = {"titulo": titulo, "ano": ano, "categoria": categoria, "sinopse": sinopse}
    obj = _upsert_db_item(db, "serie", item_id, form, uploads)
    await manager.broadcast("item_editado", {"tipo": "serie", "id": obj.id, "titulo": obj.titulo})
    return {"ok": True, "id": obj.id, "titulo": obj.titulo}

@app.put("/admin/canal/{item_id}")
async def editar_canal(item_id: str, titulo: Optional[str] = Form(None), categoria: Optional[str] = Form(None), url: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    uploads = {"capa": await _salvar_upload(capa, "capas")}
    form = {"titulo": titulo, "categoria": categoria, "url": url}
    obj = _upsert_db_item(db, "canal", item_id, form, uploads)
    await manager.broadcast("item_editado", {"tipo": "canal", "id": obj.id, "titulo": obj.titulo})
    return {"ok": True, "id": obj.id, "titulo": obj.titulo}

# ==========================================================
# IPTV BUSCA E IMPORTAÇÃO
# ==========================================================
class IPTVAddPayload(BaseModel):
    id: str; tipo: str; titulo: str; capa: Optional[str] = None; ano: Optional[int] = None; url: Optional[str] = None
    added_by_name: Optional[str] = None; added_by_uid: Optional[str] = None

class IPTVLotePayload(BaseModel):
    items: list[IPTVAddPayload]

@app.get("/admin/iptv/buscar")
async def admin_iptv_buscar(q: str, tipo: str = "filme", db: Session = Depends(get_db)):
    q_norm = _normalizar_busca(q)
    action = "get_vod_streams" if tipo == "filme" else "get_series"
    async with httpx.AsyncClient(timeout=60) as client:
        try: data = (await client.get(f"{IPTV_HOST}/player_api.php?username={IPTV_USERNAME}&password={IPTV_PASSWORD}&action={action}")).json()
        except: raise HTTPException(500, "Erro provedor.")
    if not isinstance(data, list): raise HTTPException(500, "Formato inválido.")
    existentes = {f.iptv_id for f in db.query(FilmeDB).all()} if tipo == "filme" else {s.iptv_id for s in db.query(SerieDB).all()}
    resultados = []
    for item in data:
        if not isinstance(item, dict): continue
        sid = str(item.get("stream_id") or item.get("series_id") or "")
        nome = item.get("name") or ""
        if not sid or q_norm not in _normalizar_busca(nome): continue
        resultados.append({
            "id": sid, "titulo": _clean_name(nome), "capa": item.get("stream_icon") or item.get("cover"),
            "ano": _extract_year(nome), "tipo": tipo, "ja_existe": sid in existentes,
            "url": f"{IPTV_HOST}/movie/{IPTV_USERNAME}/{IPTV_PASSWORD}/{sid}.mp4" if tipo == "filme" else ""
        })
    return {"items": resultados[:200]}

@app.post("/admin/iptv/adicionar")
async def admin_iptv_adicionar(payload: IPTVAddPayload, db: Session = Depends(get_db)):
    global _FILMES, _SERIES
    if payload.tipo == "filme":
        if not db.query(FilmeDB).filter(FilmeDB.iptv_id == payload.id).first():
            db.add(FilmeDB(iptv_id=payload.id, titulo=payload.titulo, capa=payload.capa, ano=payload.ano, categoria="Manual", url=payload.url or "", added_by_name=payload.added_by_name, added_by_uid=payload.added_by_uid))
            db.commit()
            _FILMES.append({"id": payload.id, "tipo": "filme", "titulo": payload.titulo, "capa": payload.capa, "ano": payload.ano, "categoria": "Manual", "url": payload.url, "added_by_name": payload.added_by_name, "added_by_uid": payload.added_by_uid})
    elif payload.tipo == "serie":
        if not db.query(SerieDB).filter(SerieDB.iptv_id == payload.id).first():
            db.add(SerieDB(iptv_id=payload.id, titulo=payload.titulo, capa=payload.capa, ano=payload.ano, categoria="Manual", added_by_name=payload.added_by_name, added_by_uid=payload.added_by_uid))
            db.commit()
            _SERIES.append({"id": payload.id, "tipo": "serie", "titulo": payload.titulo, "capa": payload.capa, "ano": payload.ano, "categoria": "Manual", "added_by_name": payload.added_by_name, "added_by_uid": payload.added_by_uid})
    _rebuild_slugs(); _save_all()
    await manager.broadcast("catalogo_sincronizado", _stats())
    return {"ok": True}

@app.post("/admin/iptv/adicionar-lote")
async def admin_iptv_adicionar_lote(payload: IPTVLotePayload, db: Session = Depends(get_db)):
    global _FILMES, _SERIES
    adicionados = 0
    for item in payload.items:
        if item.tipo == "filme" and not db.query(FilmeDB).filter(FilmeDB.iptv_id == item.id).first():
            db.add(FilmeDB(iptv_id=item.id, titulo=item.titulo, capa=item.capa, ano=item.ano, categoria="Manual", url=item.url or "", added_by_name=item.added_by_name, added_by_uid=item.added_by_uid))
            _FILMES.append({"id": item.id, "tipo": "filme", "titulo": item.titulo, "capa": item.capa, "ano": item.ano, "categoria": "Manual", "url": item.url, "added_by_name": item.added_by_name, "added_by_uid": item.added_by_uid})
            adicionados += 1
        elif item.tipo == "serie" and not db.query(SerieDB).filter(SerieDB.iptv_id == item.id).first():
            db.add(SerieDB(iptv_id=item.id, titulo=item.titulo, capa=item.capa, ano=item.ano, categoria="Manual", added_by_name=item.added_by_name, added_by_uid=item.added_by_uid))
            _SERIES.append({"id": item.id, "tipo": "serie", "titulo": item.titulo, "capa": item.capa, "ano": item.ano, "categoria": "Manual", "added_by_name": item.added_by_name, "added_by_uid": item.added_by_uid})
            adicionados += 1
    db.commit(); _rebuild_slugs(); _save_all()
    await manager.broadcast("catalogo_sincronizado", _stats())
    return {"ok": True, "adicionados": adicionados}

# ==========================================================
# IMPORTAÇÃO LOTE BG
# ==========================================================
async def _importar_lote_bg(tipo: str, quantidade: int):
    global _IMPORT_ATIVO, _IMPORT_LOGS, _FILMES, _SERIES, _CANAIS
    _IMPORT_ATIVO = True
    _IMPORT_LOGS = [f"[•] Iniciando importação de até {quantidade if quantidade > 0 else 'TODOS os'} {tipo}(s)..."]
    db = SessionLocal()
    try:
        action = "get_vod_streams" if tipo == "filme" else ("get_series" if tipo == "serie" else "get_live_streams")
        cat_action = "get_vod_categories" if tipo == "filme" else ("get_series_categories" if tipo == "serie" else "get_live_categories")
        
        url = f"{IPTV_HOST}/player_api.php?username={IPTV_USERNAME}&password={IPTV_PASSWORD}&action={action}"
        cat_url = f"{IPTV_HOST}/player_api.php?username={IPTV_USERNAME}&password={IPTV_PASSWORD}&action={cat_action}"
        
        _IMPORT_LOGS.append(f"[•] Baixando lista do provedor...")
        async with httpx.AsyncClient(timeout=120) as client:
            r_data, r_cat = await asyncio.gather(client.get(url), client.get(cat_url))
            data = r_data.json()
            cats = r_cat.json()
            
        if not isinstance(data, list):
            _IMPORT_LOGS.append("[!] Erro: Provedor retornou formato inválido.")
            return

        cat_map = {str(c.get("category_id")): _clean_category(c.get("category_name") or "Geral") for c in cats if isinstance(c, dict)} if isinstance(cats, list) else {}

        _IMPORT_LOGS.append(f"[✓] Lista baixada. Total no provedor: {len(data)}")

        if tipo == "filme":
            existentes = {f.iptv_id for f in db.query(FilmeDB).all() if f.iptv_id}
        elif tipo == "serie":
            existentes = {s.iptv_id for s in db.query(SerieDB).all() if s.iptv_id}
        else:
            existentes = {c.iptv_id for c in db.query(CanalDB).all() if c.iptv_id}

        adicionados = 0
        _IMPORT_LOGS.append(f"[•] Filtrando e inserindo...")
        
        for item in data:
            if not isinstance(item, dict): continue
            if quantidade > 0 and adicionados >= quantidade:
                break
                
            if tipo == "filme":
                sid = str(item.get("stream_id") or "")
                if not sid or sid in existentes: continue
                nome = _clean_name(item.get("name") or "")
                capa = item.get("stream_icon") or item.get("cover")
                ano = _extract_year(item.get("name"))
                cat = cat_map.get(str(item.get("category_id") or ""), "Geral")
                ext = item.get("container_extension") or "mp4"
                stream_url = f"{IPTV_HOST}/movie/{IPTV_USERNAME}/{IPTV_PASSWORD}/{sid}.{ext}"
                
                db.add(FilmeDB(iptv_id=sid, titulo=nome, capa=capa, ano=ano, categoria=cat, url=stream_url))
                _FILMES.append({"id": sid, "tipo": "filme", "titulo": nome, "capa": capa, "ano": ano, "categoria": cat, "url": stream_url})
                
            elif tipo == "serie":
                sid = str(item.get("series_id") or "")
                if not sid or sid in existentes: continue
                nome = _clean_name(item.get("name") or "")
                capa = item.get("cover") or item.get("stream_icon")
                ano = _extract_year(item.get("releaseDate")) or _extract_year(item.get("name"))
                cat = cat_map.get(str(item.get("category_id") or ""), "Geral")
                
                db.add(SerieDB(iptv_id=sid, titulo=nome, capa=capa, ano=ano, categoria=cat))
                _SERIES.append({"id": sid, "tipo": "serie", "titulo": nome, "capa": capa, "ano": ano, "categoria": cat})
                
            else:
                sid = str(item.get("stream_id") or "")
                if not sid or sid in existentes: continue
                nome = _clean_name(item.get("name") or "")
                capa = item.get("stream_icon")
                cat = cat_map.get(str(item.get("category_id") or ""), "Geral")
                stream_url = f"{IPTV_HOST}/live/{IPTV_USERNAME}/{IPTV_PASSWORD}/{sid}.ts"
                
                db.add(CanalDB(iptv_id=sid, titulo=nome, capa=capa, categoria=cat, url=stream_url))
                _CANAIS.append({"id": sid, "tipo": "canal", "titulo": nome, "capa": capa, "categoria": cat, "url": stream_url})
                
            adicionados += 1
            existentes.add(sid)
            
            if adicionados % 100 == 0:
                _IMPORT_LOGS.append(f"[•] {adicionados} adicionados...")
                db.commit()

        db.commit()
        _rebuild_slugs()
        _save_all()
        
        _IMPORT_LOGS.append(f"[✓] Importação concluída! {adicionados} novos itens adicionados.")
        await manager.broadcast("catalogo_sincronizado", _stats())
        
    except Exception as e:
        _IMPORT_LOGS.append(f"[!] Erro: {e}")
        print(f"[!] Erro importação lote: {e}")
    finally:
        db.close()
        _IMPORT_ATIVO = False

@app.post("/admin/iptv/importar-lote-bg")
async def importar_lote_bg(tipo: str = Query(...), quantidade: int = Query(...)):
    global _IMPORT_ATIVO
    if _IMPORT_ATIVO: return {"ok": False, "msg": "Já existe uma importação em andamento."}
    asyncio.create_task(_importar_lote_bg(tipo, quantidade))
    return {"ok": True, "msg": "Importação iniciada."}

@app.get("/admin/iptv/importar-lote-bg/status")
async def importar_lote_bg_status():
    return {"ativo": _IMPORT_ATIVO, "logs": _IMPORT_LOGS, "stats": _stats()}

# ==========================================================
# LIMPEZA
# ==========================================================
async def _wipe_all_bg():
    global _SYNC_ATIVO, _FILMES, _SERIES, _CANAIS
    _SYNC_ATIVO = True
    for f in [FILMES_FILE, SERIES_FILE, CANAIS_FILE, META_FILE, NOVIDADES_FILE]:
        if f.exists(): f.unlink()
    for f in CACHE_DIR.glob("slug_map_*.json"): f.unlink()
    if (CACHE_DIR / "hero_cache.json").exists(): (CACHE_DIR / "hero_cache.json").unlink()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _FILMES.clear(); _SERIES.clear(); _CANAIS.clear(); limpar_proxy()
    await manager.broadcast("catalogo_sincronizado", _stats())
    _SYNC_ATIVO = False

@app.post("/admin/wipe-all")
async def wipe_all():
    if _SYNC_ATIVO: return {"ok": False, "msg": "Aguarde."}
    asyncio.create_task(_wipe_all_bg())
    return {"ok": True}

@app.get("/admin/force-sync/status")
async def force_sync_status():
    return {"ativo": _SYNC_ATIVO, "stats": _stats(), "logs": _SYNC_LOGS}

# ==========================================================
# SLIDER
# ==========================================================
@app.get("/admin/slider")
def admin_slider_listar(db: Session = Depends(get_db)):
    slides = db.query(SlideDB).order_by(SlideDB.ordem).all(); out = []
    for s in slides:
        ref = None
        if s.ref_tipo == "filme":
            r = db.query(FilmeDB).get(s.ref_id); ref = {"titulo": r.titulo, "capa": r.capa, "ano": r.ano} if r else None
        elif s.ref_tipo == "serie":
            r = db.query(SerieDB).get(s.ref_id); ref = {"titulo": r.titulo, "capa": r.capa, "ano": r.ano} if r else None
        out.append({"id": s.id, "ordem": s.ordem, "ref_tipo": s.ref_tipo, "ref_id": s.ref_id, "titulo": s.titulo or (ref["titulo"] if ref else "?"), "banner": s.banner or (ref.get("capa") if ref else None), "logo": s.logo, "sinopse": s.sinopse, "ref": ref, "ativo": s.ativo})
    return {"total": len(out), "items": out}

@app.post("/admin/slider")
async def admin_slider_adicionar(ref_tipo: str = Form(...), ref_id: str = Form(...), db: Session = Depends(get_db)):
    global _HOME_CACHE_TS, _FILMES, _SERIES
    item_db = None
    if ref_tipo == "filme":
        item_db = db.query(FilmeDB).filter(FilmeDB.iptv_id == ref_id).first()
        if not item_db:
            mem_item = next((x for x in _FILMES if str(x.get("id")) == str(ref_id)), None)
            if mem_item:
                item_db = FilmeDB(iptv_id=ref_id, titulo=mem_item.get("titulo"), capa=mem_item.get("capa"), ano=mem_item.get("ano"), categoria=mem_item.get("categoria"), url=mem_item.get("url") or "")
                db.add(item_db); db.commit(); db.refresh(item_db)
    elif ref_tipo == "serie":
        item_db = db.query(SerieDB).filter(SerieDB.iptv_id == ref_id).first()
        if not item_db:
            mem_item = next((x for x in _SERIES if str(x.get("id")) == str(ref_id)), None)
            if mem_item:
                item_db = SerieDB(iptv_id=ref_id, titulo=mem_item.get("titulo"), capa=mem_item.get("capa"), ano=mem_item.get("ano"), categoria=mem_item.get("categoria"))
                db.add(item_db); db.commit(); db.refresh(item_db)

    if not item_db: raise HTTPException(404, "Item não encontrado no banco nem na memória.")
    ultimo = db.query(SlideDB).order_by(SlideDB.ordem.desc()).first(); proxima = (ultimo.ordem + 1) if ultimo else 0
    novo = SlideDB(ordem=proxima, ref_tipo=ref_tipo, ref_id=item_db.id, ativo=True)
    db.add(novo); db.commit(); db.refresh(novo); _HOME_CACHE_TS = 0
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
    except: raise HTTPException(400, "inválido")
    for i, sid in enumerate(ids):
        s = db.query(SlideDB).get(sid)
        if s: s.ordem = i
    db.commit(); _HOME_CACHE_TS = 0
    await manager.broadcast("slider_atualizado", {"acao": "reordenar"})
    return {"ok": True}

# ==========================================================
# PROXY STREAM
# ==========================================================
_EPISODIO_RE = re.compile(r'^(.+?)-t(\d+)-e(\d+)$')

async def _resolver_episodio_sob_demanda(slug: str):
    m = _EPISODIO_RE.match(slug)
    if not m: return None
    base = m.group(1); serie_encontrada = None
    for s in _SERIES:
        if _slugify(s.get("titulo") or "") == base: serie_encontrada = s; break
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
    if not item: raise HTTPException(404, "não encontrado")
    return await stream_proxy(item["url"], request)

@app.get("/stream/{slug_path:path}")
async def proxy_por_slug(slug_path: str, request: Request):
    slug = slug_path
    for ext in (".mp4", ".mkv", ".ts", ".m3u8", ".avi", ".mov", ".flv", ".webm"):
        if slug.lower().endswith(ext): slug = slug[:-len(ext)]; break
    item = get_by_slug(slug)
    if not item: item = await _resolver_episodio_sob_demanda(slug)
    if not item: raise HTTPException(404, "não encontrado")
    return await stream_proxy(item["url"], request)

@app.get("/proxy/stats")
def proxy_monitor():
    return {"slugs_carregados": total_slugs(), **proxy_stats()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)