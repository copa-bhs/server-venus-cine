from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Float,
    Boolean, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cache" / "venus.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Filme(Base):
    __tablename__ = "filmes"
    id         = Column(Integer, primary_key=True)
    iptv_id    = Column(String, unique=True, index=True) # Para manuais, geraremos um ID "m_XXXX"
    titulo     = Column(String, nullable=False, index=True)
    capa       = Column(String)
    banner     = Column(String)
    logo       = Column(String)
    ano        = Column(Integer, index=True)
    categoria  = Column(String, index=True)
    sinopse    = Column(String)
    url        = Column(String, nullable=False) # URL do .mp4
    editado    = Column(Boolean, default=False)
    added_by_name = Column(String)
    added_by_uid  = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Serie(Base):
    __tablename__ = "series"
    id         = Column(Integer, primary_key=True)
    iptv_id    = Column(String, unique=True, index=True)
    titulo     = Column(String, nullable=False, index=True)
    capa       = Column(String)
    banner     = Column(String)
    logo       = Column(String)
    ano        = Column(Integer, index=True)
    categoria  = Column(String, index=True)
    sinopse    = Column(String)
    editado    = Column(Boolean, default=False)
    added_by_name = Column(String)
    added_by_uid  = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    episodios = relationship("Episodio", back_populates="serie", cascade="all, delete-orphan")

class Episodio(Base):
    __tablename__ = "episodios"
    id         = Column(Integer, primary_key=True)
    serie_id   = Column(Integer, ForeignKey("series.id"), nullable=False)
    temporada  = Column(Integer, index=True)
    episodio   = Column(Integer, index=True)
    titulo     = Column(String)
    url        = Column(String, nullable=False) # URL do .mp4 do episódio
    capa       = Column(String)
    sinopse    = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    serie = relationship("Serie", back_populates="episodios")

class Canal(Base):
    __tablename__ = "canais"
    id         = Column(Integer, primary_key=True)
    iptv_id    = Column(String, unique=True, index=True)
    titulo     = Column(String, nullable=False, index=True)
    capa       = Column(String)
    categoria  = Column(String, index=True)
    url        = Column(String, nullable=False)
    editado    = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Slide(Base):
    __tablename__ = "slides"
    id         = Column(Integer, primary_key=True)
    ordem      = Column(Integer, default=0, index=True)
    ref_tipo   = Column(String, nullable=False)
    ref_id     = Column(Integer, nullable=False)
    titulo     = Column(String)
    banner     = Column(String)
    logo       = Column(String)
    sinopse    = Column(String)
    ativo      = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

def criar_tabelas():
    Base.metadata.create_all(bind=engine)
    print(f"[DB] Tabelas OK → {DB_PATH}")

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def sincronizar(filmes: list, series: list, canais: list):
    # Mantém a sincronização original (não apaga os manuais se não estiverem na lista do IPTV)
    db = SessionLocal()
    try:
        existentes = {f.iptv_id: f for f in db.query(Filme).all()}
        for f in filmes:
            iptv_id = str(f.get("id") or "")
            if not iptv_id: continue
            if iptv_id in existentes:
                ex = existentes[iptv_id]
                if not ex.editado:
                    ex.titulo = f.get("titulo") or ex.titulo
                    ex.capa = f.get("capa") or ex.capa
                    ex.ano = f.get("ano") or ex.ano
                    ex.categoria = f.get("categoria") or ex.categoria
                    ex.url = f.get("url") or ex.url
            else:
                db.add(Filme(iptv_id=iptv_id, titulo=f.get("titulo") or "", capa=f.get("capa"), ano=f.get("ano"), categoria=f.get("categoria"), url=f.get("url") or ""))
        db.commit()
    finally: db.close()