import asyncio, json, secrets, time, re, unicodedata
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from pydantic import BaseModel

from config import *
from info import fetch_movie_info, fetch_series_info
from home import build_home_async, _slugify
from proxy import build_slug_map, get_by_slug, get_by_id, stream_proxy, stats as proxy_stats, total_slugs, limpar_tudo as limpar_proxy
import tmdb
from db import criar_tabelas, SessionLocal, get_db, engine, Base, Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Slide as SlideDB, Falha as FalhaDB
from importer import importar_catalogo
from iptv_catalog import rodar_importacao
from websocket_manager import manager
from notificacoes import router as notificacoes_router

app = FastAPI(title="IPTV Organizer Pro", version="13.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])
app.include_router(notificacoes_router)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

_HOME_CACHE, _HOME_CACHE_TS = {}, 0.0
_SYNC_ATIVO = False

def _stats(db: Session = None) -> dict:
    close_db = False
    if not db:
        db = SessionLocal()
        close_db = True
    try:
        return {
            "total_filmes": db.query(FilmeDB).count(),
            "total_series": db.query(SerieDB).count(),
            "total_canais": db.query(CanalDB).count(),
            "total_falhas": db.query(FalhaDB).count()
        }
    finally:
        if close_db: db.close()

def _rebuild_slugs():
    db = SessionLocal()
    try:
        filmes = db.query(FilmeDB).all()
        series = db.query(SerieDB).all()
        canais = db.query(CanalDB).all()
        build_slug_map({"filmes": [{"id": f.iptv_id, "url": f.url, "titulo": f.title, "ano": f.year} for f in filmes],
                        "series": [{"id": s.iptv_id, "url": "", "titulo": s.title, "ano": s.year, "episodios": []} for s in series],
                        "canais": [{"id": c.iptv_id, "url": c.url, "titulo": c.title} for c in canais]})
    finally: db.close()

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
    if isinstance(item, dict):
        t = tipo or item.get("tipo") or "filme"
        item_id = str(item.get("id") or item.get("iptv_id"))
        titulo = item.get("titulo") or item.get("title")
        capa = item.get("capa") or item.get("poster")
        ano = item.get("ano") or item.get("year")
        slug = _slugify(titulo or "", ano)
        return {"id": item_id, "title": titulo, "poster": capa, "type": "movie" if t=="filme" else "series", "year": ano, "slug": slug, "titulo": titulo, "capa": capa, "tipo": t, "ano": ano}
    else:
        t = tipo or "filme"
        slug = item.slug or _slugify(item.title or "", item.year)
        return {"id": str(item.iptv_id), "title": item.title, "poster": item.poster, "type": "movie" if t=="filme" else "series", "year": item.year, "slug": slug, "titulo": item.title, "capa": item.poster, "tipo": t, "ano": item.year, "rating_avg": item.rating_avg, "backdrop": item.backdrop, "genres": json.loads(item.genres) if item.genres else []}

@app.on_event("startup")
async def startup():
    criar_tabelas()
    importar_catalogo()
    _rebuild_slugs()
    print(f"[✓] Carregado do SQL: {_stats()}")

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps({"evento": "conectado", "dados": {"stats": _stats()}}))
        while True:
            data = await ws.receive_text()
            if data == "ping": await ws.send_text("pong")
    except: await manager.disconnect(ws)

@app.get("/")
def root(): return FileResponse(BASE_DIR / "index.html") if (BASE_DIR / "index.html").exists() else {"service": "IPTV"}

@app.get("/status")
async def status(db: Session = Depends(get_db)):
    return {"stats": _stats(db), "sync_ativo": _SYNC_ATIVO, "proxy": {"slugs_carregados": total_slugs(), **proxy_stats()}}

@app.get("/home")
async def home(force: bool = False):
    global _HOME_CACHE, _HOME_CACHE_TS
    db = SessionLocal()
    try:
        if db.query(FilmeDB).count() == 0 and db.query(SerieDB).count() == 0:
            return {"hero": [], "colecoes": [], "trailers": [], "stats": _stats(db)}
        if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < 300: return _HOME_CACHE
        _HOME_CACHE = await build_home_async(db, force_hero=force)
        _HOME_CACHE_TS = time.time()
        return _HOME_CACHE
    finally: db.close()

@app.get("/filmes")
def listar_filmes(page: int = Query(1), size: int = Query(30), categoria: str | None = None, db: Session = Depends(get_db)):
    q = db.query(FilmeDB)
    if categoria: q = q.filter(FilmeDB.coletany.ilike(f"%{categoria}%"))
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return {"page": page, "page_size": size, "total_items": total, "total_pages": (total + size - 1)//size, "items": [_card_app(it, "filme") for it in items]}

@app.get("/series")
def listar_series(page: int = Query(1), size: int = Query(30), categoria: str | None = None, db: Session = Depends(get_db)):
    q = db.query(SerieDB)
    if categoria: q = q.filter(SerieDB.coletany.ilike(f"%{categoria}%"))
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return {"page": page, "page_size": size, "total_items": total, "total_pages": (total + size - 1)//size, "items": [_card_app(it, "serie") for it in items]}

@app.get("/buscar")
def buscar(q: str, tipo: str | None = None, page: int = Query(1), size: int = Query(30), db: Session = Depends(get_db)):
    q_norm = f"%{q}%"
    resultados = []
    if tipo in (None, "filme", "movie"):
        for f in db.query(FilmeDB).filter(FilmeDB.title.ilike(q_norm)).limit(50).all(): resultados.append(_card_app(f, "filme"))
    if tipo in (None, "serie", "series"):
        for s in db.query(SerieDB).filter(SerieDB.title.ilike(q_norm)).limit(50).all(): resultados.append(_card_app(s, "serie"))
    return paginate(resultados, page, size)

@app.get("/categorias")
def categorias(db: Session = Depends(get_db)):
    f_cats = [r[0] for r in db.query(FilmeDB.coletany).distinct() if r[0]]
    s_cats = [r[0] for r in db.query(SerieDB.coletany).distinct() if r[0]]
    return {"filmes": sorted(set(f_cats)), "series": sorted(set(s_cats))}

@app.get("/info/filme/{filme_id}")
async def info_filme(filme_id: str, db: Session = Depends(get_db)):
    f = db.query(FilmeDB).filter(FilmeDB.iptv_id == filme_id).first()
    if f:
        return {"id": f.iptv_id, "tipo": "filme", "titulo": f.title, "capa": f.poster, "banner": f.backdrop, "logo": f.logo, "sinopse": f.description, "generos": json.loads(f.genres) if f.genres else [], "ano": f.year, "duracao": f.duration, "score": f.rating_avg, "classificacao": f.age_rating, "url_stream": f.url, "tmdb_id": f.tmdb_id}
    return await fetch_movie_info(filme_id)

@app.get("/info/serie/{serie_id}")
async def info_serie(serie_id: str, db: Session = Depends(get_db)):
    return await fetch_series_info(serie_id)

@app.get("/admin/filmes")
def admin_filmes(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(FilmeDB)
    if q: query = query.filter(FilmeDB.title.ilike(f"%{q}%"))
    total = query.count()
    items = query.offset((page-1)*size).limit(size).all()
    out = []
    for it in items:
        d = {c.name: getattr(it, c.name) for c in it.__table__.columns}
        d['titulo'] = it.title; d['capa'] = it.poster; d['iptv_id'] = it.iptv_id
        out.append(d)
    return {"page": page, "page_size": size, "total_items": total, "items": out}

@app.get("/admin/series")
def admin_series(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(SerieDB)
    if q: query = query.filter(SerieDB.title.ilike(f"%{q}%"))
    total = query.count()
    items = query.offset((page-1)*size).limit(size).all()
    out = []
    for it in items:
        d = {c.name: getattr(it, c.name) for c in it.__table__.columns}
        d['titulo'] = it.title; d['capa'] = it.poster; d['iptv_id'] = it.iptv_id
        out.append(d)
    return {"page": page, "page_size": size, "total_items": total, "items": out}

@app.get("/admin/canais")
def admin_canais(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(CanalDB)
    if q: query = query.filter(CanalDB.title.ilike(f"%{q}%"))
    total = query.count()
    items = query.offset((page-1)*size).limit(size).all()
    out = []
    for it in items:
        d = {c.name: getattr(it, c.name) for c in it.__table__.columns}
        d['titulo'] = it.title; d['capa'] = it.poster; d['iptv_id'] = it.iptv_id
        out.append(d)
    return {"page": page, "page_size": size, "total_items": total, "items": out}

@app.get("/admin/falhas")
def admin_falhas(page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    total = db.query(FalhaDB).count()
    items = db.query(FalhaDB).offset((page-1)*size).limit(size).all()
    return {"total": total, "items": [{"id": f.id, "iptv_id": f.iptv_id, "nome_original": f.nome_original, "tipo": f.tipo} for f in items]}

@app.post("/admin/importar-catalogo")
async def admin_importar_catalogo():
    global _SYNC_ATIVO
    if _SYNC_ATIVO: return {"ok": False, "msg": "Já existe uma importação em andamento."}
    _SYNC_ATIVO = True
    try:
        # 1. Roda o script que baixa do IPTV e enriquece com TMDB
        await rodar_importacao()
        # 2. Importa o JSON gerado para o SQL
        sucesso = importar_catalogo()
        _rebuild_slugs()
        await manager.broadcast("catalogo_sincronizado", _stats())
        return {"ok": sucesso, "msg": "Catálogo sincronizado com sucesso!" if sucesso else "Erro na importação."}
    except Exception as e:
        return {"ok": False, "msg": f"Erro: {str(e)}"}
    finally:
        _SYNC_ATIVO = False

@app.get("/admin/item/{tipo}/{item_id}")
def admin_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    if tipo == "filme": it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    elif tipo == "serie": it = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    elif tipo == "canal": it = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()
    else: raise HTTPException(400, "inválido")
    if not it: raise HTTPException(404, "Não encontrado")
    d = {c.name: getattr(it, c.name) for c in it.__table__.columns}
    d['titulo'] = it.title; d['capa'] = it.poster; d['sinopse'] = it.description
    return d

@app.delete("/admin/item/{tipo}/{item_id}")
def deletar_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    if tipo == "filme": it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    elif tipo == "serie": it = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    elif tipo == "canal": it = db.query(CanalDB).filter(CanalDB.iptv_id == item_id).first()
    if it: db.delete(it); db.commit()
    _rebuild_slugs(); return {"ok": True}

async def _salvar_upload(arq: Optional[UploadFile], sub: str) -> Optional[str]:
    if not arq or not arq.filename: return None
    ext = Path(arq.filename).suffix.lower()
    nome = f"{secrets.token_hex(8)}{ext}"; destino = UPLOAD_DIR / sub / nome
    with destino.open("wb") as f:
        while chunk := await arq.read(1024 * 1024): f.write(chunk)
    base = PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/uploads/{sub}/{nome}"

@app.put("/admin/filme/{item_id}")
async def editar_filme(item_id: str, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    if not it: raise HTTPException(404, "Não encontrado")
    if titulo: it.title = titulo
    if ano: it.year = ano
    if categoria: it.coletany = categoria
    if sinopse: it.description = sinopse
    if capa: it.poster = await _salvar_upload(capa, "capas")
    it.editado = True; db.commit()
    await manager.broadcast("item_editado", {"tipo": "filme", "id": it.id, "titulo": it.title})
    return {"ok": True, "titulo": it.title}

@app.get("/admin/slider")
def admin_slider_listar(db: Session = Depends(get_db)):
    slides = db.query(SlideDB).order_by(SlideDB.ordem).all()
    return {"items": [{"id": s.id, "ordem": s.ordem, "ref_tipo": s.ref_tipo, "ref_id": s.ref_id, "titulo": s.titulo} for s in slides]}

@app.post("/admin/slider")
async def admin_slider_adicionar(ref_tipo: str = Form(...), ref_id: str = Form(...), db: Session = Depends(get_db)):
    item_db = None
    if ref_tipo == "filme": item_db = db.query(FilmeDB).filter(FilmeDB.iptv_id == ref_id).first()
    elif ref_tipo == "serie": item_db = db.query(SerieDB).filter(SerieDB.iptv_id == ref_id).first()
    if not item_db: raise HTTPException(404, "Item não encontrado.")
    ultimo = db.query(SlideDB).order_by(SlideDB.ordem.desc()).first(); proxima = (ultimo.ordem + 1) if ultimo else 0
    novo = SlideDB(ordem=proxima, ref_tipo=ref_tipo, ref_id=item_db.id, ativo=True)
    db.add(novo); db.commit(); db.refresh(novo)
    await manager.broadcast("slider_atualizado", {"acao": "adicionar", "id": novo.id})
    return {"ok": True, "id": novo.id}

@app.put("/admin/slider/{slide_id}")
async def admin_slider_editar(slide_id: int, titulo: Optional[str] = Form(None), banner: Optional[str] = Form(None), logo: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), db: Session = Depends(get_db)):
    s = db.query(SlideDB).get(slide_id)
    if not s: raise HTTPException(404, "não encontrado")
    if titulo is not None: s.titulo = titulo or None
    if banner is not None: s.banner = banner or None
    if logo is not None: s.logo = logo or None
    if sinopse is not None: s.sinopse = sinopse or None
    db.commit()
    await manager.broadcast("slider_atualizado", {"acao": "editar", "id": slide_id})
    return {"ok": True}

@app.delete("/admin/slider/{slide_id}")
async def admin_slider_remover(slide_id: int, db: Session = Depends(get_db)):
    s = db.query(SlideDB).get(slide_id)
    if not s: raise HTTPException(404, "não encontrado")
    db.delete(s); db.commit()
    await manager.broadcast("slider_atualizado", {"acao": "remover", "id": slide_id})
    return {"ok": True}

@app.post("/admin/slider/reordenar")
async def admin_slider_reordenar(ordem: str = Form(...), db: Session = Depends(get_db)):
    try: ids = [int(x.strip()) for x in ordem.split(",") if x.strip()]
    except: raise HTTPException(400, "inválido")
    for i, sid in enumerate(ids):
        s = db.query(SlideDB).get(sid)
        if s: s.ordem = i
    db.commit()
    await manager.broadcast("slider_atualizado", {"acao": "reordenar"})
    return {"ok": True}

@app.get("/stream/id/{item_id}")
async def proxy_por_id(item_id: str, request: Request):
    cid = item_id
    for ext in (".mp4", ".mkv", ".ts", ".m3u8"):
        if cid.lower().endswith(ext): cid = cid[:-len(ext)]; break
    item = get_by_id(cid)
    if not item: raise HTTPException(404, "não encontrado")
    return await stream_proxy(item["url"], request)

@app.get("/stream/{slug_path:path}")
async def proxy_por_slug(slug_path: str, request: Request):
    slug = slug_path
    for ext in (".mp4", ".mkv", ".ts", ".m3u8"):
        if slug.lower().endswith(ext): slug = slug[:-len(ext)]; break
    item = get_by_slug(slug)
    if not item: raise HTTPException(404, "não encontrado")
    return await stream_proxy(item["url"], request)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)