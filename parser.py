from __future__ import annotations

import re
import hashlib
from urllib.parse import urlparse


ATTR_RE = re.compile(r'([a-zA-Z0-9_\-]+)="([^"]*)"')
YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')
EPISODE_RE = re.compile(r'\s*S(\d+)\s*[E\xD7x]\s*(\d+)', re.I)


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


def clean_name(name: str) -> str:
    if not name:
        return ""
    t = name
    t = EPISODE_RE.sub(" ", t)
    t = re.sub(r'[\(\[]\s*(19|20)\d{2}\s*[\)\]]', ' ', t)
    t = re.sub(r'#\S+', ' ', t)
    t = re.sub(r'\[[^\]]*\]', ' ', t)
    t = re.sub(r'\(([^)]*)\)', r'\1', t)
    t = re.sub(r'^\s*(filme|série|serie|movie|vod|novela|anime)\s*[:\-–—|]\s*', '', t, flags=re.I)
    t = t.replace("|", " ").replace("/", " ").replace("\\", " ")
    t = re.sub(r'[–—]', '-', t)
    t = re.sub(r'\s*-\s*-\s*', ' - ', t)
    t = re.sub(r'\s+', ' ', t)
    t = t.strip(" -–—_.,;:()[]{}")
    return t


def clean_category(cat: str) -> str:
    if not cat:
        return "Geral"
    c = cat.strip()
    c = re.sub(r'^\s*(filmes?|séries?|series?|canais?|movies?|tv|vod|novelas?|animes?)\s*[•·\-–—:|,]\s*', '', c, flags=re.I)
    c = re.sub(r'^\s*(filmes?|séries?|series?|canais?|movies?|tv|vod)\s+', '', c, flags=re.I)
    c = re.sub(r'\s*[•·|/]\s*', ' ', c)
    c = re.sub(r'\s+', ' ', c).strip()
    if c.isupper():
        c = c.title()
    return c or "Geral"


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


def build_movie(item: dict) -> dict:
    raw_name = item.get("name", "")
    titulo = clean_name(raw_name)
    url = item.get("url")
    return {
        "id": extract_provider_id(url) or stable_id(item),
        "tipo": "filme",
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
        "tipo": "serie",
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
        "tipo": "canal",
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
                "tipo": "serie",
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
        g["episodios"].sort(key=lambda e: (e["temporada"] or 0, e["episodio"] or 0))
        result.append(g)
    return result


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