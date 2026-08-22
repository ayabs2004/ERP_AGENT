# 🤖 Copilot ERP Sage 100 v9.4

Assistant conversationnel intelligent pour l'ERP Sage 100, basé sur une architecture multi-agents avec LangGraph.

## 📊 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        UTILISATEUR                               │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API FastAPI (api.py)                         │
│  • Authentification JWT                                        │
│  • Rate limiting                                               │
│  • CORS configuré                                              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              ORCHESTRATEUR GÉNÉRAL (orchestrateur_general.py)   │
│                                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Classifier  │→ │   Planner    │→ │   Router (LangGraph)  │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│                            │                                    │
│     ┌──────────────────────┼──────────────────────┐            │
│     │                      │                      │            │
│     ▼                      ▼                      ▼            │
│  ┌─────────┐  ┌──────────────┐  ┌──────────────────────┐      │
│  │  Hors   │  │    Aide      │  │   Clarification      │      │
│  │ Sujet   │  │              │  │   (ambiguïté)        │      │
│  └─────────┘  └──────────────┘  └──────────────────────┘      │
│     │              │                      │                    │
│     └──────────────┼──────────────────────┘                    │
│                    ▼                                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              NŒUD SYNTHÈSE (graph_nodes/)               │   │
│  │  • Formatage des réponses                               │   │
│  │  • Anti-hallucination                                   │   │
│  │  • Mémorisation Mem0                                    │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
│   AGENT      │  │   AGENT          │  │   AGENT      │
│   LECTURE    │  │   NL2SQL         │  │   KB         │
│              │  │                  │  │              │
│ • Clients    │  │ • Vanna AI       │  │ • Procédures │
│ • Articles   │  │ • Patterns SQL   │  │ • Fiches     │
│ • Factures   │  │ • Fallback LLM   │  │ • SAV        │
│ • Stock      │  │                  │  │              │
└──────┬───────┘  └────────┬─────────┘  └──────┬───────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ▼
              ┌────────────────────────┐
              │   MCP POOL (mcp_pool)  │
              │  • nl2sql              │
              │  • actions             │
              │  • kb                  │
              │  • hub                 │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   SAGE 100 (ERP)       │
              │  • F_COMPTET           │
              │  • F_ARTICLE           │
              │  • F_DOCENTETE         │
              │  • F_DOCLIGNE          │
              │  • F_ARTSTOCK          │
              └────────────────────────┘
```

## 📁 Structure du projet

```
agent_sage_v4/
├── orchestrateur_general.py  # Orchestrateur principal (v9.4)
├── api.py                     # API FastAPI avec auth + rate limiting
├── common.py                  # Constantes et fonctions partagées
│
├── classification/            # Module de classification
│   └── __init__.py           # Patterns regex + pré-classification
│
├── extraction/                # Module d'extraction d'entités
│   └── __init__.py           # NER, extraction client/article
│
├── session/                   # Gestion d'état et session
│   └── __init__.py           # CopilotState TypedDict
│
├── formatting/                # Formatage des réponses
│   └── __init__.py           # Formateurs par action
│
├── graph_nodes/               # Nœuds du graphe LangGraph
│   └── __init__.py           # noeud_classifier, noeud_lecture, etc.
│
├── mcp_*/                     # Serveurs MCP
│   ├── mcp_pool.py           # Pool de connexions MCP
│   ├── mcp_nl2sql.py         # MCP NL2SQL
│   ├── mcp_actions_sage.py   # MCP Actions
│   └── mcp_knowledge_base.py # MCP Base de connaissances
│
├── semantic_*/                # Classification sémantique
│   ├── semantic_classifier.py
│   ├── semantic_examples.yaml
│   └── classification_engine.py
│
├── draft_flow.py              # Gestion des brouillons
├── llm_anonymizer.py          # Anonymisation LLM
├── formatters.py              # Formateurs (legacy)
├── extraction.py              # Extraction (legacy)
│
├── tests/                     # Tests
│   ├── test_workflows.py
│   └── test_document_workflows.py
│
├── kb_docs/                   # Base de connaissances (Markdown)
├── kb_docs_pdf/              # Base de connaissances (PDF)
│
├── documents_generes/         # Documents PDF générés
├── declarations_generes/      # Déclarations Excel
│
├── entreprise_mock.db         # Base de données SQLite
├── .env                       # Variables d'environnement
└── requirements.txt           # Dépendances Python
```

## 🚀 Installation

### 1. Prérequis

```bash
# Python 3.10+
python --version

# Ollama (pour les modèles LLM)
# Windows : https://ollama.com/download/windows
# Linux/Mac : curl -fsSL https://ollama.ai/install.sh | sh

# Modèles Ollama
ollama pull llama-3.1-8b-instant
ollama pull openai/gpt-oss-120b
```

### 2. Installation des dépendances

```bash
pip install -r requirements.txt
```

### 3. Configuration

Copier `.env.example` vers `.env` et configurer :

```env
# LLM (Ollama local)
OLLAMA_BASE_URL=http://localhost:11434
GROQ_FAST=llama-3.1-8b-instant
GROQ_SMART=openai/gpt-oss-120b

# Optionnel : Fallback Groq Cloud
LLM_FALLBACK_KEY=gsk_...
LLM_FALLBACK_URL=https://api.groq.com/openai/v1
LLM_FALLBACK_MODEL=openai/gpt-oss-120b

# Optionnel : Vanna AI (NL2SQL avancé)
ENABLE_VANNA=true

# Optionnel : GLiNER (extraction d'entités)
ENABLE_GLINER=true

# Optionnel : Mem0 (mémoire conversationnelle)
ENABLE_MEM0=true

# Base de données
DB_PATH=./entreprise_mock.db

# API
API_KEY=your-secret-api-key
RATE_LIMIT=100
```

### 4. Initialisation de la base de données

```bash
python init_db_complet.py
```

### 5. Lancement

```bash
# Mode interactif (CLI)
python orchestrateur_general.py

# Mode API
uvicorn api:app --host 0.0.0.0 --port 8000
```

## 🎯 Fonctionnalités

### 📊 Lecture & Analyse
- ✅ Liste clients/articles/fournisseurs
- ✅ Top clients par CA
- ✅ Palmarès des articles les plus vendus
- ✅ CA global et saisonnalité
- ✅ Rentabilité par article
- ✅ DSO (délai de paiement)
- ✅ RFM (segmentation clients)
- ✅ Clients en baisse de CA
- ✅ Factures impayées (client/fournisseur)
- ✅ Documents par période
- ✅ Stock articles

### ✍️ Écriture
- ✅ Création client/fournisseur
- ✅ Modification statut client
- ✅ Génération BL/Facture/BC/OF/BF
- ✅ Transformation document (OF→BF, BL→Facture)
- ✅ Création avoir
- ✅ Règlement facture
- ✅ Mouvement stock

### 🔄 Workflow
- ✅ Flux commande complet (vérification → production → livraison → facturation)

### 📚 Base de connaissances
- ✅ Procédures internes
- ✅ Fiches techniques articles
- ✅ Réclamations SAV
- ✅ Recommandations

### 📁 Export Excel
- ✅ Offre de prix
- ✅ Déclaration fiscale mensuelle
- ✅ Balance âgée
- ✅ Dashboard KPI

## 🔒 Sécurité

- **Authentification JWT** : Toutes les requêtes API nécessitent un token
- **Rate limiting** : 100 requêtes/minute par défaut
- **Anonymisation LLM** : Les données sensibles sont anonymisées avant envoi aux LLM
- **Validation SQL** : Toutes les requêtes SQL sont validées par sqlglot
- **CORS configuré** : Accès restreint aux origines autorisées

## 🧠 Intelligence Artificielle

### Classification
- **Regex** (0ms) : Patterns métier pré-définis
- **Sémantique** : Classification hiérarchique avec seuils adaptatifs
- **LLM** : Fallback pour cas complexes

### NL2SQL
- **Vanna AI** : Génération SQL par similarité sémantique
- **Patterns SQL** : Requêtes pré-définies pour cas courants
- **Fallback LLM** : Génération SQL via LLM si Vanna échoue

### Extraction d'entités
- **GLiNER** : NER multi-langues
- **Regex** : Patterns codes clients/articles
- **LLM** : Extraction structurée en dernier recours

### Mémoire
- **Mem0** : Mémoire conversationnelle vectorielle
- **Session** : Contexte persistant entre tours

## 📈 Performance

- **Cache disque** : TTL 10min pour réponses LLM
- **Cache mémoire** : Références articles, noms clients
- **Warmup Ollama** : Préchargement modèles au démarrage
- **Semaphore LLM** : Limite de concurrence (2 appels simultanés)
- **Timeout adaptatifs** : 120s (fast) / 300s (smart)

## 🐛 Debug

```bash
# Logs détaillés
LOG_LEVEL=DEBUG python orchestrateur_general.py

# Session info (dans le CLI)
session

# Cache stats
cache

# Warmup manuel
warmup

# Reset session
reset

# Vanna retrain
vanna_retrain
```

## 📝 License

Propriétaire - Tous droits réservés

## 👥 Contributeurs

- Équipe ERP Sage 100

---

**Version** : 9.4 (patchée)  
**Dernière mise à jour** : 2026-07-08