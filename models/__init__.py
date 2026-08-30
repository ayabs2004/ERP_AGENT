"""
models/ — Modèles SQLAlchemy pour PostgreSQL.
Contient :
  - Base : base déclarative SQLAlchemy
  - Modèles métier : Client, Article, Document, LigneDocument, etc.
  - Session : configuration SQLAlchemy
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────
DATABASE_URL = "postgresql://user:password@localhost:5432/erp_sage"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ─────────────────────────────────────────────────────────────────────
# MODÈLES
# ─────────────────────────────────────────────────────────────────────
class Client(Base):
    """Modèle Client (F_COMPTET)."""
    __tablename__ = "clients"
    
    id = Column(Integer, primary_key=True, index=True)
    ct_num = Column(String(20), unique=True, index=True, nullable=False)
    ct_intitule = Column(String(100), nullable=False)
    ct_type = Column(Integer, nullable=False)  # 0=client, 1=fournisseur, 2=tiers interne
    ct_validite = Column(String(20), nullable=False, default="VALIDE")  # VALIDE, SUSPECT, BLOQUE
    ct_encours = Column(Float, nullable=False, default=0.0)
    
    # Relations
    documents = relationship("Document", back_populates="client")
    
    def __repr__(self):
        return f"<Client {self.ct_num} - {self.ct_intitule}>"


class Article(Base):
    """Modèle Article (F_ARTICLE)."""
    __tablename__ = "articles"
    
    id = Column(Integer, primary_key=True, index=True)
    ar_ref = Column(String(50), unique=True, index=True, nullable=False)
    ar_design = Column(String(200), nullable=False)
    ar_prix_ach = Column(Float, nullable=False, default=0.0)
    ar_prix_ven = Column(Float, nullable=False, default=0.0)
    ar_type = Column(Integer, nullable=False, default=0)
    
    # Relations
    stocks = relationship("StockArticle", back_populates="article", uselist=False)
    lignes_document = relationship("LigneDocument", back_populates="article")
    
    def __repr__(self):
        return f"<Article {self.ar_ref} - {self.ar_design}>"


class StockArticle(Base):
    """Modèle Stock Article (F_ARTSTOCK)."""
    __tablename__ = "stocks_articles"
    
    id = Column(Integer, primary_key=True, index=True)
    ar_ref = Column(String(50), ForeignKey("articles.ar_ref"), unique=True, nullable=False)
    as_qte_sto = Column(Float, nullable=False, default=0.0)
    as_qte_com = Column(Float, nullable=False, default=0.0)
    as_qte_acha_com = Column(Float, nullable=False, default=0.0)
    
    # Relations
    article = relationship("Article", back_populates="stocks")
    
    @property
    def stock_net(self) -> float:
        """Stock disponible (stock physique - commandes en cours)."""
        return self.as_qte_sto - self.as_qte_com
    
    def __repr__(self):
        return f"<Stock {self.ar_ref} - {self.stock_net:.0f} dispo>"


class Document(Base):
    """Modèle Document (F_DOCENTETE)."""
    __tablename__ = "documents"
    
    id = Column(Integer, primary_key=True, index=True)
    do_piece = Column(String(50), unique=True, index=True, nullable=False)
    do_domaine = Column(Integer, nullable=False)  # 0=vente, 1=achat, 2=fabrication
    do_type = Column(Integer, nullable=False)  # 1=OF, 2=BL, 3=Facture, 4=BF, 6=BC
    do_date = Column(String(20), nullable=False)  # Format YYYY-MM-DD
    do_ref = Column(String(50), nullable=True)  # Référence document source (pour transformations)
    ct_num = Column(String(20), ForeignKey("clients.ct_num"), nullable=False)
    
    # Relations
    client = relationship("Client", back_populates="documents")
    lignes = relationship("LigneDocument", back_populates="document", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Document {self.do_piece} - Type:{self.do_type} Domaine:{self.do_domaine}>"


class LigneDocument(Base):
    """Modèle Ligne Document (F_DOCLIGNE)."""
    __tablename__ = "lignes_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    do_piece = Column(String(50), ForeignKey("documents.do_piece"), nullable=False)
    ar_ref = Column(String(50), ForeignKey("articles.ar_ref"), nullable=False)
    dl_qte = Column(Float, nullable=False, default=0.0)
    dl_prix_unitaire = Column(Float, nullable=False, default=0.0)
    
    # Relations
    document = relationship("Document", back_populates="lignes")
    article = relationship("Article", back_populates="lignes_document")
    
    @property
    def montant_ht(self) -> float:
        """Montant HT de la ligne."""
        return self.dl_qte * self.dl_prix_unitaire
    
    def __repr__(self):
        return f"<Ligne {self.do_piece} - {self.ar_ref} x{self.dl_qte:.0f} @ {self.dl_prix_unitaire:.2f}€>"


class Reglement(Base):
    """Modèle Règlement."""
    __tablename__ = "reglements"
    
    id = Column(Integer, primary_key=True, index=True)
    do_piece = Column(String(50), ForeignKey("documents.do_piece"), nullable=False)
    mode_paiement = Column(String(50), nullable=False)  # Virement, Cheque, Traite, Especes, CB
    montant = Column(Float, nullable=False, default=0.0)
    date_reglement = Column(String(20), nullable=False)  # Format YYYY-MM-DD
    numero_piece_paiement = Column(String(100), nullable=True)  # N° chèque/traite
    
    def __repr__(self):
        return f"<Reglement {self.do_piece} - {self.montant:.2f}€ ({self.mode_paiement})>"


class MouvementStock(Base):
    """Modèle Mouvement Stock."""
    __tablename__ = "mouvements_stock"
    
    id = Column(Integer, primary_key=True, index=True)
    ar_ref = Column(String(50), ForeignKey("articles.ar_ref"), nullable=False)
    type_mouvement = Column(String(20), nullable=False)  # ENTREE, SORTIE, AJUSTEMENT
    qte = Column(Float, nullable=False)
    motif = Column(Text, nullable=True)
    date_mouvement = Column(String(20), nullable=False)  # Format YYYY-MM-DD
    
    def __repr__(self):
        return f"<MouvementStock {self.ar_ref} - {self.type_mouvement} {self.qte:+.0f}>"


class Nomenclature(Base):
    """Modèle Nomenclature (F_NOMENCLAT)."""
    __tablename__ = "nomenclatures"
    
    id = Column(Integer, primary_key=True, index=True)
    no_ref_pf = Column(String(50), ForeignKey("articles.ar_ref"), nullable=False)  # Produit fini
    no_ref_mp = Column(String(50), ForeignKey("articles.ar_ref"), nullable=False)  # Matière première
    no_qte = Column(Float, nullable=False)  # Quantité de MP nécessaire pour 1 PF
    
    def __repr__(self):
        return f"<Nomenclature {self.no_ref_pf} = {self.no_qte:.2f} x {self.no_ref_mp}>"


# ─────────────────────────────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────────────────────────────
def get_db():
    """Générateur de session DB pour FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crée toutes les tables."""
    Base.metadata.create_all(bind=engine)


def drop_all_tables():
    """Supprime toutes les tables (ATTENTION: destructif)."""
    Base.metadata.drop_all(bind=engine)