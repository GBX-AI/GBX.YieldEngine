import React, { useState, useEffect } from 'react';
import { getSettings, updateSettings, getRiskProfile, setRiskProfile, kiteLogin, kiteAutoLogin, setCircuitBreaker, getStatus } from '../api';

const c = {
  bg: '#0a0f1a',
  card: 'rgba(15,23,42,0.7)',
  border: 'rgba(148,163,184,0.1)',
  text: '#e2e8f0',
  muted: '#94a3b8',
  emerald: '#6ee7b7',
  red: '#f87171',
  amber: '#fcd34d',
  blue: '#38bdf8',
  purple: '#a78bfa',
};

const mono = "'IBM Plex Mono', monospace";
const sans = "'DM Sans', sans-serif";

const STRATEGY_TYPES = [
  'Covered Call',
  'Cash Secured Put',
  'Bull Put Spread',
  'Bear Call Spread',
  'Iron Condor',
  'Straddle',
  'Strangle',
  'Jade Lizard',
  'Wheel',
];

const NOTIFICATION_TYPES = [
  { key: 'trade_executed', label: 'Trade Executed' },
  { key: 'trade_closed', label: 'Trade Closed' },
  { key: 'stop_loss_triggered', label: 'Stop Loss Triggered' },
  { key: 'delta_alert', label: 'Delta Alert' },
  { key: 'expiry_reminder', label: 'Expiry Reminder' },
  { key: 'daily_summary', label: 'Daily Summary' },
  { key: 'risk_alert', label: 'Risk Alert' },
  { key: 'circuit_breaker', label: 'Circuit Breaker' },
];

const RISK_PROFILES = [
  {
    key: 'conservative',
    name: 'Conservative',
    description: 'Low risk, high probability trades. Focus on capital preservation with smaller premiums.',
    deltaRange: '0.10 - 0.20',
    color: c.emerald,
  },
  {
    key: 'moderate',
    name: 'Moderate',
    description: 'Balanced approach between risk and reward. Standard premium collection strategies.',
    deltaRange: '0.20 - 0.35',
    color: c.blue,
  },
  {
    key: 'aggressive',
    name: 'Aggressive',
    description: 'Higher risk, higher reward. Closer strikes with larger premiums but more active management.',
    deltaRange: '0.30 - 0.50',
    color: c.amber,
  },
];

const s = {
  page: {
    minHeight: '100vh',
    background: c.bg,
    padding: '24px',
    fontFamily: sans,
    maxWidth: 860,
    margin: '0 auto',
  },
  header: {
    fontSize: 22,
    fontWeight: 700,
    color: c.text,
    marginBottom: 32,
  },
  card: {
    background: c.card,
    border: `1px solid ${c.border}`,
    borderRadius: 16,
    padding: 24,
    backdropFilter: 'blur(12px)',
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: 600,
    color: c.text,
    marginBottom: 4,
  },
  sectionSub: {
    fontSize: 12,
    color: c.muted,
    marginBottom: 20,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    marginBottom: 16,
  },
  label: {
    fontSize: 13,
    color: c.muted,
    marginBottom: 6,
    fontWeight: 500,
  },
  input: {
    width: '100%',
    padding: '10px 14px',
    borderRadius: 10,
    border: `1px solid ${c.border}`,
    background: 'rgba(15,23,42,0.5)',
    color: c.text,
    fontSize: 13,
    fontFamily: mono,
    outline: 'none',
    transition: 'border-color 0.15s ease',
  },
  inputFocus: {
    borderColor: c.blue,
  },
  btn: {
    padding: '10px 24px',
    borderRadius: 10,
    border: 'none',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 600,
    fontFamily: sans,
    transition: 'opacity 0.15s ease',
  },
  btnPrimary: {
    background: c.blue,
    color: '#0a0f1a',
  },
  btnDanger: {
    background: c.red,
    color: '#0a0f1a',
  },
  btnOutline: {
    background: 'transparent',
    border: `1px solid ${c.border}`,
    color: c.text,
  },
  statusDot: (connected) => ({
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: connected ? c.emerald : c.red,
    boxShadow: connected ? `0 0 8px ${c.emerald}` : `0 0 8px ${c.red}`,
    flexShrink: 0,
  }),
  toggle: (on) => ({
    width: 44,
    height: 24,
    borderRadius: 12,
    background: on ? c.blue : 'rgba(148,163,184,0.2)',
    border: 'none',
    cursor: 'pointer',
    position: 'relative',
    transition: 'background 0.2s ease',
    flexShrink: 0,
  }),
  toggleKnob: (on) => ({
    width: 18,
    height: 18,
    borderRadius: '50%',
    background: '#fff',
    position: 'absolute',
    top: 3,
    left: on ? 23 : 3,
    transition: 'left 0.2s ease',
    boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
  }),
  radioCard: (selected, color) => ({
    flex: 1,
    padding: 20,
    borderRadius: 12,
    border: `2px solid ${selected ? color : c.border}`,
    background: selected ? `${color}10` : 'transparent',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  }),
  radioTitle: (selected, color) => ({
    fontSize: 14,
    fontWeight: 600,
    color: selected ? color : c.text,
    marginBottom: 6,
  }),
  radioDesc: {
    fontSize: 12,
    color: c.muted,
    lineHeight: 1.5,
    marginBottom: 8,
  },
  radioDelta: {
    fontSize: 11,
    fontFamily: mono,
    color: c.muted,
    padding: '4px 8px',
    background: 'rgba(148,163,184,0.08)',
    borderRadius: 6,
    display: 'inline-block',
  },
  checkbox: (checked) => ({
    width: 18,
    height: 18,
    borderRadius: 5,
    border: `2px solid ${checked ? c.blue : c.border}`,
    background: checked ? c.blue : 'transparent',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    transition: 'all 0.15s ease',
  }),
  checkMark: {
    color: '#0a0f1a',
    fontSize: 11,
    fontWeight: 700,
  },
  fieldGroup: {
    marginBottom: 16,
  },
  inputRow: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 16,
    marginBottom: 16,
  },
  warningBox: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '12px 16px',
    borderRadius: 10,
    background: 'rgba(248,113,113,0.08)',
    border: `1px solid rgba(248,113,113,0.2)`,
    marginTop: 8,
    marginBottom: 16,
  },
  warningText: {
    fontSize: 12,
    color: c.red,
    lineHeight: 1.5,
  },
  separator: {
    height: 1,
    background: c.border,
    margin: '20px 0',
  },
  flexBetween: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  strategyGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
    gap: 10,
  },
  saving: {
    position: 'fixed',
    bottom: 24,
    right: 24,
    padding: '12px 32px',
    borderRadius: 12,
    background: c.blue,
    color: '#0a0f1a',
    fontSize: 14,
    fontWeight: 700,
    fontFamily: sans,
    border: 'none',
    cursor: 'pointer',
    boxShadow: `0 4px 20px rgba(56,189,248,0.3)`,
    transition: 'opacity 0.15s ease',
    zIndex: 100,
  },
};

const Toggle = ({ on, onToggle }) => (
  <button style={s.toggle(on)} onClick={onToggle} type="button">
    <div style={s.toggleKnob(on)} />
  </button>
);

const Checkbox = ({ checked, onChange }) => (
  <div style={s.checkbox(checked)} onClick={onChange}>
    {checked && <span style={s.checkMark}>✓</span>}
  </div>
);

const Field = ({ label, children }) => (
  <div style={s.fieldGroup}>
    <div style={s.label}>{label}</div>
    {children}
  </div>
);

export default function Settings() {
  const [settings, setSettings] = useState(null);
  const [riskProfile, setRiskProfileState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [kiteConnected, setKiteConnected] = useState(false);
  const [kiteConfigured, setKiteConfigured] = useState(false);
  const [simulationMode, setSimulationMode] = useState(true);
  const [priceSources, setPriceSources] = useState(null);
  const [kiteLoading, setKiteLoading] = useState(false);

  // Local form state
  const [totp, setTotp] = useState('');
  const [autoLogin, setAutoLogin] = useState(false);
  const [userId, setUserId] = useState('');
  const [profile, setProfile] = useState('moderate');
  const [strikeMode, setStrikeMode] = useState('auto');
  const [minOtm, setMinOtm] = useState('');
  const [maxOtm, setMaxOtm] = useState('');
  const [targetDelta, setTargetDelta] = useState('');
  const [skipIvRank, setSkipIvRank] = useState('');
  const [stopLossMultiplier, setStopLossMultiplier] = useState('');
  const [deltaAlertThreshold, setDeltaAlertThreshold] = useState('');
  const [dailyLossLimit, setDailyLossLimit] = useState('');
  const [circuitBreaker, setCircuitBreakerState] = useState(false);
  const [autoStopLoss, setAutoStopLoss] = useState(false);
  const [autoGtt, setAutoGtt] = useState(false);
  const [closeItm, setCloseItm] = useState(false);
  const [intradayDrop, setIntradayDrop] = useState('');
  const [allowedStrategies, setAllowedStrategies] = useState([]);
  const [notifications, setNotifications] = useState({});

  useEffect(() => {
    // Check simulation mode
    getStatus().then((st) => {
      setSimulationMode(st?.simulation_mode ?? true);
      setKiteConnected(st?.kite_connected ?? false);
      setPriceSources(st?.price_sources ?? null);
    }).catch(() => {});

    Promise.all([
      getSettings().catch(() => null),
      getRiskProfile().catch(() => null),
      kiteLogin().catch(() => null),
    ]).then(([sett, rp, kiteStatus]) => {
      if (kiteStatus) {
        setKiteConfigured(!!kiteStatus.kite_configured);
        setKiteConnected(kiteStatus.authenticated ?? false);
      }
      if (sett) {
        setSettings(sett);
        setKiteConnected(sett.kite_connected ?? false);
        setAutoLogin(sett.auto_login ?? false);
        setUserId(sett.user_id ?? '');
        setTotp(sett.totp_secret ?? '');
        setStrikeMode(sett.strike_mode ?? 'auto');
        setMinOtm(sett.min_otm_pct ?? '');
        setMaxOtm(sett.max_otm_pct ?? '');
        setTargetDelta(sett.target_delta ?? '');
        setSkipIvRank(sett.skip_iv_rank_threshold ?? '');
        setStopLossMultiplier(sett.stop_loss_multiplier ?? '');
        setDeltaAlertThreshold(sett.delta_alert_threshold ?? '');
        setDailyLossLimit(sett.daily_loss_limit ?? '');
        setCircuitBreakerState(sett.circuit_breaker ?? false);
        setAutoStopLoss(sett.auto_stop_loss ?? false);
        setAutoGtt(sett.auto_gtt ?? false);
        setCloseItm(sett.close_itm_before_expiry ?? false);
        setIntradayDrop(sett.intraday_drop_pct ?? '');
        setAllowedStrategies(sett.allowed_strategies ?? []);
        const notifState = {};
        NOTIFICATION_TYPES.forEach(({ key }) => {
          notifState[key] = sett.notifications?.[key] ?? true;
        });
        setNotifications(notifState);
      }
      if (rp) {
        setRiskProfileState(rp);
        setProfile(rp.profile ?? rp.name ?? 'moderate');
      }
      setLoading(false);
    });
  }, []);

  const handleKiteLogin = async () => {
    setKiteLoading(true);
    try {
      const result = await kiteLogin();
      if (result?.login_url) {
        window.open(result.login_url, '_blank');
      }
      setKiteConnected(true);
    } catch {
      // silent
    }
    setKiteLoading(false);
  };

  const handleAutoLogin = async () => {
    setKiteLoading(true);
    try {
      await kiteAutoLogin();
      setKiteConnected(true);
    } catch {
      // silent
    }
    setKiteLoading(false);
  };

  const toggleStrategy = (st) => {
    setAllowedStrategies((prev) =>
      prev.includes(st) ? prev.filter((x) => x !== st) : [...prev, st]
    );
  };

  const handleCircuitBreaker = async (val) => {
    setCircuitBreakerState(val);
    try {
      await setCircuitBreaker(val);
    } catch {
      setCircuitBreakerState(!val);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await Promise.all([
        updateSettings({
          totp_secret: totp,
          auto_login: autoLogin,
          user_id: userId,
          strike_mode: strikeMode,
          min_otm_pct: minOtm ? parseFloat(minOtm) : null,
          max_otm_pct: maxOtm ? parseFloat(maxOtm) : null,
          target_delta: targetDelta ? parseFloat(targetDelta) : null,
          skip_iv_rank_threshold: skipIvRank ? parseFloat(skipIvRank) : null,
          stop_loss_multiplier: stopLossMultiplier ? parseFloat(stopLossMultiplier) : null,
          delta_alert_threshold: deltaAlertThreshold ? parseFloat(deltaAlertThreshold) : null,
          daily_loss_limit: dailyLossLimit ? parseFloat(dailyLossLimit) : null,
          circuit_breaker: circuitBreaker,
          auto_stop_loss: autoStopLoss,
          auto_gtt: autoGtt,
          close_itm_before_expiry: closeItm,
          intraday_drop_pct: intradayDrop ? parseFloat(intradayDrop) : null,
          allowed_strategies: allowedStrategies,
          notifications,
        }),
        setRiskProfile({ profile }),
      ]);
    } catch {
      // silent
    }
    setSaving(false);
  };

  const handleExportCsv = () => {
    const link = document.createElement('a');
    link.href = `${import.meta.env.VITE_API_BASE || ''}/api/trades/export/csv`;
    link.download = 'trades.csv';
    link.click();
  };

  const handleReset = async () => {
    if (!confirmReset) {
      setConfirmReset(true);
      return;
    }
    try {
      await updateSettings({});
      window.location.reload();
    } catch {
      // silent
    }
    setConfirmReset(false);
  };

  if (loading) {
    return (
      <div style={{ ...s.page, display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
        <span style={{ color: c.muted, fontSize: 14 }}>Loading settings...</span>
      </div>
    );
  }

  return (
    <div style={s.page}>
      <div style={s.header}>Settings</div>

      {/* Mode & Broker Connection */}
      <div style={s.card}>
        <div style={s.sectionTitle}>Broker Connection</div>
        <div style={s.sectionSub}>Connect to Zerodha Kite for live trading, or use simulation mode for recommendations only</div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <div style={s.statusDot(kiteConnected)} />
          <span style={{ fontSize: 13, fontWeight: 600, color: kiteConnected ? c.emerald : c.amber }}>
            {kiteConnected ? 'Kite Connected' : simulationMode ? 'Simulation Mode' : 'Disconnected'}
          </span>
          {simulationMode && !kiteConnected && (
            <span style={{
              fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 6,
              background: 'rgba(252,211,77,0.12)', color: c.amber, fontFamily: mono,
            }}>
              SIM
            </span>
          )}
        </div>

        {simulationMode && !kiteConfigured && (
          <div style={{
            padding: '14px 18px', borderRadius: 10, marginBottom: 20,
            background: priceSources?.spot_source === 'yahoo' ? 'rgba(110,231,183,0.06)' : 'rgba(56,189,248,0.06)',
            border: priceSources?.spot_source === 'yahoo' ? '1px solid rgba(110,231,183,0.15)' : '1px solid rgba(56,189,248,0.15)',
          }}>
            <div style={{ fontSize: 13, color: priceSources?.spot_source === 'yahoo' ? c.emerald : c.blue, fontWeight: 600, marginBottom: 6 }}>
              {priceSources?.spot_source === 'yahoo' ? 'Live Prices Active' : 'Simulation Mode Active'}
            </div>
            <div style={{ fontSize: 12, color: c.muted, lineHeight: 1.6 }}>
              {priceSources?.spot_source === 'yahoo' ? (
                <>
                  No Kite broker connected, but <strong>live market prices</strong> are active via Yahoo Finance
                  {priceSources?.option_chain_source === 'nse' ? ' and NSE India' : ''}.
                  Spot prices, holdings valuations, and option chains use real market data.
                  To enable live order execution, configure your Kite API credentials below.
                </>
              ) : (
                <>
                  No Kite API key configured. The engine uses simulated market data to generate strategy recommendations,
                  risk alerts, and notifications. Import your holdings via CSV or manual entry to get personalized recommendations.
                  To enable live trading, configure your Kite API credentials below.
                </>
              )}
            </div>
            {priceSources && (
              <div style={{ marginTop: 10, display: 'flex', gap: 12, fontSize: 11, fontFamily: "'IBM Plex Mono', monospace" }}>
                <span style={{ color: priceSources.spot_source === 'yahoo' ? c.emerald : c.muted }}>
                  Spot: {priceSources.spot_source === 'yahoo' ? 'Yahoo Finance' : 'Simulated'}
                </span>
                <span style={{ color: priceSources.option_chain_source === 'nse' ? c.emerald : c.muted }}>
                  Options: {priceSources.option_chain_source === 'nse' ? 'NSE India' : 'Black-Scholes'}
                </span>
              </div>
            )}
          </div>
        )}

        {(kiteConfigured || kiteConnected) && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
              <div style={{ flex: 1 }} />
              <button
                style={{ ...s.btn, ...s.btnPrimary, opacity: kiteLoading ? 0.6 : 1 }}
                onClick={handleKiteLogin}
                disabled={kiteLoading}
              >
                {kiteLoading ? 'Connecting...' : kiteConnected ? 'Reconnect' : 'Login to Kite'}
              </button>
            </div>
          </>
        )}

        <div style={s.inputRow}>
          <Field label="Kite API Key">
            <input
              style={s.input}
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="Not configured (optional)"
            />
          </Field>
          <Field label="TOTP Secret">
            <input
              style={s.input}
              type="password"
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              placeholder="Not configured (optional)"
            />
          </Field>
        </div>

        {(kiteConfigured || userId || totp) && (
          <div style={s.flexBetween}>
            <div>
              <div style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>Auto-Login</div>
              <div style={{ fontSize: 11, color: c.muted }}>Automatically login to Kite on system startup</div>
            </div>
            <Toggle on={autoLogin} onToggle={() => setAutoLogin(!autoLogin)} />
          </div>
        )}
      </div>

      {/* Risk Profile */}
      <div style={s.card}>
        <div style={s.sectionTitle}>Risk Profile</div>
        <div style={s.sectionSub}>Choose your risk tolerance level for strategy selection</div>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {RISK_PROFILES.map((rp) => (
            <div
              key={rp.key}
              style={s.radioCard(profile === rp.key, rp.color)}
              onClick={() => setProfile(rp.key)}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <div
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    border: `2px solid ${profile === rp.key ? rp.color : c.border}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {profile === rp.key && (
                    <div style={{ width: 8, height: 8, borderRadius: '50%', background: rp.color }} />
                  )}
                </div>
                <span style={s.radioTitle(profile === rp.key, rp.color)}>{rp.name}</span>
              </div>
              <div style={s.radioDesc}>{rp.description}</div>
              <span style={s.radioDelta}>Delta: {rp.deltaRange}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Strike Selection */}
      <div style={s.card}>
        <div style={s.sectionTitle}>Strike Selection</div>
        <div style={s.sectionSub}>Configure how option strikes are selected for trades</div>

        <div style={s.flexBetween}>
          <div>
            <div style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>Mode</div>
            <div style={{ fontSize: 11, color: c.muted }}>Auto uses risk profile defaults</div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              style={{
                ...s.btn,
                ...(strikeMode === 'auto' ? s.btnPrimary : s.btnOutline),
                padding: '8px 20px',
              }}
              onClick={() => setStrikeMode('auto')}
            >
              Auto
            </button>
            <button
              style={{
                ...s.btn,
                ...(strikeMode === 'manual' ? s.btnPrimary : s.btnOutline),
                padding: '8px 20px',
              }}
              onClick={() => setStrikeMode('manual')}
            >
              Manual
            </button>
          </div>
        </div>

        {strikeMode === 'manual' && (
          <>
            <div style={s.separator} />
            <div style={s.inputRow}>
              <Field label="Min OTM %">
                <input
                  style={s.input}
                  type="number"
                  value={minOtm}
                  onChange={(e) => setMinOtm(e.target.value)}
                  placeholder="e.g. 3"
                />
              </Field>
              <Field label="Max OTM %">
                <input
                  style={s.input}
                  type="number"
                  value={maxOtm}
                  onChange={(e) => setMaxOtm(e.target.value)}
                  placeholder="e.g. 10"
                />
              </Field>
            </div>
            <div style={s.inputRow}>
              <Field label="Target Delta">
                <input
                  style={s.input}
                  type="number"
                  step="0.01"
                  value={targetDelta}
                  onChange={(e) => setTargetDelta(e.target.value)}
                  placeholder="e.g. 0.25"
                />
              </Field>
              <Field label="Skip IV Rank Threshold">
                <input
                  style={s.input}
                  type="number"
                  value={skipIvRank}
                  onChange={(e) => setSkipIvRank(e.target.value)}
                  placeholder="e.g. 30"
                />
              </Field>
            </div>
          </>
        )}
      </div>

      {/* Risk Management */}
      <div style={s.card}>
        <div style={s.sectionTitle}>Risk Management</div>
        <div style={s.sectionSub}>Configure risk controls and automated safety features</div>

        <div style={s.inputRow}>
          <Field label="Stop-Loss Multiplier">
            <input
              style={s.input}
              type="number"
              step="0.1"
              value={stopLossMultiplier}
              onChange={(e) => setStopLossMultiplier(e.target.value)}
              placeholder="e.g. 2.0"
            />
          </Field>
          <Field label="Delta Alert Threshold">
            <input
              style={s.input}
              type="number"
              step="0.01"
              value={deltaAlertThreshold}
              onChange={(e) => setDeltaAlertThreshold(e.target.value)}
              placeholder="e.g. 0.40"
            />
          </Field>
        </div>

        <div style={s.inputRow}>
          <Field label="Daily Loss Limit (₹)">
            <input
              style={s.input}
              type="number"
              value={dailyLossLimit}
              onChange={(e) => setDailyLossLimit(e.target.value)}
              placeholder="e.g. 25000"
            />
          </Field>
          <Field label="Intraday Drop %">
            <input
              style={s.input}
              type="number"
              step="0.1"
              value={intradayDrop}
              onChange={(e) => setIntradayDrop(e.target.value)}
              placeholder="e.g. 5.0"
            />
          </Field>
        </div>

        <div style={s.separator} />

        {/* Circuit Breaker */}
        <div style={s.flexBetween}>
          <div>
            <div style={{ fontSize: 13, color: c.red, fontWeight: 600 }}>Circuit Breaker</div>
            <div style={{ fontSize: 11, color: c.muted }}>Kill switch - immediately stops all trading</div>
          </div>
          <Toggle on={circuitBreaker} onToggle={() => handleCircuitBreaker(!circuitBreaker)} />
        </div>
        {circuitBreaker && (
          <div style={s.warningBox}>
            <span style={{ fontSize: 16 }}>⚠</span>
            <span style={s.warningText}>
              Circuit breaker is active. All automated trading is halted. No new positions will be opened
              and no adjustments will be made until this is disabled.
            </span>
          </div>
        )}

        <div style={s.flexBetween}>
          <div>
            <div style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>Auto Stop-Loss</div>
            <div style={{ fontSize: 11, color: c.muted }}>Automatically place stop-loss orders on new positions</div>
          </div>
          <Toggle on={autoStopLoss} onToggle={() => setAutoStopLoss(!autoStopLoss)} />
        </div>

        <div style={s.flexBetween}>
          <div>
            <div style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>Auto GTT Orders</div>
            <div style={{ fontSize: 11, color: c.muted }}>Automatically place GTT (Good Till Triggered) orders</div>
          </div>
          <Toggle on={autoGtt} onToggle={() => setAutoGtt(!autoGtt)} />
        </div>

        <div style={s.flexBetween}>
          <div>
            <div style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>Close ITM Before Expiry</div>
            <div style={{ fontSize: 11, color: c.muted }}>Automatically close in-the-money positions before expiry</div>
          </div>
          <Toggle on={closeItm} onToggle={() => setCloseItm(!closeItm)} />
        </div>
      </div>

      {/* Allowed Strategies */}
      <div style={s.card}>
        <div style={s.sectionTitle}>Allowed Strategies</div>
        <div style={s.sectionSub}>Select which strategy types the engine can use</div>

        <div style={s.strategyGrid}>
          {STRATEGY_TYPES.map((st) => (
            <div
              key={st}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '10px 14px',
                borderRadius: 10,
                border: `1px solid ${allowedStrategies.includes(st) ? c.blue : c.border}`,
                background: allowedStrategies.includes(st) ? 'rgba(56,189,248,0.06)' : 'transparent',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              onClick={() => toggleStrategy(st)}
            >
              <Checkbox checked={allowedStrategies.includes(st)} onChange={() => {}} />
              <span style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>{st}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Notifications */}
      <div style={s.card}>
        <div style={s.sectionTitle}>Notifications</div>
        <div style={s.sectionSub}>Configure which notifications you want to receive</div>

        {NOTIFICATION_TYPES.map(({ key, label }) => (
          <div key={key} style={s.flexBetween}>
            <span style={{ fontSize: 13, color: c.text, fontWeight: 500 }}>{label}</span>
            <Toggle
              on={notifications[key] ?? true}
              onToggle={() =>
                setNotifications((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }))
              }
            />
          </div>
        ))}
      </div>

      {/* Data Management */}
      <div style={s.card}>
        <div style={s.sectionTitle}>Data Management</div>
        <div style={s.sectionSub}>Export data and manage application state</div>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <button style={{ ...s.btn, ...s.btnOutline }} onClick={handleExportCsv}>
            Export Trades CSV
          </button>
          <button
            style={{
              ...s.btn,
              ...(confirmReset ? s.btnDanger : s.btnOutline),
              borderColor: confirmReset ? c.red : undefined,
            }}
            onClick={handleReset}
          >
            {confirmReset ? 'Confirm Reset' : 'Reset Settings'}
          </button>
          {confirmReset && (
            <button
              style={{ ...s.btn, ...s.btnOutline }}
              onClick={() => setConfirmReset(false)}
            >
              Cancel
            </button>
          )}
        </div>
        {confirmReset && (
          <div style={{ ...s.warningBox, marginTop: 12 }}>
            <span style={{ fontSize: 16 }}>⚠</span>
            <span style={s.warningText}>
              This will reset all settings to their default values. This action cannot be undone.
            </span>
          </div>
        )}
      </div>

      {/* Save Button */}
      <button
        style={{ ...s.saving, opacity: saving ? 0.6 : 1 }}
        onClick={handleSave}
        disabled={saving}
      >
        {saving ? 'Saving...' : 'Save Settings'}
      </button>
    </div>
  );
}
