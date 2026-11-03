import re
import asyncio
import httpx
from config import IPTV_HOST, IPTV_USERNAME, IPTV_PASSWORD, USER_AGENT

BASE_API = f"{IPTV_HOST}/player_api.php"
USERNAME = IPTV_USERNAME
PASSWORD = IPTV_PASSWORD

EPISODE_RE = re.compile(r'\s*S\d+\s*[E\xD7x]\s*\d+.*$', re.I)
YEAR_RE = re.compile(r'\b(19\d{2}|20\d{2})\b')

_TAGS_TECNICAS = re.compile(
    r'\b(4k|uhd|fhd|hd|sd|cam|ts|hdcam|web[\s\-]?dl|webrip|bluray|brrip|bdrip|dvdrip|hdrip|hdtv|hdr|dv|sdr|remux|proper|repack|x264|x265|h264|h265|hevc|avc|aac|ac3|eac3|ddp?5[\s\.]?1|5[\s\.]?1|7[\s\.]?1|atmos|dual[\s\-]?[aá]udio|dual|dublado|legendado|dublagem|legend|dub|leg|nacional|original|1080p?|720p?|480p?|360p?|2160p?|1440p?|2k|8k|extended|director[\'s]*[\s]*cut|unrated|imax|theatrical|fullhd|full[\s\-]?hd|br|tv|online|h\.264|h\.265)\b', re.I)
_PREFIXO_RE = re.compile(r'^\s*(filmes?|s[eé]ries?|movies?|vod|novelas?|animes?|canais?|tv|show|document[áa]rio)\s*[:\-–—|•·]\s*', re.I)
_SEPARADOR_FIM_RE = re.compile(r'\s*[\-–—|•·]\s*(?:\d{4}|\d+p|dual|dub|leg|hd|sd|4k|uhd|fhd|web|bluray|bdrip|x26\d|h26\d|hevc)\b.*$', re.I)

def detectar_audio_type(nome: str) -> str:
    nome_upper = nome.upper()
    if "NACIONAL" in nome_upper: return "national"
    if re.search(r'\b(LEG|LEGENDADO|LEGENDAS?)\b', nome_upper): return "subtitled"
    return "dubbed"

def _clean_name(name: str) -> str:
    if not name: return ""
    t = name.strip()
    t = _PREFIXO_RE.sub('', t)
    t = EPISODE_RE.sub(" ", t)
    t = re.sub(r'[\(\[]\s*(19|20)\d{2}\s*[\)\]]', ' ', t)
    t = re.sub(r'#\S+', ' ', t)
    t = re.sub(r'\[[^\]]*\]', ' ', t)
    t = re.sub(r'\(\s*\)', ' ', t)
    t = re.sub(r'\(\s*(?:dub|leg|hd|sd|4k|1080p?|720p?|dual)\s*\)', ' ', t, flags=re.I)
    t = _SEPARADOR_FIM_RE.sub('', t)
    t = _TAGS_TECNICAS.sub(' ', t)
    t = re.sub(r'[|/\\]', ' ', t)
    t = re.sub(r'[–—]', '-', t)
    t = re.sub(r'[\s\-–—_.,;:]+(19|20)\d{2}\s*$', '', t)
    t = re.sub(r'[\s\-–—_.,;:]+#?\d{4,}\s*$', '', t)
    t = re.sub(r'\s*-\s*-\s*', ' - ', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip(" -–—_.,;:()[]{}")

def _clean_category(cat: str) -> str:
    if not cat: return "Geral"
    c = cat.strip()
    
    # ⚡ 1. Remove prefixos comuns ("FILMES |", "SÉRIES •", etc)
    c = re.sub(r'^\s*(filmes?|s[eé]ries?|canais?|movies?|tv|vod|novelas?|animes?)\s*[•·\-–—:|,]\s*', '', c, flags=re.I)
    c = re.sub(r'^\s*(filmes?|s[eé]ries?|canais?|movies?|tv|vod)\s+', '', c, flags=re.I)
    c = re.sub(r'\s*[•·|/]\s*', ' ', c)
    
    # ⚡ 2. Padroniza nomes de streamings conhecidos
    c_upper = c.upper()
    if "NETFLIX" in c_upper: return "Netflix"
    if "AMAZON" in c_upper or "PRIME" in c_upper: return "Amazon Prime"
    if "DISNEY" in c_upper: return "Disney+"
    if "APPLE" in c_upper: return "Apple TV"
    if "GLOBO" in c_upper: return "GloboPlay"
    if "HBO" in c_upper or "MAX" in c_upper: return "HBO Max"
    if "PARAMOUNT" in c_upper: return "Paramount+"
    if "STAR" in c_upper and "PLUS" in c_upper: return "Star+"
    if "LOOKE" in c_upper: return "Looke"
    if "TELECINE" in c_upper: return "Telecine"
    
    # 3. Se for apenas um ano ou tag genérica, mantém como está (vai ser filtrado no home.py)
    if re.match(r'^\d{4}$', c): return c
    if c_upper in ["4K", "FHD", "HD", "SD", "LANÇAMENTOS", "LANCAMENTOS", "FILMES", "SÉRIES"]: return c_upper
    
    # 4. Formata em Title Case
    c = re.sub(r'\s+', ' ', c).strip()
    return c.title() or "Geral"

def _extract_year(text):
    if not text: return None
    m = YEAR_RE.search(str(text))
    return int(m.group(1)) if m else None

async def _fetch(client: httpx.AsyncClient, action: str) -> list:
    url = f"{BASE_API}?username={USERNAME}&password={PASSWORD}&action={action}"
    try:
        r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=300)
        if r.status_code != 200: return []
        data = r.json()
        if isinstance(data, dict) and "user_info" in data and data["user_info"].get("auth") == 0: return []
        return data if isinstance(data, list) else []
    except Exception:
        return []

async def fetch_filmes() -> list:
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        cats, streams = await asyncio.gather(_fetch(client, "get_vod_categories"), _fetch(client, "get_vod_streams"))
    cat_map = {str(c.get("category_id")): _clean_category(c.get("category_name") or "Geral") for c in cats if isinstance(c, dict)}
    filmes = []
    for s in streams:
        if not isinstance(s, dict): continue
        sid = s.get("stream_id")
        if not sid: continue
        ext = s.get("container_extension") or "mp4"
        nome = s.get("name") or ""
        filmes.append({
            "id": str(sid), "tipo": "movie", "titulo": _clean_name(nome) or nome.strip(),
            "nome_bruto": nome, "capa": s.get("stream_icon") or s.get("cover") or None, "ano": _extract_year(nome),
            "categoria": cat_map.get(str(s.get("category_id") or ""), "Geral"),
            "url": f"{IPTV_HOST}/movie/{USERNAME}/{PASSWORD}/{sid}.{ext}",
            "audio_type": detectar_audio_type(nome),
            "added": int(s.get("added") or 0)
        })
    filmes.sort(key=lambda x: (x["titulo"] or "").lower())
    return filmes

async def fetch_series() -> list:
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        cats, series = await asyncio.gather(_fetch(client, "get_series_categories"), _fetch(client, "get_series"))
    cat_map = {str(c.get("category_id")): _clean_category(c.get("category_name") or "Geral") for c in cats if isinstance(c, dict)}
    result = []
    for s in series:
        if not isinstance(s, dict): continue
        sid = s.get("series_id") or s.get("num")
        if not sid: continue
        nome = s.get("name") or ""
        release = s.get("releaseDate") or s.get("release_date") or ""
        result.append({
            "id": str(sid), "tipo": "tv", "titulo": _clean_name(nome) or nome.strip(),
            "nome_bruto": nome, "capa": s.get("cover") or s.get("stream_icon") or None,
            "ano": _extract_year(release) or _extract_year(nome),
            "categoria": cat_map.get(str(s.get("category_id") or ""), "Geral"),
            "url": "",
            "audio_type": detectar_audio_type(nome),
            "added": int(s.get("last_modified") or s.get("added") or 0)
        })
    result.sort(key=lambda x: (x["titulo"] or "").lower())
    return result