import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import useAuthStore from '../stores/authStore';

const BASE = import.meta.env.VITE_API_BASE || '';

const C = {
  bg: '#0a0f1a', card: 'rgba(15,23,42,0.85)', border: 'rgba(148,163,184,0.15)',
  text: '#e2e8f0', muted: 'rgba(226,232,240,0.5)', emerald: '#6ee7b7',
  red: '#f87171', blue: '#38bdf8',
};

export default function Login() {
  const [isSignup, setIsSignup] = useState(false);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { login, signup } = useAuthStore();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (isSignup && password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setLoading(true);
    try {
      if (isSignup) {
        await signup(email, name, password);
      } else {
        await login(email, password);
      }
      navigate('/', { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.logo}>
          <span style={{ color: C.emerald, fontSize: 24 }}>&#9670;</span>
          <span style={s.logoText}>Yield Engine</span>
        </div>

        <div style={s.tabs}>
          <button style={{ ...s.tab, ...(isSignup ? {} : s.tabActive) }} onClick={() => { setIsSignup(false); setError(''); }}>
            Login
          </button>
          <button style={{ ...s.tab, ...(isSignup ? s.tabActive : {}) }} onClick={() => { setIsSignup(true); setError(''); }}>
            Sign Up
          </button>
        </div>

        {error && <div style={s.error}>{error}</div>}

        <form onSubmit={handleSubmit} style={s.form}>
          {isSignup && (
            <div style={s.field}>
              <label style={s.label}>Name</label>
              <input style={s.input} type="text" value={name} onChange={(e) => setName(e.target.value)} required placeholder="Your name" />
            </div>
          )}
          <div style={s.field}>
            <label style={s.label}>Email</label>
            <input style={s.input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="you@example.com" />
          </div>
          <div style={s.field}>
            <label style={s.label}>Password</label>
            <input style={s.input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} required placeholder="Min 8 characters" />
          </div>
          {isSignup && (
            <div style={s.field}>
              <label style={s.label}>Confirm Password</label>
              <input style={s.input} type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required placeholder="Repeat password" />
            </div>
          )}
          <button style={s.submit} type="submit" disabled={loading}>
            {loading ? 'Please wait...' : isSignup ? 'Create Account' : 'Login'}
          </button>
          {!isSignup && (
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <Link to="/forgot-password" style={{ fontSize: 12, color: C.blue, textDecoration: 'none' }}>
                Forgot password?
              </Link>
            </div>
          )}
        </form>

        <div style={s.footer}>
          Options analytics for smarter decisions
        </div>
      </div>
    </div>
  );
}

const s = {
  page: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    minHeight: '100vh', background: C.bg, padding: 20,
  },
  card: {
    width: '100%', maxWidth: 400, background: C.card,
    border: `1px solid ${C.border}`, borderRadius: 16, padding: 32,
  },
  logo: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    gap: 8, marginBottom: 28,
  },
  logoText: {
    fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600,
    fontSize: 18, color: C.text,
  },
  tabs: {
    display: 'flex', gap: 0, marginBottom: 20,
    background: 'rgba(148,163,184,0.06)', borderRadius: 8, padding: 3,
  },
  tab: {
    flex: 1, padding: '8px 0', border: 'none', borderRadius: 6,
    background: 'transparent', color: C.muted, fontSize: 13,
    fontWeight: 500, cursor: 'pointer', fontFamily: "'DM Sans', sans-serif",
  },
  tabActive: {
    background: 'rgba(110,231,183,0.1)', color: C.emerald,
  },
  error: {
    background: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.2)',
    borderRadius: 8, padding: '10px 14px', marginBottom: 16,
    fontSize: 13, color: C.red,
  },
  form: { display: 'flex', flexDirection: 'column', gap: 16 },
  field: { display: 'flex', flexDirection: 'column', gap: 6 },
  label: { fontSize: 12, fontWeight: 500, color: C.muted },
  input: {
    padding: '10px 14px', borderRadius: 8, border: `1px solid ${C.border}`,
    background: 'rgba(15,23,42,0.5)', color: C.text, fontSize: 14,
    fontFamily: "'DM Sans', sans-serif", outline: 'none',
  },
  submit: {
    padding: '12px 0', borderRadius: 8, border: 'none',
    background: C.emerald, color: '#0a0f1a', fontSize: 14,
    fontWeight: 600, cursor: 'pointer', fontFamily: "'DM Sans', sans-serif",
    marginTop: 4,
  },
  footer: {
    textAlign: 'center', marginTop: 24, fontSize: 12,
    color: C.muted, fontStyle: 'italic',
  },
};
