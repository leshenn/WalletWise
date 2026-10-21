export default function StatCard({ label, value, helper, icon: Icon, tone = 'default' }) {
  return (
    <article className={`stat-card tone-${tone}`}>
      <div className="stat-card-top">
        <span>{label}</span>
        {Icon && <span className="stat-icon"><Icon size={18} /></span>}
      </div>
      <strong>{value}</strong>
      {helper && <small>{helper}</small>}
    </article>
  );
}
