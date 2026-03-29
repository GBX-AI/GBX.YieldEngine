const BASE = import.meta.env.VITE_API_BASE || '';

let _isRefreshing = false;
let _refreshQueue = [];

async function _tryRefresh() {
  const refreshToken = localStorage.getItem('refreshToken');
  if (!refreshToken) return false;
  try {
    const res = await fetch(`${BASE}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    localStorage.setItem('accessToken', data.access_token);
    return true;
  } catch {
    return false;
  }
}

async function request(path, options = {}) {
  const token = localStorage.getItem('accessToken');
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let res = await fetch(`${BASE}${path}`, { ...options, headers });

  // Handle 401 — try refresh once
  if (res.status === 401) {
    if (!_isRefreshing) {
      _isRefreshing = true;
      const refreshed = await _tryRefresh();
      _isRefreshing = false;
      _refreshQueue.forEach((cb) => cb(refreshed));
      _refreshQueue = [];

      if (refreshed) {
        const newToken = localStorage.getItem('accessToken');
        headers['Authorization'] = `Bearer ${newToken}`;
        res = await fetch(`${BASE}${path}`, { ...options, headers });
      } else {
        localStorage.removeItem('accessToken');
        localStorage.removeItem('refreshToken');
        window.location.href = '/login';
        throw new Error('Session expired');
      }
    } else {
      // Wait for ongoing refresh
      const refreshed = await new Promise((resolve) => _refreshQueue.push(resolve));
      if (refreshed) {
        const newToken = localStorage.getItem('accessToken');
        headers['Authorization'] = `Bearer ${newToken}`;
        res = await fetch(`${BASE}${path}`, { ...options, headers });
      } else {
        throw new Error('Session expired');
      }
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.error || err.detail || res.statusText);
  }
  return res.json();
}

// Authenticated file upload (FormData — no Content-Type header, browser sets it)
async function uploadRequest(path, formData) {
  const token = localStorage.getItem('accessToken');
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers, body: formData });
  if (res.status === 401) {
    const refreshed = await _tryRefresh();
    if (refreshed) {
      headers['Authorization'] = `Bearer ${localStorage.getItem('accessToken')}`;
      const res2 = await fetch(`${BASE}${path}`, { method: 'POST', headers, body: formData });
      return res2.json();
    }
    window.location.href = '/login';
    throw new Error('Session expired');
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
export const detectCsvColumns = (formData) => uploadRequest('/api/import/csv/detect', formData);
export const importCsv = (formData) => uploadRequest('/api/import/csv', formData);
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
export const kiteConnect = (requestToken) => request('/api/kite/connect', { method: 'POST', body: JSON.stringify({ request_token: requestToken }) });
export const kiteStatus = () => request('/api/kite/status');
export const kiteDisconnect = () => request('/api/kite/disconnect', { method: 'POST' });
export const kiteSaveCredentials = (apiKey, apiSecret, permission = 'readonly') => request('/api/kite/credentials', { method: 'POST', body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, permission }) });

// Manual Trade Tracking
export const getManualTrades = () => request('/api/trades/manual');
export const createManualTrade = (data) => request('/api/trades/manual', { method: 'POST', body: JSON.stringify(data) });
export const exitManualTrade = (id, data) => request(`/api/trades/manual/${id}/exit`, { method: 'POST', body: JSON.stringify(data) });

// Auth
export const authMe = () => request('/api/auth/me');
