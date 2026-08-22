# debug_imports2.py — safer capture of stdout including .buffer writes
import importlib, sys, io

class CaptureStdout(io.StringIO):
    def __init__(self):
        super().__init__()
        self._buf = io.BytesIO()
    @property
    def buffer(self):
        parent = self
        class B:
            def write(self, b):
                if isinstance(b, (bytes, bytearray)):
                    try:
                        s = b.decode('utf-8')
                    except Exception:
                        s = str(b)
                else:
                    s = str(b)
                # write decoded string into parent text buffer
                parent.write(s)
                # also store raw bytes
                try:
                    parent._buf.write(b if isinstance(b, (bytes, bytearray)) else str(b).encode('utf-8'))
                except Exception:
                    pass
                return len(b) if isinstance(b, (bytes, bytearray)) else len(s)
            def flush(self):
                try:
                    parent.flush()
                except Exception:
                    pass
            def readable(self):
                return True
            def writable(self):
                return True
            def seekable(self):
                return False
            def seek(self, offset, whence=0):
                try:
                    return parent._buf.seek(offset, whence)
                except Exception:
                    return 0
            def tell(self):
                try:
                    return parent._buf.tell()
                except Exception:
                    return 0
            def read(self, n=-1):
                try:
                    return parent._buf.read(n)
                except Exception:
                    return b""
            def close(self):
                # Do not close the parent StringIO to allow getvalue() after imports
                return
            @property
            def closed(self):
                return False
        return B()

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
    cap = CaptureStdout()
    sys.stdout = cap
    try:
        importlib.import_module(m)
        s = cap.getvalue()
    except Exception as e:
        s = f"<import error: {e}>"
    finally:
        sys.stdout = orig
    print(f"--- {m} ---\n{repr(s)}\n")
