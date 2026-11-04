import json
import re
import os
import time
import asyncio
import httpx
import random
from datetime import datetime, timezone
from pathlib import Path
import logs

from config import (
    TMDB_API_KEY, IPTV_HOST, IPTV_USERNAME, IPTV_PASSWORD, USER_AGENT, CACHE_DIR
)
from tmdb import normalizar_classificacao # ⚡ IMPORTANDO A TRADUÇÃO AQUI TAMBÉM

ARQUIVO_CATALOGO = CACHE_DIR / "catalogo.json"
ARQUIVO_FALHAS = CACHE_DIR / "falhas.json"
TMDB_BASE = "https://api.themoviedb.org/3"

def _salvar_json(dados, path):
    temp = str(path) + ".tmp"
    with open(temp, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)

async def _buscar_tmdb(client, nome, tipo, ano=None):
    endpoint = "movie" if tipo == "movie" else "tv"
    params = {"api_key": TMDB_API_KEY, "query": nome, "language": "pt-BR"}
    if ano: params["year" if tipo == "movie" else "first_air_date_year"] = str(ano)
    try:
        r = await client.get(f"{TMDB_BASE}/search/{endpoint}", params=params, timeout=10)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results: return results[0]["id"]
    except: pass
    return None

async def _obter_detalhes_tmdb(client, tmdb_id, tipo):
    endpoint = "movie" if tipo == "movie" else "tv"
    append = "external_ids,release_dates,images" if tipo == "movie" else "external_ids,content_ratings,images"
    params = {"api_key": TMDB_API_KEY, "language": "pt-BR", "append_to_response": append, "include_image_language": "pt,en,null"}
    try:
        r = await client.get(f"{TMDB_BASE}/{endpoint}/{tmdb_id}", params=params, timeout=10)
        if r.status_code != 200: return None
        d = r.json()
        data_str = d.get('release_date') or d.get('first_air_date') or ""
        ano = data_str[:4] if data_str else None
        
        logo_url = None
        logos = d.get('images', {}).get('logos', [])
        if logos:
            for lg in logos:
                if lg.get('iso_639_1') == 'pt': logo_url = f"https://image.tmdb.org/t/p/w500{lg['file_path']}"; break
            if not logo_url:
                for lg in logos:
                    if lg.get('iso_639_1') in ['en', None]: logo_url = f"https://image.tmdb.org/t/p/w500{lg['file_path']}"; break

        age_rating = None
        if tipo == "movie":
            for c in d.get('release_dates', {}).get('results', []):
                if c.get('iso_3166_1') in ['BR', 'US']:
                    for rd in c.get('release_dates', []):
                        if rd.get('certification', '').strip(): age_rating = rd['certification'].strip(); break
                    if age_rating: break
        else:
            for c in d.get('content_ratings', {}).get('results', []):
                if c.get('iso_3166_1') in ['BR', 'US'] and c.get('rating', '').strip(): age_rating = c['rating'].strip(); break

        # ⚡ APLICANDO A TRADUÇÃO AQUI
        if age_rating:
            age_rating = normalizar_classificacao(age_rating)

        titulo_base = d.get('title') or d.get('name') or ""
        slug_gerado = re.sub(r'[^a-z0-9]+', '-', titulo_base.lower()).strip('-')
        slug_gerado = f"{slug_gerado}-{ano}" if ano else slug_gerado

        return {
            "tmdb_id": d.get('id'), "imdb_id": d.get('external_ids', {}).get('imdb_id'),
            "title": d.get('title') or d.get('name'), "original_title": d.get('original_title') or d.get('original_name'),
            "slug": slug_gerado, "type": "movie" if tipo == "movie" else "series",
            "year": int(ano) if ano and ano.isdigit() else None,
            "duration": d.get('runtime') or (d.get('episode_run_time', [None])[0] if d.get('episode_run_time') else None),
            "description": d.get('overview'),
            "poster": f"https://image.tmdb.org/t/p/w500{d['poster_path']}" if d.get('poster_path') else None,
            "logo": logo_url,
            "backdrop": f"https://image.tmdb.org/t/p/w780{d['backdrop_path']}" if d.get('backdrop_path') else None,
            "rating_avg": d.get('vote_average', 0), "rating_count": d.get('vote_count', 0),
            "views_count": 0, "premium": True, "audio_type": "dubbed",
            "genres": [g['name'] for g in d.get('genres', [])],
            "age_rating": age_rating
        }
    except Exception as e:
        return None

async def processar_item(client, item, semaforo):
    async with semaforo:
        nome_limpo = item["titulo"]
        tmdb_id = await _buscar_tmdb(client, nome_limpo, item["tipo"], item.get("ano"))
        
        if not tmdb_id:
            action = "get_vod_info" if item["tipo"] == "movie" else "get_series_info"
            url_info = f"{IPTV_HOST}/player_api.php?username={IPTV_USERNAME}&password={IPTV_PASSWORD}&action={action}&vod_id={item['id']}" if item["tipo"] == "movie" else f"{IPTV_HOST}/player_api.php?username={IPTV_USERNAME}&password={IPTV_PASSWORD}&action={action}&series_id={item['id']}"
            try:
                r = await client.get(url_info, timeout=10)
                if r.status_code == 200: tmdb_id = r.json().get('info', {}).get('tmdb_id')
            except: pass

        ts_added = item.get("added", 0)
        created_at_iso = None
        if ts_added and ts_added > 0:
            try:
                dt = datetime.fromtimestamp(ts_added, tz=timezone.utc)
                created_at_iso = dt.isoformat().replace("+00:00", "Z")
            except:
                pass

        if tmdb_id:
            meta = await _obter_detalhes_tmdb(client, tmdb_id, item["tipo"])
            if meta:
                meta["audio_type"] = item.get("audio_type", "dubbed")
                return {"status": "success", "data": {
                    "iptv_id": item["id"], 
                    "stream_type": item["tipo"], 
                    "coletany": item.get("categoria", "Desconhecida"), 
                    "url": item.get("url", ""), 
                    "created_at": created_at_iso,
                    **meta
                }}
        
        return {"status": "fail", "data": {"iptv_id": item["id"], "nome_original": item.get("nome_bruto") or item["titulo"], "tipo": item["tipo"]}}

async def rodar_importacao():
    import parser as iptv_parser
    
    logs.adicionar_log("📥 Baixando listas do IPTV...")
    filmes, series = await asyncio.gather(iptv_parser.fetch_filmes(), iptv_parser.fetch_series())
    itens = filmes + series
    total = len(itens)
    random.shuffle(itens) 
    
    logs.adicionar_log(f"✅ {total} itens encontrados. Enriquecendo (MODO TURBO MISTURADO)...")

    catalogo, falhas = [], []
    semaforo = asyncio.Semaphore(80)

    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}, limits=httpx.Limits(max_connections=100)) as client:
        for i in range(0, total, 50):
            chunk = itens[i:i+50]
            tasks = [processar_item(client, it, semaforo) for it in chunk]
            results = await asyncio.gather(*tasks)
            
            for res in results:
                if res["status"] == "success": catalogo.append(res["data"])
                else: falhas.append(res["data"])
            
            logs.adicionar_log(f"[{min(i+50, total)}/{total}] Processados e salvos no cache...")
            _salvar_json(catalogo, ARQUIVO_CATALOGO)
            _salvar_json(falhas, ARQUIVO_FALHAS)

    logs.adicionar_log(f"🏁 Enriquecimento concluído! {len(catalogo)} sucessos. Iniciando envio para o BANCO DE DADOS...")