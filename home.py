import time
import json
from sqlalchemy.orm import Session
from sqlalchemy import desc
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL

_HOME_CACHE = {}
_HOME_CACHE_TS = 0
CACHE_TTL = 900  # 15 minutos

def _serializar_leve(item):
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
    
    def add(id_, titulo, items, tipo_padrao="misto", icon="folder", min_itens=10):
        if not items: return
        
        # Remove duplicados
        vistos = set()
        items_unicos = []
        for it in items:
            chave = str(it.tmdb_id) if it.tmdb_id else (it.title.lower() if it.title else str(it.id))
            if chave not in vistos:
                vistos.add(chave)
                items_unicos.append(it)
        
        if len(items_unicos) < min_itens: return 
        
        colecoes.append({
            "id": id_,
            "titulo": titulo, # Nome bonito
            "icon": icon,
            "tipo": tipo_padrao,
            "items": [_serializar_leve(it) for it in items_unicos]
        })

    # 1. Recomendações
    rec_f = db.query(FilmeDB).filter(FilmeDB.rating_avg >= 6.5, FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.year)).limit(30).all()
    rec_s = db.query(SerieDB).filter(SerieDB.rating_avg >= 6.5, SerieDB.poster.isnot(None)).order_by(desc(SerieDB.year)).limit(30).all()
    add("recomendacoes", "✨ Escolhidos a Dedo para Você", sorted(rec_f + rec_s, key=lambda x: (x.year or 0, x.rating_avg or 0), reverse=True), "misto", "sparkles")

    # 2. Em Alta
    top_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(30).all()
    top_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(30).all()
    add("em_alta", "🔥 O Que Todo Mundo Está Assistindo", sorted(top_f + top_s, key=lambda x: x.rating_avg or 0, reverse=True), "misto", "fire")

    # 3. Top 10 Filmes
    add("top10_filmes", "🏆 As Melhores Críticas (Filmes)", top_f, "filme", "star", min_itens=10)

    # 4. Top 10 Séries
    add("top10_series", "🏆 As Melhores Críticas (Séries)", top_s, "serie", "star", min_itens=10)

    # 5. Recentes
    recentes_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.id)).limit(30).all()
    add("filmes_recentes", "🆕 Chegou Agora no Catálogo (Filmes)", recentes_f, "filme", "clock")

    recentes_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.id)).limit(30).all()
    add("series_recentes", "🆕 Chegou Agora no Catálogo (Séries)", recentes_s, "serie", "clock")

    # 6. Gêneros com nomes mais atraentes
    def get_by_genre(genre):
        f = db.query(FilmeDB).filter(FilmeDB.genres.like(f'%{genre}%'), FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(30).all()
        s = db.query(SerieDB).filter(SerieDB.genres.like(f'%{genre}%'), SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(30).all()
        return sorted(f + s, key=lambda x: x.rating_avg or 0, reverse=True)

    add("acao", "💥 Adrenalina Pura: Ação e Aventura", get_by_genre("Ação"), "misto", "action")
    add("comedia", "😂 Para Dar Muitas Risadas: Comédia", get_by_genre("Comédia"), "misto", "comedy")
    add("terror", "👻 Prepare o Coração: Terror e Suspense", get_by_genre("Terror"), "misto", "horror")
    add("animacao", "🎨 Mundo Mágico: Animação e Família", get_by_genre("Animação"), "misto", "animation")

    # ⚡ 7. FRANQUIAS COM NOMES ÉPICOS
    franquias_config = {
        "Harry Potter": "⚡ Bem-vindo a Hogwarts: A Saga Harry Potter",
        "Os Simpsons": "🍩 A Família Amarela Mais Famosa: Os Simpsons",
        "Vingadores": "🛡️ Os Heróis Mais Poderosos da Terra: Vingadores",
        "Velozes e Furiosos": "🏎️ Adrenalina Sobre Rodas: Velozes e Furiosos",
        "Missão Impossível": "🕵️ Sua Missão, Se Aceitar: Missão Impossível",
        "Resident Evil": "🧟 O Pesadelo Biológico: Resident Evil",
        "Star Wars": "🌌 Que a Força Esteja com Você: Star Wars",
        "Homem-Aranha": "🕷️ Com Grandes Poderes: Homem-Aranha",
        "O Senhor dos Anéis": "💍 A Jornada Épica: O Senhor dos Anéis",
        "Matrix": "💊 A Realidade é uma Ilusão: Matrix",
        "John Wick": "🔫 Ação Sem Limites: A Saga John Wick",
        "Transformers": "🤖 Robôs em Guerra: Transformers",
        "Jurassic": "🦖 Sobreviva ao Caos: Jurassic World"
    }

    for franquia, nome_epico in franquias_config.items():
        items = db.query(FilmeDB).filter(FilmeDB.title.ilike(f"{franquia}%"), FilmeDB.poster.isnot(None)).order_by(FilmeDB.year).all()
        
        # ⚠️ AQUI: Franquias precisam de no mínimo 3 itens para aparecer.
        # (Coloquei 3 porque Harry Potter tem 8 filmes e Senhor dos Anéis tem 3).
        add(f"franquia_{franquia}", nome_epico, items, "filme", "collection", min_itens=3)
            
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