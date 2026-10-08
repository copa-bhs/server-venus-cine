from __future__ import annotations

import asyncio

import tmdb
import logos
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

        # Capa — séries SEMPRE sobrescrevem (M3U traz capa do episódio)
        if tipo == "serie":
            if dados.get("capa"):
                item["capa"] = dados["capa"]
        else:
            if dados.get("capa") and not item.get("capa"):
                item["capa"] = dados["capa"]

        # ==========================================================
        # LOGO — agregação multi-fonte: TMDB → Fanart.tv
        # ==========================================================
        logo_tmdb = dados.get("logo")
        logo_final = None

        if tipo == "filme":
            logo_final = await logos.buscar_logo_filme(
                titulo=item.get("titulo") or "",
                ano=item.get("ano"),
                tmdb_id=dados.get("tmdb_id"),
                logo_tmdb=logo_tmdb,
            )
        else:
            logo_final = await logos.buscar_logo_serie(
                titulo=item.get("titulo") or "",
                ano=item.get("ano"),
                tmdb_id=dados.get("tmdb_id"),
                tvdb_id=dados.get("tvdb_id"),
                logo_tmdb=logo_tmdb,
            )

        if logo_final:
            item["logo"] = logo_final
            item["logo_fonte"] = "tmdb" if logo_tmdb else "fanart"

        # Banner 4K
        if dados.get("banner_4k"):
            item["banner_4k"] = dados["banner_4k"]
        if dados.get("banner"):
            item["banner"] = dados["banner"]

        # Capa grande
        if dados.get("capa_grande"):
            item["capa_grande"] = dados["capa_grande"]

        # Trailer
        if dados.get("trailer_url"):
            item["trailer_url"] = dados["trailer_url"]
            item["trailer_nome"] = dados.get("trailer_nome")
            item["trailer"] = dados.get("trailer")

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