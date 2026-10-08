import asyncio

import tmdb
from config import (
    TMDB_ENABLED, ENRICH_BATCH_SIZE, ENRICH_INTERVAL, ENRICH_STARTUP_DELAY,
)


def _ja_enriquecido(item: dict) -> bool:
    return bool(item.get("tmdb_id") or item.get("classificacao"))


async def _enriquecer_item(item: dict, tipo: str):
    try:
        if tipo == "filme":
            dados = await tmdb.buscar_filme(item.get("titulo") or "", item.get("ano"))
        else:
            dados = await tmdb.buscar_serie(item.get("titulo") or "", item.get("ano"))

        if not dados:
            item["tmdb_nao_encontrado"] = True
            return

        item["tmdb_id"] = dados["tmdb_id"]
        item["classificacao"] = dados.get("classificacao")
        item["score"] = dados.get("score")
        item["generos"] = dados.get("generos") or []

        if dados.get("capa") and not item.get("capa"):
            item["capa"] = dados["capa"]

        if dados.get("logo"):
            item["logo"] = dados["logo"]

        item["tmdb_enriquecido"] = True

    except Exception as e:
        print(f"[!] Erro enriquecendo {item.get('titulo')}: {e}")


async def enricher_loop(index_ref: dict, save_callback):
    if not TMDB_ENABLED:
        print("[i] Enricher desativado (TMDB sem API key).")
        return

    print(f"[✓] Enricher ativo (lote de {ENRICH_BATCH_SIZE} itens a cada {ENRICH_INTERVAL}s)")
    await asyncio.sleep(ENRICH_STARTUP_DELAY)

    while True:
        try:
            filmes = index_ref.get("filmes", [])
            series = index_ref.get("series", [])

            pendentes = []
            for f in filmes:
                if not _ja_enriquecido(f) and not f.get("tmdb_nao_encontrado"):
                    pendentes.append((f, "filme"))
            for s in series:
                if not _ja_enriquecido(s) and not s.get("tmdb_nao_encontrado"):
                    pendentes.append((s, "serie"))

            if not pendentes:
                print("[✓] Enricher: catálogo 100% enriquecido. Dormindo 1h.")
                await asyncio.sleep(3600)
                continue

            lote = pendentes[:ENRICH_BATCH_SIZE]
            print(f"[•] Enricher: processando {len(lote)} itens "
                  f"({len(pendentes)} pendentes no total)")

            for item, tipo in lote:
                await _enriquecer_item(item, tipo)
                await asyncio.sleep(0.15)

            try:
                save_callback()
                print(f"[✓] Enricher: lote salvo. Faltam {len(pendentes) - len(lote)}.")
            except Exception as e:
                print(f"[!] Enricher: erro ao salvar: {e}")

        except Exception as e:
            print(f"[!] Enricher: erro no loop: {e}")

        await asyncio.sleep(ENRICH_INTERVAL)