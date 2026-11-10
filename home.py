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

def _is_conteudo_proibido(item) -> bool:
    """Bloqueia documentários, músicas, filmes nacionais e filmes clássicos/antigos (antes de 2000)."""
    genres = [g.lower() for g in _parse_genres(getattr(item, "genres", None))]
    pais = str(getattr(item, "country", "")).lower()
    coletany = str(getattr(item, "coletany", "")).strip()
    ano = getattr(item, "year", None)

    if "documentário" in genres or "documentary" in genres:
        return True
    if "música" in genres or "music" in genres or "musical" in genres:
        return True
    if "brasil" in pais or "nacional" in coletany.lower() or "brasil" in coletany.lower():
        return True
    if ano and int(ano) < 2000:
        return True

    return False

def _is_anime(item) -> bool:
    genres = [g.lower() for g in _parse_genres(getattr(item, "genres", None))]
    coletany = str(getattr(item, "coletany", "")).strip()
    return "anime" in genres or "anime" in coletany.lower() or "animação japonesa" in coletany.lower()

def _is_dorama_categoria(coletany: str) -> bool:
    if not coletany:
        return False
    if not (coletany[0].isupper() and coletany[-1].islower()):
        return False
    c_lower = coletany.lower()
    termos = ["dorama", "doramas", "kdrama", "j-drama", "c-drama", "coreana", "coreano"]
    return any(t in c_lower for t in termos)

def _is_anime_categoria(coletany: str) -> bool:
    if not coletany:
        return False
    if not (coletany[0].isupper() and coletany[-1].islower()):
        return False
    return "anime" in coletany.lower() or "animes" in coletany.lower()

def _is_desenho_categoria(coletany: str) -> bool:
    """Valida categoria de desenhos/animações ocidentais seguindo a regra Maiúscula -> minúscula."""
    if not coletany:
        return False
    if not (coletany[0].isupper() and coletany[-1].islower()):
        return False
    c_lower = coletany.lower()
    termos = ["desenho", "desenhos", "animação", "animacao", "infantil", "cartoon"]
    return any(t in c_lower for t in termos)

def _serializar_leve(item, badge_especial=None):
    tipo_final = "movie" if getattr(item, "stream_type", "movie") == "movie" else "series"
    sinopse_texto = getattr(item, "description", None) or getattr(item, "sinopse", None) or ""

    return {
        "iptv_id": str(getattr(item, "iptv_id", None) or item.id),
        "title": getattr(item, "title", "") or "Sem título",
        "poster": getattr(item, "poster", None),
        "backdrop": getattr(item, "backdrop", None),
        "logo": getattr(item, "logo", None),
        "overview": sinopse_texto,
        "sinopse": sinopse_texto,
        "year": getattr(item, "year", None),
        "rating_avg": float(getattr(item, "rating_avg", 0.0) or 0.0),
        "age_rating": getattr(item, "age_rating", None),
        "genres": _parse_genres(getattr(item, "genres", None)),
        "type": tipo_final,
        "badge": badge_especial
    }

def _construir_hero(db: Session) -> list:
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

        sinopse_ref = s.sinopse or getattr(ref, "description", None) or getattr(ref, "sinopse", None) or ""

        hero.append({
            "id": item_id,
            "tipo": "filme" if s.ref_tipo == "filme" else "serie",
            "titulo": s.titulo or ref.title,
            "banner": s.banner or ref.backdrop or ref.poster,
            "capa": ref.poster,
            "logo": s.logo or ref.logo,
            "sinopse": sinopse_ref,
            "overview": sinopse_ref,
            "score": float(ref.rating_avg or 0.0),
            "classificacao": ref.age_rating,
            "ano": ref.year,
            "generos": _parse_genres(ref.genres)
        })

    return hero

async def _fetch_tmdb_list(endpoint: str, params: dict) -> list:
    if not TMDB_ENABLED:
        return []
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(endpoint, params=params)
            if r.status_code == 200:
                return [item['id'] for item in r.json().get('results', [])][:40]
    except Exception:
        pass
    return []

def _get_items_filtrados(db: Session, Tabela, tmdb_ids: list, limit=12, tipo_filtro="geral"):
    itens = []
    vistos_ids = set()

    # 1. Puxa Doramas estritamente pela regra de categoria
    if tipo_filtro == "dorama":
        db_itens = db.query(Tabela).all()
        for item in db_itens:
            coletany = str(getattr(item, "coletany", "")).strip()
            if _is_dorama_categoria(coletany) and item.id not in vistos_ids:
                if not _is_conteudo_proibido(item):
                    vistos_ids.add(item.id)
                    itens.append(item)
            if len(itens) >= limit:
                return itens
        return itens

    # 2. Puxa Animes estritamente pela regra de categoria ou metadados
    if tipo_filtro == "anime":
        db_itens = db.query(Tabela).all()
        for item in db_itens:
            coletany = str(getattr(item, "coletany", "")).strip()
            if (_is_anime_categoria(coletany) or _is_anime(item)) and item.id not in vistos_ids:
                if not _is_conteudo_proibido(item):
                    vistos_ids.add(item.id)
                    itens.append(item)
            if len(itens) >= limit:
                return itens
        return itens

    # 3. Puxa Desenhos Animados estritamente pela categoria formatada
    if tipo_filtro == "desenho":
        db_itens = db.query(Tabela).all()
        for item in db_itens:
            coletany = str(getattr(item, "coletany", "")).strip()
            if _is_desenho_categoria(coletany) and item.id not in vistos_ids:
                if not _is_conteudo_proibido(item):
                    vistos_ids.add(item.id)
                    itens.append(item)
            if len(itens) >= limit:
                return itens
        return itens

    # 4. Fluxo normal para as demais prateleiras (bloqueando animes, desenhos e doramas)
    if tmdb_ids:
        db_itens = db.query(Tabela).filter(Tabela.tmdb_id.in_(tmdb_ids)).all()
        map_db = {str(it.tmdb_id): it for it in db_itens if it.tmdb_id is not None}
        for t_id in tmdb_ids:
            item = map_db.get(str(t_id))
            if item and item.id not in vistos_ids:
                if _is_conteudo_proibido(item):
                    continue
                coletany = str(getattr(item, "coletany", "")).strip()
                if _is_anime(item) or _is_anime_categoria(coletany) or _is_dorama_categoria(coletany) or _is_desenho_categoria(coletany):
                    continue
                
                vistos_ids.add(item.id)
                itens.append(item)
            if len(itens) >= limit:
                break

    if len(itens) < limit:
        query = db.query(Tabela).order_by(desc(Tabela.rating_avg))
        extras = query.limit(400).all()
        for ext in extras:
            if ext.id not in vistos_ids:
                if _is_conteudo_proibido(ext):
                    continue
                coletany = str(getattr(ext, "coletany", "")).strip()
                if _is_anime(ext) or _is_anime_categoria(coletany) or _is_dorama_categoria(coletany) or _is_desenho_categoria(coletany):
                    continue

                vistos_ids.add(ext.id)
                itens.append(ext)
                if len(itens) >= limit:
                    break

    return itens[:limit]

def _construir_colecoes(db: Session, t_day_f: list, t_day_s: list, t_week_f: list, t_week_s: list, on_the_air: list) -> list:
    colecoes = []
    MAX_COLECOES = 16

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

    # 1. 🔥 Em Alta
    em_alta_f = _get_items_filtrados(db, FilmeDB, t_week_f, 12, "geral")
    em_alta_s = _get_items_filtrados(db, SerieDB, t_week_s, 12, "geral")
    em_alta_mix = sorted(em_alta_f + em_alta_s, key=lambda x: (x.rating_avg or 0), reverse=True)[:15]
    add("em_alta", "🔥 O Que Todo Mundo Está Assistindo", em_alta_mix, "misto", "fire")

    # 2. 🏆 Top 10 Filmes
    top_f = _get_items_filtrados(db, FilmeDB, t_week_f, 10, "top10")
    add("top10_filmes", "🏆 Top 10 Filmes", top_f, "filme", "star")

    # 3. 🏆 Top 10 Séries
    top_s = _get_items_filtrados(db, SerieDB, t_week_s, 10, "top10")
    add("top10_series", "🏆 Top 10 Séries", top_s, "serie", "star")

    # 4. 📈 Tendências de Hoje
    tend_hoje_f = _get_items_filtrados(db, FilmeDB, t_day_f, 12, "geral")
    tend_hoje_s = _get_items_filtrados(db, SerieDB, t_day_s, 12, "geral")
    add("tendencias_hoje", "📈 Tendências de Hoje", sorted(tend_hoje_f + tend_hoje_s, key=lambda x: (x.rating_avg or 0), reverse=True), "misto", "trending-up")

    # 5. 📈 Tendências da Semana
    tend_semana_f = _get_items_filtrados(db, FilmeDB, t_week_f, 12, "geral")
    tend_semana_s = _get_items_filtrados(db, SerieDB, t_week_s, 12, "geral")
    add("tendencias_semana", "📈 Tendências da Semana", sorted(tend_semana_f + tend_semana_s, key=lambda x: (x.rating_avg or 0), reverse=True), "misto", "trending-up")

    # 6. 🌸 Doramas
    doramas_f = _get_items_filtrados(db, FilmeDB, [], 15, "dorama")
    doramas_s = _get_items_filtrados(db, SerieDB, [], 15, "dorama")
    add("doramas", "🌸 Doramas e Séries Asiáticas", doramas_f + doramas_s, "misto", "heart")

    # 7. ⚡ Animes
    animes_f = _get_items_filtrados(db, FilmeDB, [], 15, "anime")
    animes_s = _get_items_filtrados(db, SerieDB, [], 15, "anime")
    add("animes", "⚡ Animes", animes_f + animes_s, "misto", "zap")

    # 8. 🎨 Desenhos Animados (Puxando rigorosamente da categoria com regra Maiúscula -> minúscula)
    desenhos_f = _get_items_filtrados(db, FilmeDB, [], 15, "desenho")
    desenhos_s = _get_items_filtrados(db, SerieDB, [], 15, "desenho")
    add("desenhos", "🎨 Desenhos Animados", desenhos_f + desenhos_s, "misto", "smile")

    # 9. 📺 Últimas Séries Atualizadas
    novos_eps = _get_items_filtrados(db, SerieDB, on_the_air, 15, "geral")
    add("novos_episodios", "📺 Últimas Séries Atualizadas", novos_eps, "serie", "tv", badge_custom="NOVOS EPISÓDIOS")

    # 10. 🚀 Adicionados Recentemente
    recentes_f = db.query(FilmeDB).filter(FilmeDB.created_at.isnot(None)).order_by(desc(FilmeDB.created_at)).limit(20).all()
    recentes_s = db.query(SerieDB).filter(SerieDB.created_at.isnot(None)).order_by(desc(SerieDB.created_at)).limit(20).all()
    if not recentes_f and not recentes_s:
        recentes_f = db.query(FilmeDB).order_by(desc(FilmeDB.id)).limit(20).all()
        recentes_s = db.query(SerieDB).order_by(desc(SerieDB.id)).limit(20).all()
    add("recentes", "🚀 Adicionados Recentemente", sorted(recentes_f + recentes_s, key=lambda x: (x.rating_avg or 0), reverse=True), "misto", "clock")

    return colecoes

async def build_home_async(db: Session, force: bool = False) -> dict:
    global _HOME_CACHE, _HOME_CACHE_TS
    if not force and _HOME_CACHE and (time.time() - _HOME_CACHE_TS) < CACHE_TTL:
        return _HOME_CACHE

    params = {"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE}

    t_day_f, t_day_s, t_week_f, t_week_s, on_the_air = await asyncio.gather(
        _fetch_tmdb_list("https://api.themoviedb.org/3/trending/movie/day", params),
        _fetch_tmdb_list("https://api.themoviedb.org/3/trending/tv/day", params),
        _fetch_tmdb_list("https://api.themoviedb.org/3/trending/movie/week", params),
        _fetch_tmdb_list("https://api.themoviedb.org/3/trending/tv/week", params),
        _fetch_tmdb_list("https://api.themoviedb.org/3/tv/on_the_air", params)
    )

    def _montar_tudo():
        hero = _construir_hero(db)
        colecoes = _construir_colecoes(db, t_day_f, t_day_s, t_week_f, t_week_s, on_the_air)
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
