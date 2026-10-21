import { useEffect, useMemo, useState } from 'react';
import { currency } from '../utils/formatters';

const PALETTE = [
  '#6D28D9', '#2563EB', '#059669', '#DC2626', '#D97706', '#7C3AED',
  '#0891B2', '#DB2777', '#4F46E5', '#65A30D', '#9333EA', '#0F766E',
  '#C2410C', '#0284C7', '#BE185D', '#475569', '#A16207', '#8B5CF6',
];

function shortMonth(label = '') {
  const [month, year] = label.split(' ');
  return year ? `${month.slice(0, 3)} ${year.slice(-2)}` : label;
}

export default function CategoryTrendChart({ trend }) {
  const categories = trend?.categories || [];
  const points = trend?.points || [];
  const categoryKey = categories.join('|');
  const [selectedCategories, setSelectedCategories] = useState(categories);

  useEffect(() => {
    setSelectedCategories(categories);
  }, [categoryKey]);

  const allSelected = categories.length > 0 && selectedCategories.length === categories.length;
  const visibleCategories = categories.filter((category) => selectedCategories.includes(category));

  const toggleCategory = (category) => {
    setSelectedCategories((current) => {
      const currentlyAll = categories.length > 0 && current.length === categories.length;
      if (currentlyAll) return [category];
      if (current.includes(category)) {
        if (current.length === 1) return current;
        return current.filter((item) => item !== category);
      }
      return [...current, category];
    });
  };

  const chart = useMemo(() => {
    const width = Math.max(720, points.length * 110);
    const height = 330;
    const margin = { top: 24, right: 30, bottom: 54, left: 72 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const values = points.flatMap((point) => visibleCategories.map((category) => Number(point.values?.[category] || 0)));
    const maxValue = Math.max(1, ...values);
    const roundedMax = Math.ceil(maxValue / 100) * 100 || maxValue;
    const yMax = Math.max(roundedMax, maxValue);
    const x = (index) => margin.left + (points.length <= 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
    const y = (value) => margin.top + plotHeight - (Number(value || 0) / yMax) * plotHeight;
    const ticks = Array.from({ length: 5 }, (_, index) => (yMax / 4) * index);
    return { width, height, margin, plotWidth, plotHeight, yMax, x, y, ticks };
  }, [visibleCategories, points]);

  if (!points.length || !categories.length) {
    return <p className="muted-copy">Add more categorised spending to build the category trend.</p>;
  }

  return (
    <div className="category-trend-shell">
      <div className="category-trend-controls" aria-label="Choose categories shown on the chart">
        <button
          type="button"
          className={`category-legend-button all-categories ${allSelected ? 'active' : ''}`}
          onClick={() => setSelectedCategories(categories)}
        >
          All categories
        </button>
        {categories.map((category, index) => {
          const active = selectedCategories.includes(category);
          const colour = PALETTE[index % PALETTE.length];
          return (
            <button
              type="button"
              key={category}
              className={`category-legend-button ${active ? 'active' : ''}`}
              onClick={() => toggleCategory(category)}
              aria-pressed={active}
              style={{ '--legend-colour': colour }}
            >
              <i style={{ background: colour }} />
              {category}
            </button>
          );
        })}
      </div>

      <p className="category-trend-hint">
        {allSelected ? 'Showing every category. Select a category to focus on it.' : `Showing ${visibleCategories.length} of ${categories.length} categories.`}
      </p>

      <div className="category-trend-scroll">
        <svg
          className="category-trend-chart"
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          role="img"
          aria-label="Spending by category trend line chart"
        >
          {chart.ticks.map((tick) => {
            const yPos = chart.y(tick);
            return (
              <g key={tick}>
                <line x1={chart.margin.left} x2={chart.width - chart.margin.right} y1={yPos} y2={yPos} className="trend-grid-line" />
                <text x={chart.margin.left - 12} y={yPos + 4} textAnchor="end" className="trend-axis-label">{currency(tick)}</text>
              </g>
            );
          })}

          {points.map((point, index) => (
            <text
              key={point.month || point.label}
              x={chart.x(index)}
              y={chart.height - 20}
              textAnchor="middle"
              className="trend-axis-label trend-month-label"
            >
              {shortMonth(point.label)}
            </text>
          ))}

          {visibleCategories.map((category) => {
            const categoryIndex = categories.indexOf(category);
            const coordinates = points.map((point, index) => ({
              x: chart.x(index),
              y: chart.y(point.values?.[category] || 0),
              value: Number(point.values?.[category] || 0),
              label: point.label,
            }));
            const path = coordinates.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');
            const colour = PALETTE[categoryIndex % PALETTE.length];
            return (
              <g key={category}>
                <path d={path} fill="none" stroke={colour} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                {coordinates.map((point) => (
                  <g key={`${category}-${point.label}`}>
                    <circle cx={point.x} cy={point.y} r="4" fill={colour}>
                      <title>{`${category} · ${point.label}: ${currency(point.value)}`}</title>
                    </circle>
                  </g>
                ))}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
