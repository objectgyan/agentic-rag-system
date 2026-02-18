export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: 'admin' | 'member' | 'viewer';
  tenant_id: string;
  avatar_url: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Collection {
  id: string;
  name: string;
  description: string | null;
  visibility: 'private' | 'shared' | 'public';
  embedding_model: string | null;
  chunk_strategy: string;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface Document {
  id: string;
  collection_id: string;
  filename: string;
  original_filename: string;
  doc_type: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  file_size: number | null;
  page_count: number | null;
  chunk_count: number;
  error_message: string | null;
  source_url: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface Citation {
  document_id: string;
  document_name: string;
  chunk_id: string;
  content: string;
  page_number: number | null;
  score: number;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  model_used: string;
  tokens_used: number | null;
  retrieval_time_ms: number;
  generation_time_ms: number;
}

export interface Conversation {
  id: string;
  title: string | null;
  collection_id: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  citations?: Citation[];
  created_at?: string;
}

export interface AgentStep {
  step_number: number;
  thought: string;
  action: string | null;
  action_input: Record<string, unknown> | null;
  observation: string | null;
}

export interface UsageStats {
  period: string;
  total_queries: number;
  total_documents: number;
  total_tokens: number;
  storage_used_mb: number;
  cost_usd: number;
}
