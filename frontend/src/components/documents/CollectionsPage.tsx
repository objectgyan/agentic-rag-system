import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { collectionsApi } from '../../services/api';
import { FolderOpen, Plus, Trash2, Lock, Users, Globe } from 'lucide-react';
import toast from 'react-hot-toast';

const visibilityIcons = { private: Lock, shared: Users, public: Globe };

export default function CollectionsPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState('shared');
  const [chunkStrategy, setChunkStrategy] = useState('semantic');
  const [enableGraph, setEnableGraph] = useState(false);
  const queryClient = useQueryClient();

  const { data: collections, isLoading } = useQuery({
    queryKey: ['collections'],
    queryFn: () => collectionsApi.list(),
  });

  const createMutation = useMutation({
    mutationFn: () => collectionsApi.create({ name, description, visibility, chunk_strategy: chunkStrategy, enable_graph: enableGraph }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['collections'] });
      setShowCreate(false);
      setName('');
      setDescription('');
      toast.success('Collection created');
    },
    onError: (err: any) => toast.error(err.response?.data?.detail || 'Failed to create'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => collectionsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['collections'] });
      toast.success('Collection deleted');
    },
  });

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Collections</h1>
          <p className="text-gray-500 mt-1">Organize your documents into searchable collections</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary flex items-center gap-2">
          <Plus size={16} /> New Collection
        </button>
      </div>

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="card p-6 w-full max-w-lg">
            <h2 className="text-lg font-semibold mb-4 text-gray-900 dark:text-white">New Collection</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Name</label>
                <input value={name} onChange={(e) => setName(e.target.value)} className="input-field" placeholder="My Knowledge Base" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Description</label>
                <textarea value={description} onChange={(e) => setDescription(e.target.value)} className="input-field" rows={2} placeholder="Optional description" />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Visibility</label>
                  <select value={visibility} onChange={(e) => setVisibility(e.target.value)} className="input-field">
                    <option value="private">Private (you only)</option>
                    <option value="shared">Shared (org-wide)</option>
                    <option value="public">Public (API access)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Chunking</label>
                  <select value={chunkStrategy} onChange={(e) => setChunkStrategy(e.target.value)} className="input-field">
                    <option value="semantic">Semantic</option>
                    <option value="fixed">Fixed Size</option>
                    <option value="recursive">Recursive</option>
                    <option value="paragraph">Paragraph</option>
                    <option value="parent_child">Parent-Child</option>
                  </select>
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                <input type="checkbox" checked={enableGraph} onChange={(e) => setEnableGraph(e.target.checked)} className="rounded" />
                Extract a knowledge graph during ingestion (enables graph-augmented answers)
              </label>
            </div>
            <div className="flex gap-3 mt-6 justify-end">
              <button onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
              <button onClick={() => createMutation.mutate()} disabled={!name.trim()} className="btn-primary">Create</button>
            </div>
          </div>
        </div>
      )}

      {/* Collections Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => <div key={i} className="card p-6 animate-pulse h-40" />)}
        </div>
      ) : collections?.data?.length === 0 ? (
        <div className="text-center py-16 card">
          <FolderOpen className="mx-auto mb-4 text-gray-300" size={48} />
          <p className="text-gray-500">No collections yet. Create one to start uploading documents.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {collections?.data?.map((c) => {
            const VisIcon = visibilityIcons[c.visibility as keyof typeof visibilityIcons] || Users;
            return (
              <Link
                key={c.id}
                to={`/collections/${c.id}`}
                className="card p-5 hover:border-primary-300 dark:hover:border-primary-700 transition-colors group"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-gray-900 dark:text-white group-hover:text-primary-600 truncate">{c.name}</h3>
                    {c.description && <p className="text-sm text-gray-500 mt-1 line-clamp-2">{c.description}</p>}
                  </div>
                  <button
                    onClick={(e) => { e.preventDefault(); if (confirm('Delete?')) deleteMutation.mutate(c.id); }}
                    className="p-1.5 text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
                <div className="flex items-center gap-3 mt-4 text-xs text-gray-500">
                  <span className="flex items-center gap-1"><VisIcon size={12} /> {c.visibility}</span>
                  <span>{c.document_count} docs</span>
                  <span>{c.chunk_strategy}</span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
