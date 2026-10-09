import time
import random
import asyncio
import re
import unicodedata
from datetime import datetime

import httpx

from config import TMDB_ENABLED, TMDB_API_KEY, TMDB_LANGUAGE, USER_AGENT, PUBLIC_BASE_URL
import tmdb


TMDB_BASE = "https://api.themoviedb.org/3"


def _semana_atual() -> int:
    iso = datetime.now().isocalendar()
    return iso.year * 100 + iso.week


def _shuffle_semanal(items: list, salt: str = "") -> list:
    seed = _semana_atual() * 7919 + abs(hash(salt)) % 9973
    rnd = random.Random(seed)
    copia = list(items)
    rnd.shuffle(copia)
    return copia


# ==========================================================
# NORMALIZAR TÍTULO PRA COMPARAR
# ==========================================================
def _normalizar(texto: str) -> str:
    """Deixa o título em formato comparável."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = t.encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    t = re.sub(r'[^a-z0-9\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


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


# ==========================================================
# ÍNDICE PARA BUSCA RÁPIDA
# ==========================================================
def _construir_indice_rapido(filmes: list, series: list) -> dict:
    """
    Monta dict: titulo_normalizado → item
    Pra busca ser O(1) ao invés de varrer a lista toda.
    """
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


def _procurar_no_indice(indice: dict, titulo_tmdb: str, tipo: str) -> dict | None:
    """Procura por match exato e depois por match parcial."""
    t_norm = _normalizar(titulo_tmdb)
    if not t_norm:
        return None

    chave = "filmes" if tipo == "filme" else "series"

    # 1. Match exato
    if t_norm in indice[chave]:
        return indice[chave][t_norm]

    # 2. Match parcial: se um contém o outro
    for titulo_iptv, item in indice[chave].items():
        if not titulo_iptv:
            continue
        # Se o título do IPTV está dentro do título do TMDB ou vice-versa
        if len(t_norm) > 4 and len(titulo_iptv) > 4:
            if t_norm in titulo_iptv or titulo_iptv in t_norm:
                return item

    return None


# ==========================================================
# TMDB — BUSCAR TENDÊNCIAS
# ==========================================================
async def _tmdb_trending_movies() -> list:
    """Filmes em alta essa semana."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/trending/movie/week",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:20]
    except Exception:
        return []


async def _tmdb_trending_tv() -> list:
    """Séries em alta essa semana."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/trending/tv/week",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:20]
    except Exception:
        return []


async def _tmdb_now_playing_movies() -> list:
    """Filmes nos cinemas agora."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/movie/now_playing",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE, "region": "BR"},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:20]
    except Exception:
        return []


async def _tmdb_on_the_air_tv() -> list:
    """Séries que estão no ar agora."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TMDB_BASE}/tv/on_the_air",
                params={"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE},
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            return r.json().get("results", [])[:20]
    except Exception:
        return []


# ==========================================================
# HERO — TMDB primeiro, IPTV depois
# ==========================================================
async def _montar_hero_async(filmes: list, series: list, n: int = 5) -> list:
    if not TMDB_ENABLED:
        return []

    # 1. Consulta TMDB em paralelo
    resultados_tmdb = await asyncio.gather(
        _tmdb_trending_movies(),
        _tmdb_trending_tv(),
        _tmdb_now_playing_movies(),
        _tmdb_on_the_air_tv(),
        return_exceptions=True,
    )

    trending_movies  = resultados_tmdb[0] if isinstance(resultados_tmdb[0], list) else []
    trending_tv      = resultados_tmdb[1] if isinstance(resultados_tmdb[1], list) else []
    now_playing      = resultados_tmdb[2] if isinstance(resultados_tmdb[2], list) else []
    on_the_air       = resultados_tmdb[3] if isinstance(resultados_tmdb[3], list) else []

    # 2. Junta tudo, removendo duplicatas por tmdb_id
    vistos = set()
    candidatos = []

    for item in now_playing + trending_movies:
        tid = item.get("id")
        if tid and tid not in vistos:
            vistos.add(tid)
            titulo = item.get("title") or item.get("name")
            if titulo:
                candidatos.append({"tipo": "filme", "titulo": titulo, "tmdb": item})

    for item in trending_tv + on_the_air:
        tid = item.get("id")
        if tid and tid not in vistos:
            vistos.add(tid)
            titulo = item.get("name") or item.get("title")
            if titulo:
                candidatos.append({"tipo": "serie", "titulo": titulo, "tmdb": item})

    if not candidatos:
        print("[i] Hero: nada encontrado no TMDB trending")
        return []

    # 3. Monta índice rápido da lista IPTV
    indice = _construir_indice_rapido(filmes, series)

    # 4. Filtra: só os que existem na IPTV
    encontrados = []
    for c in candidatos:
        match = _procurar_no_indice(indice, c["titulo"], c["tipo"])
        if match:
            encontrados.append({
                "item_iptv": match,
                "tmdb_item": c["tmdb"],
                "tipo": c["tipo"],
            })

    if not encontrados:
        print("[i] Hero: nenhum item do TMDB está na lista IPTV")
        return []

    print(f"[✓] Hero: {len(encontrados)} matches TMDB × IPTV")

    # 5. Se tiver mais que n, escolhe semanalmente
    if len(encontrados) > n:
        randomizados = _shuffle_semanal(encontrados, "hero")
        selecionados = randomizados[:n]
    else:
        selecionados = encontrados

    # 6. Enriquecer com TMDB completo (banner, logo, sinopse)
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

            # Verifica se tem pelo menos banner + sinopse
            if not dados.get("banner") or not dados.get("sinopse"):
                return None

            slug_base = _slugify(item.get("titulo") or "", item.get("ano"))
            base = (PUBLIC_BASE_URL or "").rstrip("/")
            url_stream = None
            if slug_base:
                url_stream = f"{base}/stream/{slug_base}.mp4" if base else f"/stream/{slug_base}.mp4"

            return {
                "id": item.get("id"),
                "tipo": tipo,
                "titulo": item.get("titulo"),
                "banner": dados.get("banner_4k") or dados.get("banner"),
                "capa": dados.get("capa") or item.get("capa"),
                "logo": dados.get("logo"),
                "sinopse": dados.get("sinopse"),
                "score": dados.get("score"),
                "classificacao": dados.get("classificacao"),
                "ano": dados.get("ano") or item.get("ano"),
                "generos": (dados.get("generos") or [])[:3],
                "trailer_url": dados.get("trailer_url"),
                "url_stream": url_stream,
            }
        except Exception as e:
            print(f"[!] Hero enriquecimento falhou pra {item.get('titulo')}: {e}")
            return None

    resultados = await asyncio.gather(*[_enriquecer(m) for m in selecionados])
    hero_final = [r for r in resultados if r]

    print(f"[✓] Hero final: {len(hero_final)} slides")
    return hero_final


# ==========================================================
# CARD SIMPLES (coleções) — SEM classificação
# ==========================================================
def _card_simples(item: dict, tipo: str | None = None) -> dict:
    return {
        "id": item.get("id"),
        "tipo": item.get("tipo") or tipo or "filme",
        "titulo": item.get("titulo"),
        "capa": item.get("capa"),
        "ano": item.get("ano"),
    }


# ==========================================================
# COLETORES
# ==========================================================
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
    return validos[:n]


# ==========================================================
# 12 COLEÇÕES
# ==========================================================
def _construir_colecoes(filmes: list, series: list) -> list:
    todos = filmes + series
    colecoes = []

    def add(id_, titulo, itens, tipo_padrao=None):
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


# ==========================================================
# BUILDER PRINCIPAL
# ==========================================================
async def build_home_async(index: dict) -> dict:
    filmes = index.get("filmes", [])
    series = index.get("series", [])

    hero = await _montar_hero_async(filmes, series, 5)

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