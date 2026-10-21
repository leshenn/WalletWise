import { AlertTriangle, CheckCircle2, CircleHelp } from 'lucide-react';

export default function ConfidenceBadge({ level = 'unknown', compact = false }) {
  const value = (level || 'unknown').toLowerCase();
  const icon = value === 'high' ? <CheckCircle2 size={14} /> : value === 'medium' ? <AlertTriangle size={14} /> : <CircleHelp size={14} />;
  return (
    <span className={`confidence-badge confidence-${value} ${compact ? 'compact' : ''}`}>
      {icon}
      {!compact && <span>{value}</span>}
    </span>
  );
}
