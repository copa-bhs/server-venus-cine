from __future__ import annotations

import time
import random
from datetime import datetime, timedelta

from config import TMDB_ENABLED, PUBLIC_BASE_URL


def _semana_atual() -> int:
    iso = datetime.now().isocalendar()
    return iso.year * 100 + iso.week


def _shuffle_semanal(items: list, salt: str = "") -> list:
    seed = _semana_atual() * 7919 + abs(hash(salt)) % 9973
    rnd = random.Random(seed)
    copia = list(items)
    rnd.shuffle(copia)
    return copia


def _tem_capa(item: dict) -> bool:
    return bool(item.get("capa"))


def _tem_banner(item: dict) -> bool:
    return bool(item.get("banner") or item.get("banner_4k"))


def _tem_logo(item: dict) -> bool:
    return bool(item.get("logo"))


def _tem_sinopse(item: dict) -> bool:
    return bool((item.get("sinopse") or "").strip())


def _score(item: dict) -> float:
    try:
        return float(item.get("score") or 0)
    except Exception:
        return 0.0


def _ano(item: dict):
    try:
        return int(item.get("ano") or 0)
    except Exception:
        return 0


def _tem_genero(item: dict, generos_alvo: list) -> bool:
    gen = [g.lower() for g in (item.get("generos") or [])]
    for g in generos_alvo:
        if g.lower() in gen:
            return True
    return False


_GENEROS_EXCLUIDOS = {
    "documentário", "documentario", "documentary",
    "música", "musica", "music", "musical",
    "talk show", "reality", "news", "notícias",
}

_TITULOS_EXCLUIDOS = [
    "rock in rio", "ao vivo", "live at", "concert", "concerto",
    "documentário", "trailer", "making of", "bastidores",
    "coletânea", "coletanea", "trilha sonora",
]


def _elegivel_top10(item: dict) -> bool:
    for g in (item.get("generos") or []):
        if g.strip().lower() in _GENEROS_EXCLUIDOS:
            return False
    titulo = (item.get("titulo") or "").lower()
    for termo in _TITULOS_EXCLUIDOS:
        if termo in titulo:
            return False
    cat = (item.get("categoria") or "").lower()
    if any(t in cat for t in ("documentário", "documentario", "musical", "show")):
        return False
    return True


def _elegivel_hero(item: dict) -> bool:
    """Hero exige capa, banner, sinopse e score decente."""
    if not _tem_capa(item):
        return False
    if not _tem_banner(item):
        return False
    if not _tem_sinopse(item):
        return False
    if _score(item) < 5.0:
        return False
    return _elegivel_top10(item)


def _tipo_do_item(item: dict, tipo_fallback: str | None = None) -> str:
    t = (item.get("tipo") or "").lower()
    if t in ("filme", "movie"):
        return "filme"
    if t in ("serie", "série", "series"):
        return "serie"
    if t == "canal":
        return "canal"
    if item.get("episodios") or item.get("total_temporadas") is not None:
        return "serie"
    return tipo_fallback or "filme"


# ==========================================================
# CARD SIMPLES (usado em coleções, listagens, busca)
# ==========================================================
def _card_simples(item: dict, tipo: str | None = None) -> dict:
    return {
        "id": item.get("id"),
        "tipo": _tipo_do_item(item, tipo),
        "titulo": item.get("titulo"),
        "capa": item.get("capa"),
        "ano": item.get("ano"),
        "classificacao": item.get("classificacao"),
    }


# ==========================================================
# CARD HERO (rico — banner, logo, sinopse, gêneros)
# ==========================================================
def _card_hero(item: dict, tipo: str | None = None) -> dict:
    slug = item.get("slug")
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    url_stream = None
    if slug:
        url_stream = f"{base}/stream/{slug}.mp4" if base else f"/stream/{slug}.mp4"

    return {
        "id": item.get("id"),
        "tipo": _tipo_do_item(item, tipo),
        "titulo": item.get("titulo"),
        # imagem de FUNDO do slide (banner, não capa)
        "banner": item.get("banner_4k") or item.get("banner") or item.get("capa"),
        # capa (poster vertical) — fallback visual
        "capa": item.get("capa"),
        # logo transparente (Netflix style)
        "logo": item.get("logo"),
        # conteúdo extra
        "sinopse": item.get("sinopse"),
        "score": item.get("score"),
        "classificacao": item.get("classificacao"),
        "ano": item.get("ano"),
        "generos": (item.get("generos") or [])[:3],
        "trailer_url": item.get("trailer_url"),
        "url_stream": url_stream,
    }


# ==========================================================
# HERO
# ==========================================================
def _build_hero(filmes: list, series: list, n: int = 5) -> list:
    """
    Monta o hero com prioridade por qualidade visual:
      1. banner_4k + logo + sinopse (melhor)
      2. banner + logo + sinopse
      3. banner + sinopse
    """
    todos = filmes + series
    elegiveis = [i for i in todos if _elegivel_hero(i)]

    if not elegiveis:
        return []

    def prioridade(item: dict) -> tuple:
        tem_banner_4k = bool(item.get("banner_4k"))
        tem_banner = _tem_banner(item)
        tem_logo = _tem_logo(item)

        if tem_banner_4k and tem_logo:
            tier = 0
        elif tem_banner and tem_logo:
            tier = 1
        elif tem_banner:
            tier = 2
        else:
            tier = 3

        ano = _ano(item)
        recente = 0 if ano >= 2023 else (1 if ano >= 2020 else 2)
        return (tier, recente, -_score(item))

    elegiveis.sort(key=prioridade)
    top = elegiveis[:40]

    randomizados = _shuffle_semanal(top, "hero")
    return [_card_hero(i) for i in randomizados[:n]]


# ==========================================================
# COLETORES
# ==========================================================
def _top_por_score(itens: list, n: int = 10, filtrar_elegiveis: bool = True) -> list:
    validos = [i for i in itens if _tem_capa(i) and _score(i) > 0]
    if filtrar_elegiveis:
        validos = [i for i in validos if _elegivel_top10(i)]
    validos.sort(key=_score, reverse=True)
    return validos[:n]


def _top_series_fallback(series: list, n: int = 10) -> list:
    validos = [s for s in series if _tem_capa(s)]
    validos.sort(key=lambda s: (-(s.get("total_episodios") or 0), -_ano(s)))
    return validos[:n]


def _top_por_ano(itens: list, n: int = 15) -> list:
    validos = [i for i in itens if _tem_capa(i) and _ano(i) > 0]
    validos.sort(key=_ano, reverse=True)
    return validos[:n]


def _por_genero(itens: list, generos: list, n: int = 15) -> list:
    validos = [i for i in itens if _tem_capa(i) and _tem_genero(i, generos)]
    validos.sort(key=_score, reverse=True)
    return validos[:n]


def _bem_avaliados(itens: list, min_score: float = 8.0, n: int = 15) -> list:
    validos = [i for i in itens if _tem_capa(i) and _score(i) >= min_score and _elegivel_top10(i)]
    validos.sort(key=_score, reverse=True)
    return validos[:n]


def _recentes(itens: list, n: int = 20) -> list:
    limite = datetime.now() - timedelta(days=365)
    validos = []
    for i in itens:
        ano = _ano(i)
        if ano and _tem_capa(i) and ano >= limite.year:
            validos.append(i)
    validos.sort(key=_ano, reverse=True)
    return validos[:n]


# ==========================================================
# 12 COLEÇÕES (cards simples)
# ==========================================================
def _construir_colecoes(filmes: list, series: list) -> list:
    todos = filmes + series
    colecoes = []

    def add(id_, titulo, itens, tipo_padrao: str | None = None):
        if not itens:
            return
        items_fmt = [_card_simples(it, tipo_padrao) for it in itens]
        colecoes.append({
            "id": id_,
            "titulo": titulo,
            "total": len(items_fmt),
            "items": items_fmt,
        })

    add("top10_filmes", "🏆 Top 10 Filmes", _top_por_score(filmes, 10), "filme")
    top_series = _top_por_score(series, 10) or _top_series_fallback(series, 10)
    add("top10_series", "🏆 Top 10 Séries", top_series, "serie")

    altos_f = _top_por_score(filmes, 30)
    altos_s = _top_por_score(series, 30) or _top_series_fallback(series, 30)
    em_alta = _shuffle_semanal(altos_f + altos_s, "em_alta")[:15]
    add("em_alta", "🔥 Em Alta", em_alta)

    lanc = [i for i in _recentes(filmes, 30) if _elegivel_top10(i)]
    add("lancamentos", "🆕 Lançamentos", lanc[:20], "filme")
    add("acao", "💥 Ação", _por_genero(todos, ["Ação", "Action", "Aventura", "Adventure"], 15))
    add("comedia", "😂 Comédia", _por_genero(todos, ["Comédia", "Comedy"], 15))
    add("drama", "🎭 Drama", _por_genero(todos, ["Drama"], 15))
    add("terror", "👻 Terror", _por_genero(todos, ["Terror", "Horror"], 15))
    add("ficcao", "🚀 Ficção Científica", _por_genero(todos, ["Ficção científica", "Science Fiction", "Sci-Fi"], 15))
    add("animacao", "🎨 Animação", _por_genero(todos, ["Animação", "Animation"], 15))
    add("bem_avaliados", "⭐ Bem Avaliados", _bem_avaliados(todos, 8.0, 15))
    add("catalogo_novo", "📚 Catálogo Novo", _top_por_ano(todos, 15))

    return colecoes


# ==========================================================
# TRAILERS
# ==========================================================
def _construir_trailers(filmes: list, series: list, n: int = 10) -> list:
    todos = []
    for f in filmes:
        if f.get("trailer_url"):
            card = _card_simples(f, "filme")
            card["trailer_url"] = f.get("trailer_url")
            card["banner"] = f.get("banner") or f.get("capa")
            todos.append(card)
    for s in series:
        if s.get("trailer_url"):
            card = _card_simples(s, "serie")
            card["trailer_url"] = s.get("trailer_url")
            card["banner"] = s.get("banner") or s.get("capa")
            todos.append(card)
    randomizados = _shuffle_semanal(todos, "trailers")
    return randomizados[:n]


# ==========================================================
# BUILDER PRINCIPAL
# ==========================================================
def build_home(index: dict) -> dict:
    filmes = index.get("filmes", [])
    series = index.get("series", [])

    return {
        "gerado_em": time.time(),
        "gerado_em_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "semana_iso": _semana_atual(),
        "tmdb_ativo": TMDB_ENABLED,
        "hero": _build_hero(filmes, series, 5),
        "colecoes": _construir_colecoes(filmes, series),
        "trailers": _construir_trailers(filmes, series, 10),
        "stats": index.get("stats", {}),
    }