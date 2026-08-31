🤖 Agent Sage — Copilot ERP Sage 100
Assistant conversationnel intelligent pour l'ERP Sage 100, piloté par une architecture multi-agents avec LangGraph, des LLM locaux via Ollama, et des serveurs MCP dédiés.

✨ Présentation
Agent Sage est un copilote IA conçu pour interagir nativement avec la base de données Sage 100 en langage naturel. Il permet à un utilisateur (commercial, gestionnaire, comptable) de :

Interroger les données ERP sans connaître SQL
Créer et transformer des documents commerciaux (BL, Factures, Avoirs…)
Analyser les performances (CA, stock, DSO, RFM…)
Exporter des rapports Excel et des PDF générés automatiquement
Apprendre continuellement grâce à une boucle de classification semi-supervisée
Mermaid diagram
🧠 Intelligence artificielle
Classificateur hybride
Le cœur du système utilise une classification à trois couches :

Couche	Méthode	Latence
🔴 Règles	Patterns regex métier	~0 ms
🟡 Sémantique	Similarité cosinus sur embeddings (centroïdes)	~10 ms
🟢 LLM	Fallback Ollama / Groq si ambiguïté	~500 ms
La boucle d'apprentissage (apprentissage/) collecte les interactions, propose des exemples candidats, et attend une validation humaine avant toute mise à jour du dataset.

NL2SQL
Vanna AI : génération SQL par similarité sémantique sur ChromaDB
Patterns pré-définis : requêtes optimisées pour les cas courants Sage 100
Fallback LLM : génération SQL guidée si Vanna échoue
Extraction d'entités
GLiNER (optionnel) : NER multi-langues
Regex : codes clients / articles
LLM : extraction structurée en dernier recours
🎯 Fonctionnalités
📊 Lecture & Analyse
✅ Liste clients / articles / fournisseurs
✅ Top clients par CA — Saisonnalité — RFM
✅ Palmarès articles les plus vendus
✅ Factures impayées (client / fournisseur)
✅ DSO (délai moyen de paiement)
✅ Stocks disponibles par article
✅ Clients en baisse de CA
✍️ Écriture & Workflow
✅ Création client / fournisseur avec contrôle d'encours
✅ Génération BL / Facture / BC / OF / BF
✅ Transformation de document (OF→BF, BL→Facture)
✅ Création avoir (toutes lignes, montants négatifs)
✅ Règlement facture (contrôle anti-doublon)
✅ Mouvement de stock atomique (refus si stock < 0)
✅ Numéros de pièce uniques (horodatage + UUID)
📁 Export
✅ PDF signés (reportlab + PyMuPDF)
✅ Offre de prix Excel
✅ Déclaration fiscale mensuelle
✅ Balance âgée
✅ Dashboard KPI
📚 Base de connaissances
✅ Procédures internes (Markdown / PDF)
✅ Fiches techniques articles
✅ Réclamations SAV
✅ Recherche hybride BM25 + vectorielle (Qdrant)
📁 Structure du projet

agent_sage/
├── api/                          # Backend FastAPI
│   ├── __init__.py               # App FastAPI, routes, sessions
│   ├── auth.py                   # Authentification JWT
│   ├── orchestrateur_general.py  # Orchestrateur LangGraph (cœur)
│   ├── mcp_pool.py               # Pool de connexions MCP
│   ├── mcp_actions_sage.py       # Serveur MCP — actions ERP
│   ├── mcp_nl2sql.py             # Serveur MCP — NL2SQL
│   ├── mcp_knowledge_base.py     # Serveur MCP — base de connaissances
│   ├── mcp_server_sage.py        # Entrée MCP serveur
│   ├── llm_anonymizer.py         # Anonymisation données sensibles
│   ├── vanna_training_neutral.py # Entraînement Vanna AI
│   └── graph_nodes/              # Nœuds LangGraph
│
├── classification/               # Classificateur intelligent
│   ├── semantic_classifier.py    # Embeddings + centroïdes
│   ├── semantic_examples.yaml    # Dataset de classification (1500+ exemples)
│   ├── classification_engine.py  # Moteur de règles
│   ├── calibrate_thresholds.py   # Calibration des seuils
│   └── valider_classification.py # Validation des performances
│
├── apprentissage/                # Boucle d'apprentissage semi-supervisé
│
├── database/                     # Base de données
│   ├── schema_sage.py            # Source de vérité : codes/préfixes Sage
│   └── init_db_complet.py        # Initialisation MSSQL
│
├── extraction/                   # Extraction d'entités (NER)
├── formatting/                   # Formatage des réponses
├── kb/                           # Base de connaissances (Markdown / PDF)
├── models/                       # Modèles Pydantic partagés
├── session/                      # Gestion d'état conversationnel
│
├── frontend/                     # Interface React
│   ├── src/
│   │   ├── App.tsx               # Application principale (chat)
│   │   └── Login.tsx             # Page de connexion
│   ├── vite.config.ts
│   └── package.json
│
├── tests/                        # Tests de non-régression
│   └── test_document_workflows.py
│
├── config/
│   └── requirements.txt          # Dépendances Python
├── documents_generes/            # PDF générés (non versionnés)
├── declarations_generes/         # Excel générés (non versionnés)
└── .env                          # Variables d'environnement (non versionné)
🚀 Installation
Prérequis
Python 3.10+
Node.js 18+ (pour le frontend)
Ollama installé et en cours d'exécution
bash

# Vérifier Python
python --version
# Installer les modèles Ollama
ollama pull llama3.1:8b
ollama pull qwen2.5:14b
1. Cloner le dépôt
bash

git clone https://github.com/<votre-organisation>/agent_sage.git
cd agent_sage
2. Backend Python
bash

# Créer un environnement virtuel
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS
# Installer les dépendances
pip install -r config/requirements.txt
3. Frontend React
bash

cd frontend
npm install
4. Configuration
Copier .env.example vers .env et adapter :

env

# ── LLM ───────────────────────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
GROQ_FAST=llama3.1:8b
GROQ_SMART=qwen2.5:14b
# ── Fallback cloud (optionnel) ─────────────────────────
LLM_FALLBACK_KEY=gsk_...
LLM_FALLBACK_URL=https://api.groq.com/openai/v1
LLM_FALLBACK_MODEL=llama-3.1-70b-versatile
# ── Base de données ────────────────────────────────────
DB_PATH=./entreprise_mock.db
# ── Modules optionnels ─────────────────────────────────
ENABLE_VANNA=true
ENABLE_GLINER=false
ENABLE_MEM0=false
ENABLE_SEMANTIC_CLASSIFIER=true
# ── API ───────────────────────────────────────────────
SECRET_KEY=your-very-secret-jwt-key
RATE_LIMIT=100
5. Initialiser la base de données
bash

python database/init_db_complet.py
6. Lancer l'application
bash

# Terminal 1 — Backend
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
# Terminal 2 — Frontend
cd frontend
npm run dev
L'interface est accessible sur http://localhost:5173.

🔒 Sécurité
Mesure	Description
JWT	Toutes les routes API requièrent un token valide
Rate limiting	100 req/min par défaut (configurable via RATE_LIMIT)
Anonymisation LLM	Données sensibles masquées avant envoi aux LLM cloud
Validation SQL	Toutes les requêtes validées via sqlglot avant exécution
PDF protégés	Téléchargement PDF via endpoint authentifié uniquement
Sessions bornées	TTL + plafond max de sessions simultanées
Anti-doublon	Vérification idempotente sur transformations, avoirs, règlements
📈 Performance
Cache disque : TTL 10 min pour les réponses LLM coûteuses
Cache mémoire : articles, noms clients (invalidation automatique)
Warmup Ollama : préchargement des modèles au démarrage de l'API
Semaphore LLM : max 2 appels LLM simultanés
Timeouts adaptatifs : 120 s (fast) / 300 s (smart)
Embeddings pré-calculés : centroïdes chargés en mémoire au démarrage
🧪 Tests
bash

# Lancer tous les tests
pytest tests/ -v
# Test spécifique
pytest tests/test_document_workflows.py -v
Les tests couvrent :

Insertion de documents multi-lignes
Génération de numéros de pièce uniques
Contrôle de stock avant création de facture
Protection contre les écritures dupliquées
🐛 Commandes de debug (mode CLI)
bash

# Démarrer en mode interactif
python -m api.orchestrateur_general
# Commandes disponibles dans le chat :
session        # Afficher l'état de la session
cache          # Statistiques du cache
warmup         # Forcer le préchargement des modèles
reset          # Réinitialiser la session
vanna_retrain  # Réentraîner Vanna AI
🔄 Boucle d'apprentissage
Le système s'améliore en continu sans intervention automatique sur le modèle :


Interactions utilisateur
        │
        ▼
interaction_logger.py  ──▶  logs_classification.jsonl
        │
        ▼
extraire_cas_logs.py   ──▶  jeux de données candidats
        │
        ▼
valider_classification.py  ──▶  métriques de performance
        │
        ▼
apprentissage_semi_auto.py  ──▶  exemples proposés (validation humaine requise)
        │
        ▼
run_learning_cycle.py  ──▶  enrichissement du dataset validé
Aucune modification du classifieur n'est effectuée sans validation humaine.

🛠️ Stack technique
Couche	Technologies
Backend	Python 3.10+, FastAPI, LangGraph, Pydantic
LLM	Ollama (local), Groq (cloud fallback), langchain-ollama
NL2SQL	Vanna AI, ChromaDB, sqlglot
Recherche vectorielle	Qdrant, sentence-transformers
Mémoire	Mem0 (optionnel), sessions FastAPI
NER	GLiNER (optionnel), regex
Génération de docs	reportlab, PyMuPDF, openpyxl
Frontend	React 18, TypeScript, Vite, TailwindCSS 4
Auth	PyJWT, hashlib
Base de données	Microsoft SQL Server (MSSQL)
