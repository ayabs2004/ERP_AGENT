import { useState, useRef, useEffect } from "react";
import axios from "axios";
import { Send, Sparkles, Bell, Loader2, FileText, Check, X, Coins } from "lucide-react";
import { v4 as uuidv4 } from "uuid";
import ReactMarkdown from "react-markdown";

const SESSION_ID = localStorage.getItem("sessionId") || uuidv4();
localStorage.setItem("sessionId", SESSION_ID);

const API_BASE = "http://localhost:8000";

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

function App() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content:
        "Bonjour ! Je suis **Copilot ERP**, votre assistant intelligent pour Sage 100.\nComment puis-je vous aider aujourd'hui ?",
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

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, suggestions, isLoading, draftStatus, confirmationStatus]);

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
      const response = await axios.post<ChatResponse>(
        `${API_BASE}/api/chat`,
        {
          session_id: SESSION_ID,
          message: text,
        },
        {
          headers: {
            Authorization: `Bearer ${import.meta.env.VITE_API_TOKEN || 'your_api_token_here'}`,
          },
        }
      );

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
      </div>

      {/* MAIN */}
      <div className="flex-1 flex flex-col">

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
              <div className="max-w-[70%]">

                <div className="bg-gray-800 rounded-xl p-4">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                  {msg.role === "assistant" &&
                    badgeConfiance(msg.scoreConfiance, msg.origineClassification)}
                </div>

                {msg.pdfUrl && (() => {
                  const isExcel = msg.pdfUrl
                    .toLowerCase()
                    .endsWith(".xlsx");

                  return (
                    <a
                      href={`${API_BASE}${msg.pdfUrl}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      download={isExcel}
                      className="inline-flex items-center gap-2 mt-2 text-blue-400 hover:text-blue-300"
                    >
                      <FileText size={16} />

                      {isExcel
                        ? "Télécharger Excel"
                        : "Ouvrir PDF"}
                    </a>
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

        {/* INPUT */}
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
      </div>
    </div>
  );
}

export default App;