# debug_imports.py — run from project root with the venv active
import importlib, sys, io

modules = [
  "api.mcp_actions_sage",
  "api.orchestrateur_general",
  "api.graph_nodes.ecriture",
  "adaptation.db_adapter",
  "database.schema_sage",
  "mcp.server",
  "mcp.server.stdio",
  "mcp",
]

orig = sys.stdout
for m in modules:
    sys.stdout = io.StringIO()
    try:
        importlib.import_module(m)
        s = sys.stdout.getvalue()
    except Exception as e:
        s = f"<import error: {e}>"
    finally:
        sys.stdout = orig
    print(f"--- {m} ---\n{repr(s)}\n")
