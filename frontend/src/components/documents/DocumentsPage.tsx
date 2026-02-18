import { useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useDropzone } from 'react-dropzone';
import { collectionsApi, documentsApi } from '../../services/api';
import { Upload, FileText, Trash2, Globe, CheckCircle, Clock, AlertCircle, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import clsx from 'clsx';
import { formatDistanceToNow } from 'date-fns';

const statusConfig = {
  pending: { icon: Clock, color: 'text-yellow-500', label: 'Pending' },
  processing: { icon: Loader2, color: 'text-blue-500', label: 'Processing' },
  completed: { icon: CheckCircle, color: 'text-green-500', label: 'Ready' },
  failed: { icon: AlertCircle, color: 'text-red-500', label: 'Failed' },
};

function formatSize(bytes: number | null) {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export default function DocumentsPage() {
  const { collectionId } = useParams<{ collectionId: string }>();
  const [urlInput, setUrlInput] = useState('');
  const [showUrl, setShowUrl] = useState(false);
  const queryClient = useQueryClient();

  const { data: collection } = useQuery({
    queryKey: ['collection', collectionId],
    queryFn: () => collectionsApi.get(collectionId!),
    enabled: !!collectionId,
  });

  const { data: documents, isLoading } = useQuery({
    queryKey: ['documents', collectionId],
    queryFn: () => documentsApi.list(collectionId),
    enabled: !!collectionId,
    refetchInterval: 5000, // Poll for status updates
  });

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => documentsApi.upload(collectionId!, files),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents', collectionId] });
      toast.success('Upload started');
    },
    onError: (err: any) => toast.error(err.response?.data?.detail || 'Upload failed'),
  });

  const urlMutation = useMutation({
    mutationFn: () => documentsApi.ingestUrl({ url: urlInput, collection_id: collectionId! }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents', collectionId] });
      setUrlInput('');
      setShowUrl(false);
      toast.success('URL ingestion started');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => documentsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents', collectionId] });
      toast.success('Document deleted');
    },
  });

  const onDrop = useCallback((files: File[]) => uploadMutation.mutate(files), [collectionId]);
  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop });

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{collection?.data?.name || 'Documents'}</h1>
        {collection?.data?.description && <p className="text-gray-500 mt-1">{collection.data.description}</p>}
      </div>

      {/* Upload Zone */}
      <div
        {...getRootProps()}
        className={clsx(
          'border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors mb-6',
          isDragActive
            ? 'border-primary-400 bg-primary-50 dark:bg-primary-900/10'
            : 'border-gray-300 dark:border-gray-700 hover:border-primary-300'
        )}
      >
        <input {...getInputProps()} />
        <Upload className="mx-auto mb-3 text-gray-400" size={36} />
        <p className="text-gray-600 dark:text-gray-400">
          {isDragActive ? 'Drop files here...' : 'Drag & drop files, or click to browse'}
        </p>
        <p className="text-xs text-gray-400 mt-2">
          PDF, DOCX, TXT, CSV, XLSX, HTML, Images, Audio, Video
        </p>
      </div>

      {/* URL Ingest */}
      <div className="mb-6">
        {showUrl ? (
          <div className="flex gap-2">
            <input
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              className="input-field flex-1"
              placeholder="https://example.com/document"
            />
            <button onClick={() => urlMutation.mutate()} disabled={!urlInput.trim()} className="btn-primary">Ingest</button>
            <button onClick={() => setShowUrl(false)} className="btn-secondary">Cancel</button>
          </div>
        ) : (
          <button onClick={() => setShowUrl(true)} className="btn-secondary flex items-center gap-2 text-sm">
            <Globe size={14} /> Ingest from URL
          </button>
        )}
      </div>

      {/* Document List */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <div key={i} className="card p-4 animate-pulse h-16" />)}
        </div>
      ) : documents?.data?.length === 0 ? (
        <div className="text-center py-16 card">
          <FileText className="mx-auto mb-4 text-gray-300" size={48} />
          <p className="text-gray-500">No documents yet. Upload files or ingest a URL above.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {documents?.data?.map((doc) => {
            const status = statusConfig[doc.status as keyof typeof statusConfig];
            const StatusIcon = status?.icon || Clock;
            return (
              <div key={doc.id} className="card px-5 py-3 flex items-center gap-4 group">
                <FileText size={18} className="text-gray-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm text-gray-900 dark:text-white truncate">{doc.original_filename}</p>
                  <div className="flex items-center gap-3 text-xs text-gray-500 mt-0.5">
                    <span>{doc.doc_type}</span>
                    <span>{formatSize(doc.file_size)}</span>
                    {doc.chunk_count > 0 && <span>{doc.chunk_count} chunks</span>}
                    <span>{formatDistanceToNow(new Date(doc.created_at), { addSuffix: true })}</span>
                  </div>
                </div>
                <div className={clsx('flex items-center gap-1.5 text-xs font-medium', status?.color)}>
                  <StatusIcon size={14} className={doc.status === 'processing' ? 'animate-spin' : ''} />
                  {status?.label}
                </div>
                <button
                  onClick={() => { if (confirm('Delete?')) deleteMutation.mutate(doc.id); }}
                  className="p-1.5 text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
