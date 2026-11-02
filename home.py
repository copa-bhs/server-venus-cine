import time
import json
from sqlalchemy.orm import Session
from sqlalchemy import desc
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL
from proxy import slugify

# Cache em memória para a Home (15 minutos)
_HOME_CACHE = {}
_HOME_CACHE_TS = 0
CACHE_TTL = 900  # 15 minutos

def _serializar_rico(item):
    """Converte o objeto do banco para o formato de card que o App espera."""
    try:
        generos = json.loads(item.genres) if item.genres else []
    except:
        generos = []
    
    return {
        "iptv_id": str(item.iptv_id),
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
        "genres": generos,
        "age_rating": item.age_rating
    }

def _construir_colecoes(db: Session) -> list:
    colecoes = []

    # A) 🔥 Em Alta (Misto, Top avaliados)
    top_filmes = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
    top_series = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(10).all()
    misto = sorted(top_filmes + top_series, key=lambda x: x.rating_avg or 0, reverse=True)[:20]
    
    if misto:
        colecoes.append({
            "titulo": "🔥 Em Alta",
            "tipo": "misto",
            "items": [_serializar_rico(x) for x in misto]
        })

    # B) Top 10 Filmes
    if top_filmes:
        colecoes.append({
            "titulo": "Top 10 Filmes",
            "tipo": "filme",
            "items": [_serializar_rico(x) for x in top_filmes[:10]]
        })

    # C) Top 10 Séries
    if top_series:
        colecoes.append({
            "titulo": "Top 10 Séries",
            "tipo": "serie",
            "items": [_serializar_rico(x) for x in top_series[:10]]
        })

    # D) Filmes Adicionados Recentemente
    filmes_recentes = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.year), desc(FilmeDB.rating_avg)).limit(15).all()
    if filmes_recentes:
        colecoes.append({
            "titulo": "Filmes Adicionados Recentemente",
            "tipo": "filme",
            "items": [_serializar_rico(x) for x in filmes_recentes]
        })

    # E) Séries Recentes
    series_recentes = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.year), desc(SerieDB.rating_avg)).limit(15).all()
    if series_recentes:
        colecoes.append({
            "titulo": "Séries Recentes",
            "tipo": "serie",
            "items": [_serializar_rico(x) for x in series_recentes]
        })

    return colecoes

def _construir_hero(db: Session) -> list:
    slides = db.query(SlideDB).filter(SlideDB.ativo == True).order_by(SlideDB.ordem).all()
    hero = []
    for s in slides:
        ref = db.query(FilmeDB).get(s.ref_id) if s.ref_tipo == "filme" else db.query(SerieDB).get(s.ref_id)
        if not ref: continue
        
        slug = ref.slug or slugify(ref.title or "", ref.year)
        base = (PUBLIC_BASE_URL or "").rstrip("/")
        
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
            "generos": json.loads(ref.genres) if ref.genres else [],
            "url_stream": f"{base}/stream/{slug}.mp4" if base else f"/stream/{slug}.mp4",
        })
    return hero

async def build_home_async(db: Session, force: bool = False) -> dict:
    global _HOME_CACHE, _HOME_CACHE_TS
    
    # Se o cache for válido (menos de 15 min) e não forçarmos atualização, retorna o cache
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < CACHE_TTL:
        return _HOME_CACHE

    # Monta o JSON do zero se o cache expirou
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

    # Salva no cache
    _HOME_CACHE = resposta
    _HOME_CACHE_TS = time.time()
    
    return resposta

def limpar_cache_home():
    """Função para ser chamada quando um item for editado/adicionado, para limpar o cache."""
    global _HOME_CACHE, _HOME_CACHE_TS
    _HOME_CACHE = {}
    _HOME_CACHE_TS = 0