import { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Send, User, Loader2, Sparkles, FileText, Bell, LogOut, Download } from 'lucide-react';
import { v4 as uuidv4 } from 'uuid';
import ReactMarkdown from 'react-markdown';
import Login, { AuthInfo } from './Login';

const SESSION_ID = localStorage.getItem('sessionId') || uuidv4();
localStorage.setItem('sessionId', SESSION_ID);

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
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
}

function badgeConfiance(score?: number, origine?: string) {
  if (!origine || score === undefined) return null;
  const pct = Math.round(score * 100);
  const couleur =
    score >= 0.85
      ? 'text-green-400 border-green-700'
      : score >= 0.6
      ? 'text-yellow-400 border-yellow-700'
      : 'text-orange-400 border-orange-700';
  const libelleOrigine: Record<string, string> = {
    REGEX: 'règle exacte',
    SEMANTIQUE: 'sémantique',
    ARBITRAGE_LLM: 'LLM confirmé',
    LLM: 'LLM',
    FALLBACK: 'repli générique',
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
  const [auth, setAuth] = useState<AuthInfo | null>(() => {
    const saved = localStorage.getItem('auth');
    if (!saved) return null;
    try {
      return JSON.parse(saved);
    } catch {
      return null;
    }
  });

  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      role: 'assistant',
      content:
        "Bonjour ! Je suis **Copilot ERP**, votre assistant intelligent pour Sage 100.\nComment puis-je vous aider aujourd'hui ?",
    },
  ]);

  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [draftStatus, setDraftStatus] = useState('');
  const [confirmationStatus, setConfirmationStatus] = useState('');
  const [alerts, setAlerts] = useState<string[]>([]);
  const [attenteComplements, setAttenteComplements] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, suggestions, isLoading, draftStatus, confirmationStatus]);

  const handleLogin = (authInfo: AuthInfo) => {
    localStorage.setItem('auth', JSON.stringify(authInfo));
    setAuth(authInfo);
  };

  const handleLogout = () => {
    localStorage.removeItem('auth');
    setAuth(null);
  };

  const openOrDownloadFile = async (fileUrl: string) => {
    if (!auth?.token) return;
    try {
      const response = await axios.get(`${API_BASE}${fileUrl}`, {
        headers: { Authorization: `Bearer ${auth.token}` },
        responseType: 'blob',
      });
      const isExcel = fileUrl.toLowerCase().endsWith('.xlsx');
      const mimeType = isExcel
        ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        : 'application/pdf';
      const blob = new Blob([response.data], { type: mimeType });
      const blobUrl = URL.createObjectURL(blob);

      if (isExcel) {
        const link = document.createElement('a');
        link.href = blobUrl;
        link.download = fileUrl.split('/').pop() || 'export.xlsx';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      } else {
        window.open(blobUrl, '_blank');
      }
    } catch (err) {
      console.error('Erreur lors du téléchargement du fichier:', err);
      alert('Impossible d’ouvrir le fichier (session expirée ou accès non autorisé).');
    }
  };

  const sendMessage = async (text: string, displayText?: string) => {
    if (!text.trim() || !auth?.token) return;

    const userMessage: Message = {
      id: uuidv4(),
      role: 'user',
      content: displayText ?? text,
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
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
            Authorization: `Bearer ${auth.token}`,
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
      } = response.data;

      const assistantMessages: Message[] = responses.map((msg, index) => ({
        id: uuidv4(),
        role: 'assistant',
        content: msg,
        pdfUrl: index === responses.length - 1 ? pdf_url : null,
        scoreConfiance: index === responses.length - 1 ? score_confiance : undefined,
        origineClassification: index === responses.length - 1 ? origine_classification : undefined,
      }));

      setMessages((prev) => [...prev, ...assistantMessages]);
      setSuggestions(newSuggestions ?? []);
      setDraftStatus(draft_status ?? '');
      setConfirmationStatus(confirmation_status ?? '');
      setAlerts(newAlerts ?? []);
      setAttenteComplements(attente_complements);
    } catch (err) {
      console.error(err);
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        setMessages((prev) => [
          ...prev,
          {
            id: uuidv4(),
            role: 'system',
            content: '🔒 Session expirée. Veuillez vous re-connecter.',
          },
        ]);
        handleLogout();
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: uuidv4(),
            role: 'system',
            content: '❌ Une erreur est survenue lors de la communication avec le serveur.',
          },
        ]);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleSuggestion = (action: string) => sendMessage(action);
  const handleDraftConfirm = () => sendMessage('CONFIRM', '✅ Confirmer le document');
  const handleDraftCancel = () => sendMessage('ANNULER', '🛑 Annuler le document');

  if (!auth) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100 overflow-hidden font-sans">
      {/* SIDEBAR */}
      <div className="w-80 bg-gray-950/80 border-r border-gray-800 flex flex-col p-6 justify-between">
        <div>
          <div className="flex items-center gap-3 mb-8">
            <div className="bg-green-600 p-2 rounded-xl text-white">
              <Sparkles size={24} />
            </div>
            <h1 className="text-2xl font-bold">Copilot ERP</h1>
          </div>

          <div className="bg-gray-900 border border-gray-800 rounded-xl p-3 mb-6 flex items-center justify-between">
            <div className="flex items-center gap-2 overflow-hidden">
              <div className="bg-gray-800 p-2 rounded-lg text-gray-300">
                <User size={16} />
              </div>
              <div className="truncate">
                <p className="text-sm font-medium truncate">{auth.username}</p>
                <p className="text-xs text-gray-400 capitalize">{auth.role}</p>
              </div>
            </div>
            <button
              onClick={handleLogout}
              title="Se déconnecter"
              className="text-gray-400 hover:text-rose-400 p-2 rounded-lg hover:bg-gray-800 transition-colors"
            >
              <LogOut size={16} />
            </button>
          </div>

          <div className="text-sm text-gray-400 space-y-2">
            <p>✔ Consultation Clients</p>
            <p>✔ Consultation Articles</p>
            <p>✔ Génération Documents</p>
            <p>✔ Workflow Commandes</p>
          </div>

          <div className="mt-8 space-y-2">
            <button
              className="w-full bg-gray-800 hover:bg-gray-700 rounded-lg p-2 text-sm font-medium transition-colors"
              onClick={() => sendMessage('reset')}
            >
              Reset Session
            </button>

            <button
              className="w-full bg-gray-800 hover:bg-gray-700 rounded-lg p-2 text-sm font-medium transition-colors"
              onClick={() => sendMessage('aide')}
            >
              Aide
            </button>
          </div>
        </div>

        <div className="text-xs text-gray-500 text-center">
          Sage 100 ERP Agent v4
        </div>
      </div>

      {/* MAIN */}
      <div className="flex-1 flex flex-col">
        {/* ALERTS */}
        {alerts.length > 0 && (
          <div className="bg-amber-900/80 p-3 text-amber-200">
            <Bell size={16} className="inline mr-2" />
            {alerts.join(' • ')}
          </div>
        )}

        {/* CHAT MESSAGES */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div className="max-w-[70%]">
                <div className="bg-gray-800 rounded-xl p-4">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                  {msg.role === 'assistant' &&
                    badgeConfiance(msg.scoreConfiance, msg.origineClassification)}
                </div>

                {msg.pdfUrl && (() => {
                  const isExcel = msg.pdfUrl.toLowerCase().endsWith('.xlsx');
                  return (
                    <button
                      onClick={() => openOrDownloadFile(msg.pdfUrl!)}
                      className="inline-flex items-center gap-2 mt-2 text-sm text-blue-400 hover:text-blue-300 hover:underline"
                    >
                      {isExcel ? <Download size={16} /> : <FileText size={16} />}
                      {isExcel ? 'Télécharger Excel' : 'Ouvrir PDF'}
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

          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => handleSuggestion(s)}
                  className="bg-gray-800 hover:bg-gray-700 text-sm px-3 py-2 rounded-lg transition-colors border border-gray-700"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {(draftStatus === 'draft' || draftStatus === 'PREVIEW' || confirmationStatus === 'ATTENTE') && (
            <div className="flex gap-4 mt-2">
              <button
                onClick={handleDraftConfirm}
                className="bg-emerald-600 px-6 py-2.5 rounded-lg flex items-center justify-center min-w-[140px] font-semibold text-white shadow-md hover:bg-emerald-500 hover:shadow-lg transition-all active:scale-95"
              >
                Confirmer
              </button>

              <button
                onClick={handleDraftCancel}
                className="bg-rose-600 px-6 py-2.5 rounded-lg flex items-center justify-center min-w-[140px] font-semibold text-white shadow-md hover:bg-rose-500 hover:shadow-lg transition-all active:scale-95"
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
            placeholder={attenteComplements ? 'Répondez au complément...' : 'Votre message...'}
            className="flex-1 bg-gray-800 rounded-xl p-3 outline-none border border-transparent focus:border-green-600 transition-colors"
          />

          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="bg-green-600 hover:bg-green-500 disabled:opacity-50 px-5 rounded-xl transition-colors flex items-center justify-center"
          >
            <Send size={18} />
          </button>
        </form>
      </div>
    </div>
  );
}

export default App;