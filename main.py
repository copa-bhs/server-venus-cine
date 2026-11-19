import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from config import PORT, CACHE_DIR, PUBLIC_BASE_URL
from db import get_db, criar_tabelas, Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Falha as FalhaDB
import proxy
import logs
import importer
import iptv_catalog
import notificacoes
from home import build_home_async, limpar_cache as limpar_home_cache
from info import fetch_movie_info, fetch_series_info

# ==========================================================
# LIFESPAN
# ==========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Iniciando Venus Cine (IPTV Organizer Pro)...")
    criar_tabelas()
    
    # Carrega os slugs do proxy
    try:
        from config import SLUG_MAP_FILMES_FILE, SLUG_MAP_SERIES_FILE, SLUG_MAP_CANAIS_FILE
        for path in [SLUG_MAP_FILMES_FILE, SLUG_MAP_SERIES_FILE, SLUG_MAP_CANAIS_FILE]:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for slug, item in data.items():
                    proxy.register_item(item.get("id", ""), item.get("url", ""), item.get("titulo", ""), item.get("tipo", ""), slug)
        print(f"[Proxy] {proxy.total_slugs()} slugs carregados")
    except Exception as e:
        print(f"[Proxy] Erro ao carregar slugs: {e}")
    
    yield
    print("👋 Servidor encerrado.")

# ==========================================================
# APP
# ==========================================================
app = FastAPI(title="Venus Cine API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclui as rotas de notificações
app.include_router(notificacoes.router)

# ==========================================================
# WEBSOCKET
# ==========================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass

# ==========================================================
# ROTAS PÚBLICAS
# ==========================================================
@app.get("/")
async def root():
    return {"status": "ok", "servidor": "Venus Cine", "versao": "1.0.0"}

@app.get("/status")
async def status(db: Session = Depends(get_db)):
    total_filmes = db.query(FilmeDB).count()
    total_series = db.query(SerieDB).count()
    total_canais = db.query(CanalDB).count()
    total_falhas = db.query(FalhaDB).count()
    
    return {
        "status": "ok",
        "stats": {
            "total_filmes": total_filmes,
            "total_series": total_series,
            "total_canais": total_canais,
            "total_falhas": total_falhas
        },
        "proxy": proxy.stats(),
        "logs": logs.IMPORT_LOGS[-20:] if logs.IMPORT_LOGS else []
    }

@app.get("/home")
async def home(db: Session = Depends(get_db)):
    return await build_home_async(db)

@app.get("/filme/{item_id}")
async def filme_info(item_id: str):
    try:
        return await fetch_movie_info(item_id)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/serie/{item_id}")
async def serie_info(item_id: str):
    try:
        return await fetch_series_info(item_id)
    except Exception as e:
        raise HTTPException(500, str(e))

# ==========================================================
# PROXY DE STREAMING
# ==========================================================
@app.get("/stream/id/{item_id}")
async def stream_by_id(item_id: str, request: Request):
    item = proxy.get_by_id(item_id)
    if not item:
        raise HTTPException(404, "Item não encontrado")
    return await proxy.stream_proxy(item["url"], request)

@app.get("/stream/slug/{slug}")
async def stream_by_slug(slug: str, request: Request):
    item = proxy.get_by_slug(slug)
    if not item:
        raise HTTPException(404, "Item não encontrado")
    return await proxy.stream_proxy(item["url"], request)

@app.get("/proxy/stats")
async def proxy_stats():
    return proxy.stats()

# ==========================================================
# ROTAS ADMIN
# ==========================================================
@app.post("/admin/importar-catalogo")
async def importar_catalogo():
    if logs.SYNC_ATIVO:
        return {"ok": False, "msg": "Importação já em andamento."}
    
    logs.iniciar_sync()
    
    async def _importar():
        try:
            await iptv_catalog.rodar_importacao()
            importer.importar_catalogo()
            limpar_home_cache()
            logs.finalizar_sync()
            logs.adicionar_log("✅ Importação concluída com sucesso!")
        except Exception as e:
            logs.finalizar_sync()
            logs.adicionar_log(f"❌ Erro na importação: {e}")
    
    asyncio.create_task(_importar())
    return {"ok": True, "msg": "Importação iniciada."}

@app.get("/admin/filmes")
async def admin_filmes(q: str = "", page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(FilmeDB)
    if q:
        query = query.filter(FilmeDB.title.ilike(f"%{q}%"))
    total = query.count()
    items = query.offset((page-1)*size).limit(size).all()
    return {"items": [serializar_item(i) for i in items], "total": total, "page": page, "size": size}

@app.get("/admin/series")
async def admin_series(q: str = "", page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(SerieDB)
    if q:
        query = query.filter(SerieDB.title.ilike(f"%{q}%"))
    total = query.count()
    items = query.offset((page-1)*size).limit(size).all()
    return {"items": [serializar_item(i) for i in items], "total": total, "page": page, "size": size}

@app.get("/admin/canais")
async def admin_canais(q: str = "", page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    query = db.query(CanalDB)
    if q:
        query = query.filter(CanalDB.title.ilike(f"%{q}%"))
    total = query.count()
    items = query.offset((page-1)*size).limit(size).all()
    return {"items": [serializar_item(i) for i in items], "total": total, "page": page, "size": size}

@app.get("/admin/item/{tipo}/{item_id}")
async def admin_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    Tabela = FilmeDB if tipo == "filme" else (SerieDB if tipo == "serie" else CanalDB)
    item = db.query(Tabela).filter(Tabela.iptv_id == item_id).first()
    if not item:
        raise HTTPException(404, "Item não encontrado")
    return serializar_item(item)

@app.delete("/admin/item/{tipo}/{item_id}")
async def admin_delete_item(tipo: str, item_id: str, db: Session = Depends(get_db)):
    Tabela = FilmeDB if tipo == "filme" else (SerieDB if tipo == "serie" else CanalDB)
    item = db.query(Tabela).filter(Tabela.iptv_id == item_id).first()
    if not item:
        raise HTTPException(404, "Item não encontrado")
    db.delete(item)
    db.commit()
    return {"ok": True}

@app.get("/admin/falhas")
async def admin_falhas(page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    query = db.query(FalhaDB)
    total = query.count()
    items = query.offset((page-1)*size).limit(size).all()
    return {"items": [{"id": f.id, "iptv_id": f.iptv_id, "nome_original": f.nome_original, "tipo": f.tipo, "motivo": f.motivo, "created_at": f.created_at.isoformat() if f.created_at else None} for f in items], "total": total}

@app.post("/admin/wipe-all")
async def admin_wipe_all(db: Session = Depends(get_db)):
    db.query(FilmeDB).delete()
    db.query(SerieDB).delete()
    db.query(CanalDB).delete()
    db.query(FalhaDB).delete()
    db.commit()
    proxy.limpar_tudo()
    limpar_home_cache()
    return {"ok": True}

@app.get("/admin/logs")
async def admin_logs():
    return {"logs": logs.IMPORT_LOGS[-100:]}

# ==========================================================
# HELPERS
# ==========================================================
def serializar_item(item):
    import json as _json
    return {
        "id": item.id,
        "iptv_id": item.iptv_id,
        "stream_type": item.stream_type,
        "coletany": item.coletany,
        "tmdb_id": item.tmdb_id,
        "imdb_id": item.imdb_id,
        "title": item.title,
        "original_title": item.original_title,
        "slug": item.slug,
        "type": item.type,
        "year": item.year,
        "duration": item.duration,
        "description": item.description,
        "poster": item.poster,
        "logo": item.logo,
        "backdrop": item.backdrop,
        "rating_avg": item.rating_avg,
        "rating_count": item.rating_count,
        "views_count": item.views_count,
        "premium": item.premium,
        "audio_type": item.audio_type,
        "genres": _json.loads(item.genres) if item.genres else [],
        "age_rating": item.age_rating,
        "url": item.url,
        "editado": item.editado,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None
    }

# ==========================================================
# STATIC FILES (Frontend)
# ==========================================================
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    
    @app.get("/admin")
    async def admin_panel():
        return FileResponse(str(STATIC_DIR / "index.html"))
    
    @app.get("/admin/{path:path}")
    async def admin_panel_path(path: str):
        file_path = STATIC_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(STATIC_DIR / "index.html"))

# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
