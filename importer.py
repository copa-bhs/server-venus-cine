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
    adicionados, atualizados, ignorados_duplicados = 0, 0, 0
    logs.adicionar_log(f"Processando {len(catalogo)} itens no banco...")

    try:
        for item in catalogo:
            iptv_id = str(item.get("iptv_id", ""))
            if not iptv_id: continue

            tipo = item.get("stream_type", "movie")
            Tabela = FilmeDB if tipo == "movie" else (SerieDB if tipo in ("tv", "series") else CanalDB)

            # 1. Tenta achar pelo ID do IPTV
            obj = db.query(Tabela).filter(Tabela.iptv_id == iptv_id).first()
            
            # ⚡ TRAVA ANTI-DUPLICAÇÃO (Evita o problema das categorias Netflix / Doramas)
            if not obj:
                tmdb_id = item.get("tmdb_id")
                title = item.get("title")
                year = item.get("year")
                
                if tmdb_id:
                    obj_duplicado = db.query(Tabela).filter(Tabela.tmdb_id == tmdb_id).first()
                    if obj_duplicado:
                        ignorados_duplicados += 1
                        continue # Pula e não salva duplicado
                elif title:
                    obj_duplicado = db.query(Tabela).filter(Tabela.title == title, Tabela.year == year).first()
                    if obj_duplicado:
                        ignorados_duplicados += 1
                        continue

            is_novo = False
            if not obj:
                obj = Tabela(iptv_id=iptv_id)
                db.add(obj)
                is_novo = True

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

            created_at_str = item.get("created_at")
            if created_at_str:
                try: obj.created_at = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                except: pass 

            generos = item.get("genres", [])
            obj.genres = json.dumps(generos, ensure_ascii=False) if isinstance(generos, list) else "[]"

            if is_novo: adicionados += 1
            else: atualizados += 1

            if (adicionados + atualizados) % 1000 == 0: db.commit()

        db.commit()
        logs.adicionar_log(f"✅ Importação: {adicionados} novos, {atualizados} atualizados, {ignorados_duplicados} duplicados ignorados.")
    except Exception as e:
        db.rollback(); logs.adicionar_log(f"❌ Erro na importação: {e}"); return False
    finally: db.close()
    return True