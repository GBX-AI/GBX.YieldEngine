const BASE = import.meta.env.VITE_API_BASE || '';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// Status & Permissions
export const getStatus = () => request('/api/status');
export const getPermission = () => request('/api/permission');
export const setPermission = (data) => request('/api/permission', { method: 'POST', body: JSON.stringify(data) });

// Holdings
export const getHoldings = () => request('/api/holdings');
export const importJson = (data) => request('/api/import/json', { method: 'POST', body: JSON.stringify(data) });
export const detectCsvColumns = (formData) => fetch(`${BASE}/api/import/csv/detect`, { method: 'POST', body: formData }).then(r => r.json());
export const importCsv = (formData) => fetch(`${BASE}/api/import/csv`, { method: 'POST', body: formData }).then(r => r.json());
export const importManual = (data) => request('/api/import/manual', { method: 'POST', body: JSON.stringify(data) });
export const importFromKite = () => request('/api/import/kite', { method: 'POST' });
export const deleteHolding = (symbol) => request(`/api/holdings/${encodeURIComponent(symbol)}`, { method: 'DELETE' });

// Portfolios
export const getPortfolios = () => request('/api/portfolios');
export const savePortfolio = (name) => request('/api/portfolios', { method: 'POST', body: JSON.stringify({ name }) });
export const deletePortfolio = (id) => request(`/api/portfolios/${id}`, { method: 'DELETE' });
export const loadPortfolio = (id) => request(`/api/portfolios/${id}/load`, { method: 'POST' });

// Scanner & Recommendations
export const scan = (cashBalance) => request('/api/scan', { method: 'POST', body: JSON.stringify({ cash_balance: cashBalance }) });
export const getRecommendations = (filters) => request('/api/recommendations', { method: 'POST', body: JSON.stringify(filters) });
export const getArbitrage = () => request('/api/arbitrage');

// Execution
export const execute = (data) => request('/api/execute', { method: 'POST', body: JSON.stringify(data) });
export const closePosition = (id, data) => request(`/api/positions/${id}/close`, { method: 'POST', body: JSON.stringify(data) });
export const rollPosition = (id, data) => request(`/api/positions/${id}/roll`, { method: 'POST', body: JSON.stringify(data) });
export const getAdjustments = (id) => request(`/api/positions/${id}/adjustments`);
export const executeAdjustment = (id, data) => request(`/api/positions/${id}/adjustments`, { method: 'POST', body: JSON.stringify(data) });

// Positions & Trades
export const getPositions = () => request('/api/positions');
export const getTrades = (filters) => {
  const params = new URLSearchParams(filters).toString();
  return request(`/api/trades${params ? '?' + params : ''}`);
};
export const getTradeDetail = (id) => request(`/api/trades/${id}`);

// Analytics
export const getAnalyticsSummary = () => request('/api/analytics/summary');
export const getAnalyticsStrategy = () => request('/api/analytics/strategy');
export const getAnalyticsMonthly = () => request('/api/analytics/monthly');
export const getAnalyticsDaily = (start, end) => request(`/api/analytics/daily?start=${start}&end=${end}`);

// Collateral
export const getCollateral = () => request('/api/collateral');

// Notifications
export const getNotifications = (page) => request(`/api/notifications?page=${page}`);
export const getUnreadCount = () => request('/api/notifications/unread-count');
export const markRead = (id) => request(`/api/notifications/${id}/read`, { method: 'POST' });
export const markAllRead = () => request('/api/notifications/read-all', { method: 'POST' });
export const deleteNotification = (id) => request(`/api/notifications/${id}`, { method: 'DELETE' });

// Settings
export const getSettings = () => request('/api/settings');
export const updateSettings = (data) => request('/api/settings', { method: 'PUT', body: JSON.stringify(data) });
export const getRiskProfile = () => request('/api/settings/risk-profile');
export const setRiskProfile = (profile) => request('/api/settings/risk-profile', { method: 'POST', body: JSON.stringify(profile) });
export const setCircuitBreaker = (enabled) => request('/api/settings/circuit-breaker', { method: 'POST', body: JSON.stringify({ enabled }) });

// Daily Summary
export const getDailySummary = () => request('/api/daily-summary');
export const getDailySummaryByDate = (date) => request(`/api/daily-summary/${date}`);

// Fees
export const getFeesEstimate = (params) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/api/fees/estimate${qs ? '?' + qs : ''}`);
};
export const getFeesSummary = (period) => request(`/api/fees/summary?period=${period}`);

// Risk
export const getRiskStatus = () => request('/api/risk/status');
export const getRiskAlerts = () => request('/api/risk/alerts');

// Safety & Audit
export const getSafetyCaps = () => request('/api/safety/caps');
export const getAuditOrders = () => request('/api/audit/orders');
export const getActiveGtt = () => request('/api/gtt/active');
export const cancelGtt = (id) => request(`/api/gtt/${id}`, { method: 'DELETE' });

// Kite Auth
export const kiteLogin = () => request('/api/kite/login');
export const kiteAutoLogin = () => request('/api/kite/auto-login', { method: 'POST' });
