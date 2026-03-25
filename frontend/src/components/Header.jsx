import React, { useState, useEffect, useRef } from 'react';
import { NavLink } from 'react-router-dom';
import { Bell, Shield, ShieldAlert, Activity, BarChart3, Settings, Wallet, Search, TrendingUp, BookOpen, Zap } from 'lucide-react';
import { getUnreadCount, getNotifications, markAllRead, getStatus } from '../api';

const navItems = [
  { to: '/', label: 'Dashboard', icon: Activity },
  { to: '/holdings', label: 'Holdings', icon: Wallet },
  { to: '/scanner', label: 'Scanner', icon: Search },
  { to: '/positions', label: 'Positions', icon: BookOpen },
  { to: '/trades', label: 'Trades', icon: TrendingUp },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/risk', label: 'Risk', icon: ShieldAlert },
  { to: '/settings', label: 'Settings', icon: Settings },
];

const styles = {
  header: {
    position: 'sticky',
    top: 0,
    zIndex: 1000,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 24px',
    height: 56,
    background: 'rgba(10,15,26,0.95)',
    borderBottom: '1px solid rgba(148,163,184,0.1)',
    backdropFilter: 'blur(16px)',
    fontFamily: "'DM Sans', sans-serif",
  },
  logoSection: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexShrink: 0,
  },
  logoText: {
    fontSize: 16,
    fontWeight: 700,
    color: '#e2e8f0',
    letterSpacing: '0.08em',
    fontFamily: "'IBM Plex Mono', monospace",
  },
  versionBadge: {
    fontSize: 10,
    fontWeight: 600,
    color: '#6ee7b7',
    background: 'rgba(110,231,183,0.12)',
    borderRadius: 6,
    padding: '2px 6px',
    letterSpacing: '0.04em',
    fontFamily: "'IBM Plex Mono', monospace",
  },
  nav: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    overflow: 'auto',
  },
  navLink: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 12px',
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 500,
    color: '#94a3b8',
    textDecoration: 'none',
    transition: 'all 0.15s ease',
    whiteSpace: 'nowrap',
  },
  navLinkActive: {
    color: '#e2e8f0',
    background: 'rgba(148,163,184,0.1)',
  },
  rightSection: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    flexShrink: 0,
  },
  permBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 10px',
    borderRadius: 8,
    letterSpacing: '0.04em',
    fontFamily: "'IBM Plex Mono', monospace",
  },
  permReadonly: {
    color: '#38bdf8',
    background: 'rgba(56,189,248,0.12)',
    border: '1px solid rgba(56,189,248,0.2)',
  },
  permExecute: {
    color: '#f87171',
    background: 'rgba(248,113,113,0.12)',
    border: '1px solid rgba(248,113,113,0.2)',
  },
  bellWrapper: {
    position: 'relative',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 36,
    height: 36,
    borderRadius: 8,
    transition: 'background 0.15s ease',
  },
  bellBadge: {
    position: 'absolute',
    top: 2,
    right: 2,
    minWidth: 16,
    height: 16,
    borderRadius: 8,
    background: '#f87171',
    color: '#fff',
    fontSize: 10,
    fontWeight: 700,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '0 4px',
    fontFamily: "'IBM Plex Mono', monospace",
  },
  dropdown: {
    position: 'absolute',
    top: 48,
    right: 0,
    width: 340,
    background: 'rgba(15,23,42,0.97)',
    border: '1px solid rgba(148,163,184,0.12)',
    borderRadius: 12,
    padding: 8,
    backdropFilter: 'blur(16px)',
    boxShadow: '0 16px 48px rgba(0,0,0,0.5)',
    zIndex: 1100,
  },
  dropdownHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '8px 12px 12px',
    borderBottom: '1px solid rgba(148,163,184,0.08)',
    marginBottom: 4,
  },
  dropdownTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#e2e8f0',
  },
  markReadBtn: {
    fontSize: 11,
    color: '#38bdf8',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    fontWeight: 500,
    fontFamily: "'DM Sans', sans-serif",
  },
  notifItem: {
    display: 'flex',
    gap: 10,
    padding: '10px 12px',
    borderRadius: 8,
    transition: 'background 0.12s ease',
    cursor: 'default',
  },
  notifDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    marginTop: 5,
    flexShrink: 0,
  },
  notifText: {
    fontSize: 12,
    color: '#e2e8f0',
    lineHeight: 1.5,
  },
  notifTime: {
    fontSize: 11,
    color: '#94a3b8',
    marginTop: 2,
    fontFamily: "'IBM Plex Mono', monospace",
  },
  emptyNotif: {
    padding: '24px 12px',
    textAlign: 'center',
    color: '#94a3b8',
    fontSize: 13,
  },
};

const severityColor = {
  info: '#38bdf8',
  success: '#6ee7b7',
  warning: '#fcd34d',
  error: '#f87171',
  critical: '#f87171',
};

export default function Header({ permission = 'READONLY' }) {
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [bellHover, setBellHover] = useState(false);
  const [simulationMode, setSimulationMode] = useState(true);
  const dropdownRef = useRef(null);

  useEffect(() => {
    getStatus().then((s) => setSimulationMode(s?.simulation_mode ?? true)).catch(() => {});
  }, []);

  useEffect(() => {
    let mounted = true;
    const fetchUnread = async () => {
      try {
        const data = await getUnreadCount();
        if (mounted) setUnreadCount(data?.count ?? data ?? 0);
      } catch { /* silent */ }
    };
    fetchUnread();
    const interval = setInterval(fetchUnread, 30000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  useEffect(() => {
    if (!dropdownOpen) return;
    let mounted = true;
    const fetchNotifs = async () => {
      try {
        const data = await getNotifications();
        if (mounted) {
          const list = Array.isArray(data) ? data : data?.notifications ?? [];
          setNotifications(list.slice(0, 5));
        }
      } catch { /* silent */ }
    };
    fetchNotifs();
    return () => { mounted = false; };
  }, [dropdownOpen]);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setDropdownOpen(false);
      }
    };
    if (dropdownOpen) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [dropdownOpen]);

  const handleMarkAllRead = async () => {
    try {
      await markAllRead();
      setUnreadCount(0);
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    } catch { /* silent */ }
  };

  const isExecute = permission === 'EXECUTE';
  const PermIcon = isExecute ? ShieldAlert : Shield;

  return (
    <header style={styles.header}>
      <div style={styles.logoSection}>
        <Activity size={20} color="#6ee7b7" />
        <span style={styles.logoText}>YIELD ENGINE</span>
        <span style={styles.versionBadge}>v3</span>
        {simulationMode && (
          <span style={{
            fontSize: 10, fontWeight: 600, color: '#fcd34d',
            background: 'rgba(252,211,77,0.12)', borderRadius: 6,
            padding: '2px 6px', letterSpacing: '0.04em',
            fontFamily: "'IBM Plex Mono', monospace",
          }}>SIM</span>
        )}
      </div>

      <nav style={styles.nav}>
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            style={({ isActive }) => ({
              ...styles.navLink,
              ...(isActive ? styles.navLinkActive : {}),
            })}
          >
            <Icon size={14} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div style={styles.rightSection}>
        <div
          style={{
            ...styles.permBadge,
            ...(isExecute ? styles.permExecute : styles.permReadonly),
          }}
        >
          <PermIcon size={13} />
          {permission}
        </div>

        <div style={{ position: 'relative' }} ref={dropdownRef}>
          <div
            style={{
              ...styles.bellWrapper,
              background: bellHover || dropdownOpen ? 'rgba(148,163,184,0.1)' : 'transparent',
            }}
            onClick={() => setDropdownOpen((prev) => !prev)}
            onMouseEnter={() => setBellHover(true)}
            onMouseLeave={() => setBellHover(false)}
          >
            <Bell size={18} color={dropdownOpen ? '#e2e8f0' : '#94a3b8'} />
            {unreadCount > 0 && (
              <span style={styles.bellBadge}>{unreadCount > 99 ? '99+' : unreadCount}</span>
            )}
          </div>

          {dropdownOpen && (
            <div style={styles.dropdown}>
              <div style={styles.dropdownHeader}>
                <span style={styles.dropdownTitle}>Notifications</span>
                {unreadCount > 0 && (
                  <button style={styles.markReadBtn} onClick={handleMarkAllRead}>
                    Mark all read
                  </button>
                )}
              </div>
              {notifications.length === 0 ? (
                <div style={styles.emptyNotif}>No notifications</div>
              ) : (
                notifications.map((n, i) => (
                  <div
                    key={n.id ?? i}
                    style={{
                      ...styles.notifItem,
                      background: !n.read ? 'rgba(148,163,184,0.04)' : 'transparent',
                    }}
                  >
                    <div
                      style={{
                        ...styles.notifDot,
                        background: severityColor[n.severity] ?? severityColor.info,
                      }}
                    />
                    <div>
                      <div style={styles.notifText}>{n.message ?? n.text ?? 'Notification'}</div>
                      {n.timestamp && <div style={styles.notifTime}>{n.timestamp}</div>}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
