import React, { useEffect } from 'react';
import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom';
import useAuthStore from './stores/authStore';
import ProtectedRoute from './components/ProtectedRoute';

// Page components
import Dashboard from './pages/Dashboard';
import Holdings from './pages/Holdings';
import Scanner from './pages/Scanner';
import Positions from './pages/Positions';
import TradeLog from './pages/TradeLog';
import Analytics from './pages/Analytics';
import Settings from './pages/Settings';
import RiskMonitor from './pages/RiskMonitor';
import Arbitrage from './pages/Arbitrage';
import Login from './pages/Login';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import KiteCallback from './pages/KiteCallback';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard' },
  { to: '/holdings', label: 'Holdings' },
  { to: '/scanner', label: 'Scanner' },
  { to: '/positions', label: 'Positions' },
  { to: '/trades', label: 'Trades' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/risk', label: 'Risk' },
  { to: '/arbitrage', label: 'Arbitrage' },
  { to: '/settings', label: 'Settings' },
];

function Header() {
  const { user, logout } = useAuthStore();

  return (
    <header style={styles.header}>
      <div style={styles.headerInner}>
        <div style={styles.logo}>
          <span style={styles.logoIcon}>&#9670;</span>
          <span style={styles.logoText}>Yield Engine</span>
          <span style={styles.versionBadge}>v4</span>
        </div>
        <nav style={styles.nav}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              style={({ isActive }) => ({
                ...styles.navLink,
                ...(isActive ? styles.navLinkActive : {}),
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        {user && (
          <div style={styles.userSection}>
            <span style={styles.userEmail}>{user.email}</span>
            <button style={styles.logoutBtn} onClick={logout}>Logout</button>
          </div>
        )}
      </div>
    </header>
  );
}

function AppLayout() {
  const location = useLocation();
  const isPublicPage = ['/login', '/forgot-password', '/reset-password'].includes(location.pathname);

  return (
    <div style={styles.app}>
      {!isPublicPage && <Header />}
      <main style={isPublicPage ? {} : styles.main}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/kite/callback" element={<ProtectedRoute><KiteCallback /></ProtectedRoute>} />
          <Route path="/" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
          <Route path="/holdings" element={<ProtectedRoute><Holdings /></ProtectedRoute>} />
          <Route path="/scanner" element={<ProtectedRoute><Scanner /></ProtectedRoute>} />
          <Route path="/positions" element={<ProtectedRoute><Positions /></ProtectedRoute>} />
          <Route path="/trades" element={<ProtectedRoute><TradeLog /></ProtectedRoute>} />
          <Route path="/analytics" element={<ProtectedRoute><Analytics /></ProtectedRoute>} />
          <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
          <Route path="/risk" element={<ProtectedRoute><RiskMonitor /></ProtectedRoute>} />
          <Route path="/arbitrage" element={<ProtectedRoute><Arbitrage /></ProtectedRoute>} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  const { checkAuth } = useAuthStore();

  useEffect(() => {
    checkAuth();
  }, []);

  return (
    <BrowserRouter>
      <AppLayout />
    </BrowserRouter>
  );
}

const colors = {
  bg: '#0a0f1a',
  cardBg: 'rgba(15,23,42,0.7)',
  border: 'rgba(148,163,184,0.1)',
  text: '#e2e8f0',
  textMuted: 'rgba(226,232,240,0.5)',
  emerald: '#6ee7b7',
  red: '#f87171',
  amber: '#fcd34d',
  blue: '#38bdf8',
  purple: '#a78bfa',
};

const styles = {
  app: {
    minHeight: '100vh',
    background: colors.bg,
    color: colors.text,
    fontFamily: "'DM Sans', sans-serif",
  },
  header: {
    position: 'sticky',
    top: 0,
    zIndex: 50,
    background: 'rgba(10,15,26,0.85)',
    backdropFilter: 'blur(12px)',
    borderBottom: `1px solid ${colors.border}`,
  },
  headerInner: {
    maxWidth: 1280,
    margin: '0 auto',
    padding: '0 24px',
    height: 56,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  logoIcon: {
    color: colors.emerald,
    fontSize: 18,
  },
  logoText: {
    fontFamily: "'IBM Plex Mono', monospace",
    fontWeight: 600,
    fontSize: 16,
    color: colors.text,
  },
  versionBadge: {
    fontFamily: "'IBM Plex Mono', monospace",
    fontSize: 11,
    fontWeight: 500,
    color: colors.emerald,
    background: 'rgba(110,231,183,0.1)',
    padding: '2px 6px',
    borderRadius: 4,
  },
  nav: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  navLink: {
    padding: '6px 12px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    color: 'rgba(226,232,240,0.6)',
    textDecoration: 'none',
    transition: 'all 0.15s ease',
  },
  navLinkActive: {
    color: colors.emerald,
    background: 'rgba(110,231,183,0.1)',
  },
  userSection: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  userEmail: {
    fontSize: 12,
    color: colors.textMuted,
    fontFamily: "'IBM Plex Mono', monospace",
  },
  logoutBtn: {
    padding: '5px 12px',
    borderRadius: 6,
    border: `1px solid ${colors.border}`,
    background: 'transparent',
    color: colors.textMuted,
    fontSize: 12,
    cursor: 'pointer',
    fontFamily: "'DM Sans', sans-serif",
  },
  main: {
    maxWidth: 1280,
    margin: '0 auto',
    padding: '32px 24px',
  },
};
