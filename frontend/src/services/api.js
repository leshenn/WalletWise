const explicitBase = (import.meta.env.VITE_API_BASE_URL || '').trim();
const API_BASE_URL = explicitBase || `http://${window.location.hostname}:8000/api`;
let selectedBank = 'all';

// Every bank-scoped endpoint uses the same validated backend selection.
function scopedUrl(path) {
  const isScoped = /^\/(?:dataset|transactions|coverage|review-groups|income-overview)(?:$|\?)/.test(path)
    || /^\/categories\/overview(?:$|\?)/.test(path)
    || /^\/reports(?:$|\/|\?)/.test(path);
  if (!isScoped || selectedBank === 'all') return `${API_BASE_URL}${path}`;
  const url = new URL(`${API_BASE_URL}${path}`);
  url.searchParams.set('bank', selectedBank);
  return url.toString();
}

async function request(path, options = {}) {
  const response = await fetch(scopedUrl(path), options);

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || payload.message || message;
    } catch {
      const text = await response.text();
      if (text) message = text;
    }
    throw new Error(message);
  }

  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  baseUrl: API_BASE_URL,

  setBankScope(value) { selectedBank = value || 'all'; },
  getBankScope() { return selectedBank; },

  health() {
    return request('/health');
  },

  getAssistantStatus() {
    return request('/assistant/status');
  },

  getAssistantChats() {
    return request('/assistant/chats');
  },

  getAssistantChat(id) {
    return request(`/assistant/chats/${id}`);
  },

  renameAssistantChat(id, title) {
    return request(`/assistant/chats/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
  },

  deleteAssistantChat(id) {
    return request(`/assistant/chats/${id}`, { method: 'DELETE' });
  },

  askAssistant(payload) {
    return request('/assistant/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, bank: selectedBank }),
    });
  },

  getDataset() {
    return request('/dataset');
  },

  getTransactions() {
    return request('/transactions', { cache: 'no-store' });
  },

  getStatements() {
    return request('/statements');
  },

  deleteStatement(id) {
    return request(`/statements/${id}`, { method: 'DELETE' });
  },

  getCoverage() {
    return request('/coverage');
  },

  getTrackingSettings() {
    return request('/tracking-settings');
  },

  updateTrackingSettings(payload) {
    return request('/tracking-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },

  getReviewGroups() {
    return request('/review-groups');
  },

  getReports() {
    return request('/reports', { cache: 'no-store' });
  },

  getCustomReport(start, end) {
    const params = new URLSearchParams({ start, end });
    return request(`/reports/custom?${params.toString()}`);
  },

  exploreReport(payload) {
    return request('/reports/explore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },

  getBudgetProfile() {
    return request('/budget-profile');
  },

  updateBudgetProfile(payload) {
    return request('/budget-profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },


  confirmReview(payload) {
    return request('/review/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },

  updateTransactionsBatch(transactionIds, payload) {
    return request('/transactions/batch', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transactionIds, ...payload }),
    });
  },

  getCategories() {
    return request('/categories');
  },

  getCategoryOverview() {
    return request('/categories/overview', { cache: 'no-store' });
  },

  renameCategory(source, name) {
    return request(`/categories/${encodeURIComponent(source)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    });
  },

  mergeCategories(source, target) {
    return request('/categories/merge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source, target }),
    });
  },

  deleteCategory(name) {
    return request(`/categories/${encodeURIComponent(name)}`, { method: 'DELETE' });
  },

  getRules() { return request('/naming-rules'); },

  getCategoryRules() { return request('/category-rules'); },

  createCategory(name) {
    return request('/categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
  },

  async processStatement(file) {
    const body = new FormData();
    body.append('file', file);
    return request('/statements/process', { method: 'POST', body });
  },

  updateTransaction(id, payload) {
    return request(`/transactions/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },

  setSalaryMarker(id, isSalary) {
    return request(`/transactions/${id}/salary`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ isSalary }),
    });
  },

  getIncomeOverview() {
    return request('/income-overview');
  },

  confirmSalarySource(transactionId, selectedTransactionIds = null) {
    return request('/income/salary-source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transactionId, selectedTransactionIds }),
    });
  },

  removeSalarySource(sourceId) {
    return request(`/income/salary-source/${sourceId}`, { method: 'DELETE' });
  },

  createRule(payload) {
    return request('/naming-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },

  updateRule(id, payload) {
    return request(`/naming-rules/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },

  deleteRule(id) { return request(`/naming-rules/${id}`, { method: 'DELETE' }); },

  createCategoryRule(payload) {
    return request('/category-rules', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  },

  updateCategoryRule(id, payload) {
    return request(`/category-rules/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  },

  deleteCategoryRule(id) { return request(`/category-rules/${id}`, { method: 'DELETE' }); },
};
