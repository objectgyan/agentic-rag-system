import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { authApi, adminApi } from '../../services/api';
import { useAuthStore } from '../../store/authStore';
import { Key, Users, Shield, Trash2, Check, UserPlus, Building2, Crown } from 'lucide-react';
import toast from 'react-hot-toast';
import clsx from 'clsx';

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const [tab, setTab] = useState<'api-keys' | 'users' | 'audit' | 'account'>('api-keys');
  const [newKeyName, setNewKeyName] = useState('');
  const [copiedKey, setCopiedKey] = useState('');
  const [showUserForm, setShowUserForm] = useState(false);
  const [newUser, setNewUser] = useState({ email: '', password: '', full_name: '', role: 'member' });
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

  const createUserMutation = useMutation({
    mutationFn: () => adminApi.createUser(newUser),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setNewUser({ email: '', password: '', full_name: '', role: 'member' });
      setShowUserForm(false);
      toast.success('User created successfully');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to create user');
    },
  });

  // Audit log
  const { data: auditLog } = useQuery({
    queryKey: ['audit-log'],
    queryFn: () => adminApi.auditLog(50),
    enabled: isAdmin && tab === 'audit',
  });

  // Tenant info
  const { data: tenant } = useQuery({
    queryKey: ['tenant'],
    queryFn: () => adminApi.tenant(),
    enabled: isAdmin && tab === 'account',
  });

  const updateTierMutation = useMutation({
    mutationFn: (tier: string) => adminApi.updateTier(tier),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenant'] });
      toast.success('Tier updated successfully');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to update tier');
    },
  });

  const tabs = [
    { key: 'api-keys' as const, label: 'API Keys', icon: Key },
    ...(isAdmin ? [
      { key: 'account' as const, label: 'Account', icon: Building2 },
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

      {/* Account */}
      {tab === 'account' && (
        <div className="space-y-6">
          {/* Tenant Info */}
          <div className="card p-6">
            <div className="flex items-center gap-3 mb-4">
              <Building2 size={24} className="text-gray-400" />
              <div>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-white">{tenant?.data?.name}</h2>
                <p className="text-sm text-gray-500">Organization ID: {tenant?.data?.id?.slice(0, 8)}...</p>
              </div>
            </div>
          </div>

          {/* Subscription/Tier */}
          <div className="card p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
              <Crown size={20} className="text-yellow-500" /> Subscription Plan
            </h3>
            
            <div className="grid grid-cols-3 gap-4 mb-6">
              {['free', 'pro', 'enterprise'].map((tier) => {
                const isCurrentTier = tenant?.data?.tier === tier;
                const tierInfo = {
                  free: { name: 'Free', price: '$0', color: 'gray', features: ['5 Collections', '100 Documents', 'Basic Support'] },
                  pro: { name: 'Pro', price: '$29', color: 'blue', features: ['50 Collections', '10K Documents', 'Priority Support', 'Advanced Analytics'] },
                  enterprise: { name: 'Enterprise', price: 'Custom', color: 'purple', features: ['Unlimited Collections', 'Unlimited Documents', '24/7 Support', 'Custom Integrations', 'SLA Guarantee'] },
                };
                const info = tierInfo[tier as keyof typeof tierInfo];

                return (
                  <div
                    key={tier}
                    className={clsx(
                      'relative border-2 rounded-lg p-4 transition-all',
                      isCurrentTier
                        ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/20'
                        : 'border-gray-200 dark:border-dark-700 hover:border-gray-300'
                    )}
                  >
                    {isCurrentTier && (
                      <div className="absolute top-2 right-2">
                        <span className="text-xs font-semibold px-2 py-1 rounded-full bg-primary-500 text-white">
                          Current
                        </span>
                      </div>
                    )}
                    <div className="mb-3">
                      <h4 className="text-lg font-bold text-gray-900 dark:text-white">{info.name}</h4>
                      <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
                        {info.price}
                        {info.price !== 'Custom' && <span className="text-sm font-normal text-gray-500">/mo</span>}
                      </p>
                    </div>
                    <ul className="space-y-2 mb-4">
                      {info.features.map((feature, idx) => (
                        <li key={idx} className="text-xs text-gray-600 dark:text-gray-400 flex items-start gap-1">
                          <Check size={14} className="text-green-500 flex-shrink-0 mt-0.5" />
                          <span>{feature}</span>
                        </li>
                      ))}
                    </ul>
                    {!isCurrentTier && (
                      <button
                        onClick={() => updateTierMutation.mutate(tier)}
                        disabled={updateTierMutation.isPending}
                        className={clsx(
                          'w-full py-2 px-4 rounded-md text-sm font-medium transition-colors',
                          tier === 'enterprise'
                            ? 'bg-purple-600 hover:bg-purple-700 text-white'
                            : 'btn-primary'
                        )}
                      >
                        {updateTierMutation.isPending ? 'Updating...' : tier === 'enterprise' ? 'Contact Sales' : 'Upgrade'}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-4 p-3 bg-gray-50 dark:bg-dark-800 rounded">
              <strong>Note:</strong> This is a demo implementation. In production, tier changes would typically go through a payment gateway and approval process.
            </div>
          </div>
        </div>
      )}

      {/* Users */}
      {tab === 'users' && (
        <div className="space-y-4">
          {/* Add User Button */}
          {!showUserForm && (
            <button
              onClick={() => setShowUserForm(true)}
              className="btn-primary flex items-center gap-2"
            >
              <UserPlus size={16} /> Add User
            </button>
          )}

          {/* User Creation Form */}
          {showUserForm && (
            <div className="card p-4 space-y-3">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Create New User</h3>
                <button
                  onClick={() => {
                    setShowUserForm(false);
                    setNewUser({ email: '', password: '', full_name: '', role: 'member' });
                  }}
                  className="text-xs text-gray-500 hover:text-gray-700"
                >
                  Cancel
                </button>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <input
                  type="email"
                  value={newUser.email}
                  onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
                  className="input-field"
                  placeholder="Email"
                  required
                />
                <input
                  type="text"
                  value={newUser.full_name}
                  onChange={(e) => setNewUser({ ...newUser, full_name: e.target.value })}
                  className="input-field"
                  placeholder="Full Name (optional)"
                />
                <input
                  type="password"
                  value={newUser.password}
                  onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                  className="input-field"
                  placeholder="Password"
                  required
                />
                <select
                  value={newUser.role}
                  onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
                  className="input-field"
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <button
                onClick={() => createUserMutation.mutate()}
                disabled={!newUser.email || !newUser.password || createUserMutation.isPending}
                className="btn-primary w-full"
              >
                {createUserMutation.isPending ? 'Creating...' : 'Create User'}
              </button>
            </div>
          )}

          {/* Users List */}
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
                  u.role === 'admin' ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400' :
                  u.role === 'member' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400'
                )}>
                  {u.role}
                </span>
              </div>
            ))}
          </div>
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
