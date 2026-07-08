import { useState, useRef, useEffect } from "react";
import axios from "axios";
import { Send, Sparkles, Bell, Loader2, FileText } from "lucide-react";
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
  pdf_url: string | null;
  alerts: string[];
  attente_complements: boolean;
  score_confiance: number;
  origine_classification: string;
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
  const [draftStatus, setDraftStatus] = useState("");
  const [alerts, setAlerts] = useState<string[]>([]);
  const [attenteComplements, setAttenteComplements] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, suggestions, isLoading, draftStatus]);

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
        }
      );

      const {
        responses,
        suggestions: newSuggestions,
        draft_status,
        pdf_url,
        alerts: newAlerts,
        attente_complements,
        score_confiance,
        origine_classification,
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
      setDraftStatus(draft_status ?? "");
      setAlerts(newAlerts ?? []);
      setAttenteComplements(attente_complements);
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

  const handleSuggestion = (action: string) => sendMessage(action);

  const handleDraftConfirm = () =>
    sendMessage("CONFIRM", "✅ Confirmer le document");

  const handleDraftCancel = () =>
    sendMessage("ANNULER", "🛑 Annuler le document");

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

          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => handleSuggestion(s)}
                  className="bg-gray-800 px-3 py-2 rounded-lg hover:bg-gray-700"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {draftStatus === "draft" && (
            <div className="flex gap-3">
              <button
                onClick={handleDraftConfirm}
                className="bg-green-600 px-4 py-2 rounded"
              >
                Confirmer
              </button>

              <button
                onClick={handleDraftCancel}
                className="bg-red-600 px-4 py-2 rounded"
              >
                Annuler
              </button>
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