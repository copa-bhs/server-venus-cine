from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from schema_rico import CatalogoRicoMixin

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cache" / "venus.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Filme(Base, CatalogoRicoMixin):
    __tablename__ = "filmes"
    id = Column(Integer, primary_key=True)

class Serie(Base, CatalogoRicoMixin):
    __tablename__ = "series"
    id = Column(Integer, primary_key=True)

class Canal(Base, CatalogoRicoMixin):
    __tablename__ = "canais"
    id = Column(Integer, primary_key=True)

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

class Falha(Base):
    __tablename__ = "falhas"
    id         = Column(Integer, primary_key=True)
    iptv_id    = Column(String, unique=True, index=True)
    nome_original = Column(String, nullable=False)
    tipo       = Column(String, nullable=False)
    motivo     = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

def criar_tabelas():
    Base.metadata.create_all(bind=engine)
    print(f"[DB] Tabelas do novo modelo criadas/verificadas → {DB_PATH}")

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()