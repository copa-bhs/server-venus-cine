import re
import asyncio
import httpx

from config import IPTV_HOST, IPTV_USERNAME, IPTV_PASSWORD, USER_AGENT

BASE_API = f"{IPTV_HOST}/player_api.php"
USERNAME = IPTV_USERNAME
PASSWORD = IPTV_PASSWORD


EPISODE_RE = re.compile(r'\s*S\d+\s*[E\xD7x]\s*\d+.*$', re.I)
YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')


def _clean_name(name: str) -> str:
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
    t = re.sub(r'\s+', ' ', t)
    return t.strip(" -–—_.,;:()[]{}")


def _clean_category(cat: str) -> str:
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


def _extract_year(text):
    if not text:
        return None
    m = YEAR_RE.search(str(text))
    return int(m.group(1)) if m else None


# ==========================================================
# FETCH — chamadas separadas por tipo
# ==========================================================
async def _fetch(client: httpx.AsyncClient, action: str) -> list:
    url = f"{BASE_API}?username={USERNAME}&password={PASSWORD}&action={action}"
    try:
        r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=90)
        if r.status_code != 200:
            print(f"[!] {action}: HTTP {r.status_code}")
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[!] {action}: {e}")
        return []


async def fetch_filmes() -> list:
    """Baixa filmes + categorias e retorna lista pronta."""
    print("[•] Baixando filmes (API Xtream)...")
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        cats, streams = await asyncio.gather(
            _fetch(client, "get_vod_categories"),
            _fetch(client, "get_vod_streams"),
        )

    cat_map = {str(c.get("category_id")): (c.get("category_name") or "Geral")
               for c in cats if isinstance(c, dict)}

    filmes = []
    for s in streams:
        if not isinstance(s, dict):
            continue
        sid = s.get("stream_id")
        if not sid:
            continue
        ext = s.get("container_extension") or "mp4"
        cat_id = str(s.get("category_id") or "")
        nome = s.get("name") or ""
        filmes.append({
            "id": str(sid),
            "tipo": "filme",
            "titulo": _clean_name(nome) or nome.strip(),
            "capa": s.get("stream_icon") or s.get("cover") or None,
            "ano": _extract_year(nome),
            "categoria": _clean_category(cat_map.get(cat_id, "Geral")),
            "url": f"{IPTV_HOST}/movie/{USERNAME}/{PASSWORD}/{sid}.{ext}",
        })

    filmes.sort(key=lambda x: (x["titulo"] or "").lower())
    print(f"[✓] {len(filmes)} filmes baixados")
    return filmes


async def fetch_series() -> list:
    """Baixa séries + categorias e retorna lista pronta."""
    print("[•] Baixando séries (API Xtream)...")
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        cats, series = await asyncio.gather(
            _fetch(client, "get_series_categories"),
            _fetch(client, "get_series"),
        )

    cat_map = {str(c.get("category_id")): (c.get("category_name") or "Geral")
               for c in cats if isinstance(c, dict)}

    result = []
    for s in series:
        if not isinstance(s, dict):
            continue
        sid = s.get("series_id") or s.get("num")
        if not sid:
            continue
        cat_id = str(s.get("category_id") or "")
        nome = s.get("name") or ""
        release = s.get("releaseDate") or s.get("release_date") or ""

        result.append({
            "id": str(sid),
            "tipo": "serie",
            "titulo": _clean_name(nome) or nome.strip(),
            "capa": s.get("cover") or s.get("stream_icon") or None,
            "ano": _extract_year(release) or _extract_year(nome),
            "categoria": _clean_category(cat_map.get(cat_id, "Geral")),
        })

    result.sort(key=lambda x: (x["titulo"] or "").lower())
    print(f"[✓] {len(result)} séries baixadas")
    return result


async def fetch_canais() -> list:
    """Baixa canais + categorias e retorna lista pronta."""
    print("[•] Baixando canais (API Xtream)...")
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        cats, streams = await asyncio.gather(
            _fetch(client, "get_live_categories"),
            _fetch(client, "get_live_streams"),
        )

    cat_map = {str(c.get("category_id")): (c.get("category_name") or "Geral")
               for c in cats if isinstance(c, dict)}

    canais = []
    for s in streams:
        if not isinstance(s, dict):
            continue
        sid = s.get("stream_id")
        if not sid:
            continue
        cat_id = str(s.get("category_id") or "")
        nome = s.get("name") or ""
        canais.append({
            "id": str(sid),
            "tipo": "canal",
            "titulo": _clean_name(nome) or nome.strip(),
            "capa": s.get("stream_icon") or None,
            "categoria": _clean_category(cat_map.get(cat_id, "Geral")),
            "url": f"{IPTV_HOST}/live/{USERNAME}/{PASSWORD}/{sid}.ts",
        })

    canais.sort(key=lambda x: (x["titulo"] or "").lower())
    print(f"[✓] {len(canais)} canais baixados")
    return canais