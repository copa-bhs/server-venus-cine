import re
import hashlib
from urllib.parse import urlparse

ATTR_RE = re.compile(r'([a-zA-Z0-9_\-]+)="([^"]*)"')
YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')
EPISODE_RE = re.compile(r'\s*S(\d+)\s*[E\xd7x]\s*(\d+)', re.I)


# ==========================================================
# PARSER M3U
# ==========================================================
def _split_name_from_extinf(line: str) -> str:
    in_quotes = False
    last_comma = -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ',' and not in_quotes:
            last_comma = i
    if last_comma == -1:
        return ""
    return line[last_comma + 1:].strip()


def parse_extinf(line: str) -> dict:
    attrs = dict(ATTR_RE.findall(line))
    name = _split_name_from_extinf(line)
    if not name:
        name = attrs.get("tvg-name", "").strip()
    attrs["name"] = name
    return attrs


def parse_m3u(content: str) -> list:
    items = []
    pending = None
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            pending = parse_extinf(line)
        elif line.startswith("#"):
            continue
        elif pending is not None:
            pending["url"] = line
            items.append(pending)
            pending = None
    return items


# ==========================================================
# CLASSIFICAÇÃO
# ==========================================================
def classify(item: dict) -> str:
    path = urlparse(item.get("url", "")).path
    if "/movie/" in path:
        return "movie"
    if "/series/" in path:
        return "series"
    if "/live/" in path:
        return "live"

    group = (item.get("group-title") or "").lower()
    if any(k in group for k in ("filme", "movie", "vod", "lançamento")):
        return "movie"
    if any(k in group for k in ("serie", "série", "series", "novela", "anime")):
        return "series"
    if any(k in group for k in ("tv", "canal", "channel", "aberto", "fechado")):
        return "live"
    return "live"


# ==========================================================
# LIMPEZA DE NOME — remove (), [], /, |, #, prefixos, etc.
# ==========================================================
def clean_name(name: str) -> str:
    """
    Deixa o título 100% limpo:
      - remove ano entre parênteses
      - remove sufixos de episódio (S01E02)
      - remove hashtags, pipes, colchetes
      - remove parênteses e mantém o conteúdo interno
      - remove barras
      - remove prefixos tipo "FILME:", "SÉRIE -"
      - remove espaços duplos
    """
    if not name:
        return ""

    t = name

    # 1) Suprime sufixo de episódio
    t = EPISODE_RE.sub(" ", t)

    # 2) Remove ano entre parênteses: (2024), [2024]
    t = re.sub(r'[\(\[]\s*(19|20)\d{2}\s*[\)\]]', ' ', t)

    # 3) Remove hashtags
    t = re.sub(r'#\S+', ' ', t)

    # 4) Remove colchetes completos: [HD], [4K], [DUB]
    t = re.sub(r'\[[^\]]*\]', ' ', t)

    # 5) Remove parênteses mantendo o conteúdo de dentro
    #    Ex: "(DES)CONTROLE" → "DESCONTROLE"
    t = re.sub(r'\(([^)]*)\)', r'\1', t)

    # 6) Remove prefixos de tipo
    t = re.sub(r'^\s*(filme|série|serie|movie|vod|novela|anime)\s*[:\-–—|]\s*',
               '', t, flags=re.I)

    # 7) Troca barras, pipes e travessões por espaço
    t = t.replace("|", " ")
    t = t.replace("/", " ")
    t = t.replace("\\", " ")
    t = re.sub(r'[–—]', '-', t)

    # 8) Remove "-" soltos nas pontas e múltiplos seguidos
    t = re.sub(r'\s*-\s*-\s*', ' - ', t)

    # 9) Normaliza espaços
    t = re.sub(r'\s+', ' ', t)

    # 10) Limpa pontuação solta nas pontas
    t = t.strip(" -–—_.,;:()[]{}")

    return t


# ==========================================================
# LIMPEZA DE CATEGORIA — remove prefixo "FILMES", "SÉRIES", etc.
# ==========================================================
def clean_category(cat: str) -> str:
    """
    Deixa a categoria limpa:
      "FILMES • 2026"      → "2026"
      "SÉRIES | DRAMA"     → "Drama"
      "CANAIS | HD"        → "HD"
      "FILMES 2024"        → "2024"
      "LANÇAMENTOS"        → "Lançamentos"
    """
    if not cat:
        return "Geral"

    c = cat.strip()

    # Remove prefixo de tipo (FILMES, SÉRIES, CANAIS, MOVIES, TV, VOD)
    c = re.sub(
        r'^\s*(filmes?|séries?|series?|canais?|movies?|tv|vod|novelas?|animes?)\s*'
        r'[•·\-–—:|,]\s*',
        '',
        c,
        flags=re.I,
    )
    # Também remove se tiver espaço simples depois
    c = re.sub(
        r'^\s*(filmes?|séries?|series?|canais?|movies?|tv|vod)\s+',
        '',
        c,
        flags=re.I,
    )

    # Troca separadores •, ·, |, / por espaço
    c = re.sub(r'\s*[•·|/]\s*', ' ', c)

    # Normaliza espaços
    c = re.sub(r'\s+', ' ', c).strip()

    # Title Case (primeira letra maiúscula de cada palavra, se estiver tudo maiúsculo)
    if c.isupper():
        c = c.title()

    return c or "Geral"


# ==========================================================
# EXTRAÇÃO DE ID / ANO
# ==========================================================
def extract_provider_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r'/(?:movie|series|live)/[^/]+/[^/]+/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'/(\d+)(?:\.[a-z0-9]+)?$', url, re.I)
    return m.group(1) if m else None


def extract_year(text: str):
    if not text:
        return None
    m = YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def stable_id(item: dict) -> str:
    if item.get("tvg-id"):
        return item["tvg-id"]
    raw = f'{item.get("name","")}|{item.get("url","")}'
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# ==========================================================
# BUILDERS — agora todos com campo "tipo"
# ==========================================================
def build_movie(item: dict) -> dict:
    raw_name = item.get("name", "")
    titulo = clean_name(raw_name)
    url = item.get("url")

    return {
        "id": extract_provider_id(url) or stable_id(item),
        "tipo": "filme",                        # ← explícito
        "titulo": titulo,
        "capa": item.get("tvg-logo") or None,
        "ano": extract_year(raw_name),
        "categoria": clean_category(item.get("group-title", "")),
        "url": url,
    }


def build_series_episode(item: dict) -> dict:
    raw_name = item.get("name", "")
    titulo = clean_name(raw_name)
    url = item.get("url")

    m = EPISODE_RE.search(raw_name)
    season = int(m.group(1)) if m else None
    episode = int(m.group(2)) if m else None

    return {
        "id": extract_provider_id(url) or stable_id(item),
        "tipo": "serie",                        # ← explícito
        "titulo": titulo,
        "titulo_episodio": raw_name.strip(),
        "capa": item.get("tvg-logo") or None,
        "ano": extract_year(raw_name),
        "categoria": clean_category(item.get("group-title", "")),
        "temporada": season,
        "episodio": episode,
        "url": url,
    }


def build_channel(item: dict) -> dict:
    raw_name = item.get("name", "")
    url = item.get("url")
    return {
        "id": extract_provider_id(url) or stable_id(item),
        "tipo": "canal",                        # ← explícito
        "titulo": clean_name(raw_name) or raw_name.strip(),
        "capa": item.get("tvg-logo") or None,
        "categoria": clean_category(item.get("group-title", "")),
        "tvg_id": item.get("tvg-id"),
        "url": url,
    }


def group_series(episodes: list) -> list:
    groups = {}
    for ep in episodes:
        key = ep["titulo"] or ep["titulo_episodio"]
        if key not in groups:
            groups[key] = {
                "id": ep["id"],
                "tipo": "serie",                    # ← explícito
                "titulo": key,
                "capa": ep["capa"],
                "ano": ep["ano"],
                "categoria": ep["categoria"],
                "total_episodios": 0,
                "temporadas": set(),
                "episodios": [],
            }
        groups[key]["episodios"].append({
            "id": ep["id"],
            "titulo": ep["titulo_episodio"],
            "temporada": ep["temporada"],
            "episodio": ep["episodio"],
            "url": ep["url"],
        })
        groups[key]["total_episodios"] += 1
        if ep["temporada"]:
            groups[key]["temporadas"].add(ep["temporada"])

    result = []
    for g in groups.values():
        g["temporadas"] = sorted(g["temporadas"])
        g["total_temporadas"] = len(g["temporadas"])
        g["episodios"].sort(
            key=lambda e: (e["temporada"] or 0, e["episodio"] or 0)
        )
        result.append(g)
    return result


# ==========================================================
# ÍNDICE
# ==========================================================
def build_index(raw_items: list) -> dict:
    movies, series_eps, channels = [], [], []
    for it in raw_items:
        kind = classify(it)
        if kind == "movie":
            movies.append(build_movie(it))
        elif kind == "series":
            series_eps.append(build_series_episode(it))
        else:
            channels.append(build_channel(it))

    series = group_series(series_eps)

    movies.sort(key=lambda x: (x["titulo"] or "").lower())
    series.sort(key=lambda x: (x["titulo"] or "").lower())
    channels.sort(key=lambda x: (x["titulo"] or "").lower())

    return {
        "filmes": movies,
        "series": series,
        "canais": channels,
        "stats": {
            "total_filmes": len(movies),
            "total_series": len(series),
            "total_episodios": len(series_eps),
            "total_canais": len(channels),
        },
    }