import time
import random
import asyncio
import re
import json
import unicodedata
from datetime import datetime

import httpx

from config import (
    TMDB_ENABLED, TMDB_API_KEY, TMDB_LANGUAGE, USER_AGENT,
    PUBLIC_BASE_URL, CACHE_DIR,
)
import tmdb
import logos


TMDB_BASE = "https://api.themoviedb.org/3"

HERO_CACHE_FILE = CACHE_DIR / "hero_cache.json"
HERO_TTL = 7 * 24 * 60 * 60


def _semana_atual() -> int:
    iso = datetime.now().isocalendar()
    return iso.year * 100 + iso.week


def _normalizar(texto: str) -> str:
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = t.encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _normalizar_titulo(titulo: str) -> str:
    if not titulo:
        return ""
    t = unicodedata.normalize("NFKD", titulo)
    t = t.encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    t = re.sub(r'\b(4k|uhd|fhd|hd|sd|cam|dublado|legendado|dub|leg)\b', '', t)
    t = re.sub(r'\(?\b(19|20)\d{2}\b\)?', '', t)
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _deduplicar(itens: list) -> list:
    vistos = set()
    resultado = []
    for item in itens:
        chave = _normalizar_titulo(item.get("titulo") or "")
        if not chave:
            continue
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append(item)
    return resultado


def _slugify(texto: str, ano=None) -> str:
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = t.encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^\w\s-]", "", t.lower())
    t = re.sub(r"[-\s]+", "-", t).strip("-")
    t = re.sub(r"^(filme|serie|série|movie|tv|vod)[-\s]+", "", t)
    if ano:
        try:
            t = f"{t}-{int(ano)}"
        except Exception:
            pass
    return t


def _hero_cache_ler() -> list | None:
    if not HERO_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(HERO_CACHE_FILE.read_text(encoding="utf-8"))
        ts = data.get("_ts", 0)
        if time.time() - ts > HERO_TTL:
            return None
        hero = data.get("hero", [])
        return hero or None
    except Exception:
        return None


def _hero_cache_salvar(hero: list):
    try:
        HERO_CACHE_FILE.write_text(
            json.dumps({"_ts": time.time(), "hero": hero}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def limpar_hero_cache():
    try:
        if HERO_CACHE_FILE.exists():
            HERO_CACHE_FILE.unlink()
    except Exception:
        pass


def _construir_indice_rapido(filmes: list, series: list) -> dict:
    indice = {"filmes": {}, "series": {}}
    for f in filmes:
        t = _normalizar(f.get("titulo") or "")
        if t:
            indice["filmes"][t] = f
    for s in series:
        t = _normalizar(s.get("titulo") or "")
        if t:
            indice["series"][t] = s
    return indice


def _procurar_no_indice(indice: dict, titulo_tmdb: str, tipo: str):
    t_norm = _normalizar(titulo_tmdb)
    if not t_norm:
        return None
    chave = "filmes" if tipo == "filme" else "series"
    if t_norm in indice[chave]:
        return indice[chave][t_norm]
    for titulo_iptv, item in indice[chave].items():
        if not titulo_iptv:
            continue
        if len(t_norm) > 4 and len(titulo_iptv) > 4:
            if t_norm in titulo_iptv or titulo_iptv in t_norm:
                return item
    return None


async def _tmdb_now_playing_movies() -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/movie/now_playing",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE, "region": "BR", "page": 1},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:30]
    except Exception:
        return []


async def _tmdb_upcoming_movies() -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/movie/upcoming",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE, "region": "BR", "page": 1},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:30]
    except Exception:
        return []


async def _tmdb_on_the_air_tv() -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/tv/on_the_air",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE, "page": 1},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:30]
    except Exception:
        return []


async def _tmdb_airing_today_tv() -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/tv/airing_today",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE, "page": 1},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:30]
    except Exception:
        return []


async def _buscar_candidatos_lancamentos() -> list:
    print("[Hero] Buscando lançamentos no TMDB...")
    resultados = await asyncio.gather(
        _tmdb_now_playing_movies(),
        _tmdb_upcoming_movies(),
        _tmdb_on_the_air_tv(),
        _tmdb_airing_today_tv(),
        return_exceptions=True,
    )

    now_playing = resultados[0] if isinstance(resultados[0], list) else []
    upcoming = resultados[1] if isinstance(resultados[1], list) else []
    on_the_air = resultados[2] if isinstance(resultados[2], list) else []
    airing_today = resultados[3] if isinstance(resultados[3], list) else []

    vistos = set()
    candidatos = []

    for item in now_playing + upcoming:
        tid = item.get("id")
        if tid and tid not in vistos:
            vistos.add(tid)
            titulo = item.get("title") or item.get("name")
            if titulo:
                candidatos.append({"tipo": "filme", "titulo": titulo})

    for item in on_the_air + airing_today:
        tid = item.get("id")
        if tid and tid not in vistos:
            vistos.add(tid)
            titulo = item.get("name") or item.get("title")
            if titulo:
                candidatos.append({"tipo": "serie", "titulo": titulo})

    return candidatos


async def _montar_hero_async(filmes: list, series: list, n: int = 5) -> list:
    if not TMDB_ENABLED:
        return []

    candidatos = await _buscar_candidatos_lancamentos()
    if not candidatos:
        return []

    indice = _construir_indice_rapido(filmes, series)

    encontrados = []
    for c in candidatos:
        match = _procurar_no_indice(indice, c["titulo"], c["tipo"])
        if match:
            encontrados.append({
                "item_iptv": match,
                "titulo_tmdb": c["titulo"],
                "tipo": c["tipo"],
            })

    if not encontrados:
        recentes = []
        for f in filmes:
            try:
                if int(f.get("ano") or 0) >= 2020:
                    recentes.append({"item_iptv": f, "titulo_tmdb": f.get("titulo"), "tipo": "filme"})
            except Exception:
                pass
        for s in series:
            try:
                if int(s.get("ano") or 0) >= 2020:
                    recentes.append({"item_iptv": s, "titulo_tmdb": s.get("titulo"), "tipo": "serie"})
            except Exception:
                pass
        recentes.sort(key=lambda x: int(x["item_iptv"].get("ano") or 0), reverse=True)
        encontrados = recentes[:20]

    if not encontrados:
        return []

    selecionados = encontrados[:n]

    async def _enriquecer(m):
        item = m["item_iptv"]
        tipo = m["tipo"]
        try:
            if tipo == "serie":
                dados = await tmdb.buscar_serie(item.get("titulo") or "", item.get("ano"))
            else:
                dados = await tmdb.buscar_filme(item.get("titulo") or "", item.get("ano"))

            if not dados:
                return None

            logo_final = dados.get("logo")
            if not logo_final and dados.get("tmdb_id"):
                try:
                    if tipo == "serie":
                        logo_fanart = await logos.buscar_logo_fanart_tv(dados.get("tvdb_id"))
                    else:
                        logo_fanart = await logos.buscar_logo_fanart_movie(dados.get("tmdb_id"))
                    if logo_fanart:
                        logo_final = logo_fanart
                except Exception:
                    pass

            banner = dados.get("banner_4k") or dados.get("banner")
            slug_base = _slugify(item.get("titulo") or "", item.get("ano"))
            base = (PUBLIC_BASE_URL or "").rstrip("/")
            url_stream = None
            if slug_base:
                url_stream = f"{base}/stream/{slug_base}.mp4" if base else f"/stream/{slug_base}.mp4"

            return {
                "id": item.get("id"),
                "tipo": tipo,
                "titulo": item.get("titulo"),
                "banner": banner or item.get("capa"),
                "capa": dados.get("capa") or item.get("capa"),
                "logo": logo_final,
                "sinopse": dados.get("sinopse"),
                "score": dados.get("score"),
                "classificacao": dados.get("classificacao"),
                "ano": dados.get("ano") or item.get("ano"),
                "generos": (dados.get("generos") or [])[:3],
                "trailer_url": dados.get("trailer_url"),
                "url_stream": url_stream,
            }
        except Exception:
            return None

    resultados = await asyncio.gather(*[_enriquecer(m) for m in selecionados])
    return [r for r in resultados if r]


async def _hero_manual_do_banco() -> list:
    """
    Lê slides manuais do banco e enriquece com dados do TMDB.
    Se o TMDB falhar, usa os dados básicos do banco.
    """
    try:
        from db import SessionLocal, Slide as SlideDB, Filme as FilmeDB, Serie as SerieDB
        db = SessionLocal()
        try:
            slides_db = db.query(SlideDB).filter(
                SlideDB.ativo == True
            ).order_by(SlideDB.ordem).all()

            if not slides_db:
                return []

            hero = []
            for s in slides_db:
                ref = None
                if s.ref_tipo == "filme":
                    ref = db.query(FilmeDB).get(s.ref_id)
                elif s.ref_tipo == "serie":
                    ref = db.query(SerieDB).get(s.ref_id)

                if not ref:
                    continue

                # Enriquecer com dados do TMDB
                dados_tmdb = None
                try:
                    if s.ref_tipo == "filme":
                        dados_tmdb = await tmdb.buscar_filme(ref.titulo, ref.ano)
                    else:
                        dados_tmdb = await tmdb.buscar_serie(ref.titulo, ref.ano)
                except Exception as e:
                    print(f"[Hero] Falha ao enriquecer '{ref.titulo}': {e}")

                slug_base = _slugify(ref.titulo or "", ref.ano)
                base = (PUBLIC_BASE_URL or "").rstrip("/")
                url_stream = f"{base}/stream/{slug_base}.mp4" if base else f"/stream/{slug_base}.mp4"

                hero.append({
                    "id": ref.id,
                    "tipo": "filme" if s.ref_tipo == "filme" else "serie",
                    "titulo": s.titulo or ref.titulo,
                    "banner": s.banner or (dados_tmdb.get("banner_4k") if dados_tmdb else None) or getattr(ref, "banner", None) or ref.capa,
                    "capa": (dados_tmdb.get("capa") if dados_tmdb else None) or ref.capa,
                    "logo": s.logo or (dados_tmdb.get("logo") if dados_tmdb else None) or getattr(ref, "logo", None),
                    "sinopse": s.sinopse or (dados_tmdb.get("sinopse") if dados_tmdb else None) or getattr(ref, "sinopse", None),
                    "score": dados_tmdb.get("score") if dados_tmdb else None,
                    "classificacao": dados_tmdb.get("classificacao") if dados_tmdb else None,
                    "ano": (dados_tmdb.get("ano") if dados_tmdb else None) or ref.ano,
                    "generos": (dados_tmdb.get("generos") if dados_tmdb else None) or [],
                    "trailer_url": dados_tmdb.get("trailer_url") if dados_tmdb else None,
                    "url_stream": url_stream,
                    "manual": True,
                })
            print(f"[Hero] Usando {len(hero)} slides manuais do banco (enriquecidos)")
            return hero
        finally:
            db.close()
    except Exception as e:
        print(f"[Hero] Erro ao ler slides manuais: {e}")
        return []


async def get_hero(filmes: list, series: list, n: int = 5, force: bool = False) -> list:
    manual = await _hero_manual_do_banco()
    if manual:
        return manual

    if not force:
        cached = _hero_cache_ler()
        if cached:
            return cached

    hero = await _montar_hero_async(filmes, series, n)
    if hero:
        _hero_cache_salvar(hero)
    return hero


def _card_simples(item: dict, tipo: str | None = None) -> dict:
    return {
        "id": item.get("id"),
        "tipo": item.get("tipo") or tipo or "filme",
        "titulo": item.get("titulo"),
        "capa": item.get("capa"),
        "ano": item.get("ano"),
    }


def _tem_capa(item: dict) -> bool:
    return bool(item.get("capa"))


def _ano(item: dict):
    try:
        return int(item.get("ano") or 0)
    except Exception:
        return 0


def _por_ano(itens: list, n: int = 15) -> list:
    validos = [i for i in itens if _tem_capa(i) and _ano(i) > 0]
    validos.sort(key=_ano, reverse=True)
    validos = _deduplicar(validos)
    return validos[:n]


def _por_categoria(itens: list, palavras: list, n: int = 15) -> list:
    validos = []
    for i in itens:
        if not _tem_capa(i):
            continue
        cat = (i.get("categoria") or "").lower()
        if any(p.lower() in cat for p in palavras):
            validos.append(i)
    validos.sort(key=_ano, reverse=True)
    validos = _deduplicar(validos)
    return validos[:n]


def _construir_colecoes(filmes: list, series: list) -> list:
    todos = filmes + series
    colecoes = []

    def add(id_, titulo, itens, tipo_padrao=None):
        if not itens:
            return
        itens = _deduplicar(itens)
        if not itens:
            return
        items_fmt = [_card_simples(it, tipo_padrao) for it in itens]
        colecoes.append({
            "id": id_,
            "titulo": titulo,
            "total": len(items_fmt),
            "items": items_fmt,
        })

    add("top10_filmes", "🎬 Filmes", _por_ano(filmes, 30)[:10], "filme")
    add("top10_series", "📺 Séries", _por_ano(series, 30)[:10], "serie")
    add("em_alta", "🔥 Em Alta", _por_ano(todos, 15))
    add("lancamentos", "🆕 Lançamentos", _por_ano(filmes, 20), "filme")
    add("acao", "💥 Ação", _por_categoria(todos, ["ação", "action", "aventura"]))
    add("comedia", "😂 Comédia", _por_categoria(todos, ["comédia", "comedy"]))
    add("drama", "🎭 Drama", _por_categoria(todos, ["drama"]))
    add("terror", "👻 Terror", _por_categoria(todos, ["terror", "horror", "suspense"]))
    add("ficcao", "🚀 Ficção", _por_categoria(todos, ["ficção", "sci-fi", "ficcao"]))
    add("animacao", "🎨 Animação", _por_categoria(todos, ["animação", "animation", "anime", "infantil"]))
    add("romance", "💕 Romance", _por_categoria(todos, ["romance", "romântico"]))
    add("catalogo_novo", "📚 Catálogo Novo", _por_ano(todos, 15))

    return colecoes


async def build_home_async(index: dict, force_hero: bool = False) -> dict:
    filmes = index.get("filmes", [])
    series = index.get("series", [])

    hero = await get_hero(filmes, series, n=5, force=force_hero)

    return {
        "gerado_em": time.time(),
        "gerado_em_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "semana_iso": _semana_atual(),
        "tmdb_ativo": TMDB_ENABLED,
        "hero": hero,
        "colecoes": _construir_colecoes(filmes, series),
        "trailers": [],
        "stats": index.get("stats", {}),
    }