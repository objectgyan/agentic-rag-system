import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { authApi, adminApi } from '../../services/api';
import { useAuthStore } from '../../store/authStore';
import { Key, Users, Shield, Trash2, Check } from 'lucide-react';
import toast from 'react-hot-toast';
import clsx from 'clsx';

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const [tab, setTab] = useState<'api-keys' | 'users' | 'audit'>('api-keys');
  const [newKeyName, setNewKeyName] = useState('');
  const [copiedKey, setCopiedKey] = useState('');
  const queryClient = useQueryClient();

  // API Keys
  const { data: apiKeys } = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => authApi.listApiKeys(),
    enabled: isAdmin,
  });

  const createKeyMutation = useMutation({
    mutationFn: () => authApi.createApiKey(newKeyName),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      setNewKeyName('');
      navigator.clipboard.writeText(res.data.key);
      setCopiedKey(res.data.key);
      toast.success('API key created and copied!');
    },
  });

  const revokeKeyMutation = useMutation({
    mutationFn: (id: string) => authApi.revokeApiKey(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      toast.success('API key revoked');
    },
  });

  // Users
  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: () => adminApi.users(),
    enabled: isAdmin && tab === 'users',
  });

  // Audit log
  const { data: auditLog } = useQuery({
    queryKey: ['audit-log'],
    queryFn: () => adminApi.auditLog(50),
    enabled: isAdmin && tab === 'audit',
  });

  const tabs = [
    { key: 'api-keys' as const, label: 'API Keys', icon: Key },
    ...(isAdmin ? [
      { key: 'users' as const, label: 'Users', icon: Users },
      { key: 'audit' as const, label: 'Audit Log', icon: Shield },
    ] : []),
  ];

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-6">Settings</h1>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-gray-100 dark:bg-dark-800 rounded-lg p-1 w-fit">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              'flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors',
              tab === t.key
                ? 'bg-white dark:bg-dark-900 text-gray-900 dark:text-white shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            )}
          >
            <t.icon size={16} /> {t.label}
          </button>
        ))}
      </div>

      {/* API Keys */}
      {tab === 'api-keys' && (
        <div className="space-y-4">
          <div className="flex gap-2">
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              className="input-field flex-1"
              placeholder="Key name (e.g., Production API)"
            />
            <button
              onClick={() => createKeyMutation.mutate()}
              disabled={!newKeyName.trim()}
              className="btn-primary"
            >
              Create Key
            </button>
          </div>

          {copiedKey && (
            <div className="card p-4 bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800">
              <div className="flex items-center gap-2 text-green-700 dark:text-green-400 text-sm mb-1">
                <Check size={16} /> Key created! Copy it now — you won't see it again.
              </div>
              <code className="text-xs bg-white dark:bg-dark-900 p-2 rounded block break-all">{copiedKey}</code>
            </div>
          )}

          <div className="space-y-2">
            {apiKeys?.data?.map((k: any) => (
              <div key={k.id} className="card px-5 py-3 flex items-center gap-4 group">
                <Key size={16} className="text-gray-400" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-900 dark:text-white">{k.name}</p>
                  <p className="text-xs text-gray-500">{k.key_prefix}••• · Created {new Date(k.created_at).toLocaleDateString()}</p>
                </div>
                <span className={clsx('text-xs font-medium', k.is_active ? 'text-green-600' : 'text-red-600')}>
                  {k.is_active ? 'Active' : 'Revoked'}
                </span>
                {k.is_active && (
                  <button
                    onClick={() => revokeKeyMutation.mutate(k.id)}
                    className="p-1.5 text-gray-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Users */}
      {tab === 'users' && (
        <div className="space-y-2">
          {users?.data?.map((u: any) => (
            <div key={u.id} className="card px-5 py-3 flex items-center gap-4">
              <div className="w-8 h-8 rounded-full bg-primary-100 dark:bg-primary-900 flex items-center justify-center text-sm font-medium text-primary-700">
                {u.full_name?.[0] || u.email[0].toUpperCase()}
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-900 dark:text-white">{u.full_name || u.email}</p>
                <p className="text-xs text-gray-500">{u.email}</p>
              </div>
              <span className={clsx(
                'text-xs font-medium px-2 py-1 rounded-full',
                u.role === 'admin' ? 'bg-purple-100 text-purple-700' :
                u.role === 'member' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-700'
              )}>
                {u.role}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Audit Log */}
      {tab === 'audit' && (
        <div className="space-y-1">
          {auditLog?.data?.map((entry: any) => (
            <div key={entry.id} className="card px-4 py-2.5 flex items-center gap-4 text-sm">
              <span className="text-xs text-gray-400 w-32 flex-shrink-0">
                {new Date(entry.created_at).toLocaleString()}
              </span>
              <span className="font-medium text-gray-900 dark:text-white">{entry.action}</span>
              {entry.resource_type && (
                <span className="text-gray-500">{entry.resource_type} {entry.resource_id?.slice(0, 8)}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
