import json
from pathlib import Path
from sqlalchemy.orm import Session
from db import SessionLocal, Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Falha as FalhaDB
from config import CACHE_DIR

CATALOGO_FILE = CACHE_DIR / "catalogo.json"
FALHAS_FILE = CACHE_DIR / "falhas.json"

def importar_catalogo():
    """
    Lê o catalogo.json e falhas.json gerados pelo iptv_catalog.py
    e faz o upsert (insere ou atualiza) no banco de dados SQL.
    """
    if not CATALOGO_FILE.exists():
        print("[Importer] ❌ Arquivo catalogo.json não encontrado.")
        return False

    try:
        catalogo = json.loads(CATALOGO_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Importer] ❌ Erro ao ler catalogo.json: {e}")
        return False

    db = SessionLocal()
    adicionados = 0
    atualizados = 0

    print(f"[Importer] Processando {len(catalogo)} itens do catálogo...")

    try:
        for item in catalogo:
            iptv_id = str(item.get("iptv_id", ""))
            if not iptv_id:
                continue

            # Define qual tabela usar baseado no tipo
            tipo = item.get("stream_type", "movie")
            if tipo == "movie":
                Tabela = FilmeDB
            elif tipo == "tv" or tipo == "series":
                Tabela = SerieDB
            else:
                Tabela = CanalDB

            # Procura no banco
            obj = db.query(Tabela).filter(Tabela.iptv_id == iptv_id).first()
            is_novo = False

            if not obj:
                obj = Tabela(iptv_id=iptv_id)
                db.add(obj)
                is_novo = True

            # Se o item foi editado manualmente no painel, não sobrescreve
            if not is_novo and getattr(obj, "editado", False):
                continue

            # Atualiza os campos ricos
            obj.stream_type = tipo
            obj.coletany = item.get("coletany", "Desconhecida")
            obj.tmdb_id = item.get("tmdb_id")
            obj.imdb_id = item.get("imdb_id")
            obj.title = item.get("title", "Sem Título")
            obj.original_title = item.get("original_title")
            obj.slug = item.get("slug")
            obj.type = item.get("type", "movie")
            obj.year = item.get("year")
            obj.duration = item.get("duration")
            obj.description = item.get("description")
            obj.poster = item.get("poster")
            obj.logo = item.get("logo")
            obj.backdrop = item.get("backdrop")
            obj.rating_avg = item.get("rating_avg", 0.0)
            obj.rating_count = item.get("rating_count", 0)
            obj.views_count = item.get("views_count", 0)
            obj.premium = item.get("premium", True)
            obj.audio_type = item.get("audio_type", "dubbed")
            obj.age_rating = item.get("age_rating")
            obj.url = item.get("url", "") # URL agora vem do catalogo.json

            # Serializa a lista de gêneros para JSON (pois no SQLite é Text)
            generos = item.get("genres", [])
            obj.genres = json.dumps(generos, ensure_ascii=False) if isinstance(generos, list) else "[]"

            if is_novo:
                adicionados += 1
            else:
                atualizados += 1

            # Commit a cada 100 itens para não travar a memória
            if (adicionados + atualizados) % 100 == 0:
                db.commit()

        db.commit()
        print(f"[Importer] ✅ Catálogo importado: {adicionados} novos, {atualizados} atualizados.")

    except Exception as e:
        db.rollback()
        print(f"[Importer] ❌ Erro durante a importação: {e}")
        return False
    finally:
        db.close()

    # ====== PARTE DE FALHAS ======
    if FALHAS_FILE.exists():
        try:
            falhas = json.loads(FALHAS_FILE.read_text(encoding="utf-8"))
            db = SessionLocal()
            print(f"[Importer] Registrando {len(falhas)} falhas...")
            
            # Limpa falhas antigas para não acumular lixo
            db.query(FalhaDB).delete()
            
            for f in falhas:
                iptv_id = str(f.get("iptv_id", ""))
                if not iptv_id: continue
                
                nova_falha = FalhaDB(
                    iptv_id=iptv_id,
                    nome_original=f.get("nome_original", "Desconhecido"),
                    tipo=f.get("tipo", "movie"),
                    motivo="Não encontrado no TMDB"
                )
                db.add(nova_falha)
            db.commit()
            print(f"[Importer] ✅ Falhas registradas no banco.")
        except Exception as e:
            print(f"[Importer] ❌ Erro ao processar falhas: {e}")
        finally:
            db.close()

    return True