import { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Send, Bot, User, Loader2, Sparkles, AlertCircle, FileText, Bell, Loader } from 'lucide-react';
import { v4 as uuidv4 } from 'uuid';
import ReactMarkdown from 'react-markdown';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const SESSION_ID = localStorage.getItem('sessionId') || uuidv4();
localStorage.setItem('sessionId', SESSION_ID);

const API_BASE = 'http://localhost:8000';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  pdfUrl?: string | null;
}

interface ChatResponse {
  responses: string[];
  suggestions: string[];
  draft_status: string;
  pdf_url: string | null;
  alerts: string[];
  attente_complements: boolean;
}

function App() {
  const [messages, setMessages] = useState<Message[]>([{
    id: '1',
    role: 'assistant',
    content: "Bonjour ! Je suis **Copilot ERP**, votre assistant intelligent pour Sage 100.\nComment puis-je vous aider aujourd'hui ?"
  }]);

  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [draftStatus, setDraftStatus] = useState<string>('');
  const [alerts, setAlerts] = useState<string[]>([]);
  const [attenteComplements, setAttenteComplements] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, suggestions, isLoading, draftStatus]);

  const sendMessage = async (text: string, displayText?: string) => {
    if (!text.trim()) return;

    const userMessage: Message = {
      id: uuidv4(),
      role: 'user',
      content: displayText ?? text
    };

    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);
    setSuggestions([]);

    try {
      const response = await axios.post<ChatResponse>(`${API_BASE}/api/chat`, {
        session_id: SESSION_ID,
        message: text
      });

      const {
        responses,
        suggestions: newSuggestions,
        draft_status,
        pdf_url,
        alerts: newAlerts,
        attente_complements
      } = response.data;

      const newMessages: Message[] = responses.map((res, idx) => ({
        id: uuidv4(),
        role: 'assistant',
        content: res,
        pdfUrl: idx === responses.length - 1 ? pdf_url : null,
      }));

      setMessages(prev => [...prev, ...newMessages]);
      setSuggestions(newSuggestions ?? []);
      setDraftStatus(draft_status ?? '');
      setAlerts(newAlerts ?? []);
      setAttenteComplements(!!attente_complements);

    } catch (error) {
      console.error(error);
      setMessages(prev => [...prev, {
        id: uuidv4(),
        role: 'system',
        content: "❌ Une erreur de connexion avec le serveur est survenue."
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSuggestion = (action: string) => sendMessage(action);
  const handleDraftConfirm = () => sendMessage('CONFIRM', '✅ Confirmer le document');
  const handleDraftCancel = () => sendMessage('ANNULER', '🛑 Annuler le document');

  const lastAssistantMessage = [...messages].reverse().find(m => m.role === 'assistant');

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100 overflow-hidden font-sans">

      {/* SIDEBAR */}
      <div className="w-80 bg-gray-950/80 border-r border-gray-800 flex flex-col p-6">
        <div className="flex items-center gap-3 mb-10">
          <div className="bg-sage-600 p-2 rounded-xl text-white">
            <Sparkles size={24} />
          </div>
          <h1 className="text-2xl font-bold">Copilot ERP</h1>
        </div>

        <div className="text-sm text-gray-400 space-y-2">
          <p>✔ Consultation Clients</p>
          <p>✔ Consultation Articles</p>
          <p>✔ Génération Documents</p>
          <p>✔ Workflow Commandes</p>
        </div>

        <div className="mt-6 space-y-2">
          <button onClick={() => sendMessage("reset")} className="text-xs bg-gray-800 px-2 py-1 rounded">
            Reset Session
          </button>
          <button onClick={() => sendMessage("aide")} className="text-xs bg-gray-800 px-2 py-1 rounded">
            Aide
          </button>
        </div>
      </div>

      {/* MAIN */}
      <div className="flex-1 flex flex-col">

        {/* ALERTS */}
        {alerts.length > 0 && (
          <div className="bg-amber-900/80 p-3 text-amber-200">
            <Bell size={16} className="inline mr-2" />
            {alerts.join('\n')}
          </div>
        )}

        {/* MESSAGES */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.map(msg => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>

              <div className="max-w-[70%]">

                <div className="bg-gray-800 p-4 rounded-xl">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                </div>

                {/* PDF LINK FIXED */}
                {msg.pdfUrl && (
                  <a
                    href={`${API_BASE}${msg.pdfUrl}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block mt-2 text-sm text-blue-400"
                  >
                    <FileText size={14} className="inline mr-1" />
                    Ouvrir PDF
                  </a>
                )}

              </div>
            </div>
          ))}

          {isLoading && (
            <div className="text-gray-400 flex items-center gap-2">
              <Loader2 className="animate-spin" />
              Copilot réfléchit...
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
          className="p-4 border-t border-gray-800 flex gap-2"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="flex-1 bg-gray-900 p-3 rounded-xl"
            placeholder={attenteComplements ? "Répondre au champ..." : "Message..."}
          />

          <button
            disabled={!input.trim() || isLoading}
            className="bg-sage-600 px-4 rounded-xl"
          >
            <Send size={18} />
          </button>
        </form>

      </div>
    </div>
  );
}

export default App;