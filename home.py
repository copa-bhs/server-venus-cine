import time
import json
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL

_HOME_CACHE = {}
_HOME_CACHE_TS = 0
CACHE_TTL = 900

def limpar_cache():
    global _HOME_CACHE, _HOME_CACHE_TS
    _HOME_CACHE = {}
    _HOME_CACHE_TS = 0
    print("[Home] Cache da Home limpo.")

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

def _construir_colecoes(db: Session) -> list:
    colecoes = []
    MAX_COLECOES = 12 # ⚡ Limite de 12 coleções

    def add(id_, titulo, items, tipo_padrao="misto", icon="folder", min_itens=3):
        if len(colecoes) >= MAX_COLECOES: return
        if not items: return
        
        vistos = set()
        items_unicos = []
        for it in items:
            chave = str(it.tmdb_id) if it.tmdb_id else (it.title.lower() if it.title else str(it.id))
            if chave not in vistos:
                vistos.add(chave)
                items_unicos.append(it)
                
        if len(items_unicos) < min_itens: return 
        
        colecoes.append({
            "id": id_, "titulo": titulo, "icon": icon, "tipo": tipo_padrao,
            "items": [_serializar_leve(it) for it in items_unicos]
        })

    # 1 e 2: TOP 10
    top_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
    add("top10_filmes", "🏆 Top 10 Filmes", top_f, "filme", "star", min_itens=1)

    top_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(10).all()
    add("top10_series", "🏆 Top 10 Séries", top_s, "serie", "star", min_itens=1)

    # 3: EM ALTA
    add("em_alta", "🔥 O Que Todo Mundo Está Assistindo", sorted(top_f + top_s, key=lambda x: x.rating_avg or 0, reverse=True), "misto", "fire")

    # 4: TENDÊNCIAS DA SEMANA (Recentes + Boa Nota)
    ano_limite = datetime.now().year - 1
    tend_f = db.query(FilmeDB).filter(FilmeDB.rating_avg >= 6.0, FilmeDB.year >= ano_limite, FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.year)).limit(15).all()
    tend_s = db.query(SerieDB).filter(SerieDB.rating_avg >= 6.0, SerieDB.year >= ano_limite, SerieDB.poster.isnot(None)).order_by(desc(SerieDB.year)).limit(15).all()
    add("tendencias", "📈 Tendências da Semana", sorted(tend_f + tend_s, key=lambda x: (x.year or 0, x.rating_avg or 0), reverse=True), "misto", "trending-up")

    # 5: RECOMENDAÇÕES
    rec_f = db.query(FilmeDB).filter(FilmeDB.rating_avg >= 6.5, FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.year)).limit(20).all()
    rec_s = db.query(SerieDB).filter(SerieDB.rating_avg >= 6.5, SerieDB.poster.isnot(None)).order_by(desc(SerieDB.year)).limit(20).all()
    add("recomendacoes", "✨ Escolhidos a Dedo para Você", sorted(rec_f + rec_s, key=lambda x: (x.year or 0, x.rating_avg or 0), reverse=True), "misto", "sparkles")

    # 6 e 7: RECENTES
    recentes_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.id)).limit(20).all()
    add("filmes_recentes", "🆕 Chegou Agora (Filmes)", recentes_f, "filme", "clock")

    recentes_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.id)).limit(20).all()
    add("series_recentes", "🆕 Chegou Agora (Séries)", recentes_s, "serie", "clock")

    # 8 a 12: STREAMINGS (Prioridade para Netflix, Amazon, etc)
    ignorar = ["GERAL", "FILMES", "SÉRIES", "4K", "FHD", "HD", "SD", "LANÇAMENTOS", "NACIONAL", "2020", "2021", "2022", "2023", "2024", "2025", "2026", "DUBLADO", "LEGENDADO", "DOCUMENTÁRIOS", "ANIMES"]
    
    cats_f = [r[0] for r in db.query(FilmeDB.coletany).distinct().all() if r[0]]
    cats_s = [r[0] for r in db.query(SerieDB.coletany).distinct().all() if r[0]]
    todas_cats = set(cats_f + cats_s)

    # Lista de prioridades para garantir que streamings entrem primeiro
    prioridade = ["Netflix", "Amazon", "Prime", "Disney", "HBO", "Max", "Apple", "Globo", "Paramount", "Star"]
    
    def score_cat(c):
        for i, p in enumerate(prioridade):
            if p.lower() in c.lower(): return i
        return 99
        
    cats_ordenadas = sorted(list(todas_cats), key=score_cat)

    for cat in cats_ordenadas:
        if len(colecoes) >= MAX_COLECOES: break
        if not cat or cat.upper() in ignorar or cat.isdigit(): continue
        
        items_f = db.query(FilmeDB).filter(FilmeDB.coletany == cat).order_by(desc(FilmeDB.rating_avg)).limit(20).all()
        items_s = db.query(SerieDB).filter(SerieDB.coletany == cat).order_by(desc(SerieDB.rating_avg)).limit(20).all()
        add(f"streaming_{cat}", f"📺 Coleção {cat}", items_f + items_s, "misto", "folder", min_itens=3)

    return colecoes

async def build_home_async(db: Session, force: bool = False) -> dict:
    global _HOME_CACHE, _HOME_CACHE_TS
    
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < CACHE_TTL:
        return _HOME_CACHE

    hero = _construir_hero(db)
    colecoes = _construir_colecoes(db)
    
    resposta = {
        "gerado_em": time.time(), "tmdb_ativo": TMDB_ENABLED, "hero": hero, "colecoes": colecoes,
        "stats": {"total_filmes": db.query(FilmeDB).count(), "total_series": db.query(SerieDB).count()}
    }

    _HOME_CACHE = resposta
    _HOME_CACHE_TS = time.time()
    return resposta