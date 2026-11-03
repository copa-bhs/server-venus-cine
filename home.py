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
    # Mantém a lógica dos slides manuais para o Slider principal
    slides = db.query(SlideDB).filter(SlideDB.ativo == True).order_by(SlideDB.ordem).all()
    hero = []
    for s in slides:
        ref = db.query(FilmeDB).get(s.ref_id) if s.ref_tipo == "filme" else db.query(SerieDB).get(s.ref_id)
        if not ref: continue
        
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
            "generos": json.loads(ref.genres) if ref.genres else []
        })
    return hero

def _construir_colecoes(db: Session) -> list:
    colecoes = []
    
    def add(id_, titulo, items, tipo_padrao="misto"):
        if not items: return
        colecoes.append({
            "titulo": titulo,
            "tipo": tipo_padrao,
            "items": [_serializar_leve(it) for it in items]
        })

    # 1. Recomendações do Algoritmo (Boa nota + Recente)
    rec_filmes = db.query(FilmeDB).filter(FilmeDB.rating_avg >= 6.5, FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.year), desc(FilmeDB.rating_avg)).limit(15).all()
    rec_series = db.query(SerieDB).filter(SerieDB.rating_avg >= 6.5, SerieDB.poster.isnot(None)).order_by(desc(SerieDB.year), desc(SerieDB.rating_avg)).limit(15).all()
    misto_rec = sorted(rec_filmes + rec_series, key=lambda x: (x.year or 0, x.rating_avg or 0), reverse=True)
    add("recomendacoes", "🍿 Recomendações Inteligentes para Você", misto_rec[:20], "misto")

    # 2. Em Alta (Misto, melhores notas)
    top_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
    top_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(10).all()
    add("em_alta", "🔥 Em Alta", sorted(top_f + top_s, key=lambda x: x.rating_avg or 0, reverse=True)[:20], "misto")

    # 3. Top 10 Filmes
    add("top10_filmes", "🏆 Top 10 Filmes", top_f[:10], "filme")

    # 4. Top 10 Séries
    add("top10_series", "🏆 Top 10 Séries", top_s[:10], "serie")

    # 5. Filmes Adicionados Recentemente
    recentes_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.id)).limit(15).all()
    add("filmes_recentes", "🎬 Filmes Adicionados Recentemente", recentes_f, "filme")

    # 6. Séries Adicionadas Recentemente
    recentes_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.id)).limit(15).all()
    add("series_recentes", "📺 Séries Adicionadas Recentemente", recentes_s, "serie")

    # 7. Coleções por Gênero (Puxando do TMDB JSON)
    def get_by_genre(genre):
        f = db.query(FilmeDB).filter(FilmeDB.genres.like(f'%{genre}%'), FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
        s = db.query(SerieDB).filter(SerieDB.genres.like(f'%{genre}%'), SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(10).all()
        return sorted(f + s, key=lambda x: x.rating_avg or 0, reverse=True)[:15]

    add("acao", "💥 Ação e Aventura", get_by_genre("Ação"), "misto")
    add("comedia", "😂 Comédia", get_by_genre("Comédia"), "misto")
    add("terror", "👻 Terror e Suspense", get_by_genre("Terror"), "misto")
    add("animacao", "🎨 Animação e Família", get_by_genre("Animação"), "misto")

    # 8. Coleções por Categoria do IPTV (coletany)
    cats = db.query(FilmeDB.coletany).distinct().limit(5).all()
    for cat in cats:
        c_nome = cat[0]
        if not c_nome or c_nome == "Manual": continue
        items = db.query(FilmeDB).filter(FilmeDB.coletany == c_nome).order_by(desc(FilmeDB.rating_avg)).limit(10).all()
        add(f"cat_{c_nome}", f"📁 Coleção {c_nome}", items, "filme")

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