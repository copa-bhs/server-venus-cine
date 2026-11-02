import time
import asyncio
import json
from datetime import datetime
from sqlalchemy.orm import Session
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL
from proxy import slugify

def _card_simples(item, tipo):
    return {
        "id": str(item.iptv_id),
        "tipo": "filme" if tipo == "filme" else "serie",
        "titulo": item.title,
        "capa": item.poster,
        "banner": item.backdrop,
        "logo": item.logo,
        "ano": item.year,
        "score": item.rating_avg
    }

def _por_ano(itens, n=15):
    validos = [i for i in itens if i.poster and i.year]
    validos.sort(key=lambda x: x.year, reverse=True)
    return validos[:n]

def _por_genero(itens, generos_busca, n=15):
    validos = []
    for i in itens:
        if not i.poster or not i.genres: continue
        try:
            g_list = json.loads(i.genres)
            if any(g.lower() in [gb.lower() for gb in generos_busca] for g in g_list):
                validos.append(i)
        except: pass
    validos.sort(key=lambda x: x.rating_avg, reverse=True)
    return validos[:n]

def _construir_colecoes(db: Session):
    filmes = db.query(FilmeDB).all()
    series = db.query(SerieDB).all()
    todos = filmes + series
    
    colecoes = []
    def add(id_, titulo, itens, tipo_padrao):
        if not itens: return
        colecoes.append({
            "id": id_,
            "titulo": titulo,
            "total": len(itens),
            "items": [_card_simples(it, tipo_padrao) for it in itens]
        })

    add("top10_filmes", "🎬 Filmes", _por_ano(filmes, 30)[:10], "filme")
    add("top10_series", "📺 Séries", _por_ano(series, 30)[:10], "serie")
    add("em_alta", "🔥 Em Alta", sorted(todos, key=lambda x: x.rating_avg or 0, reverse=True)[:15], "filme")
    add("lancamentos", "🆕 Lançamentos", _por_ano(filmes, 20), "filme")
    add("acao", "💥 Ação", _por_genero(todos, ["Ação", "Action", "Aventura"]), "filme")
    add("comedia", "😂 Comédia", _por_genero(todos, ["Comédia", "Comedy"]), "filme")
    add("drama", "🎭 Drama", _por_genero(todos, ["Drama"]), "filme")
    add("terror", "👻 Terror", _por_genero(todos, ["Terror", "Horror", "Suspense"]), "filme")
    add("ficcao", "🚀 Ficção", _por_genero(todos, ["Ficção científica", "Sci-Fi", "Ficção"]), "filme")
    add("animacao", "🎨 Animação", _por_genero(todos, ["Animação", "Animation", "Anime", "Infantil"]), "filme")
    add("romance", "💕 Romance", _por_genero(todos, ["Romance", "Romântico"]), "filme")
    
    return colecoes

async def build_home_async(db: Session, force_hero: bool = False) -> dict:
    slides = db.query(SlideDB).filter(SlideDB.ativo == True).order_by(SlideDB.ordem).all()
    hero = []
    for s in slides:
        ref = db.query(FilmeDB).get(s.ref_id) if s.ref_tipo == "filme" else db.query(SerieDB).get(s.ref_id)
        if not ref: continue
        
        slug = slugify(ref.title or "", ref.year)
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

    return {
        "gerado_em": time.time(),
        "tmdb_ativo": TMDB_ENABLED,
        "hero": hero,
        "colecoes": _construir_colecoes(db),
        "stats": {"total_filmes": db.query(FilmeDB).count(), "total_series": db.query(SerieDB).count()}
    }