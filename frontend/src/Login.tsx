import { useState, FormEvent } from "react";
import axios from "axios";
import { Sparkles, Eye, EyeOff, Loader2, AlertCircle } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// ─────────────────────────────────────────────────────────────────────
// AuthInfo : informations renvoyées après un login réussi.
// Exporté ici pour être consommé par App.tsx.
// ─────────────────────────────────────────────────────────────────────
export interface AuthInfo {
  token: string;
  username: string;
  role: string;
}

interface LoginProps {
  onLogin: (info: AuthInfo) => void;
}

export default function Login({ onLogin }: LoginProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;

    setIsLoading(true);
    setError(null);

    try {
      const res = await axios.post(`${API_BASE}/api/auth/login`, {
        username: username.trim(),
        password,
      });

      const { access_token, username: user, role } = res.data;
      onLogin({ token: access_token, username: user, role });
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        setError(
          typeof detail === "string"
            ? detail
            : "Identifiant ou mot de passe incorrect."
        );
      } else {
        setError("Impossible de contacter le serveur. Vérifiez votre connexion.");
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={styles.page}>
      {/* Animated background blobs */}
      <div style={styles.blob1} />
      <div style={styles.blob2} />

      <div style={styles.card}>
        {/* Logo */}
        <div style={styles.logoRow}>
          <div style={styles.iconWrap}>
            <Sparkles size={22} color="#fff" />
          </div>
          <span style={styles.appName}>Copilot ERP</span>
        </div>

        <h1 style={styles.title}>Connexion</h1>
        <p style={styles.subtitle}>
          Accédez à votre assistant Sage 100 intelligent
        </p>

        <form onSubmit={handleSubmit} style={styles.form} autoComplete="on">
          {/* Username */}
          <div style={styles.field}>
            <label htmlFor="username" style={styles.label}>
              Identifiant
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Votre identifiant"
              style={styles.input}
              onFocus={(e) =>
                Object.assign(e.currentTarget.style, styles.inputFocus)
              }
              onBlur={(e) =>
                Object.assign(e.currentTarget.style, styles.inputBlur)
              }
              disabled={isLoading}
            />
          </div>

          {/* Password */}
          <div style={styles.field}>
            <label htmlFor="password" style={styles.label}>
              Mot de passe
            </label>
            <div style={styles.passwordWrap}>
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                style={{ ...styles.input, paddingRight: "3rem" }}
                onFocus={(e) =>
                  Object.assign(e.currentTarget.style, styles.inputFocus)
                }
                onBlur={(e) =>
                  Object.assign(e.currentTarget.style, styles.inputBlur)
                }
                disabled={isLoading}
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                style={styles.eyeBtn}
                tabIndex={-1}
                aria-label={showPassword ? "Masquer" : "Afficher"}
              >
                {showPassword ? (
                  <EyeOff size={17} color="#6b7280" />
                ) : (
                  <Eye size={17} color="#6b7280" />
                )}
              </button>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div style={styles.errorBox}>
              <AlertCircle size={16} color="#f87171" style={{ flexShrink: 0 }} />
              <span>{error}</span>
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={isLoading || !username.trim() || !password}
            style={
              isLoading || !username.trim() || !password
                ? { ...styles.btn, ...styles.btnDisabled }
                : styles.btn
            }
          >
            {isLoading ? (
              <>
                <Loader2
                  size={18}
                  style={{ animation: "spin 1s linear infinite" }}
                />
                Connexion…
              </>
            ) : (
              "Se connecter"
            )}
          </button>
        </form>

        <p style={styles.footer}>
          Copilot ERP · Sage 100 &copy; {new Date().getFullYear()}
        </p>
      </div>

      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        * { box-sizing: border-box; }
        body { margin: 0; font-family: 'Inter', sans-serif; }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeIn { from { opacity:0; transform:translateY(18px); } to { opacity:1; transform:translateY(0); } }
        @keyframes blob { 0%,100%{border-radius:60% 40% 30% 70%/60% 30% 70% 40%} 50%{border-radius:30% 60% 70% 40%/50% 60% 30% 60%} }
      `}</style>
    </div>
  );
}

// ── Styles inline (no TailwindCSS dependency) ──────────────────────────
const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "linear-gradient(135deg, #0f0f1a 0%, #111827 50%, #0d1117 100%)",
    fontFamily: "'Inter', sans-serif",
    position: "relative",
    overflow: "hidden",
    padding: "1rem",
  },
  blob1: {
    position: "absolute",
    width: 420,
    height: 420,
    background:
      "radial-gradient(circle, rgba(16,185,129,0.18) 0%, transparent 70%)",
    borderRadius: "60% 40% 30% 70% / 60% 30% 70% 40%",
    top: "-80px",
    left: "-100px",
    animation: "blob 8s ease-in-out infinite",
    zIndex: 0,
  },
  blob2: {
    position: "absolute",
    width: 380,
    height: 380,
    background:
      "radial-gradient(circle, rgba(99,102,241,0.15) 0%, transparent 70%)",
    borderRadius: "30% 60% 70% 40% / 50% 60% 30% 60%",
    bottom: "-60px",
    right: "-80px",
    animation: "blob 10s ease-in-out infinite reverse",
    zIndex: 0,
  },
  card: {
    position: "relative",
    zIndex: 1,
    background:
      "linear-gradient(145deg, rgba(31,41,55,0.95) 0%, rgba(17,24,39,0.98) 100%)",
    border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: 20,
    padding: "2.5rem 2.25rem",
    width: "100%",
    maxWidth: 420,
    boxShadow:
      "0 25px 60px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.04)",
    animation: "fadeIn 0.45s ease both",
  },
  logoRow: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    marginBottom: "1.75rem",
  },
  iconWrap: {
    background: "linear-gradient(135deg, #059669 0%, #10b981 100%)",
    borderRadius: 12,
    padding: "8px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxShadow: "0 4px 14px rgba(16,185,129,0.4)",
  },
  appName: {
    fontSize: "1.2rem",
    fontWeight: 700,
    color: "#f9fafb",
    letterSpacing: "-0.3px",
  },
  title: {
    margin: "0 0 0.35rem",
    fontSize: "1.65rem",
    fontWeight: 700,
    color: "#f9fafb",
    letterSpacing: "-0.5px",
  },
  subtitle: {
    margin: "0 0 1.75rem",
    fontSize: "0.875rem",
    color: "#6b7280",
    lineHeight: 1.5,
  },
  form: {
    display: "flex",
    flexDirection: "column",
    gap: "1.1rem",
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: "0.4rem",
  },
  label: {
    fontSize: "0.8rem",
    fontWeight: 600,
    color: "#9ca3af",
    textTransform: "uppercase",
    letterSpacing: "0.6px",
  },
  input: {
    background: "rgba(255,255,255,0.04)",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: 10,
    padding: "0.75rem 1rem",
    color: "#f9fafb",
    fontSize: "0.95rem",
    outline: "none",
    width: "100%",
    transition: "border-color 0.2s, box-shadow 0.2s",
  },
  inputFocus: {
    borderColor: "#10b981",
    boxShadow: "0 0 0 3px rgba(16,185,129,0.15)",
    background: "rgba(255,255,255,0.06)",
  },
  inputBlur: {
    borderColor: "rgba(255,255,255,0.1)",
    boxShadow: "none",
    background: "rgba(255,255,255,0.04)",
  },
  passwordWrap: {
    position: "relative",
  },
  eyeBtn: {
    position: "absolute",
    right: "0.75rem",
    top: "50%",
    transform: "translateY(-50%)",
    background: "transparent",
    border: "none",
    cursor: "pointer",
    padding: 4,
    display: "flex",
    alignItems: "center",
  },
  errorBox: {
    display: "flex",
    alignItems: "flex-start",
    gap: 8,
    background: "rgba(239,68,68,0.1)",
    border: "1px solid rgba(239,68,68,0.25)",
    borderRadius: 8,
    padding: "0.6rem 0.85rem",
    color: "#fca5a5",
    fontSize: "0.85rem",
    lineHeight: 1.5,
  },
  btn: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    background: "linear-gradient(135deg, #059669 0%, #10b981 100%)",
    color: "#fff",
    border: "none",
    borderRadius: 10,
    padding: "0.8rem",
    fontWeight: 600,
    fontSize: "0.95rem",
    cursor: "pointer",
    marginTop: "0.5rem",
    transition: "opacity 0.2s, transform 0.1s",
    boxShadow: "0 4px 14px rgba(16,185,129,0.35)",
  },
  btnDisabled: {
    opacity: 0.45,
    cursor: "not-allowed",
    boxShadow: "none",
  },
  footer: {
    marginTop: "1.75rem",
    textAlign: "center",
    fontSize: "0.75rem",
    color: "#374151",
  },
};
