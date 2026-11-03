import time
import json
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL

_HOME_CACHE = {}
_HOME_CACHE_TS = 0
CACHE_TTL = 900

def _serializar_leve(item):
    try: generos = json.loads(item.genres) if item.genres else []
    except: generos = []
    
    tipo_final = "movie" if item.stream_type == "movie" else "series"
    
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
        "type": tipo_final
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
    
    def add(id_, titulo, items, tipo_padrao="misto", icon="folder", min_itens=5):
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

    # 1. Recomendações (Nota >= 6.5)
    rec_f = db.query(FilmeDB).filter(FilmeDB.rating_avg >= 6.5, FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.year)).limit(30).all()
    rec_s = db.query(SerieDB).filter(SerieDB.rating_avg >= 6.5, SerieDB.poster.isnot(None)).order_by(desc(SerieDB.year)).limit(30).all()
    add("recomendacoes", "✨ Escolhidos a Dedo para Você", sorted(rec_f + rec_s, key=lambda x: (x.year or 0, x.rating_avg or 0), reverse=True), "misto", "sparkles")

    # 2. Em Alta
    top_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(30).all()
    top_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(30).all()
    add("em_alta", "🔥 O Que Todo Mundo Está Assistindo", sorted(top_f + top_s, key=lambda x: x.rating_avg or 0, reverse=True), "misto", "fire")

    # 3. Top 10
    add("top10_filmes", "🏆 As Melhores Críticas (Filmes)", top_f, "filme", "star", min_itens=10)
    add("top10_series", "🏆 As Melhores Críticas (Séries)", top_s, "serie", "star", min_itens=10)

    # 4. Recentes
    recentes_f = db.query(FilmeDB).filter(FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.id)).limit(30).all()
    add("filmes_recentes", "🆕 Chegou Agora no Catálogo (Filmes)", recentes_f, "filme", "clock")

    recentes_s = db.query(SerieDB).filter(SerieDB.poster.isnot(None)).order_by(desc(SerieDB.id)).limit(30).all()
    add("series_recentes", "🆕 Chegou Agora no Catálogo (Séries)", recentes_s, "serie", "clock")

    # 5. Gêneros
    def get_by_genre(genre):
        f = db.query(FilmeDB).filter(FilmeDB.genres.like(f'%{genre}%'), FilmeDB.poster.isnot(None)).order_by(desc(FilmeDB.rating_avg)).limit(30).all()
        s = db.query(SerieDB).filter(SerieDB.genres.like(f'%{genre}%'), SerieDB.poster.isnot(None)).order_by(desc(SerieDB.rating_avg)).limit(30).all()
        return sorted(f + s, key=lambda x: x.rating_avg or 0, reverse=True)

    add("acao", "💥 Adrenalina Pura: Ação e Aventura", get_by_genre("Ação"), "misto", "action")
    add("comedia", "😂 Para Dar Muitas Risadas: Comédia", get_by_genre("Comédia"), "misto", "comedy")
    add("terror", "👻 Prepare o Coração: Terror e Suspense", get_by_genre("Terror"), "misto", "horror")
    add("animacao", "🎨 Mundo Mágico: Animação e Família", get_by_genre("Animação"), "misto", "animation")

    # 6. Franquias
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
        add(f"franquia_{franquia}", nome_epico, items, "filme", "collection", min_itens=3)

    # ⚡ 7. STREAMINGS E CATEGORIAS DO IPTV (Netflix, Amazon, etc)
    # Lista de categorias que devem ser IGNORADAS (anos, qualidades, termos genéricos)
    ignorar_cats = [
        "GERAL", "DESCONHECIDA", "OUTROS", "DIVERSOS", "LANÇAMENTOS", "LANCAMENTOS", 
        "FILMES", "SÉRIES", "FILME", "SÉRIE", "4K", "FHD", "HD", "SD", 
        "NACIONAL", "DUBLADO", "LEGENDADO", "2020", "2021", "2022", "2023", "2024", "2025", "2026"
    ]
    
    # Pega todas as categorias únicas de filmes e séries no banco
    cats_f = [r[0] for r in db.query(FilmeDB.coletany).distinct().all() if r[0]]
    cats_s = [r[0] for r in db.query(SerieDB.coletany).distinct().all() if r[0]]
    todas_cats = set(cats_f + cats_s)
    
    for cat in todas_cats:
        if not cat: continue
        cat_upper = cat.upper()
        
        # Verifica se a categoria está na lista de ignoradas ou se é um ano (ex: "2024")
        if cat_upper in ignorar_cats or cat.isdigit(): 
            continue
            
        # Busca os itens dessa categoria
        items_f = db.query(FilmeDB).filter(FilmeDB.coletany == cat).order_by(desc(FilmeDB.rating_avg)).limit(20).all()
        items_s = db.query(SerieDB).filter(SerieDB.coletany == cat).order_by(desc(SerieDB.rating_avg)).limit(20).all()
        
        # Adiciona a coleção (ex: "Coleção Netflix")
        add(f"streaming_{cat}", f"📺 Coleção {cat}", items_f + items_s, "misto", "folder", min_itens=5)

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