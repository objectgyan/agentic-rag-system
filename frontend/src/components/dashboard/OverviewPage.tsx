import { useQuery } from '@tanstack/react-query';
import { adminApi, collectionsApi } from '../../services/api';
import { useAuthStore } from '../../store/authStore';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import { FileText, MessageSquare, Zap, Database } from 'lucide-react';

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'];

export default function OverviewPage() {
  const user = useAuthStore((s) => s.user);
  const { data: usage } = useQuery({ queryKey: ['usage'], queryFn: () => adminApi.usage(), enabled: user?.role === 'admin' });
  const { data: collections } = useQuery({ queryKey: ['collections'], queryFn: () => collectionsApi.list() });

  const stats = [
    { label: 'Collections', value: collections?.data?.length || 0, icon: Database, color: 'text-blue-600' },
    { label: 'Documents', value: usage?.data?.total_documents || 0, icon: FileText, color: 'text-green-600' },
    { label: 'Queries', value: usage?.data?.total_queries || 0, icon: MessageSquare, color: 'text-amber-600' },
    { label: 'Tokens Used', value: usage?.data?.total_tokens?.toLocaleString() || '0', icon: Zap, color: 'text-red-600' },
  ];

  const chartData = collections?.data?.map((c) => ({ name: c.name.slice(0, 15), docs: c.document_count })) || [];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Dashboard</h1>
        <p className="text-gray-500 mt-1">Welcome back{user?.full_name ? `, ${user.full_name}` : ''}</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((s) => (
          <div key={s.label} className="card p-5">
            <div className="flex items-center gap-3">
              <div className={`${s.color} bg-opacity-10 p-2 rounded-lg`}>
                <s.icon size={20} className={s.color} />
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{s.value}</p>
                <p className="text-xs text-gray-500">{s.label}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className="grid lg:grid-cols-2 gap-6">
        <div className="card p-6">
          <h2 className="text-lg font-semibold mb-4 text-gray-900 dark:text-white">Documents by Collection</h2>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={chartData}>
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="docs" fill="#3b82f6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[250px] flex items-center justify-center text-gray-400">
              No collections yet. Create one to get started.
            </div>
          )}
        </div>

        <div className="card p-6">
          <h2 className="text-lg font-semibold mb-4 text-gray-900 dark:text-white">Collection Distribution</h2>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie data={chartData} dataKey="docs" nameKey="name" cx="50%" cy="50%" outerRadius={90} label>
                  {chartData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[250px] flex items-center justify-center text-gray-400">
              Upload documents to see distribution.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
