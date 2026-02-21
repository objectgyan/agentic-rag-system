import { useState, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { queryApi, collectionsApi } from '../../services/api';
import { Send, Sparkles, FileText, ChevronDown } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import type { Citation } from '../../types';
import clsx from 'clsx';

interface StreamMessage {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  isStreaming?: boolean;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<StreamMessage[]>([]);
  const [input, setInput] = useState('');
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [showCollections, setShowCollections] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: collections } = useQuery({ queryKey: ['collections'], queryFn: () => collectionsApi.list() });

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  // Check if citations should be shown based on answer content
  const shouldShowCitations = (content: string): boolean => {
    const lowerContent = content.toLowerCase();
    const noInfoPhrases = [
      'does not contain',
      'do not contain',
      'cannot provide',
      'no information',
      'not mentioned',
      'does not mention',
      'i don\'t have',
      'there is no information',
      'not found in the context',
      'context doesn\'t',
      'context does not',
    ];
    return !noInfoPhrases.some(phrase => lowerContent.includes(phrase));
  };

  const handleSend = async () => {
    if (!input.trim() || isStreaming) return;
    const query = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: query }]);
    setIsStreaming(true);

    try {
      const response = await queryApi.streamQuery({
        query,
        collection_ids: selectedCollections.length > 0 ? selectedCollections : undefined,
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

        const text = decoder.decode(value);
        for (const line of text.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') break;

          try {
            const parsed = JSON.parse(data);
            if (parsed.type === 'token') {
              assistantMsg += parsed.content;
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: 'assistant', content: assistantMsg, citations, isStreaming: true };
                return updated;
              });
            } else if (parsed.type === 'citations') {
              citations = parsed.citations;
            }
          } catch {}
        }
      }

      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: 'assistant', content: assistantMsg, citations, isStreaming: false };
        return updated;
      });
    } catch (err: any) {
      setMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${err.message}` }]);
    } finally {
      setIsStreaming(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Collection selector */}
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-dark-900">
        <div className="max-w-3xl mx-auto">
          <button
            onClick={() => setShowCollections(!showCollections)}
            className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900"
          >
            <FileText size={16} />
            {selectedCollections.length > 0 ? `${selectedCollections.length} collection(s) selected` : 'All collections'}
            <ChevronDown size={14} className={clsx('transition-transform', showCollections && 'rotate-180')} />
          </button>
          {showCollections && (
            <div className="mt-2 flex flex-wrap gap-2">
              {collections?.data?.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setSelectedCollections((prev) =>
                    prev.includes(c.id) ? prev.filter((id) => id !== c.id) : [...prev, c.id]
                  )}
                  className={clsx(
                    'px-3 py-1 rounded-full text-xs font-medium transition-colors',
                    selectedCollections.includes(c.id)
                      ? 'bg-primary-100 text-primary-700 dark:bg-primary-900 dark:text-primary-300'
                      : 'bg-gray-100 text-gray-600 dark:bg-dark-800 dark:text-gray-400'
                  )}
                >
                  {c.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-auto px-4 py-6">
        <div className="max-w-3xl mx-auto space-y-6">
          {messages.length === 0 && (
            <div className="text-center py-20">
              <Sparkles className="mx-auto mb-4 text-primary-400" size={48} />
              <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">Ask anything about your documents</h2>
              <p className="text-gray-500">Select collections and start chatting with your knowledge base.</p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={clsx('flex gap-3', msg.role === 'user' && 'justify-end')}>
              <div className={clsx(
                'max-w-[85%] rounded-2xl px-4 py-3',
                msg.role === 'user'
                  ? 'bg-primary-600 text-white'
                  : 'bg-gray-100 dark:bg-dark-800 text-gray-900 dark:text-gray-100'
              )}>
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                </div>
                {msg.isStreaming && <span className="inline-block w-2 h-4 bg-current animate-pulse ml-1" />}
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
