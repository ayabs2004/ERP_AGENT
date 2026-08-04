import { useState } from "react";
import axios from "axios";
import { Lock, User, Loader2, Sparkles, AlertCircle } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export interface AuthInfo {
  token: string;
  username: string;
  role: string;
}

interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  username: string;
  role: string;
}

interface LoginProps {
  onLogin: (auth: AuthInfo) => void;
}

function Login({ onLogin }: LoginProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await axios.post<LoginResponse>(`${API_BASE}/api/auth/login`, {
        username: username.trim(),
        password,
      });

      const { access_token, username: uname, role } = response.data;
      onLogin({ token: access_token, username: uname, role });
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        setError("Identifiant ou mot de passe incorrect.");
      } else {
        setError("Impossible de contacter le serveur. Réessayez.");
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-screen items-center justify-center bg-gray-900 text-gray-100">
      <div className="w-full max-w-sm bg-gray-950 border border-gray-800 rounded-2xl p-8 shadow-xl">
        <div className="flex items-center gap-3 mb-8 justify-center">
          <div className="bg-green-600 p-2 rounded-xl">
            <Sparkles />
          </div>
          <h1 className="text-2xl font-bold">Copilot ERP</h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-sm text-gray-400 mb-1 block">Identifiant</label>
            <div className="relative">
              <User size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-gray-800 rounded-xl p-3 pl-9 outline-none border border-transparent focus:border-green-600 transition-colors"
                placeholder="votre.identifiant"
                autoComplete="username"
              />
            </div>
          </div>

          <div>
            <label className="text-sm text-gray-400 mb-1 block">Mot de passe</label>
            <div className="relative">
              <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-gray-800 rounded-xl p-3 pl-9 outline-none border border-transparent focus:border-green-600 transition-colors"
                placeholder="••••••••"
                autoComplete="current-password"
              />
            </div>
          </div>

          {error && (
            <div className="flex items-center gap-2 text-sm text-rose-400 bg-rose-950/40 border border-rose-900 rounded-lg p-2">
              <AlertCircle size={16} className="shrink-0" />
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={!username.trim() || !password || isLoading}
            className="w-full bg-green-600 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-xl p-3 font-semibold flex items-center justify-center gap-2 transition-colors"
          >
            {isLoading ? <Loader2 className="animate-spin" size={18} /> : "Se connecter"}
          </button>
        </form>

        <p className="text-xs text-gray-500 mt-6 text-center">
          Pas de compte ? Demandez à un administrateur de vous en créer un.
        </p>
      </div>
    </div>
  );
}

export default Login;
