import asyncio, json, secrets, time, re, unicodedata
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import or_

from config import *
from info import fetch_movie_info, fetch_series_info
from home import build_home_async
from proxy import build_slug_map, get_by_slug, get_by_id, stream_proxy, stats as proxy_stats, total_slugs, limpar_tudo as limpar_proxy, slugify
from db import criar_tabelas, SessionLocal, get_db, engine, Base, Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Slide as SlideDB, Falha as FalhaDB
from importer import importar_catalogo
from iptv_catalog import rodar_importacao
import iptv_catalog
from websocket_manager import manager
from notificacoes import router as notificacoes_router, disparar_push_geral
import parser as iptv_parser

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

def _serializar_rico(item):
    try: generos = json.loads(item.genres) if item.genres else []
    except: generos = []
    slug = item.slug or slugify(item.title or "", item.year)
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    url_proxy = f"{base}/stream/{slug}.mp4" if base else f"/stream/{slug}.mp4"
    return {
        "iptv_id": str(item.iptv_id), "stream_type": item.stream_type, "coletany": item.coletany,
        "tmdb_id": item.tmdb_id, "imdb_id": item.imdb_id, "title": item.title, "original_title": item.original_title,
        "slug": slug, "type": item.type, "year": item.year, "duration": item.duration, "description": item.description,
        "poster": item.poster, "logo": item.logo, "backdrop": item.backdrop, "rating_avg": item.rating_avg,
        "rating_count": item.rating_count, "views_count": item.views_count, "premium": item.premium,
        "audio_type": item.audio_type, "genres": generos, "age_rating": item.age_rating,
        "url_stream": url_proxy, "url_stream_direto": item.url
    }

@app.on_event("startup")
async def startup():
    criar_tabelas()
    importar_catalogo()
    _rebuild_slugs()

# --- ROTAS BÁSICAS E HOME ---
@app.get("/status")
async def status(db: Session = Depends(get_db)):
    return {"stats": _stats(db), "sync_ativo": _SYNC_ATIVO, "proxy": {"slugs_carregados": total_slugs(), **proxy_stats()}}

@app.get("/home")
async def home(force: bool = False):
    global _HOME_CACHE, _HOME_CACHE_TS
    db = SessionLocal()
    try:
        if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < 300: return _HOME_CACHE
        _HOME_CACHE = await build_home_async(db, force=force)
        _HOME_CACHE_TS = time.time()
        return _HOME_CACHE
    finally: db.close()

# --- ROTAS ADMIN ---
@app.post("/admin/wipe-all")
async def admin_wipe_all():
    global _SYNC_ATIVO
    if _SYNC_ATIVO: return {"ok": False, "msg": "Aguarde a sincronização terminar."}
    _SYNC_ATIVO = True
    try:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        limpar_proxy()
        _HOME_CACHE = {}
        return {"ok": True, "msg": "Banco de dados limpo com sucesso."}
    finally:
        _SYNC_ATIVO = False

@app.post("/admin/importar-catalogo")
async def admin_importar_catalogo():
    global _SYNC_ATIVO
    if _SYNC_ATIVO: return {"ok": False, "msg": "Já em andamento."}
    _SYNC_ATIVO = True
    try:
        await rodar_importacao()
        sucesso = importar_catalogo()
        _rebuild_slugs()
        await manager.broadcast("catalogo_sincronizado", _stats())
        return {"ok": sucesso, "msg": "Catálogo completo sincronizado!" if sucesso else "Erro."}
    finally:
        _SYNC_ATIVO = False

# --- ROTAS IPTV ---
@app.get("/admin/iptv/buscar")
async def admin_iptv_buscar(q: str, tipo: str = "filme", db: Session = Depends(get_db)):
    q_norm = iptv_parser._clean_name(q).lower()
    action = "get_vod_streams" if tipo == "filme" else "get_series"
    async with httpx.AsyncClient(timeout=60) as client:
        try: data = (await client.get(f"{IPTV_HOST}/player_api.php?username={IPTV_USERNAME}&password={IPTV_PASSWORD}&action={action}")).json()
        except: raise HTTPException(500, "Erro provedor.")
    
    existentes = {f.iptv_id for f in db.query(FilmeDB.iptv_id).all()} if tipo == "filme" else {s.iptv_id for s in db.query(SerieDB.iptv_id).all()}
    resultados = []
    
    for item in data:
        if not isinstance(item, dict): continue
        sid = str(item.get("stream_id") or item.get("series_id") or "")
        nome = item.get("name") or ""
        if not sid or q_norm not in nome.lower(): continue
        
        resultados.append({
            "id": sid, "titulo": iptv_parser._clean_name(nome), "capa": item.get("stream_icon") or item.get("cover"),
            "ano": iptv_parser._extract_year(nome), "tipo": "movie" if tipo == "filme" else "tv",
            "ja_existe": sid in existentes,
            "url": f"{IPTV_HOST}/movie/{IPTV_USERNAME}/{IPTV_PASSWORD}/{sid}.mp4" if tipo == "filme" else ""
        })
    return {"items": resultados[:100]}

@app.get("/admin/iptv/recentes")
async def admin_iptv_recentes(db: Session = Depends(get_db)):
    filmes = await iptv_parser.fetch_filmes()
    series = await iptv_parser.fetch_series()
    
    filmes.sort(key=lambda x: x.get("added", 0), reverse=True)
    series.sort(key=lambda x: x.get("added", 0), reverse=True)

    recentes = filmes[:20] + series[:20]

    existentes_f = {f.iptv_id for f in db.query(FilmeDB.iptv_id).all()}
    existentes_s = {s.iptv_id for s in db.query(SerieDB.iptv_id).all()}

    resultados = []
    for item in recentes:
        iptv_id = str(item["id"])
        tipo = item["tipo"]
        ja_existe = (iptv_id in existentes_f) if tipo == "movie" else (iptv_id in existentes_s)
        resultados.append({
            "id": iptv_id, "titulo": item["titulo"], "capa": item.get("capa"), "ano": item.get("ano"),
            "tipo": tipo, "ja_existe": ja_existe, "url": item.get("url", "")
        })
    return {"items": resultados}

@app.post("/admin/iptv/adicionar")
async def admin_iptv_adicionar(payload: dict, db: Session = Depends(get_db)):
    iptv_id = str(payload.get("id"))
    tipo = payload.get("tipo")
    
    if tipo == "movie" and db.query(FilmeDB).filter(FilmeDB.iptv_id == iptv_id).first():
        return {"ok": False, "msg": "Já existe no banco."}
    if tipo == "tv" and db.query(SerieDB).filter(SerieDB.iptv_id == iptv_id).first():
        return {"ok": False, "msg": "Já existe no banco."}

    async with httpx.AsyncClient(timeout=30) as client:
        tmdb_id = await iptv_catalog._buscar_tmdb(client, payload["titulo"], tipo, payload.get("ano"))
        meta = await iptv_catalog._obter_detalhes_tmdb(client, tmdb_id, tipo) if tmdb_id else None
    
    if tipo == "movie":
        novo = FilmeDB(iptv_id=iptv_id, stream_type="movie", coletany="Manual", title=payload["titulo"], poster=payload.get("capa"), url=payload.get("url", ""), year=payload.get("ano"))
    else:
        novo = SerieDB(iptv_id=iptv_id, stream_type="tv", coletany="Manual", title=payload["titulo"], poster=payload.get("capa"), url="", year=payload.get("ano"))
        
    if meta:
        novo.tmdb_id = meta.get("tmdb_id"); novo.imdb_id = meta.get("imdb_id")
        novo.title = meta.get("title"); novo.original_title = meta.get("original_title")
        novo.slug = meta.get("slug"); novo.duration = meta.get("duration")
        novo.description = meta.get("description"); novo.poster = meta.get("poster") or novo.poster
        novo.logo = meta.get("logo"); novo.backdrop = meta.get("backdrop")
        novo.rating_avg = meta.get("rating_avg", 0); novo.rating_count = meta.get("rating_count", 0)
        novo.genres = json.dumps(meta.get("genres", []), ensure_ascii=False)
        novo.age_rating = meta.get("age_rating")
    
    db.add(novo); db.commit(); db.refresh(novo)
    _rebuild_slugs()
    
    await disparar_push_geral(
        titulo="🎬 Novo conteúdo adicionado!",
        corpo=f"O { 'Filme' if tipo == 'movie' else 'Série' } '{novo.title}' acabou de chegar. Clique para assistir!",
        imagem=novo.poster, item_id=novo.iptv_id, item_type=tipo
    )
    return {"ok": True, "msg": "Adicionado, enriquecido e notificado!"}

# --- ROTA DE SINCRONIZAR TMDB INDIVIDUAL ---
@app.post("/admin/item/{tipo}/{item_id}/sync-tmdb")
async def admin_item_sync_tmdb(tipo: str, item_id: str, db: Session = Depends(get_db)):
    item = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first() if tipo == "filme" else db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    if not item: raise HTTPException(404, "Item não encontrado no banco")
    
    db_type = "movie" if tipo == "filme" else "tv"
    async with httpx.AsyncClient(timeout=30) as client:
        tmdb_id = await iptv_catalog._buscar_tmdb(client, item.title, db_type, item.year)
        if not tmdb_id: raise HTTPException(404, "Não encontrado no TMDB")
        meta = await iptv_catalog._obter_detalhes_tmdb(client, tmdb_id, db_type)
        if not meta: raise HTTPException(500, "Erro ao buscar detalhes no TMDB")

        item.tmdb_id = meta.get("tmdb_id"); item.imdb_id = meta.get("imdb_id")
        item.title = meta.get("title"); item.original_title = meta.get("original_title")
        item.slug = meta.get("slug"); item.duration = meta.get("duration")
        item.description = meta.get("description"); item.poster = meta.get("poster")
        item.logo = meta.get("logo"); item.backdrop = meta.get("backdrop")
        item.rating_avg = meta.get("rating_avg", 0); item.rating_count = meta.get("rating_count", 0)
        item.genres = json.dumps(meta.get("genres", []), ensure_ascii=False)
        item.age_rating = meta.get("age_rating")
        
        db.commit(); db.refresh(item)
    return _serializar_rico(item)

# --- OUTRAS ROTAS (Listagens, Info, Edição, Slider, Proxy) ---
@app.get("/filmes")
def listar_filmes(page: int = Query(1), size: int = Query(30), db: Session = Depends(get_db)):
    q = db.query(FilmeDB); total = q.count(); items = q.offset((page - 1) * size).limit(size).all()
    return {"page": page, "total_items": total, "items": [_serializar_rico(it) for it in items]}

@app.get("/series")
def listar_series(page: int = Query(1), size: int = Query(30), db: Session = Depends(get_db)):
    q = db.query(SerieDB); total = q.count(); items = q.offset((page - 1) * size).limit(size).all()
    return {"page": page, "total_items": total, "items": [_serializar_rico(it) for it in items]}

@app.get("/admin/filmes")
def admin_filmes(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(FilmeDB)
    if q: query = query.filter(FilmeDB.title.ilike(f"%{q}%"))
    items = query.offset((page-1)*size).limit(size).all()
    return {"page": page, "total_items": query.count(), "items": [_serializar_rico(it) for it in items]}

@app.get("/admin/series")
def admin_series(q: str | None = None, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(SerieDB)
    if q: query = query.filter(SerieDB.title.ilike(f"%{q}%"))
    items = query.offset((page-1)*size).limit(size).all()
    return {"page": page, "total_items": query.count(), "items": [_serializar_rico(it) for it in items]}

@app.get("/admin/item/{tipo}/{item_id}")
def admin_item_get(tipo: str, item_id: str, db: Session = Depends(get_db)):
    item = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first() if tipo == "filme" else db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    if not item: raise HTTPException(404, "Não encontrado")
    return _serializar_rico(item)

async def _salvar_upload(arq: Optional[UploadFile], sub: str) -> Optional[str]:
    if not arq or not arq.filename: return None
    ext = Path(arq.filename).suffix.lower()
    nome = f"{secrets.token_hex(8)}{ext}"; destino = UPLOAD_DIR / sub / nome
    with destino.open("wb") as f:
        while chunk := await arq.read(1024 * 1024): f.write(chunk)
    base = PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/uploads/{sub}/{nome}"

@app.put("/admin/filme/{item_id}")
async def editar_filme(item_id: str, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    it = db.query(FilmeDB).filter(FilmeDB.iptv_id == item_id).first()
    if not it: raise HTTPException(404, "Não encontrado")
    if titulo: it.title = titulo
    if ano: it.year = ano
    if categoria: it.coletany = categoria
    if sinopse: it.description = sinopse
    if capa: it.poster = await _salvar_upload(capa, "capas")
    if banner: it.backdrop = await _salvar_upload(banner, "banners")
    if logo: it.logo = await _salvar_upload(logo, "logos")
    it.editado = True; db.commit()
    await manager.broadcast("item_editado", {"tipo": "filme", "id": it.id, "titulo": it.title})
    return {"ok": True, "titulo": it.title}

@app.put("/admin/serie/{item_id}")
async def editar_serie(item_id: str, titulo: Optional[str] = Form(None), ano: Optional[int] = Form(None), categoria: Optional[str] = Form(None), sinopse: Optional[str] = Form(None), capa: Optional[UploadFile] = File(None), banner: Optional[UploadFile] = File(None), logo: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    it = db.query(SerieDB).filter(SerieDB.iptv_id == item_id).first()
    if not it: raise HTTPException(404, "Não encontrado")
    if titulo: it.title = titulo
    if ano: it.year = ano
    if categoria: it.coletany = categoria
    if sinopse: it.description = sinopse
    if capa: it.poster = await _salvar_upload(capa, "capas")
    if banner: it.backdrop = await _salvar_upload(banner, "banners")
    if logo: it.logo = await _salvar_upload(logo, "logos")
    it.editado = True; db.commit()
    await manager.broadcast("item_editado", {"tipo": "serie", "id": it.id, "titulo": it.title})
    return {"ok": True, "titulo": it.title}

@app.get("/info/filme/{item_id}")
@app.get("/info/movie/{item_id}")
async def info_filme(item_id: str, db: Session = Depends(get_db)):
    f = db.query(FilmeDB).filter(or_(FilmeDB.iptv_id == item_id, FilmeDB.tmdb_id == item_id)).first()
    if f: return _serializar_rico(f)
    return await fetch_movie_info(item_id)

@app.get("/info/serie/{item_id}")
@app.get("/info/tv/{item_id}")
async def info_serie(item_id: str, db: Session = Depends(get_db)):
    s = db.query(SerieDB).filter(or_(SerieDB.iptv_id == item_id, SerieDB.tmdb_id == item_id)).first()
    if s: item_id = s.iptv_id 
    return await fetch_series_info(item_id)

@app.get("/admin/slider")
def admin_slider_listar(db: Session = Depends(get_db)):
    slides = db.query(SlideDB).order_by(SlideDB.ordem).all()
    return {"items": [{"id": s.id, "ordem": s.ordem, "ref_tipo": s.ref_tipo, "ref_id": s.ref_id, "titulo": s.titulo} for s in slides]}

@app.post("/admin/slider")
async def admin_slider_adicionar(ref_tipo: str = Form(...), ref_id: str = Form(...), db: Session = Depends(get_db)):
    item_db = db.query(FilmeDB).filter(FilmeDB.iptv_id == ref_id).first() if ref_tipo == "filme" else db.query(SerieDB).filter(SerieDB.iptv_id == ref_id).first()
    if not item_db: raise HTTPException(404, "Item não encontrado.")
    ultimo = db.query(SlideDB).order_by(SlideDB.ordem.desc()).first(); proxima = (ultimo.ordem + 1) if ultimo else 0
    novo = SlideDB(ordem=proxima, ref_tipo=ref_tipo, ref_id=item_db.id, ativo=True)
    db.add(novo); db.commit(); db.refresh(novo)
    return {"ok": True, "id": novo.id}

@app.delete("/admin/slider/{slide_id}")
async def admin_slider_remover(slide_id: int, db: Session = Depends(get_db)):
    s = db.query(SlideDB).get(slide_id)
    if s: db.delete(s); db.commit()
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