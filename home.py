import time
import json
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL

_HOME_CACHE = {}
_HOME_CACHE_TS = 0
CACHE_TTL = 900  # 15 minutos

def _serializar_leve(item):
    """Versão SEM link de vídeo, para usar nas prateleiras da Home."""
    try: generos = json.loads(item.genres) if item.genres else []
    except: generos = []
    
    return {
        "iptv_id": str(item.iptv_id),
        "title": item.title,
        "poster": item.poster,
        "backdrop": item.backdrop,
        "logo": item.logo,
        "year": item.year,
        "rating_avg": item.rating_avg,
        "age_rating": item.age_rating,
        "genres": generos,
        "type": item.type
    }

def _construir_hero(db: Session) -> list:
    slides = db.query(SlideDB).filter(SlideDB.ativo == True).order_by(SlideDB.ordem).all()
    hero = []
    for s in slides:
        ref = db.query(FilmeDB).get(s.ref_id) if s.ref_tipo == "filme" else db.query(SerieDB).get(s.ref_id)
        if not ref: continue
        
        hero.append({
            "id": str(ref.iptv_id),
            "tipo": "filme" if s.ref_tipo == "filme" else "serie",
            "titulo": s.titulo or ref.title,
            "banner": s.banner or ref.backdrop,
            "capa": ref.poster,
            "logo": s.logo or ref.logo,
            "sinopse": s.sinopse or ref.description,
            "score": ref.rating_avg,
            "classificacao": ref.age_rating,
            "ano": ref.year,
            "generos": json.loads(ref.genres) if ref.genres else []
        })
    return hero

def _construir_colecoes(db: Session) -> list:
    colecoes = []
    
    # Função atualizada para receber o nome do ícone em vez do emoji no título
    def add(id_, titulo, items, tipo_padrao="misto", icon="folder"):
        if not items: return
        colecoes.append({
            "id": id_,
            "titulo": titulo, # ⚡ Título limpo, sem emoji
            "icon": icon,     # ⚡ Ícone para o App renderizar em SVG
            "tipo": tipo_padrao,
            "items": [_serializar_leve(it) for it in items]
        })

    # 1. Recomendações do Algoritmo (Boa nota + Recente)
    rec_filmes = db.query(FilmeDB).filter(FilmeDB.rating_avg >= 6.5, FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.year), desc(FilmeDB.rating_avg)).limit(15).all()
    rec_series = db.query(SerieDB).filter(SerieDB.rating_avg >= 6.5, SerieDB.poster.isnot(None)).order_by(desc(SerieDB.year), desc(SerieDB.rating_avg)).limit(15).all()
    misto_rec = sorted(rec_filmes + rec_series, key=lambda x: (x.year or 0, x.rating_avg or 0), reverse=True)
    add("recomendacoes", "Recomendações Inteligentes para Você", misto_rec[:20], "misto", "sparkles")

    # 2. Em Alta (Misto, melhores notas)
    top_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
    top_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(10).all()
    add("em_alta", "Em Alta", sorted(top_f + top_s, key=lambda x: x.rating_avg or 0, reverse=True)[:20], "misto", "fire")

    # 3. Top 10 Filmes
    add("top10_filmes", "Top 10 Filmes", top_f[:10], "filme", "star")

    # 4. Top 10 Séries
    add("top10_series", "Top 10 Séries", top_s[:10], "serie", "star")

    # 5. Filmes Adicionados Recentemente
    recentes_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.id)).limit(15).all()
    add("filmes_recentes", "Filmes Adicionados Recentemente", recentes_f, "filme", "clock")

    # 6. Séries Adicionadas Recentemente
    recentes_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.id)).limit(15).all()
    add("series_recentes", "Séries Adicionadas Recentemente", recentes_s, "serie", "clock")

    # 7. Coleções por Gênero
    def get_by_genre(genre):
        f = db.query(FilmeDB).filter(FilmeDB.genres.like(f'%{genre}%'), FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
        s = db.query(SerieDB).filter(SerieDB.genres.like(f'%{genre}%'), SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(10).all()
        return sorted(f + s, key=lambda x: x.rating_avg or 0, reverse=True)[:15]

    add("acao", "Ação e Aventura", get_by_genre("Ação"), "misto", "action")
    add("comedia", "Comédia", get_by_genre("Comédia"), "misto", "comedy")
    add("terror", "Terror e Suspense", get_by_genre("Terror"), "misto", "horror")
    add("animacao", "Animação e Família", get_by_genre("Animação"), "misto", "animation")

    # ⚡ 8. FRANQUIAS (Harry Potter, Simpsons, Vingadores, etc.)
    # Lista de franquias populares para buscar no banco
    franquias = [
        "Harry Potter", "Os Simpsons", "Vingadores", "Velozes e Furiosos", 
        "Missão Impossível", "Resident Evil", "Star Wars", "Homem-Aranha",
        "O Senhor dos Anéis", "Matrix", "John Wick", "Transformers", "Jurassic"
    ]
    
    for franquia in franquias:
        # Busca filmes que começam com o nome da franquia
        items = db.query(FilmeDB).filter(FilmeDB.title.ilike(f"{franquia}%"), FilmeDB.poster.isnot(None)).order_by(FilmeDB.year).all()
        # Se tiver 2 ou mais filmes, cria a prateleira (ordenado por ano para ficar 1, 2, 3...)
        if len(items) >= 2:
            add(f"franquia_{franquia}", f"Coleção {franquia}", items[:20], "filme", "collection")
            
    return colecoes

async def build_home_async(db: Session, force: bool = False) -> dict:
    global _HOME_CACHE, _HOME_CACHE_TS
    
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < CACHE_TTL:
        return _HOME_CACHE

    hero = _construir_hero(db)
    colecoes = _construir_colecoes(db)
    
    resposta = {
        "gerado_em": time.time(),
        "tmdb_ativo": TMDB_ENABLED,
        "hero": hero,
        "colecoes": colecoes,
        "stats": {
            "total_filmes": db.query(FilmeDB).count(),
            "total_series": db.query(SerieDB).count()
        }
    }

    _HOME_CACHE = resposta
    _HOME_CACHE_TS = time.time()
    
    return resposta