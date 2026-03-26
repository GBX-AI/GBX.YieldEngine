import React, { useState } from 'react';
import { Link } from 'react-router-dom';

const BASE = import.meta.env.VITE_API_BASE || '';
const C = {
  bg: '#0a0f1a', card: 'rgba(15,23,42,0.85)', border: 'rgba(148,163,184,0.15)',
  text: '#e2e8f0', muted: 'rgba(226,232,240,0.5)', emerald: '#6ee7b7',
  red: '#f87171', blue: '#38bdf8',
};

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${BASE}/api/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      setSent(true);
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

        <div style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 8, textAlign: 'center' }}>
          Reset Password
        </div>

        {sent ? (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 13, color: C.emerald, marginBottom: 16, lineHeight: 1.6 }}>
              If an account exists with that email, we've sent a reset link. Check your inbox (and spam folder).
            </div>
            <Link to="/login" style={{ fontSize: 13, color: C.blue, textDecoration: 'none' }}>
              Back to Login
            </Link>
          </div>
        ) : (
          <>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 20, textAlign: 'center', lineHeight: 1.5 }}>
              Enter your email and we'll send you a link to reset your password.
            </div>
            {error && <div style={s.error}>{error}</div>}
            <form onSubmit={handleSubmit} style={s.form}>
              <div style={s.field}>
                <label style={s.label}>Email</label>
                <input style={s.input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="you@example.com" />
              </div>
              <button style={s.submit} type="submit" disabled={loading}>
                {loading ? 'Sending...' : 'Send Reset Link'}
              </button>
            </form>
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <Link to="/login" style={{ fontSize: 12, color: C.muted, textDecoration: 'none' }}>
                Back to Login
              </Link>
            </div>
          </>
        )}
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
    gap: 8, marginBottom: 24,
  },
  logoText: {
    fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600,
    fontSize: 18, color: C.text,
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
  },
};
