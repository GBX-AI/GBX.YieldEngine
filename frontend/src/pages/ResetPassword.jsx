import React, { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

const BASE = import.meta.env.VITE_API_BASE || '';
const C = {
  bg: '#0a0f1a', card: 'rgba(15,23,42,0.85)', border: 'rgba(148,163,184,0.15)',
  text: '#e2e8f0', muted: 'rgba(226,232,240,0.5)', emerald: '#6ee7b7',
  red: '#f87171', blue: '#38bdf8',
};

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  if (!token) {
    return (
      <div style={s.page}>
        <div style={s.card}>
          <div style={{ color: C.red, fontSize: 14, textAlign: 'center' }}>
            Invalid reset link. No token provided.
          </div>
          <div style={{ textAlign: 'center', marginTop: 16 }}>
            <Link to="/forgot-password" style={{ color: C.blue, fontSize: 13, textDecoration: 'none' }}>
              Request a new reset link
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password !== confirmPassword) { setError('Passwords do not match'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return; }

    setLoading(true);
    try {
      const res = await fetch(`${BASE}/api/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      setDone(true);
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

        <div style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 16, textAlign: 'center' }}>
          Set New Password
        </div>

        {done ? (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 13, color: C.emerald, marginBottom: 16 }}>
              Password reset successfully!
            </div>
            <Link to="/login" style={{
              display: 'inline-block', padding: '10px 24px', borderRadius: 8,
              background: C.emerald, color: '#0a0f1a', fontSize: 14,
              fontWeight: 600, textDecoration: 'none',
            }}>
              Login with New Password
            </Link>
          </div>
        ) : (
          <>
            {error && <div style={s.error}>{error}</div>}
            <form onSubmit={handleSubmit} style={s.form}>
              <div style={s.field}>
                <label style={s.label}>New Password</label>
                <input style={s.input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} required placeholder="Min 8 characters" />
              </div>
              <div style={s.field}>
                <label style={s.label}>Confirm Password</label>
                <input style={s.input} type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required placeholder="Repeat password" />
              </div>
              <button style={s.submit} type="submit" disabled={loading}>
                {loading ? 'Resetting...' : 'Reset Password'}
              </button>
            </form>
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
