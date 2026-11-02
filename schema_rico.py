from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from datetime import datetime

class CatalogoRicoMixin:
    """Mixin com as colunas do novo formato rico."""
    iptv_id = Column(String, unique=True, index=True)
    stream_type = Column(String, default="movie")
    coletany = Column(String, index=True)
    tmdb_id = Column(Integer, index=True)
    imdb_id = Column(String)
    title = Column(String, nullable=False, index=True)
    original_title = Column(String)
    slug = Column(String, index=True)
    type = Column(String, default="movie")
    year = Column(Integer, index=True)
    duration = Column(Integer)
    description = Column(Text)
    poster = Column(String)
    logo = Column(String)
    backdrop = Column(String)
    rating_avg = Column(Float, default=0.0)
    rating_count = Column(Integer, default=0)
    views_count = Column(Integer, default=0)
    premium = Column(Boolean, default=True)
    audio_type = Column(String, default="dubbed")
    genres = Column(Text)  # Salvaremos a lista como JSON string
    age_rating = Column(String)
    url = Column(String)   # URL do stream de vídeo
    editado = Column(Boolean, default=False)
    added_by_name = Column(String)
    added_by_uid = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)