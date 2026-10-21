export const currency = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return new Intl.NumberFormat('en-ZA', {
    style: 'currency',
    currency: 'ZAR',
    maximumFractionDigits: 2,
  }).format(Number(value));
};

export const shortDate = (value) => {
  if (!value) return '—';
  return new Intl.DateTimeFormat('en-ZA', { day: '2-digit', month: 'short' }).format(new Date(`${value}T00:00:00`));
};

export const fullDate = (value) => {
  if (!value) return '—';
  return new Intl.DateTimeFormat('en-ZA', { day: 'numeric', month: 'long', year: 'numeric' }).format(new Date(`${value}T00:00:00`));
};

export const titleCase = (value = '') => value ? value.charAt(0).toUpperCase() + value.slice(1) : '';
