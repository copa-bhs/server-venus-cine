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
    TMDB_ENABLED, TMDB_MAX_PER_CYCLE, PUBLIC_BASE_URL, IPTV_PROVIDERS
)
from parser import _clean_name, _extract_year, _clean_category
from info import fetch_movie_info, fetch_series_info
from home import build_home_async, _slugify
from proxy import (
    build_slug_map, get_by_slug, get_by_id, stream_proxy, stats as proxy_stats, 
    total_slugs, limpar_tudo as limpar_proxy, _SLUG_MAP, _ID_MAP,
    register_item as proxy_register
)
import tmdb
from db import (
    criar_tabelas, sincronizar, SessionLocal, get_db, engine, Base,
    Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Slide as SlideDB, Episodio as EpisodioDB
)
from websocket_manager import manager
from notificacoes import router as notificacoes_router

app = FastAPI(title="IPTV Organizer Pro", version="12.1.0")
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
# ADMIN - BUSCA E ADIÇÃO
# ==========================================================
class IPTVAddPayload(BaseModel):
    id: str; tipo: str; titulo: str; capa: Optional[str] = None; ano: Optional[int] = None; url: Optional[str] = None
    added_by_name: Optional[str] = None; added_by_uid: Optional[str] = None

class IPTVLotePayload(BaseModel):
    items: list[IPTVAddPayload]

@app.get("/admin/iptv/buscar")
async def admin_iptv_buscar(q: str, tipo: str = "filme", db: Session = Depends(get_db)):
    q_norm = _normalizar_busca(q)
    resultados = []
    
    for p in IPTV_PROVIDERS:
        action = "get_vod_streams" if tipo == "filme" else "get_series"
        url = f"{p['host']}/player_api.php?username={p['user']}&password={p['pass']}&action={action}"
        async with httpx.AsyncClient(timeout=60) as client:
            try: data = (await client.get(url)).json()
            except: continue
        
        if not isinstance(data, list): continue
        
        existentes = {f.iptv_id for f in db.query(FilmeDB).all()} if tipo == "filme" else {s.iptv_id for s in db.query(SerieDB).all()}
        
        for item in data:
            if not isinstance(item, dict): continue
            sid = str(item.get("stream_id") or item.get("series_id") or "")
            nome = item.get("name") or ""
            full_id = f"{p['prefix']}{sid}"
            
            if not sid or q_norm not in _normalizar_busca(nome): continue
            
            resultados.append({
                "id": full_id, "titulo": _clean_name(nome), "capa": item.get("stream_icon") or item.get("cover"),
                "ano": _extract_year(nome), "tipo": tipo, "ja_existe": full_id in existentes,
                "url": f"{p['host']}/movie/{p['user']}/{p['pass']}/{sid}.mp4" if tipo == "filme" else "",
                "provider": p['name']
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
# UPLOAD MANUAL
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
    global _FILMES
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
    
    serie = db.query(SerieDB).get(db_id)
    if serie:
        slug = _slugify(serie.titulo) + f"-t{temporada:02d}-e{episodio:02d}"
        proxy_register(str(ep.id), url, titulo, "serie", slug)
        
    return {"ok": True}

# ==========================================================
# IMPORTAÇÃO EM MASSA (TODOS OS PROVEDORES)
# ==========================================================
async def _importar_lote_bg(tipo: str, quantidade: int):
    global _IMPORT_ATIVO, _IMPORT_LOGS, _FILMES, _SERIES, _CANAIS
    _IMPORT_ATIVO = True
    _IMPORT_LOGS = [f"[•] Iniciando importação de até {quantidade if quantidade > 0 else 'TODOS os'} {tipo}(s)..."]
    db = SessionLocal()
    try:
        for p in IPTV_PROVIDERS:
            _IMPORT_LOGS.append(f"[•] Baixando lista do provedor {p['name']}...")
            action = "get_vod_streams" if tipo == "filme" else ("get_series" if tipo == "serie" else "get_live_streams")
            cat_action = "get_vod_categories" if tipo == "filme" else ("get_series_categories" if tipo == "serie" else "get_live_categories")
            
            url = f"{p['host']}/player_api.php?username={p['user']}&password={p['pass']}&action={action}"
            cat_url = f"{p['host']}/player_api.php?username={p['user']}&password={p['pass']}&action={cat_action}"
            
            async with httpx.AsyncClient(timeout=120) as client:
                r_data, r_cat = await asyncio.gather(client.get(url), client.get(cat_url))
                data = r_data.json()
                cats = r_cat.json()
                
            if not isinstance(data, list):
                _IMPORT_LOGS.append(f"[!] Erro no provedor {p['name']}.")
                continue

            cat_map = {str(c.get("category_id")): _clean_category(c.get("category_name") or "Geral") for c in cats if isinstance(c, dict)} if isinstance(cats, list) else {}
            
            if tipo == "filme": existentes = {f.iptv_id for f in db.query(FilmeDB).all() if f.iptv_id}
            elif tipo == "serie": existentes = {s.iptv_id for s in db.query(SerieDB).all() if s.iptv_id}
            else: existentes = {c.iptv_id for c in db.query(CanalDB).all() if c.iptv_id}

            adicionados = 0
            _IMPORT_LOGS.append(f"[•] Filtrando e inserindo {p['name']}...")
            
            for item in data:
                if not isinstance(item, dict): continue
                if quantidade > 0 and adicionados >= quantidade: break
                    
                if tipo == "filme":
                    sid = str(item.get("stream_id") or "")
                    full_id = f"{p['prefix']}{sid}"
                    if not sid or full_id in existentes: continue
                    nome = _clean_name(item.get("name") or "")
                    capa = item.get("stream_icon") or item.get("cover")
                    ano = _extract_year(item.get("name"))
                    cat = cat_map.get(str(item.get("category_id") or ""), "Geral")
                    ext = item.get("container_extension") or "mp4"
                    stream_url = f"{p['host']}/movie/{p['user']}/{p['pass']}/{sid}.{ext}"
                    
                    db.add(FilmeDB(iptv_id=full_id, titulo=nome, capa=capa, ano=ano, categoria=cat, url=stream_url))
                    _FILMES.append({"id": full_id, "tipo": "filme", "titulo": nome, "capa": capa, "ano": ano, "categoria": cat, "url": stream_url})
                    
                elif tipo == "serie":
                    sid = str(item.get("series_id") or "")
                    full_id = f"{p['prefix']}{sid}"
                    if not sid or full_id in existentes: continue
                    nome = _clean_name(item.get("name") or "")
                    capa = item.get("cover") or item.get("stream_icon")
                    ano = _extract_year(item.get("releaseDate")) or _extract_year(item.get("name"))
                    cat = cat_map.get(str(item.get("category_id") or ""), "Geral")
                    
                    db.add(SerieDB(iptv_id=full_id, titulo=nome, capa=capa, ano=ano, categoria=cat))
                    _SERIES.append({"id": full_id, "tipo": "serie", "titulo": nome, "capa": capa, "ano": ano, "categoria": cat})
                    
                else:
                    sid = str(item.get("stream_id") or "")
                    full_id = f"{p['prefix']}{sid}"
                    if not sid or full_id in existentes: continue
                    nome = _clean_name(item.get("name") or "")
                    capa = item.get("stream_icon")
                    cat = cat_map.get(str(item.get("category_id") or ""), "Geral")
                    stream_url = f"{p['host']}/live/{p['user']}/{p['pass']}/{sid}.ts"
                    
                    db.add(CanalDB(iptv_id=full_id, titulo=nome, capa=capa, categoria=cat, url=stream_url))
                    _CANAIS.append({"id": full_id, "tipo": "canal", "titulo": nome, "capa": capa, "categoria": cat, "url": stream_url})
                    
                adicionados += 1
                existentes.add(full_id)
                
                if adicionados % 100 == 0:
                    _IMPORT_LOGS.append(f"[•] {p['name']}: {adicionados} adicionados...")
                    db.commit()

            db.commit()
            _IMPORT_LOGS.append(f"[✓] Provedor {p['name']}: {adicionados} adicionados.")
            
        _rebuild_slugs()
        _save_all()
        await manager.broadcast("catalogo_sincronizado", _stats())
    except Exception as e:
        _IMPORT_LOGS.append(f"[!] Erro: {e}")
    finally:
        db.close()
        _IMPORT_ATIVO = False