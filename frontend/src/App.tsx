import { useState, useRef, useEffect } from "react";
import axios from "axios";
import { Send, Sparkles, Bell, Loader2, FileText, Check, X, Coins, LogOut, Calendar } from "lucide-react";
import { v4 as uuidv4 } from "uuid";
import ReactMarkdown from "react-markdown";
import Login, { AuthInfo } from "./Login";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// ─────────────────────────────────────────────────────────────────────
// Le session_id reste local au navigateur (comme avant), mais il n'a plus
// aucune valeur d'identité à lui seul : côté serveur, chaque session est
// désormais namespacée par l'utilisateur authentifié (issu du JWT), donc
// deux personnes ne peuvent jamais se retrouver à partager le même
// historique même si ce sessionId venait à se répéter.
// ─────────────────────────────────────────────────────────────────────
const SESSION_ID = localStorage.getItem("sessionId") || uuidv4();
localStorage.setItem("sessionId", SESSION_ID);

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  pdfUrl?: string | null;
  scoreConfiance?: number;
  origineClassification?: string;
}

interface ChatResponse {
  responses: string[];
  suggestions: string[];
  draft_status: string;
  confirmation_status: string;
  pdf_url: string | null;
  alerts: string[];
  attente_complements: boolean;
  score_confiance: number;
  origine_classification: string;
  action_buttons?: string[];  // Nouveau champ pour boutons d'action
}

// Amélioration #7 : badge de confiance visible par l'utilisateur, pour
// qu'il sache quand l'action a été devinée avec une confiance moyenne
// et puisse la corriger facilement plutôt que de laisser l'agent
// trancher en silence.
function badgeConfiance(score?: number, origine?: string) {
  if (!origine || score === undefined) return null;
  const pct = Math.round(score * 100);
  const couleur =
    score >= 0.85
      ? "text-green-400 border-green-700"
      : score >= 0.6
      ? "text-yellow-400 border-yellow-700"
      : "text-orange-400 border-orange-700";
  const libelleOrigine: Record<string, string> = {
    REGEX: "règle exacte",
    SEMANTIQUE: "sémantique",
    ARBITRAGE_LLM: "LLM confirmé",
    LLM: "LLM",
    FALLBACK: "repli générique",
  };
  return (
    <span
      className={`inline-block mt-2 text-xs border rounded-full px-2 py-0.5 ${couleur}`}
      title="Confiance de classification de la demande"
    >
      {libelleOrigine[origine] ?? origine} · {pct}%
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// AUTH : chargement / persistance locale du JWT obtenu au login.
// On garde le token en localStorage pour survivre à un rafraîchissement
// de page (comme n'importe quelle appli web classique) ; il expire côté
// serveur au bout de JWT_EXPIRE_MINUTES et redemandera alors une connexion.
// ─────────────────────────────────────────────────────────────────────
const AUTH_STORAGE_KEY = "authInfo";

function chargerAuthStockee(): AuthInfo | null {
  try {
    const brut = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!brut) return null;
    const parsed = JSON.parse(brut);
    if (!parsed?.token || !parsed?.username) return null;

    // Decode JWT payload (base64url) and check expiration
    // so the login screen shows immediately instead of after a 401
    try {
      const payload = JSON.parse(atob(parsed.token.split(".")[1]));
      if (payload.exp && payload.exp * 1000 < Date.now()) {
        localStorage.removeItem(AUTH_STORAGE_KEY);
        return null; // expired → force re-login
      }
    } catch {
      // If JWT decode fails, clear and force re-login
      localStorage.removeItem(AUTH_STORAGE_KEY);
      return null;
    }

    return parsed as AuthInfo;
  } catch {
    localStorage.removeItem(AUTH_STORAGE_KEY);
    return null;
  }
}

function App() {
  const [auth, setAuth] = useState<AuthInfo | null>(chargerAuthStockee());

  const handleLogin = (info: AuthInfo) => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(info));
    setAuth(info);
  };

  const handleLogout = () => {
    localStorage.removeItem(AUTH_STORAGE_KEY);
    setAuth(null);
  };

  if (!auth) {
    return <Login onLogin={handleLogin} />;
  }

  return <ChatApp auth={auth} onLogout={handleLogout} />;
}

interface ChatAppProps {
  auth: AuthInfo;
  onLogout: () => void;
}
const markdownComponents = {
  table: ({ children }: any) => (
    <div className="overflow-x-auto my-3 rounded-lg border border-gray-700">
      <table className="min-w-full border-collapse text-sm">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }: any) => (
    <thead className="bg-gray-900/60">{children}</thead>
  ),
  tbody: ({ children }: any) => (
    <tbody className="divide-y divide-gray-700">{children}</tbody>
  ),
  tr: ({ children }: any) => (
    <tr className="even:bg-gray-800/40 hover:bg-gray-700/40 transition-colors">
      {children}
    </tr>
  ),
  th: ({ children }: any) => (
    <th className="border border-gray-700 px-3 py-2 text-left font-semibold text-gray-200 whitespace-nowrap">
      {children}
    </th>
  ),
  td: ({ children }: any) => (
    <td className="border border-gray-700 px-3 py-2 text-gray-300 align-top">
      {children}
    </td>
  ),
  // bonus : les listes / gras / titres rendus par tes formatters restent lisibles aussi
  strong: ({ children }: any) => (
    <strong className="font-semibold text-white">{children}</strong>
  ),
  h3: ({ children }: any) => (
    <h3 className="text-base font-bold mt-1 mb-2">{children}</h3>
  ),
};
function ChatApp({ auth, onLogout }: ChatAppProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content:
        `Bonjour **${auth.username}** ! Je suis **Copilot ERP**, votre assistant intelligent pour Sage 100.\nComment puis-je vous aider aujourd'hui ?`,
    },
  ]);

  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [actionButtons, setActionButtons] = useState<string[]>([]);  // Nouvel état pour boutons d'action
  const [draftStatus, setDraftStatus] = useState("");
  const [confirmationStatus, setConfirmationStatus] = useState("");
  const [alerts, setAlerts] = useState<string[]>([]);
  const [attenteComplements, setAttenteComplements] = useState(false);
  const [montantPerso, setMontantPerso] = useState("");
  const [datePerso, setDatePerso] = useState("");

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Client axios dédié : porte le JWT de l'utilisateur connecté sur chaque
  // appel, et se déconnecte automatiquement si le serveur répond 401
  // (token expiré ou compte désactivé) plutôt que de rester coincé.
  const apiRef = useRef(
    axios.create({
      baseURL: API_BASE,
      headers: { Authorization: `Bearer ${auth.token}` },
    })
  );

  useEffect(() => {
    apiRef.current = axios.create({
      baseURL: API_BASE,
      headers: { Authorization: `Bearer ${auth.token}` },
    });
    const id = apiRef.current.interceptors.response.use(
      (res) => res,
      (err) => {
        if (axios.isAxiosError(err) && err.response?.status === 401) {
          onLogout();
        }
        return Promise.reject(err);
      }
    );

    // Valider le token en tâche de fond au démarrage.
    // Si le token est invalide/expiré ou si le backend a redémarré (changement de secret),
    // cela renverra une 401 et l'intercepteur déclenchera immédiatement onLogout().
    apiRef.current.get("/api/auth/me").catch(() => {});

    return () => apiRef.current.interceptors.response.eject(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.token]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, suggestions, isLoading, draftStatus, confirmationStatus]);

  // Télécharge/ouvre un PDF protégé : l'URL renvoyée par le backend exige
  // désormais un JWT valide, donc un simple <a href> ne suffit plus (pas
  // de moyen d'y attacher un en-tête Authorization). On récupère le
  // fichier en mémoire (blob) avec notre client authentifié, puis on
  // l'ouvre via une URL objet temporaire.
  const ouvrirDocument = async (pdfUrl: string, isExcel: boolean) => {
    try {
      const res = await apiRef.current.get(pdfUrl, { responseType: "blob" });
      const blobUrl = URL.createObjectURL(res.data);
      if (isExcel) {
        const a = document.createElement("a");
        a.href = blobUrl;
        a.download = pdfUrl.split("/").pop() || "document.xlsx";
        document.body.appendChild(a);
        a.click();
        a.remove();
      } else {
        window.open(blobUrl, "_blank", "noopener,noreferrer");
      }
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
    } catch (err) {
      console.error(err);
      setMessages((prev) => [
        ...prev,
        {
          id: uuidv4(),
          role: "system",
          content: "❌ Impossible de récupérer le document (session peut-être expirée).",
        },
      ]);
    }
  };

  const sendMessage = async (text: string, displayText?: string) => {
    if (!text.trim()) return;

    const userMessage: Message = {
      id: uuidv4(),
      role: "user",
      content: displayText ?? text,
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);
    setSuggestions([]);

    try {
      const response = await apiRef.current.post<ChatResponse>("/api/chat", {
        session_id: SESSION_ID,
        message: text,
      });

      const {
        responses,
        suggestions: newSuggestions,
        draft_status,
        confirmation_status,
        pdf_url,
        alerts: newAlerts,
        attente_complements,
        score_confiance,
        origine_classification,
        action_buttons: newActionButtons,
      } = response.data;

      const assistantMessages: Message[] = responses.map((msg, index) => ({
        id: uuidv4(),
        role: "assistant",
        content: msg,
        pdfUrl: index === responses.length - 1 ? pdf_url : null,
        scoreConfiance: index === responses.length - 1 ? score_confiance : undefined,
        origineClassification: index === responses.length - 1 ? origine_classification : undefined,
      }));

      setMessages((prev) => [...prev, ...assistantMessages]);
      setSuggestions(newSuggestions ?? []);
      setActionButtons(newActionButtons ?? []);
      setDraftStatus(draft_status ?? "");
      setConfirmationStatus(confirmation_status ?? "");
      setAlerts(newAlerts ?? []);
      setAttenteComplements(attente_complements);

      // Réinitialiser l'état si le message de reset est reçu
      if (responses.some(r => r.includes("Session réinitialisée"))) {
        setMessages([
          {
            id: uuidv4(),
            role: "assistant",
            content: "🔄 Session réinitialisée avec succès.",
          },
        ]);
        setSuggestions([]);
        setActionButtons([]);
        setDraftStatus("");
        setConfirmationStatus("");
        setAlerts([]);
        setAttenteComplements(false);
        setMontantPerso("");
      }
    } catch (err) {
      console.error(err);

      if (axios.isAxiosError(err) && err.response?.status === 401) {
        // L'intercepteur a déjà déclenché la déconnexion ; pas besoin
        // d'ajouter un message ici, l'écran de login va s'afficher.
        return;
      }

      setMessages((prev) => [
        ...prev,
        {
          id: uuidv4(),
          role: "system",
          content:
            "❌ Une erreur est survenue lors de la communication avec le serveur.",
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  // Libellé affiché côté utilisateur pour les actions principales,
  // pour rester cohérent quel que soit l'endroit d'où l'action est déclenchée.
  const libelleAction = (action: string) => {
    const cle = action.trim().toUpperCase();
    if (cle === "CONFIRMER" || cle === "CONFIRM") return `✅ ${action}`;
    if (cle === "ANNULER") return `🛑 ${action}`;
    return action;
  };

  const handleSuggestion = (action: string) =>
    sendMessage(action, libelleAction(action));

  // Boutons principaux à afficher : priorité aux action_buttons envoyés par
  // le backend, sinon repli sur les statuts de draft/offre historiques.
  const enAttenteValidation =
    draftStatus === "draft" ||
    draftStatus === "PREVIEW" ||
    draftStatus === "OFFRE_PRIX" ||
    confirmationStatus === "ATTENTE";

  const boutonsPrincipaux =
    actionButtons.length > 0
      ? actionButtons
      : enAttenteValidation
      ? ["CONFIRMER", "ANNULER"]
      : [];

  // Suggestions déjà représentées par les boutons principaux → on ne les
  // affiche pas deux fois. Le reste (ex: "non", "10%") devient des puces
  // secondaires à l'intérieur de la même carte, pour une UI cohérente.
  const chipsSecondaires =
    boutonsPrincipaux.length > 0
      ? suggestions.filter(
          (s) => !boutonsPrincipaux.some((b) => b.toUpperCase() === s.trim().toUpperCase())
        )
      : [];

  const suggestionsLibres = boutonsPrincipaux.length > 0 ? [] : suggestions;

  const handleMontantSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const montant = montantPerso.trim();
    if (!montant) return;
    sendMessage(montant, `💰 ${montant} TND`);
    setMontantPerso("");
  };

  const dernierMessageAssistant =
    [...messages].reverse().find((msg) => msg.role === "assistant")?.content ?? "";

  const afficherCalendrier = /date\s*(de\s*livraison|de\s*facturation|livraison|facturation)|livraison\s*souhait|facturation\s*souhait/i.test(
    dernierMessageAssistant
  );

  const handleDateSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!datePerso) return;
    sendMessage(datePerso, `📅 ${datePerso}`);
    setDatePerso("");
  };

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100 overflow-hidden">

      {/* SIDEBAR */}
      <div className="w-80 bg-gray-950 border-r border-gray-800 p-6 flex flex-col">
        <div className="flex items-center gap-3 mb-10">
          <div className="bg-green-600 p-2 rounded-xl">
            <Sparkles />
          </div>

          <h1 className="text-2xl font-bold">
            Copilot ERP
          </h1>
        </div>

        <div className="text-sm text-gray-400 space-y-2">
          <p>✔ Consultation Clients</p>
          <p>✔ Consultation Articles</p>
          <p>✔ Génération Documents</p>
          <p>✔ Workflow Commandes</p>
        </div>

        <div className="mt-8 space-y-2">
          <button
            className="w-full bg-gray-800 rounded p-2"
            onClick={() => sendMessage("reset")}
          >
            Reset Session
          </button>

          <button
            className="w-full bg-gray-800 rounded p-2"
            onClick={() => sendMessage("aide")}
          >
            Aide
          </button>
        </div>

        {/* Compte connecté + déconnexion */}
        <div className="mt-auto pt-6 border-t border-gray-800">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="text-sm font-medium truncate">{auth.username}</p>
              <p className="text-xs text-gray-500">{auth.role}</p>
            </div>
            <button
              onClick={onLogout}
              title="Se déconnecter"
              className="shrink-0 p-2 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* MAIN */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">

        {alerts.length > 0 && (
          <div className="bg-amber-900 text-amber-200 p-3">
            <Bell className="inline mr-2" size={16} />
            {alerts.join(" • ")}
          </div>
        )}

        {/* CHAT */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${
                msg.role === "user"
                  ? "justify-end"
                  : "justify-start"
              }`}
            >
              <div className="max-w-[85%] sm:max-w-[70%]">

                <div className="bg-gray-800 rounded-xl p-4 overflow-x-auto">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownComponents}>
  {msg.content}
</ReactMarkdown>
                  {msg.role === "assistant" &&
                    badgeConfiance(msg.scoreConfiance, msg.origineClassification)}
                </div>

                {msg.pdfUrl && (() => {
                  const isExcel = msg.pdfUrl
                    .toLowerCase()
                    .endsWith(".xlsx");

                  return (
                    <button
                      onClick={() => ouvrirDocument(msg.pdfUrl as string, isExcel)}
                      className="inline-flex items-center gap-2 mt-2 text-blue-400 hover:text-blue-300"
                    >
                      <FileText size={16} />

                      {isExcel
                        ? "Télécharger Excel"
                        : "Ouvrir PDF"}
                    </button>
                  );
                })()}
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="flex items-center gap-2 text-gray-400">
              <Loader2 className="animate-spin" />
              Copilot réfléchit...
            </div>
          )}

          {suggestionsLibres.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {suggestionsLibres.map((s) => (
                <button
                  key={s}
                  onClick={() => handleSuggestion(s)}
                  className="bg-gray-800 px-3 py-2 rounded-lg hover:bg-gray-700 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {boutonsPrincipaux.length > 0 && (
            <div className="bg-gray-800/60 border border-gray-700 rounded-xl p-4 space-y-3">
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-gray-400">
                <Sparkles size={13} />
                Validation requise
              </div>

              <div className="flex flex-wrap gap-3">
                {boutonsPrincipaux.map((btn) => {
                  const cle = btn.trim().toUpperCase();
                  const estConfirmer = cle === "CONFIRMER" || cle === "CONFIRM";
                  const estAnnuler = cle === "ANNULER";
                  const style = estConfirmer
                    ? "bg-emerald-600 hover:bg-emerald-500"
                    : estAnnuler
                    ? "bg-rose-600 hover:bg-rose-500"
                    : "bg-gray-700 hover:bg-gray-600";
                  return (
                    <button
                      key={btn}
                      onClick={() => handleSuggestion(btn)}
                      className={`${style} px-6 py-2.5 rounded-lg flex items-center gap-2 justify-center min-w-[140px] font-semibold text-white shadow-md hover:shadow-lg transition-all active:scale-95`}
                    >
                      {estConfirmer && <Check size={16} />}
                      {estAnnuler && <X size={16} />}
                      {btn}
                    </button>
                  );
                })}
              </div>

              {chipsSecondaires.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {chipsSecondaires.map((s) => (
                    <button
                      key={s}
                      onClick={() => handleSuggestion(s)}
                      className="bg-gray-900 border border-gray-700 px-3 py-1.5 rounded-lg text-sm hover:bg-gray-700 transition-colors"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}

              {draftStatus === "OFFRE_PRIX" && (
                <form
                  onSubmit={handleMontantSubmit}
                  className="flex items-center gap-2 pt-3 mt-1 border-t border-gray-700/60"
                >
                  <Coins size={16} className="text-gray-500 shrink-0" />
                  <input
                    type="text"
                    inputMode="decimal"
                    value={montantPerso}
                    onChange={(e) => setMontantPerso(e.target.value)}
                    placeholder="Ou saisissez un autre montant (TND)…"
                    className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm outline-none focus:border-emerald-600 transition-colors"
                  />
                  <button
                    type="submit"
                    disabled={!montantPerso.trim()}
                    className="bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed px-4 py-2 rounded-lg text-sm font-medium transition-all"
                  >
                    Valider
                  </button>
                </form>
              )}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {afficherCalendrier && (
          <form
            onSubmit={handleDateSubmit}
            className="border-t border-gray-800 p-4 flex gap-2"
          >
            <div className="flex flex-1 items-center gap-2 bg-gray-800 rounded-xl px-3 py-2 border border-gray-700">
              <Calendar size={18} className="text-emerald-400" />
              <input
                type="date"
                value={datePerso}
                onChange={(e) => setDatePerso(e.target.value)}
                className="flex-1 bg-transparent text-gray-100 outline-none"
              />
            </div>

            <button
              type="submit"
              disabled={!datePerso || isLoading}
              className="bg-emerald-600 px-5 rounded-xl disabled:opacity-50"
            >
              <Check size={18} />
            </button>
          </form>
        )}

        {!afficherCalendrier && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              sendMessage(input);
            }}
            className="border-t border-gray-800 p-4 flex gap-2"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                attenteComplements
                  ? "Répondez au complément..."
                  : "Votre message..."
              }
              className="flex-1 bg-gray-800 rounded-xl p-3 outline-none"
            />

            <button
              type="submit"
              disabled={!input.trim() || isLoading}
              className="bg-green-600 px-5 rounded-xl disabled:opacity-50"
            >
              <Send size={18} />
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export default App;
