export default function EmptyState({ icon: Icon, title, body, action }) {
  return (
    <div className="empty-state">
      {Icon && <div className="empty-icon"><Icon size={24} /></div>}
      <h3>{title}</h3>
      <p>{body}</p>
      {action}
    </div>
  );
}
