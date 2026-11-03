import { lazy, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from './store/authStore';
import LoginPage from './components/auth/LoginPage';
import RegisterPage from './components/auth/RegisterPage';
import DashboardLayout from './components/dashboard/DashboardLayout';

// Pages load on first visit, so the login screen doesn't download charts and markdown rendering.
const ChatPage = lazy(() => import('./components/chat/ChatPage'));
const DocumentsPage = lazy(() => import('./components/documents/DocumentsPage'));
const CollectionsPage = lazy(() => import('./components/documents/CollectionsPage'));
const SettingsPage = lazy(() => import('./components/settings/SettingsPage'));
const OverviewPage = lazy(() => import('./components/dashboard/OverviewPage'));

function Spinner() {
  return <div className="flex items-center justify-center h-screen"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600" /></div>;
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();
  if (isLoading) return <Spinner />;
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" />;
}

export default function App() {
  const { fetchUser, isAuthenticated } = useAuthStore();

  useEffect(() => {
    if (isAuthenticated) fetchUser();
    else useAuthStore.setState({ isLoading: false });
  }, []);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/" element={<ProtectedRoute><DashboardLayout /></ProtectedRoute>}>
        <Route index element={<OverviewPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="chat/:conversationId" element={<ChatPage />} />
        <Route path="collections" element={<CollectionsPage />} />
        <Route path="collections/:collectionId" element={<DocumentsPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
