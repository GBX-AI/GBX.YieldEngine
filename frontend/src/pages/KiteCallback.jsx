import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

const BASE = import.meta.env.VITE_API_BASE || '';

export default function KiteCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState('Connecting to Zerodha...');
  const [error, setError] = useState('');

  useEffect(() => {
    const requestToken = searchParams.get('request_token');
    if (!requestToken) {
      setError('No request_token received from Zerodha');
      return;
    }

    const connect = async () => {
      try {
        const token = localStorage.getItem('accessToken');
        const res = await fetch(`${BASE}/api/kite/connect`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify({ request_token: requestToken }),
        });
        const data = await res.json();
        if (res.ok) {
          const msg = data.holdings_imported
            ? `Connected! ${data.holdings_imported} holdings imported. Redirecting...`
            : 'Connected! Redirecting...';
          setStatus(msg);
          setTimeout(() => navigate('/holdings', { replace: true }), 1500);
        } else {
          setError(data.error || 'Failed to connect');
        }
      } catch (err) {
        setError(err.message);
      }
    };

    connect();
  }, [searchParams, navigate]);

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: '100vh', background: '#0a0f1a', color: '#e2e8f0',
      fontFamily: "'DM Sans', sans-serif",
    }}>
      <div style={{
        textAlign: 'center', padding: 40, maxWidth: 400,
        background: 'rgba(15,23,42,0.85)', borderRadius: 16,
        border: '1px solid rgba(148,163,184,0.15)',
      }}>
        <div style={{ color: '#6ee7b7', fontSize: 24, marginBottom: 16 }}>&#9670;</div>
        {error ? (
          <>
            <div style={{ color: '#f87171', fontSize: 14, marginBottom: 12 }}>{error}</div>
            <button onClick={() => navigate('/settings')} style={{
              padding: '8px 20px', borderRadius: 8, border: 'none',
              background: 'rgba(148,163,184,0.1)', color: '#e2e8f0',
              cursor: 'pointer', fontSize: 13,
            }}>
              Back to Settings
            </button>
          </>
        ) : (
          <div style={{ fontSize: 14, opacity: 0.7 }}>{status}</div>
        )}
      </div>
    </div>
  );
}
