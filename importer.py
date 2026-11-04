import json
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from db import SessionLocal, Filme as FilmeDB, Serie as SerieDB, Canal as CanalDB, Falha as FalhaDB
from config import CACHE_DIR
import logs

CATALOGO_FILE = CACHE_DIR / "catalogo.json"
FALHAS_FILE = CACHE_DIR / "falhas.json"

def importar_catalogo():
    if not CATALOGO_FILE.exists():
        logs.adicionar_log("❌ Arquivo catalogo.json não encontrado.")
        return False

    try:
        catalogo = json.loads(CATALOGO_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logs.adicionar_log(f"❌ Erro ao ler catalogo.json: {e}")
        return False

    db = SessionLocal()
    adicionados, atualizados = 0, 0
    logs.adicionar_log(f"Processando {len(catalogo)} itens do catálogo no banco de dados...")

    try:
        for item in catalogo:
            iptv_id = str(item.get("iptv_id", ""))
            if not iptv_id: continue

            tipo = item.get("stream_type", "movie")
            Tabela = FilmeDB if tipo == "movie" else (SerieDB if tipo in ("tv", "series") else CanalDB)

            obj = db.query(Tabela).filter(Tabela.iptv_id == iptv_id).first()
            is_novo = False

            if not obj:
                obj = Tabela(iptv_id=iptv_id)
                db.add(obj); is_novo = True

            if not is_novo and getattr(obj, "editado", False): continue

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
            obj.url = item.get("url", "")

            # ⚡ SALVANDO A DATA REAL DE ADIÇÃO
            created_at_str = item.get("created_at")
            if created_at_str:
                try:
                    # Formato esperado: 2026-10-26T16:09:55.000000Z
                    dt = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                    obj.created_at = dt
                except:
                    pass # Se falhar, mantém a data padrão do banco

            generos = item.get("genres", [])
            obj.genres = json.dumps(generos, ensure_ascii=False) if isinstance(generos, list) else "[]"

            if is_novo: adicionados += 1
            else: atualizados += 1

            if (adicionados + atualizados) % 1000 == 0:
                db.commit()

        db.commit()
        logs.adicionar_log(f"✅ Catálogo importado no banco: {adicionados} novos, {atualizados} atualizados.")
    except Exception as e:
        db.rollback(); logs.adicionar_log(f"❌ Erro durante a importação: {e}"); return False
    finally: db.close()

    if FALHAS_FILE.exists():
        try:
            falhas = json.loads(FALHAS_FILE.read_text(encoding="utf-8"))
            db = SessionLocal()
            logs.adicionar_log(f"Registrando {len(falhas)} falhas de TMDB...")
            db.query(FalhaDB).delete()
            for f in falhas:
                iptv_id = str(f.get("iptv_id", ""))
                if not iptv_id: continue
                db.add(FalhaDB(iptv_id=iptv_id, nome_original=f.get("nome_original", "Desconhecido"), tipo=f.get("tipo", "movie"), motivo="Não encontrado no TMDB"))
            db.commit()
            logs.adicionar_log("✅ Falhas registradas.")
        except Exception as e: logs.adicionar_log(f"❌ Erro ao processar falhas: {e}")
        finally: db.close()

    return True