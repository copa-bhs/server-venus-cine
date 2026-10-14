"""
Banco de dados SQLite do sistema.
- Tabelas espelho dos JSONs (filmes, series, canais)
- Coluna 'editado' marca itens modificados pelo painel
- Sincronização inteligente: não sobrescreve o que foi editado
- Tabela 'slides' para o hero manual do painel
"""
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cache" / "venus.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Filme(Base):
    __tablename__ = "filmes"
    id         = Column(Integer, primary_key=True)
    iptv_id    = Column(String, unique=True, index=True)
    titulo     = Column(String, nullable=False, index=True)
    capa       = Column(String)
    banner     = Column(String)
    logo       = Column(String)
    ano        = Column(Integer, index=True)
    categoria  = Column(String, index=True)
    sinopse    = Column(String)
    url        = Column(String, nullable=False)
    editado    = Column(Boolean, default=False)
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    ref_tipo   = Column(String, nullable=False)   # "filme" | "serie"
    ref_id     = Column(Integer, nullable=False)  # id na tabela filmes/series
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
    try:
        yield db
    finally:
        db.close()


def sincronizar(filmes: list, series: list, canais: list):
    db = SessionLocal()
    try:
        existentes = {f.iptv_id: f for f in db.query(Filme).all()}
        novos = atual = mantidos = 0
        for f in filmes:
            iptv_id = str(f.get("id") or "")
            if not iptv_id:
                continue
            if iptv_id in existentes:
                ex = existentes[iptv_id]
                if not ex.editado:
                    ex.titulo = f.get("titulo") or ex.titulo
                    ex.capa = f.get("capa") or ex.capa
                    ex.ano = f.get("ano") or ex.ano
                    ex.categoria = f.get("categoria") or ex.categoria
                    ex.url = f.get("url") or ex.url
                    atual += 1
                else:
                    mantidos += 1
            else:
                db.add(Filme(
                    iptv_id=iptv_id,
                    titulo=f.get("titulo") or "",
                    capa=f.get("capa"),
                    ano=f.get("ano"),
                    categoria=f.get("categoria"),
                    url=f.get("url") or "",
                ))
                novos += 1

        existentes_s = {s.iptv_id: s for s in db.query(Serie).all()}
        for s in series:
            iptv_id = str(s.get("id") or "")
            if not iptv_id:
                continue
            if iptv_id in existentes_s:
                ex = existentes_s[iptv_id]
                if not ex.editado:
                    ex.titulo = s.get("titulo") or ex.titulo
                    ex.capa = s.get("capa") or ex.capa
                    ex.ano = s.get("ano") or ex.ano
                    ex.categoria = s.get("categoria") or ex.categoria
            else:
                db.add(Serie(
                    iptv_id=iptv_id,
                    titulo=s.get("titulo") or "",
                    capa=s.get("capa"),
                    ano=s.get("ano"),
                    categoria=s.get("categoria"),
                ))

        existentes_c = {c.iptv_id: c for c in db.query(Canal).all()}
        for c in canais:
            iptv_id = str(c.get("id") or "")
            if not iptv_id:
                continue
            if iptv_id in existentes_c:
                ex = existentes_c[iptv_id]
                if not ex.editado:
                    ex.titulo = c.get("titulo") or ex.titulo
                    ex.capa = c.get("capa") or ex.capa
                    ex.categoria = c.get("categoria") or ex.categoria
                    ex.url = c.get("url") or ex.url
            else:
                db.add(Canal(
                    iptv_id=iptv_id,
                    titulo=c.get("titulo") or "",
                    capa=c.get("capa"),
                    categoria=c.get("categoria"),
                    url=c.get("url") or "",
                ))

        db.commit()
        print(f"[DB] Sincronizado: {novos} novos · {atual} atualizados · {mantidos} mantidos (editados)")
    except Exception as e:
        print(f"[DB] Erro ao sincronizar: {e}")
    finally:
        db.close()