"""
Job de re-verificação de trailers.
Varre os caches de info (filmes/séries) que NÃO têm trailer,
re-consulta o TMDB e, se encontrou agora, atualiza o cache local.
Útil quando você adiciona manualmente um trailer no TMDB depois
que o item já tinha sido cacheado sem trailer.
"""
import asyncio
import json
import time
from pathlib import Path

import tmdb
from config import (
    TMDB_ENABLED, INFO_CACHE_DIR,
    TRAILER_CHECK_INTERVAL, TRAILER_CHECK_BATCH, TRAILER_CHECK_STARTUP_DELAY,
)


def _log(msg: str):
    print(f"[TRAILER] {msg}")


def _ler_cache(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _salvar_cache(p: Path, payload: dict):
    p.write_text(
        json.dumps({"_ts": time.time(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def _sem_trailer(payload: dict) -> bool:
    """True se o payload não tem trailer válido."""
    if not payload:
        return False
    trailer = payload.get("trailer")
    trailer_url = payload.get("trailer_url")
    return not trailer and not trailer_url


def _tem_titulo(payload: dict) -> bool:
    return bool((payload.get("titulo") or "").strip())


async def _reverificar_item(p: Path, tipo: str) -> bool:
    """
    Re-consulta o TMDB pra um arquivo de cache.
    Retorna True se atualizou, False se nada mudou.
    """
    data = _ler_cache(p)
    if not data:
        return False

    payload = data.get("payload") or {}
    if not _tem_titulo(payload):
        return False

    titulo = payload.get("titulo")
    ano = payload.get("ano")

    try:
        if tipo == "filme":
            dados = await tmdb.buscar_filme(titulo, ano)
        else:
            dados = await tmdb.buscar_serie(titulo, ano)
    except Exception as e:
        _log(f"erro ao consultar '{titulo}': {e}")
        return False

    if not dados:
        return False

    trailer = dados.get("trailer")
    trailer_url = dados.get("trailer_url")
    if not trailer and not trailer_url:
        return False

    # Atualiza o payload com o trailer encontrado
    payload["trailer"] = trailer
    payload["trailer_url"] = trailer_url
    payload["trailer_nome"] = dados.get("trailer_nome")

    # Também pode preencher outras coisas que faltavam
    if not payload.get("banner") and dados.get("banner"):
        payload["banner"] = dados["banner"]
    if not payload.get("banner_4k") and dados.get("banner_4k"):
        payload["banner_4k"] = dados["banner_4k"]
    if not payload.get("logo") and dados.get("logo"):
        payload["logo"] = dados["logo"]
    if not payload.get("classificacao") and dados.get("classificacao"):
        payload["classificacao"] = dados["classificacao"]

    # Marca a origem da atualização
    anteriores = payload.get("campos_preenchidos_por_tmdb") or []
    if "trailer" not in anteriores:
        anteriores.append("trailer")
    payload["campos_preenchidos_por_tmdb"] = anteriores

    _salvar_cache(p, payload)
    _log(f"✅ trailer adicionado: {titulo} ({ano or 's/ano'})")
    return True


async def trailer_check_loop():
    """
    Loop infinito. A cada TRAILER_CHECK_INTERVAL segundos:
      1. Varre os caches de info de filmes e séries
      2. Filtra os que NÃO têm trailer
      3. Re-consulta o TMDB num lote limitado
      4. Atualiza o cache local quando encontra
    """
    if not TMDB_ENABLED:
        _log("desativado (TMDB sem API key).")
        return

    _log(f"ativo — varredura a cada {TRAILER_CHECK_INTERVAL}s, lote de {TRAILER_CHECK_BATCH}")

    # Espera inicial pra não competir com o enricher no boot
    await asyncio.sleep(TRAILER_CHECK_STARTUP_DELAY)

    while True:
        try:
            filmes = sorted(INFO_CACHE_DIR.glob("movie_*.json"))
            series = sorted(INFO_CACHE_DIR.glob("series_*.json"))

            pendentes = []
            for p in filmes:
                data = _ler_cache(p)
                if data and _sem_trailer(data.get("payload") or {}):
                    pendentes.append((p, "filme"))
            for p in series:
                data = _ler_cache(p)
                if data and _sem_trailer(data.get("payload") or {}):
                    pendentes.append((p, "serie"))

            if not pendentes:
                _log("nenhum item sem trailer. Dormindo 6h.")
                await asyncio.sleep(3600 * 6)
                continue

            lote = pendentes[:TRAILER_CHECK_BATCH]
            _log(f"reverificando {len(lote)} itens ({len(pendentes)} pendentes no total)")

            atualizados = 0
            for p, tipo in lote:
                if await _reverificar_item(p, tipo):
                    atualizados += 1
                await asyncio.sleep(0.2)   # respeita rate limit

            _log(f"lote concluído — {atualizados} trailer(es) encontrado(s)")

        except Exception as e:
            _log(f"erro no loop: {e}")

        await asyncio.sleep(TRAILER_CHECK_INTERVAL)


async def checar_agora(limit: int = 50) -> dict:
    """
    Executa uma varredura única agora (usado pela rota manual /trailers/verificar).
    Retorna resumo.
    """
    if not TMDB_ENABLED:
        return {"ok": False, "erro": "TMDB desativado"}

    filmes = sorted(INFO_CACHE_DIR.glob("movie_*.json"))
    series = sorted(INFO_CACHE_DIR.glob("series_*.json"))

    pendentes = []
    for p in filmes:
        data = _ler_cache(p)
        if data and _sem_trailer(data.get("payload") or {}):
            pendentes.append((p, "filme"))
    for p in series:
        data = _ler_cache(p)
        if data and _sem_trailer(data.get("payload") or {}):
            pendentes.append((p, "serie"))

    total_pendentes = len(pendentes)
    lote = pendentes[:limit]

    atualizados = []
    for p, tipo in lote:
        data = _ler_cache(p)
        titulo = (data or {}).get("payload", {}).get("titulo", "?")
        if await _reverificar_item(p, tipo):
            atualizados.append(titulo)
        await asyncio.sleep(0.2)

    return {
        "ok": True,
        "total_sem_trailer": total_pendentes,
        "verificados": len(lote),
        "atualizados": len(atualizados),
        "lista_atualizados": atualizados,
    }