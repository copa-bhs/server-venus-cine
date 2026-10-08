"""
Dashboard /home — monta todas as coleções da tela inicial.
Rotação semanal automática via seed da semana ISO. Sem IA.
"""
import time
import random
from datetime import datetime, timedelta

from config import TMDB_ENABLED


# ==========================================================
# ROTAÇÃO SEMANAL
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


def _tem_logo(item: dict) -> bool:
    return bool(item.get("logo"))


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
# FILTROS DE CONTEÚDO — exclui documentário, música, shows, etc.
# ==========================================================
_GENEROS_EXCLUIDOS = {
    "documentário", "documentario", "documentary",
    "música", "musica", "music", "musical",
    "talk show", "reality", "news", "notícias",
    "tv movie", "novela",
}

_TITULOS_EXCLUIDOS = [
    "rock in rio", "ao vivo", "live at", "concert", "concerto",
    "documentário", "trailer", "making of", "bastidores",
    "coletânea", "coletanea", "temporada completa", "trilha sonora",
]


def _elegivel_top10(item: dict) -> bool:
    """Exclui documentários, shows, música, etc. do Top 10."""
    # Pelo gênero (após enriquecimento TMDB)
    for g in (item.get("generos") or []):
        if g.strip().lower() in _GENEROS_EXCLUIDOS:
            return False

    # Pelo título
    titulo = (item.get("titulo") or "").lower()
    for termo in _TITULOS_EXCLUIDOS:
        if termo in titulo:
            return False

    # Pela categoria (caso não tenha sido enriquecido ainda)
    cat = (item.get("categoria") or "").lower()
    if any(t in cat for t in ("documentário", "documentario", "musical", "show")):
        return False

    return True


def _elegivel_hero(item: dict) -> bool:
    """Só filmes/séries de verdade, com capa boa."""
    if not _tem_capa(item):
        return False
    if _score(item) < 5.0:
        return False
    return _elegivel_top10(item)


# ==========================================================
# DETECÇÃO DE TIPO
# ==========================================================
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
# HERO — 5 slides com fallback inteligente
# ==========================================================
def _build_hero(filmes: list, series: list, n: int = 5) -> list:
    """
    Monta o hero com prioridade:
      1. Itens com banner_4k + logo (ideal — slide completo)
      2. Itens com banner + capa
      3. Itens com logo + capa
      4. Itens só com capa e score alto
    Prioriza lançamentos recentes (últimos 2 anos).
    """
    todos = filmes + series

    # Filtra só elegíveis
    elegiveis = [i for i in todos if _elegivel_hero(i)]

    if not elegiveis:
        return []

    def prioridade(item: dict) -> tuple:
        """Menor = melhor (ordenação crescente)."""
        tem_banner = _tem_banner(item)
        tem_logo = _tem_logo(item)
        tem_banner_4k = bool(item.get("banner_4k"))

        # Tiers (0 = melhor)
        if tem_banner_4k and tem_logo:
            tier = 0
        elif tem_banner and tem_logo:
            tier = 1
        elif tem_banner:
            tier = 2
        elif tem_logo:
            tier = 3
        else:
            tier = 4

        ano = _ano(item)
        # Prioriza ano recente (>= 2023)
        recente = 0 if ano >= 2023 else (1 if ano >= 2020 else 2)
        # Score invertido (maior primeiro)
        return (tier, recente, -_score(item))

    elegiveis.sort(key=prioridade)
    top = elegiveis[:30]

    # Rotaciona semanalmente entre os 30 melhores
    randomizados = _shuffle_semanal(top, "hero")
    return [_item_para_card(i) for i in randomizados[:n]]


# ==========================================================
# COLETORES COM FILTROS
# ==========================================================
def _top_por_score(itens: list, n: int = 10, filtrar_elegiveis: bool = True) -> list:
    validos = [i for i in itens if _tem_capa(i) and _score(i) > 0]
    if filtrar_elegiveis:
        validos = [i for i in validos if _elegivel_top10(i)]
    validos.sort(key=_score, reverse=True)
    return validos[:n]


def _top_series_fallback(series: list, n: int = 10) -> list:
    """Fallback quando séries ainda não têm score: usa total_episodios."""
    validos = [s for s in series if _tem_capa(s)]
    validos.sort(
        key=lambda s: (
            -(s.get("total_episodios") or 0),
            -_ano(s),
        )
    )
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
    validos = [
        i for i in itens
        if _tem_capa(i) and _score(i) >= min_score and _elegivel_top10(i)
    ]
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
        items_fmt = [_item_para_card(it, tipo_padrao) for it in itens]
        colecoes.append({
            "id": id_,
            "titulo": titulo,
            "layout": "poster",
            "total": len(items_fmt),
            "items": items_fmt,
        })

    # 1 — Top 10 Filmes (filtrando documentários, shows, música)
    top_filmes = _top_por_score(filmes, 10)
    add("top10_filmes", "🏆 Top 10 Filmes", top_filmes, "filme")

    # 2 — Top 10 Séries (com fallback se ainda não enriquecidas)
    top_series = _top_por_score(series, 10)
    if not top_series:
        top_series = _top_series_fallback(series, 10)
    add("top10_series", "🏆 Top 10 Séries", top_series, "serie")

    # 3 — Em Alta (rotativo semanal, filmes e séries)
    altos_f = _top_por_score(filmes, 30)
    altos_s = _top_por_score(series, 30) or _top_series_fallback(series, 30)
    em_alta = _shuffle_semanal(altos_f + altos_s, "em_alta")[:15]
    add("em_alta", "🔥 Em Alta", em_alta)

    # 4 — Lançamentos (recentes, com filtro)
    lanc = [i for i in _recentes(filmes, 30) if _elegivel_top10(i)]
    add("lancamentos", "🆕 Lançamentos", lanc[:20], "filme")

    # 5 — Ação
    add("acao", "💥 Ação",
        _por_genero(todos, ["Ação", "Action", "Aventura", "Adventure"], 15))

    # 6 — Comédia
    add("comedia", "😂 Comédia",
        _por_genero(todos, ["Comédia", "Comedy"], 15))

    # 7 — Drama
    add("drama", "🎭 Drama",
        _por_genero(todos, ["Drama"], 15))

    # 8 — Terror
    add("terror", "👻 Terror",
        _por_genero(todos, ["Terror", "Horror"], 15))

    # 9 — Ficção Científica
    add("ficcao", "🚀 Ficção Científica",
        _por_genero(todos, ["Ficção científica", "Science Fiction", "Sci-Fi"], 15))

    # 10 — Animação
    add("animacao", "🎨 Animação",
        _por_genero(todos, ["Animação", "Animation"], 15))

    # 11 — Bem Avaliados (score >= 8)
    add("bem_avaliados", "⭐ Bem Avaliados", _bem_avaliados(todos, 8.0, 15))

    # 12 — Catálogo Novo (mais recentes)
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
