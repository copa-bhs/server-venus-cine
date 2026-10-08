"""
Dashboard /home — monta todas as coleções da tela inicial.
12 coleções + hero (5 slides rotativos) + seção de trailers.
Rotação semanal automática via seed da semana ISO. Sem IA necessária.
"""
import time
import random
from datetime import datetime, timedelta

from config import TMDB_ENABLED


# ==========================================================
# ROTAÇÃO SEMANAL DETERMINÍSTICA
# ==========================================================
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
# HELPERS
# ==========================================================
def _tem_capa(item: dict) -> bool:
    return bool(item.get("capa"))


def _tem_banner(item: dict) -> bool:
    return bool(item.get("banner") or item.get("banner_4k"))


def _tem_banner_e_logo(item: dict) -> bool:
    return _tem_banner(item) and bool(item.get("logo"))


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


# ==========================================================
# DETECÇÃO DE TIPO — prioriza o campo "tipo" do próprio item
# ==========================================================
def _tipo_do_item(item: dict, tipo_fallback: str | None = None) -> str:
    """
    Descobre o tipo real do item.
    Ordem de prioridade:
      1) campo "tipo" do item (vem do parser.py)
      2) campo "total_temporadas" (indício de série)
      3) campo "episodios" presente (indício de série)
      4) fallback passado pelo caller
    """
    t = (item.get("tipo") or "").lower()
    if t in ("filme", "movie"):
        return "filme"
    if t in ("serie", "série", "series"):
        return "serie"
    if t == "canal":
        return "canal"

    # Fallback por estrutura
    if item.get("episodios") or item.get("total_temporadas") is not None:
        return "serie"

    return tipo_fallback or "filme"


def _item_para_card(item: dict, tipo_fallback: str | None = None) -> dict:
    tipo = _tipo_do_item(item, tipo_fallback)
    return {
        "id": item.get("id"),
        "tipo": tipo,
        "titulo": item.get("titulo"),
        "ano": item.get("ano"),
        "capa": item.get("capa"),
        "capa_grande": item.get("capa_grande"),
        "logo": item.get("logo"),
        "banner": item.get("banner"),
        "banner_4k": item.get("banner_4k"),
        "score": item.get("score"),
        "classificacao": item.get("classificacao"),
        "generos": item.get("generos") or [],
        "categoria": item.get("categoria"),
        "trailer_url": item.get("trailer_url"),
        "trailer_nome": item.get("trailer_nome"),
        "total_temporadas": item.get("total_temporadas"),
        "total_episodios": item.get("total_episodios"),
    }


# ==========================================================
# HERO — 5 slides rotativos
# ==========================================================
def _build_hero(filmes: list, series: list, n: int = 5) -> list:
    candidatos = []

    for f in filmes:
        if _tem_banner_e_logo(f) and _tem_capa(f):
            candidatos.append(_item_para_card(f, "filme"))
    for s in series:
        if _tem_banner_e_logo(s) and _tem_capa(s):
            candidatos.append(_item_para_card(s, "serie"))

    if len(candidatos) < n:
        vistos = {c["id"] for c in candidatos}
        for f in filmes:
            if _tem_banner(f) and f.get("id") not in vistos:
                candidatos.append(_item_para_card(f, "filme"))
                vistos.add(f.get("id"))
        for s in series:
            if _tem_banner(s) and s.get("id") not in vistos:
                candidatos.append(_item_para_card(s, "serie"))
                vistos.add(s.get("id"))

    randomizados = _shuffle_semanal(candidatos, "hero")
    return randomizados[:n]


# ==========================================================
# COLETORES
# ==========================================================
def _top_por_score(itens: list, n: int = 10) -> list:
    validos = [i for i in itens if _tem_capa(i) and _score(i) > 0]
    validos.sort(key=_score, reverse=True)
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
    validos = [i for i in itens if _tem_capa(i) and _score(i) >= min_score]
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
# 12 COLEÇÕES
# ==========================================================
def _construir_colecoes(filmes: list, series: list) -> list:
    todos = filmes + series
    colecoes = []

    def add(id_, titulo, itens, tipo_padrao: str | None = None):
        if not itens:
            return
        items_fmt = [
            _item_para_card(it, tipo_padrao) for it in itens
        ]
        colecoes.append({
            "id": id_,
            "titulo": titulo,
            "layout": "poster",
            "total": len(items_fmt),
            "items": items_fmt,
        })

    # 1 — Top 10 Filmes
    add("top10_filmes", "🏆 Top 10 Filmes", _top_por_score(filmes, 10), "filme")

    # 2 — Top 10 Séries
    add("top10_series", "🏆 Top 10 Séries", _top_por_score(series, 10), "serie")

    # 3 — Em Alta (rotativo semanal)
    altos_f = _top_por_score(filmes, 30)
    altos_s = _top_por_score(series, 30)
    em_alta = _shuffle_semanal(altos_f + altos_s, "em_alta")[:15]
    add("em_alta", "🔥 Em Alta", em_alta)

    # 4 — Lançamentos
    add("lancamentos", "🆕 Lançamentos", _recentes(filmes, 20), "filme")

    # 5 — Ação
    add("acao", "💥 Ação",
        _por_genero(todos, ["Ação", "Action", "Aventura"], 15))

    # 6 — Comédia
    add("comedia", "😂 Comédia",
        _por_genero(todos, ["Comédia", "Comedy"], 15))

    # 7 — Drama
    add("drama", "🎭 Drama", _por_genero(todos, ["Drama"], 15))

    # 8 — Terror
    add("terror", "👻 Terror",
        _por_genero(todos, ["Terror", "Horror"], 15))

    # 9 — Ficção Científica
    add("ficcao", "🚀 Ficção Científica",
        _por_genero(todos, ["Ficção científica", "Science Fiction", "Sci-Fi"], 15))

    # 10 — Animação
    add("animacao", "🎨 Animação",
        _por_genero(todos, ["Animação", "Animation"], 15))

    # 11 — Bem Avaliados
    add("bem_avaliados", "⭐ Bem Avaliados", _bem_avaliados(todos, 8.0, 15))

    # 12 — Catálogo Novo
    add("catalogo_novo", "📚 Catálogo Novo", _top_por_ano(todos, 15))

    return colecoes


# ==========================================================
# TRAILERS
# ==========================================================
def _construir_trailers(filmes: list, series: list, n: int = 10) -> list:
    todos = []
    for f in filmes:
        if f.get("trailer_url"):
            todos.append(_item_para_card(f, "filme"))
    for s in series:
        if s.get("trailer_url"):
            todos.append(_item_para_card(s, "serie"))

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