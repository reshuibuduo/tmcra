type EvidenceRecordProps = {
  actor: "USER" | "AGENT";
  layer: "SOURCE" | "FAST" | "SLOW";
  scope: string;
  session: string;
  time: string;
  children: string;
  active?: boolean;
  compact?: boolean;
};

export default function EvidenceRecord({
  actor,
  layer,
  scope,
  session,
  time,
  children,
  active = false,
  compact = false,
}: EvidenceRecordProps) {
  return (
    <article
      className={`evidence-record is-${actor.toLowerCase()}${active ? " is-active" : ""}${compact ? " is-compact" : ""}`}
      aria-label={`${actor} ${layer} evidence, ${scope}, ${session}, ${time}`}
    >
      <header>
        <span className="evidence-actor">{actor}</span>
        <span className="evidence-layer">{layer}</span>
      </header>
      <p>{children}</p>
      <footer>
        <span>{scope}</span>
        <span>{session}</span>
        <time>{time}</time>
      </footer>
    </article>
  );
}
