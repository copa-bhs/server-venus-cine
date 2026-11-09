import time
import json
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL

_HOME_CACHE = {}
_HOME_CACHE_TS = 0
CACHE_TTL = 10 # ⚡ Cache de apenas 10 segundos para testes rápidos

def limpar_cache():
    global _HOME_CACHE, _HOME_CACHE_TS
    _HOME_CACHE = {}
    _HOME_CACHE_TS = 0
    print("[Home] Cache limpo.")

def _serializar_leve(item):
    try: generos = json.loads(item.genres) if item.genres else []
    except: generos = []
    tipo_final = "movie" if item.stream_type == "movie" else "series"
    return {
        "iptv_id": str(item.iptv_id), "title": item.title, "poster": item.poster, "backdrop": item.backdrop,
        "logo": item.logo, "year": item.year, "rating_avg": item.rating_avg, "age_rating": item.age_rating,
        "genres": generos, "type": tipo_final
    }

def _construir_hero(db: Session) -> list:
    try:
        slides = db.query(SlideDB).filter(SlideDB.ativo == True).order_by(SlideDB.ordem).all()
        hero = []
        for s in slides:
            ref = db.query(FilmeDB).get(s.ref_id) if s.ref_tipo == "filme" else db.query(SerieDB).get(s.ref_id)
            if not ref: continue
            hero.append({
                "id": str(ref.iptv_id), "tipo": "filme" if s.ref_tipo == "filme" else "serie",
                "titulo": s.titulo or ref.title, "banner": s.banner or ref.backdrop,
                "capa": ref.poster, "logo": s.logo or ref.logo, "sinopse": s.sinopse or ref.description,
                "score": ref.rating_avg, "classificacao": ref.age_rating, "ano": ref.year,
                "generos": json.loads(ref.genres) if ref.genres else []
            })
        return hero
    except Exception as e:
        print(f"[Home] Erro Hero: {e}")
        return []

def _construir_colecoes(db: Session) -> list:
    colecoes = []
    
    def add(id_, titulo, items, tipo_padrao="misto", icon="folder"):
        if not items: return
        vistos = set()
        items_unicos = []
        for it in items:
            chave = str(it.id)
            if chave not in vistos:
                vistos.add(chave)
                items_unicos.append(it)
        if not items_unicos: return 
        colecoes.append({
            "id": id_, "titulo": titulo, "icon": icon, "tipo": tipo_padrao,
            "items": [_serializar_leve(it) for it in items_unicos[:15]]
        })

    # 1. TOP 10 FILMES (Direto do banco)
    try:
        top_f = db.query(FilmeDB).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
        add("top10_filmes", "🏆 Top 10 Filmes", top_f, "filme", "star")
    except Exception as e: print(f"[Home] Erro Top Filmes: {e}")

    # 2. TOP 10 SÉRIES (Direto do banco)
    try:
        top_s = db.query(SerieDB).order_by(desc(SerieDB.rating_avg)).limit(10).all()
        add("top10_series", "🏆 Top 10 Séries", top_s, "serie", "star")
    except Exception as e: print(f"[Home] Erro Top Series: {e}")

    # 3. RECENTES (Direto do banco)
    try:
        rec_f = db.query(FilmeDB).order_by(desc(FilmeDB.id)).limit(15).all()
        rec_s = db.query(SerieDB).order_by(desc(SerieDB.id)).limit(15).all()
        add("recentes", "🚀 Adicionados Recentemente", rec_f + rec_s, "misto", "clock")
    except Exception as e: print(f"[Home] Erro Recentes: {e}")

    # 4. NOVOS EPISÓDIOS (Direto do banco)
    try:
        eps = db.query(SerieDB).order_by(desc(SerieDB.id)).limit(15).all()
        add("novos_episodios", "📺 Novos Episódios", eps, "serie", "tv")
    except Exception as e: print(f"[Home] Erro Novos Ep: {e}")

    # 5. EM ALTA (Misturado)
    try:
        em_alta = db.query(FilmeDB).order_by(desc(FilmeDB.rating_avg)).limit(8).all() + db.query(SerieDB).order_by(desc(SerieDB.rating_avg)).limit(7).all()
        add("em_alta", "🔥 O Que Todo Mundo Está Assistindo", em_alta, "misto", "fire")
    except Exception as e: print(f"[Home] Erro Em Alta: {e}")

    # 6. CATEGORIAS (Netflix, etc)
    try:
        cats_f = [r[0] for r in db.query(FilmeDB.coletany).distinct().all() if r[0] and len(r[0]) > 2 and not r[0].isdigit()]
        for cat in cats_f[:3]:
            items = db.query(FilmeDB).filter(FilmeDB.coletany == cat).limit(10).all()
            add(f"cat_{cat}", f"📺 Coleção {cat}", items, "filme", "folder")
    except Exception as e: print(f"[Home] Erro Categorias: {e}")

    # 🚨 SE TUDO FALHAR, CRIA UMA COLEÇÃO GERAL PARA NÃO DEIXAR A TELA VAZIA
    if not colecoes:
        try:
            todos = db.query(FilmeDB).limit(20).all() + db.query(SerieDB).limit(20).all()
            add("geral", "📚 Catálogo Completo", todos, "misto", "folder")
        except: pass

    return colecoes

async def build_home_async(db: Session, force: bool = False) -> dict:
    global _HOME_CACHE, _HOME_CACHE_TS
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < CACHE_TTL:
        return _HOME_CACHE

    hero = _construir_hero(db)
    colecoes = _construir_colecoes(db)
    
    resposta = {
        "gerado_em": time.time(), 
        "tmdb_ativo": False, # Desativado para modo de emergência
        "hero": hero, 
        "colecoes": colecoes,
        "stats": {"total_filmes": db.query(FilmeDB).count(), "total_series": db.query(SerieDB).count()}
    }

    _HOME_CACHE = resposta
    _HOME_CACHE_TS = time.time()
    print(f"[Home] Emergência: Enviando {len(colecoes)} coleções.")
    return resposta