import { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Send, Bot, User, Loader2, Sparkles, AlertCircle } from 'lucide-react';
import { v4 as uuidv4 } from 'uuid';
import ReactMarkdown from 'react-markdown';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Generate a session ID for the user
const SESSION_ID = localStorage.getItem('sessionId') || uuidv4();
localStorage.setItem('sessionId', SESSION_ID);

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  isSuggestion?: boolean;
}

interface ChatResponse {
  responses: string[];
  suggestions: string[];
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
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, suggestions, isLoading]);

  const sendMessage = async (text: string) => {
    if (!text.trim()) return;

    const userMessage: Message = { id: uuidv4(), role: 'user', content: text };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);
    setSuggestions([]);

    try {
      const response = await axios.post<ChatResponse>('http://localhost:8000/api/chat', {
        session_id: SESSION_ID,
        message: text
      });

      const { responses, suggestions: newSuggestions } = response.data;
      
      const newMessages = responses.map(res => ({
        id: uuidv4(),
        role: 'assistant' as const,
        content: res
      }));
      
      setMessages(prev => [...prev, ...newMessages]);
      if (newSuggestions && newSuggestions.length > 0) {
        setSuggestions(newSuggestions);
      }
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

  const handleSuggestion = (action: string) => {
    sendMessage(action);
  };

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100 overflow-hidden font-sans">
      {/* Sidebar */}
      <div className="w-80 bg-gray-950/80 border-r border-gray-800 flex flex-col p-6 shadow-2xl z-10 backdrop-blur-xl">
        <div className="flex items-center gap-3 mb-10">
          <div className="bg-sage-600 p-2 rounded-xl text-white shadow-[0_0_15px_rgba(47,131,95,0.5)]">
            <Sparkles size={24} />
          </div>
          <h1 className="text-2xl font-bold bg-gradient-to-r from-sage-100 to-sage-500 bg-clip-text text-transparent">
            Copilot ERP
          </h1>
        </div>

        <div className="flex-1 space-y-6">
          <div className="space-y-3">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Capacités</h2>
            <ul className="text-sm text-gray-400 space-y-2">
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-sage-500"></span> Consultation Clients</li>
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-sage-500"></span> Consultation Articles</li>
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-sage-500"></span> Génération de Documents</li>
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-sage-500"></span> Workflow de Commandes</li>
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-sage-500"></span> Statistiques Avancées</li>
            </ul>
          </div>
          <div className="space-y-3">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Raccourcis utiles</h2>
             <div className="flex flex-wrap gap-2">
               <button onClick={() => sendMessage("reset")} className="text-xs px-2 py-1 bg-gray-800 rounded border border-gray-700 hover:border-gray-500 transition-colors">🔄 Reset Session</button>
               <button onClick={() => sendMessage("aide")} className="text-xs px-2 py-1 bg-gray-800 rounded border border-gray-700 hover:border-gray-500 transition-colors">ℹ️ Aide</button>
             </div>
          </div>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col relative bg-[url('https://www.transparenttextures.com/patterns/cubes.png')] bg-opacity-5">
        <div className="absolute inset-0 bg-gradient-to-b from-gray-900/40 via-gray-900/10 to-gray-900 pointer-events-none"></div>
        
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-8 space-y-8 scroll-smooth z-0 scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent">
          {messages.map((msg) => (
            <div key={msg.id} className={cn("flex max-w-4xl mx-auto gap-4", msg.role === 'user' ? "flex-row-reverse" : "flex-row")}>
              {/* Avatar */}
              <div className={cn(
                "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-lg",
                msg.role === 'user' ? "bg-blue-600 text-white" : 
                msg.role === 'assistant' ? "bg-sage-600 text-white" : "bg-red-500/20 text-red-500"
              )}>
                {msg.role === 'user' ? <User size={20} /> : 
                 msg.role === 'assistant' ? <Bot size={20} /> : <AlertCircle size={20} />}
              </div>

              {/* Message Bubble */}
              <div className={cn(
                "px-6 py-4 rounded-2xl shadow-sm text-sm/relaxed backdrop-blur-sm max-w-[80%]",
                msg.role === 'user' ? "bg-blue-600/90 text-white rounded-tr-sm" : 
                msg.role === 'assistant' ? "glassmorphism text-gray-200 rounded-tl-sm border-gray-700/50" : 
                "bg-red-500/10 border border-red-500/30 text-red-400 rounded-tl-sm"
              )}>
                <div className="prose prose-invert max-w-none prose-p:leading-relaxed prose-pre:bg-gray-950 prose-pre:border prose-pre:border-gray-800 prose-pre:shadow-inner">
                  <ReactMarkdown 
                    components={{
                      table: ({node, ...props}) => <div className="overflow-x-auto"><table className="border-collapse table-auto w-full text-sm" {...props} /></div>,
                      th: ({node, ...props}) => <th className="border-b border-gray-700 font-medium p-4 pl-8 pt-0 pb-3 text-left" {...props} />,
                      td: ({node, ...props}) => <td className="border-b border-gray-800 p-4 pl-8" {...props} />
                    }}
                  >
                    {msg.content}
                  </ReactMarkdown>
                </div>
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="flex max-w-4xl mx-auto gap-4">
               <div className="w-10 h-10 rounded-xl bg-sage-600 text-white flex items-center justify-center shrink-0 shadow-lg">
                <Bot size={20} />
              </div>
              <div className="glassmorphism px-6 py-4 rounded-2xl rounded-tl-sm flex items-center gap-3 text-gray-400 text-sm">
                <Loader2 size={16} className="animate-spin text-sage-500" />
                Copilot réfléchit...
              </div>
            </div>
          )}

          {suggestions.length > 0 && !isLoading && (
            <div className="flex max-w-4xl mx-auto gap-4 mt-2">
              <div className="w-10 h-10 shrink-0"></div>
              <div className="flex flex-col gap-2">
                {suggestions.map((sugg, idx) => (
                  <div key={idx} className="text-sm text-gray-400 italic mb-2">💡 {sugg}</div>
                ))}
                <div className="flex gap-3">
                  <button onClick={() => handleSuggestion("oui")} className="px-5 py-2 bg-sage-600 hover:bg-sage-500 text-white rounded-lg shadow-lg shadow-sage-900/20 text-sm font-medium transition-all hover:-translate-y-0.5 active:translate-y-0">
                    ✅ Confirmer (Oui)
                  </button>
                  <button onClick={() => handleSuggestion("non")} className="px-5 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg border border-gray-700 shadow-lg text-sm font-medium transition-all hover:-translate-y-0.5 active:translate-y-0">
                    ❌ Annuler (Non)
                  </button>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="p-6 bg-gray-950/50 backdrop-blur-md border-t border-gray-800 z-10">
          <form 
            onSubmit={(e) => { e.preventDefault(); sendMessage(input); }}
            className="max-w-4xl mx-auto relative flex items-center"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Posez votre question à l'ERP (ex: Quelles sont les factures impayées ?)"
              className="w-full bg-gray-900/80 border border-gray-700/80 text-gray-100 rounded-2xl pl-6 pr-14 py-4 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500 shadow-inner placeholder:text-gray-500 transition-all"
              disabled={isLoading}
            />
            <button
              type="submit"
              disabled={!input.trim() || isLoading}
              className="absolute right-3 p-2 bg-sage-600 hover:bg-sage-500 disabled:bg-gray-800 disabled:text-gray-500 text-white rounded-xl transition-colors shadow-lg"
            >
              <Send size={18} />
            </button>
          </form>
          <div className="max-w-4xl mx-auto mt-3 text-center text-xs text-gray-600">
            Copilot ERP peut faire des erreurs. Vérifiez les actions sensibles dans Sage.
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
