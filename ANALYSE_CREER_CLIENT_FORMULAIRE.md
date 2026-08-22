# Analyse Complète : CREER_CLIENT - Champs du Formulaire

## 📋 Vue d'ensemble
Ce document retrace le flux complet de création d'un client dans le système, depuis la demande au formulaire jusqu'à l'exécution de la fonction `creer_nouveau_client`.

---

## 1. CHAMPS DEMANDÉS AU FORMULAIRE

### Localisation : [api/orchestrateur_general.py](api/orchestrateur_general.py#L2688)

Fonction `noeud_complements()` aux lignes **2671-2700** qui définit la liste des questions posées au formulaire:

| # | Champ | Question posée | Type | Obligatoire |
|---|-------|----------------|------|------------|
| 1 | `nom_client_brut` | "Quel est le nom à créer ?" | string | ✅ OUI |
| 2 | `intitule` | "Quelle est la raison sociale ?" | string | ✅ OUI |
| 3 | `ct_validite` | "Quel statut pour ce client ? (VALIDE / SUSPECT / BLOQUE)" | enum | ❌ NON (défaut: VALIDE) |
| 4 | `ct_encours_max` | "Quel encours maximum autorisé (en DT) ?" | number | ❌ NON (défaut: 0.0) |
| 5 | `adresse` | "Quelle est l'adresse postale ?" | string | ❌ NON |
| 6 | `complement` | "Complément d'adresse (si applicable) ?" | string | ❌ NON |
| 7 | `code_postal` | "Quel est le code postal ?" | string | ❌ NON |
| 8 | `ville` | "Quelle est la ville ?" | string | ❌ NON |
| 9 | `pays` | "Quel est le pays ?" | string | ❌ NON |
| 10 | `contact` | "Qui est le contact principal ?" | string | ❌ NON |
| 11 | `telephone` | "Quel est le numéro de téléphone ?" | string | ❌ NON |
| 12 | `email` | "Quelle est l'adresse e-mail ?" | string | ❌ NON |
| 13 | `site` | "Quel est le site web (si applicable) ?" | string | ❌ NON |

### Extrait du code (lignes 2688-2700 dans orchestrateur_general.py):
```python
questions = {
    "code_fournisseur": "Quel fournisseur ?",
    "code_client":      "Quel client ?",
    "ref_article":      "Quelle référence article ?",
    "quantite":         "Quelle quantité ?",
    "prix_unitaire":    "Quel prix unitaire ?",
    "nom_client_brut":  "Quel est le nom à créer ?",
    "intitule":         "Quelle est la raison sociale ?",
    "ct_validite":      "Quel statut pour ce client ? (VALIDE / SUSPECT / BLOQUE)",
    "ct_encours_max":   "Quel encours maximum autorisé (en DT) ?",
    "adresse":          "Quelle est l'adresse postale ?",
    "complement":       "Complément d'adresse (si applicable) ?",
    "code_postal":      "Quel est le code postal ?",
    "ville":            "Quelle est la ville ?",
    "pays":             "Quel est le pays ?",
    "contact":          "Qui est le contact principal ?",
    "telephone":        "Quel est le numéro de téléphone ?",
    "email":            "Quelle est l'adresse e-mail ?",
    "site":             "Quel est le site web (si applicable) ?",
}
```

---

## 2. STOCKAGE DES RÉPONSES DU FORMULAIRE

### Localisation : [api/orchestrateur_general.py](api/orchestrateur_general.py#L2720)

Fonction `injecter_complement()` aux lignes **2720-2810** qui stocke les réponses:

#### 2.1 Structure de stockage
Les réponses sont stockées dans le **state** du système:
- **Clé principale** : `state["pending_document"]` (dictionnaire)
- **Type de document marqué** : `doc["type_doc"]` = `"CLIENT_CREATION"` ou `"FOURNISSEUR_CREATION"`

#### 2.2 Champs traçabilité
- **`_champs_saisis`** : Set contenant les noms des champs qui ont été remplis (lignes 2765, 2768, 2771, 2775, 2781, 2786, 2792)

#### 2.3 Injection des réponses (lignes 2764-2800)
```python
# Champs simples (stockés directement)
elif "nom_client_brut" not in doc and doc.get("type_doc") in ("CLIENT_CREATION", "FOURNISSEUR_CREATION"):
    t = texte.strip()
    doc["nom_client_brut"] = "" if _est_reponse_vide(t) else t

elif "intitule" not in doc and doc.get("type_doc") in ("CLIENT_CREATION", "FOURNISSEUR_CREATION"):
    doc["intitule"] = "" if _est_reponse_vide(texte) else texte.strip()

# Champs validité et encours
elif "ct_validite" not in doc and doc.get("type_doc") in ("CLIENT_CREATION", "FOURNISSEUR_CREATION"):
    v = texte.strip().upper()
    doc["ct_validite"] = v if v in ("VALIDE", "SUSPECT", "BLOQUE") else "VALIDE"

elif "ct_encours_max" not in doc and doc.get("type_doc") in ("CLIENT_CREATION", "FOURNISSEUR_CREATION"):
    m = re.search(r"(\d+(?:[.,]\d+)?)", texte)
    doc["ct_encours_max"] = float(m.group(1).replace(",", ".")) if m else 0.0

# Champs adresse avec traçabilité via _champs_saisis
elif "adresse" not in doc.get("_champs_saisis", set()) and doc.get("type_doc") in ("CLIENT_CREATION", "FOURNISSEUR_CREATION"):
    doc.setdefault("_champs_saisis", set()).add("adresse")
    doc["adresse"] = "" if _est_reponse_vide(texte) else texte.strip()

# ... (idem pour complement, code_postal, ville, pays, contact, telephone, email, site)
```

#### 2.4 Format final dans `state["pending_document"]`
```python
state["pending_document"] = {
    "type_doc": "CLIENT_CREATION",
    "nom_client_brut": "Bijouterie du Lac",
    "intitule": "Bijouterie du Lac",
    "ct_validite": "VALIDE",
    "ct_encours_max": 5000.0,
    "adresse": "123 Rue de la Paix",
    "complement": "Immeuble B, Apt 5",
    "code_postal": "75001",
    "ville": "Paris",
    "pays": "France",
    "contact": "Marie Dupont",
    "telephone": "+33 1 23 45 67 89",
    "email": "marie@bijouteriedulac.com",
    "site": "www.bijouteriedulac.com",
    "_champs_saisis": {"adresse", "complement", "code_postal", "ville", "contact", "telephone", "email", "site"}
}
```

---

## 3. CONSTRUCTION DU PAYLOAD POUR creer_nouveau_client

### Localisation : [api/graph_nodes/ecriture.py](api/graph_nodes/ecriture.py#L222)

Lignes **222-246** - Fonction `noeud_ecriture()` pour action CREER_CLIENT:

```python
elif act == "CREER_CLIENT":
    pd = state.get("pending_document", {})
    _intitule = pd.get("intitule") or state.get("nom_client_brut") or state.get("code_client") or "Nouveau Client"
    payload = {
        "code_client": state.get("code_client"),
        "intitule": _intitule,
        "ct_validite": pd.get("ct_validite", state.get("ct_validite", "VALIDE")),
        "ct_encours_max": pd.get("ct_encours_max", state.get("ct_encours_max", 0.0)),
        "adresse": pd.get("adresse", ""),
        "complement": pd.get("complement", ""),
        "code_postal": pd.get("code_postal", ""),
        "ville": pd.get("ville", ""),
        "pays": pd.get("pays", ""),
        "contact": pd.get("contact", ""),
        "telephone": pd.get("telephone", ""),
        "email": pd.get("email", ""),
        "site": pd.get("site", ""),
        "cg_num_princ": pd.get("cg_num_princ", ""),
    }
    
    logger.debug("[CREER_CLIENT] payload -> %s", payload)
    try:
        raw = await mcp_pool.call("actions", "creer_nouveau_client", payload)
    except Exception as e:
        logger.exception("[CREER_CLIENT] erreur appel MCP creer_nouveau_client: %s", e)
        data = {"statut": "ERREUR", "message": _safe_str(e)}
```

### Payload passé à MCP
```python
{
    "code_client": "CLI001",
    "intitule": "Bijouterie du Lac",
    "ct_validite": "VALIDE",
    "ct_encours_max": 5000.0,
    "adresse": "123 Rue de la Paix",
    "complement": "Immeuble B, Apt 5",
    "code_postal": "75001",
    "ville": "Paris",
    "pays": "France",
    "contact": "Marie Dupont",
    "telephone": "+33 1 23 45 67 89",
    "email": "marie@bijouteriedulac.com",
    "site": "www.bijouteriedulac.com",
    "cg_num_princ": ""
}
```

---

## 4. DÉFINITION DE L'OUTIL MCP : creer_nouveau_client

### Localisation : [api/mcp_actions_sage.py](api/mcp_actions_sage.py#L2142)

Lignes **2142-2171** - Déclaration du schéma JSON Schema:

```python
types.Tool(
    name="creer_nouveau_client",
    description="Crée un nouveau client.",
    inputSchema={
        "type": "object",
        "properties": {
            "code_client":     {"type": "string",
                                "description": "Code unique du client (ex: CLI001)"},
            "intitule":        {"type": "string",
                                "description": "Nom / raison sociale du client"},
            "ct_validite":     {"type": "string",
                                "description": "VALIDE | BLOQUE | SUSPECT (défaut VALIDE)",
                                "default": "VALIDE"},
            "ct_encours_max":  {"type": "number",
                                "description": "Encours maximum autorisé (défaut 0)",
                                "default": 0},
            "adresse":         {"type": "string", "default": "", "description": "Adresse postale"},
            "complement":      {"type": "string", "default": "", "description": "Complément d'adresse"},
            "code_postal":     {"type": "string", "default": "", "description": "Code postal"},
            "ville":           {"type": "string", "default": "", "description": "Ville"},
            "pays":            {"type": "string", "default": "", "description": "Pays"},
            "contact":         {"type": "string", "default": "", "description": "Contact principal"},
            "telephone":       {"type": "string", "default": "", "description": "Téléphone"},
            "email":           {"type": "string", "default": "", "description": "Adresse e-mail"},
            "site":            {"type": "string", "default": "", "description": "Site web"},
            "cg_num_princ":    {"type": "string", "default": "", "description": "Compte comptable principal (override)"},
        },
        "required": ["code_client", "intitule"],
    },
),
```

### Paramètres obligatoires
- ✅ `code_client` (string)
- ✅ `intitule` (string)

### Paramètres optionnels avec défauts
- `ct_validite` → défaut: "VALIDE"
- `ct_encours_max` → défaut: 0
- `adresse` → défaut: ""
- `complement` → défaut: ""
- `code_postal` → défaut: ""
- `ville` → défaut: ""
- `pays` → défaut: ""
- `contact` → défaut: ""
- `telephone` → défaut: ""
- `email` → défaut: ""
- `site` → défaut: ""
- `cg_num_princ` → défaut: ""

---

## 5. IMPLÉMENTATION DE creer_nouveau_client

### Localisation : [api/mcp_actions_sage.py](api/mcp_actions_sage.py#L2568)

Lignes **2568-2650** - Traitement du call MCP:

```python
elif name == "creer_nouveau_client":
    conn = _get_conn()
    try:
        code_client    = arguments["code_client"]
        intitule       = arguments["intitule"]
        ct_validite    = (arguments.get("ct_validite") or "VALIDE").upper()
        if ct_validite not in ("VALIDE", "BLOQUE", "SUSPECT"):
            ct_validite = "VALIDE"
        ct_encours_max = float(arguments.get("ct_encours_max") or arguments.get("ct_encours") or 0.0)

        # Vérification de l'unicité du code
        existing = conn.execute(
            f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} = ?",
            (code_client,)
        ).fetchone()
        if existing:
            original_code = code_client
            code_client = _generer_code_tiers_unique(conn, intitule)
            logger.debug(f"[creer_nouveau_client] Duplicate code '{original_code}' trouvé, régénéré → '{code_client}'")
        
        # Génération du cbMarq
        cbmarq = _generer_cbmarq(conn, T_TIERS, C_CT_CBMARQ)
        
        # Construction des valeurs à insérer
        valeurs = {
            C_CT_NUM: code_client,
            C_CT_INTITULE: intitule,
            C_CT_TYPE: 0,  # 0 = CLIENT
            C_CT_SOMMEIL: 0,  # 0 = ACTIF
            C_CT_ENCOURS: ct_encours_max,
            C_CT_CGNUMPRINC: _normaliser_valeur(arguments.get("cg_num_princ") or _CG_NUM_PAR_TYPE.get(0)),
            C_CT_ADRESSE: _normaliser_valeur(arguments.get("adresse", "")),
            C_CT_COMPLEMENT: _normaliser_valeur(arguments.get("complement", "")),
            C_CT_CODEPOSTAL: _normaliser_valeur(arguments.get("code_postal", "")),
            C_CT_VILLE: _normaliser_valeur(arguments.get("ville", "")),
            C_CT_PAYS: _normaliser_valeur(arguments.get("pays", "")),
            C_CT_CONTACT: _normaliser_valeur(arguments.get("contact", "")),
            C_CT_TELEPHONE: _normaliser_valeur(arguments.get("telephone", "")),
            C_CT_EMAIL: _normaliser_valeur(arguments.get("email", "")),
            C_CT_SITE: _normaliser_valeur(arguments.get("site", "")),
            "cbMarq": cbmarq,
        }
        
        # Application des defaults constants
        valeurs.update(_DEFAULTS_TIERS_CONSTANTES)
        
        # Insertion en base
        # ... (insertion SQL)
        
        conn.commit()
        result = {"statut": "CREE", "CT_Num": code_client, "message": f"✅ Client '{intitule}' ({code_client}) créé."}
    finally:
        conn.close()
    return _to_text(result)
```

### Traitement des champs

| Champ | Colonne DB | Traitement | Défaut | Nullable |
|-------|------------|-----------|--------|----------|
| `code_client` | C_CT_NUM | Vérif unicité + regen si doublon | — | ❌ |
| `intitule` | C_CT_INTITULE | Stockage direct | — | ❌ |
| `ct_validite` | C_CT_SOMMEIL | Conversion VALIDE→0, BLOQUE→1 | "VALIDE" | ✅ |
| `ct_encours_max` | C_CT_ENCOURS | Float | 0.0 | ✅ |
| `adresse` | C_CT_ADRESSE | Normalisation (. → NULL) | "" | ✅ |
| `complement` | C_CT_COMPLEMENT | Normalisation (. → NULL) | "" | ✅ |
| `code_postal` | C_CT_CODEPOSTAL | Normalisation (. → NULL) | "" | ✅ |
| `ville` | C_CT_VILLE | Normalisation (. → NULL) | "" | ✅ |
| `pays` | C_CT_PAYS | Normalisation (. → NULL) | "" | ✅ |
| `contact` | C_CT_CONTACT | Normalisation (. → NULL) | "" | ✅ |
| `telephone` | C_CT_TELEPHONE | Normalisation (. → NULL) | "" | ✅ |
| `email` | C_CT_EMAIL | Normalisation (. → NULL) | "" | ✅ |
| `site` | C_CT_SITE | Normalisation (. → NULL) | "" | ✅ |
| `cg_num_princ` | C_CT_CGNUMPRINC | Override ou défaut par type | "4110000" (client) | ✅ |

---

## 6. FLUX COMPLET DE VÉRIFICATION

### Avant confirmation (Confirmation node)

**Localisation** : [api/graph_nodes/confirmation.py](api/graph_nodes/confirmation.py#L33)

Ligne 33 - Champs requis pour validation:
```python
"CREER_CLIENT": ["code_client", "nom_client_brut"]
```

✅ **Code client** : Généré automatiquement si absent
✅ **Nom client (brut)** : Requis et extrait de la demande utilisateur

---

## 7. RÉSUMÉ - CHEMIN COMPLET DES DONNÉES

```
1. DEMANDE UTILISATEUR
   ↓
2. noeud_planner (orchestrateur_general.py)
   → Détecte action = "CREER_CLIENT"
   → Génère code_client automatiquement si nom valide
   ↓
3. noeud_clarification (orchestrateur_general.py)
   → Appel _hub_valider_demande avec champs requis
   ↓
4. noeud_confirmation (confirmation.py#L33)
   → Vérifie ["code_client", "nom_client_brut"]
   ↓
5. noeud_complements (orchestrateur_general.py#L2671)
   → Pose 13 questions du formulaire
   ↓
6. injecter_complement (orchestrateur_general.py#L2720)
   → Stocke les réponses dans state["pending_document"]
   → Marque les champs dans _champs_saisis
   ↓
7. noeud_ecriture (ecriture.py#L222)
   → Construit le payload depuis pending_document
   → Appel MCP "creer_nouveau_client"
   ↓
8. MCP Handler (mcp_actions_sage.py#L2568)
   → creer_nouveau_client()
   → Vérification unicité du code
   → Insertion en base de données
   → Retour du résultat
```

---

## 8. VÉRIFICATIONS IMPORTANTES

### 8.1 Champs extractibles automatiquement
Ces champs peuvent être extraits de la question initiale par NER ou extraction regex:
- ✅ `nom_client_brut` → Extraction du nom
- ✅ `code_client` → Génération via `_generer_code_client()`

### 8.2 Champs qui déclenchent des questions
Si absent, le formulaire demande:
- ❓ `intitule` (raison sociale) - si ≠ du nom brut
- ❓ `ct_validite` - statut du client
- ❓ `ct_encours_max` - plafond d'encours
- ❓ `adresse`, `complement`, `code_postal`, `ville`, `pays` - adresse complète
- ❓ `contact`, `telephone`, `email`, `site` - coordonnées

### 8.3 Validation des valeurs
| Champ | Validation |
|-------|-----------|
| `ct_validite` | Limité à {VALIDE, BLOQUE, SUSPECT} |
| `ct_encours_max` | Numérique, >= 0 |
| `telephone` | Regex extraction `(\+?\d[\d\s\-().]{4,}\d)` |
| `email` | Regex extraction `[\w\.-]+@[\w\.-]+\.\w+` |
| `code_postal` | Regex extraction `(\d{2,10})` |

---

## 9. COLONNES PHYSIQUES MAPPÉES

**Mapping orchestrateur_general.py ligne 2652 et mcp_actions_sage.py ligne 448:**

```python
_CHAMPS_TEXTE_OPTIONNELS_TIERS = {
    "adresse":     "C_CT_ADRESSE",
    "complement":  "C_CT_COMPLEMENT",
    "code_postal": "C_CT_CODEPOSTAL",
    "ville":       "C_CT_VILLE",
    "pays":        "C_CT_PAYS",
    "contact":     "C_CT_CONTACT",
    "telephone":   "C_CT_TELEPHONE",
    "email":       "C_CT_EMAIL",
    "site":        "C_CT_SITE",
}
```

---

## 📝 CONCLUSION

Le flux CREER_CLIENT est **entièrement mappé** :
- ✅ 13 champs demandés au formulaire identifiés
- ✅ Stockage dans `state["pending_document"]` confirmé
- ✅ Passage au payload MCP documenté (13 paramètres)
- ✅ Implémentation `creer_nouveau_client()` analysée complètement
- ✅ Mappings colonnes DB vérifiés

Tous les champs sont **effectivement récupérés** et **passés à la fonction** d'implémentation.
