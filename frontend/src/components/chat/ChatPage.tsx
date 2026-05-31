import { useState, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { queryApi, collectionsApi, chatApi } from '../../services/api';
import { Send, Sparkles, FileText, ChevronDown, SlidersHorizontal, Network, GitBranch } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import type { Citation, QueryOptions } from '../../types';
import clsx from 'clsx';

interface ChatMsg {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  isStreaming?: boolean;
  graphFacts?: string[];
  hops?: string[];
  degraded?: string[];
  evaluation?: Record<string, number> | null;
}

// Toggles that require the full (non-streaming) pipeline; the streaming path only
// supports basic retrieval + conversation memory.
const ADVANCED_KEYS: (keyof QueryOptions)[] = [
  'use_hyde', 'use_multi_query', 'use_compression', 'use_multi_hop', 'use_graph', 'evaluate',
];

const TOGGLES: { key: keyof QueryOptions; label: string }[] = [
  { key: 'use_reranking', label: 'Rerank' },
  { key: 'use_hyde', label: 'HyDE' },
  { key: 'use_multi_query', label: 'Multi-query' },
  { key: 'use_compression', label: 'Compression' },
  { key: 'use_multi_hop', label: 'Multi-hop' },
  { key: 'use_graph', label: 'Knowledge graph' },
  { key: 'evaluate', label: 'Evaluate' },
];

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [showCollections, setShowCollections] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const [options, setOptions] = useState<QueryOptions>({ use_reranking: true });
  const [conversationId, setConversationId] = useState<string | undefined>();
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: collections } = useQuery({ queryKey: ['collections'], queryFn: () => collectionsApi.list() });

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const toggle = (k: keyof QueryOptions) => setOptions((o) => ({ ...o, [k]: !o[k] }));

  const shouldShowCitations = (content: string): boolean => {
    const lc = content.toLowerCase();
    const noInfo = ['does not contain', 'do not contain', 'cannot provide', 'no information',
      'not mentioned', 'does not mention', "i don't have", 'there is no information',
      'not found in the context', "context doesn't", 'context does not'];
    return !noInfo.some((p) => lc.includes(p));
  };

  const ensureConversation = async (title: string): Promise<string | undefined> => {
    if (conversationId) return conversationId;
    try {
      const { data } = await chatApi.createConversation({ title: title.slice(0, 60) });
      setConversationId(data.id);
      return data.id;
    } catch {
      return undefined; // proceed without memory if it fails
    }
  };

  const handleSend = async () => {
    if (!input.trim() || isStreaming) return;
    const query = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: query }]);
    setIsStreaming(true);

    const convId = await ensureConversation(query);
    const collection_ids = selectedCollections.length > 0 ? selectedCollections : undefined;
    const useAdvanced = ADVANCED_KEYS.some((k) => options[k]);

    try {
      if (useAdvanced) {
        // Full pipeline (non-streaming): supports graph / multi-hop / compression / eval.
        const { data } = await queryApi.query({ query, collection_ids, conversation_id: convId, ...options });
        setMessages((prev) => [...prev, {
          role: 'assistant', content: data.answer, citations: data.citations,
          graphFacts: data.graph_facts, hops: data.hops, degraded: data.degraded, evaluation: data.evaluation,
        }]);
      } else {
        // Streaming path (now stateful via conversation_id).
        const response = await queryApi.streamQuery({
          query, collection_ids, conversation_id: convId, use_reranking: options.use_reranking,
        });
        const reader = response.body?.getReader();
        if (!reader) throw new Error('No reader');

        let assistantMsg = '';
        let citations: Citation[] = [];
        setMessages((prev) => [...prev, { role: 'assistant', content: '', isStreaming: true }]);
        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const line of decoder.decode(value).split('\n')) {
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6);
            if (data === '[DONE]') break;
            try {
              const parsed = JSON.parse(data);
              if (parsed.type === 'token') {
                assistantMsg += parsed.content;
                setMessages((prev) => {
                  const u = [...prev];
                  u[u.length - 1] = { role: 'assistant', content: assistantMsg, citations, isStreaming: true };
                  return u;
                });
              } else if (parsed.type === 'citations') {
                citations = parsed.citations;
              }
            } catch { /* ignore partial frames */ }
          }
        }
        setMessages((prev) => {
          const u = [...prev];
          u[u.length - 1] = { role: 'assistant', content: assistantMsg, citations, isStreaming: false };
          return u;
        });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'request failed';
      setMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${msg}` }]);
    } finally {
      setIsStreaming(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Controls */}
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-dark-900">
        <div className="max-w-3xl mx-auto flex items-center gap-4">
          <button
            onClick={() => setShowCollections(!showCollections)}
            className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900"
          >
            <FileText size={16} />
            {selectedCollections.length > 0 ? `${selectedCollections.length} collection(s)` : 'All collections'}
            <ChevronDown size={14} className={clsx('transition-transform', showCollections && 'rotate-180')} />
          </button>
          <button
            onClick={() => setShowOptions(!showOptions)}
            className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900"
          >
            <SlidersHorizontal size={16} /> Retrieval options
            <ChevronDown size={14} className={clsx('transition-transform', showOptions && 'rotate-180')} />
          </button>
          {conversationId && <span className="text-xs text-green-600 dark:text-green-400">memory on</span>}
        </div>

        {showCollections && (
          <div className="max-w-3xl mx-auto mt-2 flex flex-wrap gap-2">
            {collections?.data?.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelectedCollections((prev) =>
                  prev.includes(c.id) ? prev.filter((id) => id !== c.id) : [...prev, c.id])}
                className={clsx('px-3 py-1 rounded-full text-xs font-medium transition-colors',
                  selectedCollections.includes(c.id)
                    ? 'bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300'
                    : 'bg-gray-100 text-gray-600 dark:bg-dark-800 dark:text-gray-400')}
              >
                {c.name}{c.enable_graph ? ' 🕸' : ''}
              </button>
            ))}
          </div>
        )}

        {showOptions && (
          <div className="max-w-3xl mx-auto mt-2 flex flex-wrap gap-2">
            {TOGGLES.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => toggle(key)}
                className={clsx('px-3 py-1 rounded-full text-xs font-medium transition-colors',
                  options[key]
                    ? 'bg-primary-600 text-white'
                    : 'bg-gray-100 text-gray-600 dark:bg-dark-800 dark:text-gray-400')}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-auto px-4 py-6">
        <div className="max-w-3xl mx-auto space-y-6">
          {messages.length === 0 && (
            <div className="text-center py-20">
              <Sparkles className="mx-auto mb-4 text-primary-400" size={48} />
              <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">Ask anything about your documents</h2>
              <p className="text-gray-500">Select collections, tune retrieval options, and chat with memory.</p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={clsx('flex gap-3', msg.role === 'user' && 'justify-end')}>
              <div className={clsx('max-w-[85%] rounded-2xl px-4 py-3',
                msg.role === 'user'
                  ? 'bg-primary-600 text-white'
                  : 'bg-gray-100 dark:bg-dark-800 text-gray-900 dark:text-gray-100')}>
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                </div>
                {msg.isStreaming && <span className="inline-block w-2 h-4 bg-current animate-pulse ml-1" />}

                {msg.graphFacts && msg.graphFacts.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                    <p className="text-xs font-medium mb-1 opacity-70 flex items-center gap-1"><Network size={12} /> Knowledge graph</p>
                    <ul className="text-xs opacity-80 list-disc list-inside">
                      {msg.graphFacts.map((f, j) => <li key={j}>{f}</li>)}
                    </ul>
                  </div>
                )}

                {msg.hops && msg.hops.length > 0 && (
                  <div className="mt-2 text-xs opacity-70 flex items-start gap-1">
                    <GitBranch size={12} className="mt-0.5" /> Follow-ups: {msg.hops.join(' · ')}
                  </div>
                )}

                {msg.evaluation && (
                  <div className="mt-2 text-xs opacity-70">
                    Eval: {Object.entries(msg.evaluation).map(([k, v]) => `${k} ${v.toFixed(2)}`).join(' · ')}
                  </div>
                )}

                {msg.degraded && msg.degraded.length > 0 && (
                  <div className="mt-2 text-xs text-amber-600 dark:text-amber-400">degraded: {msg.degraded.join(', ')}</div>
                )}

                {msg.citations && msg.citations.length > 0 && shouldShowCitations(msg.content) && (
                  <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
                    <p className="text-xs font-medium mb-1 opacity-70">Sources:</p>
                    <div className="flex flex-wrap gap-1">
                      {msg.citations.map((c, j) => (
                        <span key={j} className="text-xs bg-white/20 dark:bg-black/20 rounded px-2 py-0.5">
                          {c.document_name}{c.page_number ? ` p.${c.page_number}` : ''}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input */}
      <div className="px-4 py-4 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-dark-900">
        <div className="max-w-3xl mx-auto flex gap-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
            placeholder="Ask a question..."
            className="input-field flex-1"
            disabled={isStreaming}
          />
          <button onClick={handleSend} disabled={isStreaming || !input.trim()} className="btn-primary px-4">
            <Send size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
