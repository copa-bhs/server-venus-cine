import time
import json
from sqlalchemy.orm import Session
from db import Filme as FilmeDB, Serie as SerieDB, Slide as SlideDB
from config import TMDB_ENABLED, PUBLIC_BASE_URL
from proxy import slugify

async def build_home_async(db: Session, force_hero: bool = False) -> dict:
    # Monta o Hero (Slider) vindo dos slides manuais
    slides = db.query(SlideDB).filter(SlideDB.ativo == True).order_by(SlideDB.ordem).all()
    hero = []
    for s in slides:
        ref = db.query(FilmeDB).get(s.ref_id) if s.ref_tipo == "filme" else db.query(SerieDB).get(s.ref_id)
        if not ref: continue
        
        slug = ref.slug or slugify(ref.title or "", ref.year)
        base = (PUBLIC_BASE_URL or "").rstrip("/")
        
        hero.append({
            "id": str(ref.iptv_id),
            "tipo": "filme" if s.ref_tipo == "filme" else "serie",
            "titulo": s.titulo or ref.title,
            "banner": s.banner or ref.backdrop,
            "capa": ref.poster,
            "logo": s.logo or ref.logo,
            "sinopse": s.sinopse or ref.description,
            "score": ref.rating_avg,
            "classificacao": ref.age_rating,
            "ano": ref.year,
            "generos": json.loads(ref.genres) if ref.genres else [],
            "url_stream": f"{base}/stream/{slug}.mp4" if base else f"/stream/{slug}.mp4",
        })

    return {
        "gerado_em": time.time(),
        "tmdb_ativo": TMDB_ENABLED,
        "hero": hero,
        "stats": {"total_filmes": db.query(FilmeDB).count(), "total_series": db.query(SerieDB).count()}
    }