import axios from 'axios';
import type { TokenResponse, User, Collection, Document, QueryResponse, Conversation, ChatMessage, UsageStats } from '../types';

const API_URL = import.meta.env.VITE_API_URL || '';

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
});

// Interceptor to attach JWT
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Interceptor to handle token refresh
api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retry) {
      error.config._retry = true;
      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken) {
        try {
          const { data } = await axios.post(`${API_URL}/api/v1/auth/refresh`, { refresh_token: refreshToken });
          localStorage.setItem('access_token', data.access_token);
          localStorage.setItem('refresh_token', data.refresh_token);
          error.config.headers.Authorization = `Bearer ${data.access_token}`;
          return api(error.config);
        } catch {
          localStorage.clear();
          window.location.href = '/login';
        }
      }
    }
    return Promise.reject(error);
  }
);

// Auth
export const authApi = {
  register: (data: { email: string; password: string; full_name?: string; org_name: string }) =>
    api.post<TokenResponse>('/auth/register', data),
  login: (data: { email: string; password: string }) =>
    api.post<TokenResponse>('/auth/login', data),
  me: () => api.get<User>('/auth/me'),
  createApiKey: (name: string) => api.post('/auth/api-keys', { name }),
  listApiKeys: () => api.get('/auth/api-keys'),
  revokeApiKey: (id: string) => api.delete(`/auth/api-keys/${id}`),
};

// Collections
export const collectionsApi = {
  list: () => api.get<Collection[]>('/collections'),
  get: (id: string) => api.get<Collection>(`/collections/${id}`),
  create: (data: { name: string; description?: string; visibility?: string; chunk_strategy?: string }) =>
    api.post<Collection>('/collections', data),
  update: (id: string, data: Partial<Collection>) => api.patch<Collection>(`/collections/${id}`, data),
  delete: (id: string) => api.delete(`/collections/${id}`),
};

// Documents
export const documentsApi = {
  list: (collectionId?: string) => api.get<Document[]>('/documents', { params: { collection_id: collectionId } }),
  get: (id: string) => api.get<Document>(`/documents/${id}`),
  upload: (collectionId: string, files: File[]) => {
    const form = new FormData();
    form.append('collection_id', collectionId);
    files.forEach((f) => form.append('files', f));
    return api.post<Document[]>('/documents/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  ingestUrl: (data: { url: string; collection_id: string }) =>
    api.post<Document>('/documents/url', data),
  delete: (id: string) => api.delete(`/documents/${id}`),
};

// Query
export const queryApi = {
  query: (data: { query: string; collection_ids?: string[]; model?: string; top_k?: number }) =>
    api.post<QueryResponse>('/query', data),
  streamQuery: (data: { query: string; collection_ids?: string[] }) => {
    const token = localStorage.getItem('access_token');
    return fetch(`${API_URL}/api/v1/query/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(data),
    });
  },
};

// Conversations
export const chatApi = {
  listConversations: () => api.get<Conversation[]>('/query/conversations'),
  createConversation: (data: { collection_id?: string; title?: string }) =>
    api.post<Conversation>('/query/conversations', data),
  getMessages: (id: string, limit?: number) =>
    api.get<ChatMessage[]>(`/query/conversations/${id}/messages`, { params: { limit } }),
};

// Agents
export const agentsApi = {
  execute: (data: { task: string; agent_type?: string; collection_ids?: string[] }) =>
    api.post('/agents/execute', data),
  types: () => api.get('/agents/types'),
};

// Admin
export const adminApi = {
  usage: (period?: string) => api.get<UsageStats>('/admin/usage', { params: { period } }),
  users: () => api.get('/admin/users'),
  updateUser: (id: string, data: { role?: string; is_active?: boolean }) =>
    api.patch(`/admin/users/${id}`, data),
  updateTier: (tier: string) => api.patch('/admin/tier', { tier }),
  auditLog: (limit?: number) => api.get('/admin/audit-log', { params: { limit } }),
};

export default api;
