import time
import json
import httpx
import asyncio
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import desc
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, TMDB_API_KEY, TMDB_LANGUAGE, PUBLIC_BASE_URL

_HOME_CACHE = {}
_HOME_CACHE_TS = 0
CACHE_TTL = 60

def limpar_cache():
    global _HOME_CACHE, _HOME_CACHE_TS
    _HOME_CACHE = {}
    _HOME_CACHE_TS = 0
    print("[Home] Cache da Home limpo.")

def _parse_genres(genres_raw):
    if not genres_raw:
        return []
    if isinstance(genres_raw, list):
        return genres_raw
    try:
        parsed = json.loads(genres_raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []

def _serializar_leve(item, badge_especial=None):
    """Serializa o item e injeta dinamicamente o selo inteligente gerado pelo backend."""
    tipo_final = "movie" if getattr(item, "stream_type", "movie") == "movie" else "series"
    return {
        "iptv_id": str(getattr(item, "iptv_id", None) or item.id),
        "title": getattr(item, "title", "") or "Sem título",
        "poster": getattr(item, "poster", None),
        "backdrop": getattr(item, "backdrop", None),
        "logo": getattr(item, "logo", None),
        "year": getattr(item, "year", None),
        "rating_avg": float(getattr(item, "rating_avg", 0.0) or 0.0),
        "age_rating": getattr(item, "age_rating", None),
        "genres": _parse_genres(getattr(item, "genres", None)),
        "type": tipo_final,
        "badge": badge_especial # 🧠 O cérebro envia a tag pronta para o front-end exibir
    }

def _construir_hero(db: Session, trending_filmes: list, trending_series: list) -> list:
    slides = db.query(SlideDB).filter(SlideDB.ativo == True).order_by(SlideDB.ordem).all()
    hero = []
    vistos_ids = set()

    for s in slides:
        ref = db.query(FilmeDB).get(s.ref_id) if s.ref_tipo == "filme" else db.query(SerieDB).get(s.ref_id)
        if not ref:
            continue
        item_id = str(getattr(ref, "iptv_id", None) or ref.id)
        if item_id in vistos_ids:
            continue
        vistos_ids.add(item_id)

        hero.append({
            "id": item_id,
            "tipo": "filme" if s.ref_tipo == "filme" else "serie",
            "titulo": s.titulo or ref.title,
            "banner": s.banner or ref.backdrop or ref.poster,
            "capa": ref.poster,
            "logo": s.logo or ref.logo,
            "sinopse": s.sinopse or getattr(ref, "description", "") or "",
            "score": float(ref.rating_avg or 0.0),
            "classificacao": ref.age_rating,
            "ano": ref.year,
            "generos": _parse_genres(ref.genres)
        })

    TARGET_HERO_COUNT = 7
    if len(hero) < TARGET_HERO_COUNT:
        extras_f = _get_top10_misto(db, FilmeDB, trending_filmes)
        extras_s = _get_top10_misto(db, SerieDB, trending_series)
        extras_mix = sorted(extras_f + extras_s, key=lambda x: (x.rating_avg or 0), reverse=True)

        for ref in extras_mix:
            if len(hero) >= TARGET_HERO_COUNT:
                break
            item_id = str(getattr(ref, "iptv_id", None) or ref.id)
            if item_id in vistos_ids:
                continue
            vistos_ids.add(item_id)

            tipo_ref = "filme" if isinstance(ref, FilmeDB) else "serie"
            hero.append({
                "id": item_id,
                "tipo": tipo_ref,
                "titulo": getattr(ref, "title", "Sem Título"),
                "banner": getattr(ref, "backdrop", None) or getattr(ref, "poster", None),
                "capa": getattr(ref, "poster", None),
                "logo": getattr(ref, "logo", None),
                "sinopse": getattr(ref, "description", "") or "",
                "score": float(getattr(ref, "rating_avg", 0.0) or 0.0),
                "classificacao": getattr(ref, "age_rating", None),
                "ano": getattr(ref, "year", None),
                "generos": _parse_genres(getattr(ref, "genres", None))
            })

    return hero

async def _fetch_trending_tmdb(tipo: str) -> list:
    if not TMDB_ENABLED:
        return []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"https://api.themoviedb.org/3/trending/{tipo}/day",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE}
            )
            if r.status_code == 200:
                return [item['id'] for item in r.json().get('results', [])][:25]
    except Exception:
        pass
    return []

async def _fetch_on_the_air_tmdb() -> list:
    """Busca séries que estão ativas com episódios recentes no TMDb (The TV Movie Database)"""
    if not TMDB_ENABLED:
        return []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://api.themoviedb.org/3/tv/on_the_air",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE}
            )
            if r.status_code == 200:
                return [item['id'] for item in r.json().get('results', [])][:25]
    except Exception:
        pass
    return []

def _get_top10_misto(db: Session, Tabela, trending_ids: list):
    itens = []
    vistos_ids = set()

    if trending_ids:
        db_itens = db.query(Tabela).filter(Tabela.tmdb_id.in_(trending_ids)).all()
        map_db = {str(it.tmdb_id): it for it in db_itens if it.tmdb_id is not None}
        for t_id in trending_ids:
            item = map_db.get(str(t_id))
            if item and item.id not in vistos_ids:
                vistos_ids.add(item.id)
                itens.append(item)
            if len(itens) >= 10:
                break

    if len(itens) < 10:
        extras = db.query(Tabela).order_by(desc(Tabela.rating_avg)).limit(50).all()
        for ext in extras:
            if ext.id not in vistos_ids:
                vistos_ids.add(ext.id)
                itens.append(ext)
                if len(itens) >= 10:
                    break

    return itens[:10]

def _get_novos_episodios(db: Session, on_the_air_ids: list):
    """Identifica com inteligência do TMDb quais séries ganharam novos episódios ou temporadas"""
    itens = []
    vistos_ids = set()

    if on_the_air_ids:
        db_itens = db.query(SerieDB).filter(SerieDB.tmdb_id.in_(on_the_air_ids)).all()
        map_db = {str(it.tmdb_id): it for it in db_itens if it.tmdb_id is not None}
        for t_id in on_the_air_ids:
            item = map_db.get(str(t_id))
            if item and item.id not in vistos_ids:
                vistos_ids.add(item.id)
                itens.append(item)
            if len(itens) >= 15:
                break

    if len(itens) < 15:
        extras = db.query(SerieDB).order_by(desc(SerieDB.id)).limit(50).all()
        for ext in extras:
            if ext.id not in vistos_ids:
                vistos_ids.add(ext.id)
                itens.append(ext)
                if len(itens) >= 15:
                    break

    return itens[:15]

def _construir_colecoes(db: Session, trending_filmes: list, trending_series: list, on_the_air: list) -> list:
    colecoes = []
    MAX_COLECOES = 12

    def add(id_, titulo, items, tipo_padrao="misto", icon="folder", min_itens=1, badge_custom=None):
        if len(colecoes) >= MAX_COLECOES:
            return
        if not items:
            return
        vistos = set()
        items_unicos = []
        for it in items:
            chave = it.id
            if chave not in vistos:
                vistos.add(chave)
                items_unicos.append(it)
        if len(items_unicos) < min_itens:
            return
        
        serialized_items = [_serializar_leve(it, badge_especial=badge_custom) for it in items_unicos]
        colecoes.append({
            "id": id_,
            "titulo": titulo,
            "icon": icon,
            "tipo": tipo_padrao,
            "items": serialized_items
        })

    # 1. EM ALTA
    em_alta_f = _get_top10_misto(db, FilmeDB, trending_filmes)
    em_alta_s = _get_top10_misto(db, SerieDB, trending_series)
    em_alta_mix = sorted(em_alta_f + em_alta_s, key=lambda x: (x.rating_avg or 0), reverse=True)[:15]
    add("em_alta", "🔥 O Que Todo Mundo Está Assistindo", em_alta_mix, "misto", "fire")

    # 2. TOP 10 FILMES
    top_f = _get_top10_misto(db, FilmeDB, trending_filmes)
    add("top10_filmes", "🏆 Top 10 Filmes", top_f, "filme", "star")

    # 3. TOP 10 SÉRIES
    top_s = _get_top10_misto(db, SerieDB, trending_series)
    add("top10_series", "🏆 Top 10 Séries", top_s, "serie", "star")

    # 4. NOVOS EPISÓDIOS (🧠 Cérebro define o selo inteligente aqui)
    novos_eps = _get_novos_episodios(db, on_the_air)
    add("novos_episodios", "📺 Últimas Séries Atualizadas", novos_eps, "serie", "tv", badge_custom="NOVOS EPISÓDIOS")

    # 5. RECENTES
    recentes_f = db.query(FilmeDB).filter(FilmeDB.created_at.isnot(None)).order_by(desc(FilmeDB.created_at)).limit(30).all()
    recentes_s = db.query(SerieDB).filter(SerieDB.created_at.isnot(None)).order_by(desc(SerieDB.created_at)).limit(30).all()
    if not recentes_f and not recentes_s:
        recentes_f = db.query(FilmeDB).order_by(desc(FilmeDB.id)).limit(30).all()
        recentes_s = db.query(SerieDB).order_by(desc(SerieDB.id)).limit(30).all()
    recentes_mix = sorted(recentes_f + recentes_s, key=lambda x: (x.rating_avg or 0), reverse=True)
    add("recentes", "🚀 Adicionados Recentemente", recentes_mix, "misto", "clock")

    # 6. TENDÊNCIAS
    ano_limite = datetime.now().year - 1
    tend_f = db.query(FilmeDB).filter(FilmeDB.year >= ano_limite).order_by(desc(FilmeDB.year)).limit(15).all()
    tend_s = db.query(SerieDB).filter(SerieDB.year >= ano_limite).order_by(desc(SerieDB.year)).limit(15).all()
    add("tendencias", "📈 Tendências da Semana", sorted(tend_f + tend_s, key=lambda x: (x.year or 0, x.rating_avg or 0), reverse=True), "misto", "trending-up")

    # 7. RECOMENDAÇÕES
    rec_f = db.query(FilmeDB).order_by(desc(FilmeDB.rating_avg)).limit(20).all()
    rec_s = db.query(SerieDB).order_by(desc(SerieDB.rating_avg)).limit(20).all()
    add("recomendacoes", "✨ Escolhidos a Dedo para Você", sorted(rec_f + rec_s, key=lambda x: (x.rating_avg or 0), reverse=True), "misto", "sparkles")

    # 8. STREAMINGS
    ignorar = ["GERAL", "FILMES", "SÉRIES", "4K", "FHD", "HD", "SD", "LANÇAMENTOS", "NACIONAL", "2020", "2021", "2022", "2023", "2024", "2025", "2026", "DUBLADO", "LEGENDADO", "DOCUMENTÁRIOS", "ANIMES"]
    cats_f = [r[0] for r in db.query(FilmeDB.coletany).distinct().all() if r[0]]
    cats_s = [r[0] for r in db.query(SerieDB.coletany).distinct().all() if r[0]]
    todas_cats = set(cats_f + cats_s)

    prioridade = ["Netflix", "Amazon", "Prime", "Disney", "HBO", "Max", "Apple", "Globo", "Paramount", "Star"]
    def score_cat(c):
        for i, p in enumerate(prioridade):
            if p.lower() in c.lower(): return i
        return 99
    cats_ordenadas = sorted(list(todas_cats), key=score_cat)

    for cat in cats_ordenadas:
        if len(colecoes) >= MAX_COLECOES: break
        if not cat or cat.upper() in ignorar or (cat.isdigit() and len(cat) == 4): continue
        items_f = db.query(FilmeDB).filter(FilmeDB.coletany == cat).order_by(desc(FilmeDB.rating_avg)).limit(20).all()
        items_s = db.query(SerieDB).filter(SerieDB.coletany == cat).order_by(desc(SerieDB.rating_avg)).limit(20).all()
        add(f"streaming_{cat}", f"📺 Coleção {cat}", items_f + items_s, "misto", "folder")

    return colecoes

async def build_home_async(db: Session, force: bool = False) -> dict:
    global _HOME_CACHE, _HOME_CACHE_TS
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < CACHE_TTL:
        return _HOME_CACHE

    trending_f, trending_s, on_the_air = await asyncio.gather(
        _fetch_trending_tmdb("movie"), 
        _fetch_trending_tmdb("tv"),
        _fetch_on_the_air_tmdb()
    )

    def _montar_tudo():
        hero = _construir_hero(db, trending_f, trending_s)
        colecoes = _construir_colecoes(db, trending_f, trending_s, on_the_air)
        return hero, colecoes

    hero, colecoes = await asyncio.to_thread(_montar_tudo)
    
    resposta = {
        "gerado_em": time.time(), 
        "tmdb_ativo": TMDB_ENABLED, 
        "hero": hero, 
        "colecoes": colecoes,
        "stats": {
            "total_filmes": await asyncio.to_thread(lambda: db.query(FilmeDB).count()),
            "total_series": await asyncio.to_thread(lambda: db.query(SerieDB).count())
        }
    }

    _HOME_CACHE = resposta
    _HOME_CACHE_TS = time.time()
    return resposta
