import os
from dotenv import load_dotenv

# Load env file from the project directory
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

GROQ_URL = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1")
GROQ_KEY = os.getenv("GROQ_KEY", "missing_key")
MODELE_FAST = os.getenv("GROQ_FAST", "openai/gpt-oss-20b")
MEM0_EMBED_DIMS = int(os.getenv("MEM0_EMBED_DIMS", "768"))
MEM0_DB_PATH = os.getenv("MEM0_DB_PATH", "./test_mem0_db")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MEM0_EMBED_MODEL = os.getenv("MEM0_EMBED_MODEL", "nomic-embed-text")

print("Init Mem0 config...")
print(f"URL: {GROQ_URL}")
print(f"Model: {MODELE_FAST}")

from mem0 import Memory

try:
    m = Memory.from_config({
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "test_erp_copilot",
                "path": MEM0_DB_PATH,
                "embedding_model_dims": MEM0_EMBED_DIMS,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": MODELE_FAST,
                "temperature": 0,
                "api_key": GROQ_KEY,
                "openai_base_url": GROQ_URL,
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": MEM0_EMBED_MODEL,
                "ollama_base_url": OLLAMA_BASE_URL,
            },
        },
    })
    print("Mem0 loaded. Adding test memory...")
    res = m.add("test message", user_id="debug")
    print("Success. Added:", res)
    
except Exception as e:
    print(f"Error occurred: {e}")
