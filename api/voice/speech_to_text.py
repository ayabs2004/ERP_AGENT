import os
import logging

# Logger propre — n'écrit jamais sur stdout (qui est réservé au protocole JSONRPC MCP).
# Les messages apparaissent dans les logs uvicorn normaux (stderr).
logger = logging.getLogger("sage.erp.voice")

from faster_whisper import WhisperModel

# Whisper sur CPU avec quantification int8 (pas besoin de CUDA).
# Pour activer le GPU : pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
# puis changer device="cuda" et compute_type="float16".
logger.info("[Voice] Chargement du modèle Whisper small (CPU/int8)...")
model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)
logger.info("[Voice] Modèle Whisper prêt.")


def transcribe_audio(audio_path: str) -> str:
    segments, info = model.transcribe(
        audio_path,
        language="fr",

        # ── Anti-hallucinations ───────────────────────────────────────────
        # 1. Filtre VAD : ignore les zones sans voix (silence, bruit de fond).
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},

        # 2. Seuil de non-parole : retourne "" plutôt qu'inventer une phrase.
        no_speech_threshold=0.6,

        # 3. Désactiver la continuation contextuelle : évite la copie de
        #    phrases répétitives du corpus d'entraînement (Amara.org, etc.).
        condition_on_previous_text=False,

        # 4. Prompt ERP : oriente Whisper vers le vocabulaire métier Sage.
        initial_prompt=(
            "Logiciel ERP Sage 100. Commandes vocales en français. "
            "Vocabulaire : client, fournisseur, facture, bon de livraison, BL, BC, "
            "bon de commande, article, stock, nomenclature, encours, règlement, avoir, "
            "statut, créer, modifier, afficher, liste, fiche, référence, code client."
        ),
    )

    text = " ".join(segment.text for segment in segments).strip()

    # Filtre de sécurité : rejeter les hallucinations connues
    _HALLUCINATIONS = {
        "sous-titres réalisés par la communauté d'amara.org",
        "merci d'avoir regardé",
        "sous-titré par",
        "transcrit par",
        "amara.org",
    }
    if any(h in text.lower() for h in _HALLUCINATIONS):
        logger.warning("[Voice] Hallucination détectée, résultat ignoré : %s", text)
        return ""

    return text


# ── Test autonome ─────────────────────────────────────────────────────────────
# Utilisation : python api/voice/speech_to_text.py
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_file = "test.wav"

    if not os.path.exists(test_file):
        print(f"Création d'un fichier audio de test {test_file}...")
        from gtts import gTTS
        tts = gTTS(text="Crée un bon de livraison pour le client ABC", lang="fr")
        tts.save(test_file)

    print(f"Test de transcription sur {test_file}...")
    try:
        result = transcribe_audio(test_file)
        print("--- Transcription réussie ---")
        print(result)
    except Exception as e:
        print("Erreur lors de la transcription :", e)
