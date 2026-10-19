/** MetricGuard mark: a guard shield carrying a rising metric line over a perforated seal edge. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <path d="M16 2.5 27 6.6V15c0 7-4.8 12-11 14.5C9.8 27 5 22 5 15V6.6Z" fill="var(--seal)" />
      <path d="M16 2.5 27 6.6V15c0 7-4.8 12-11 14.5" fill="none" stroke="var(--seal-strong)" strokeWidth="1" opacity="0.6" />
      <polyline points="9.5,17.5 13.4,13.6 16.8,16.4 22.5,10.4" fill="none" stroke="var(--on-accent)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="22.5" cy="10.4" r="1.9" fill="var(--on-accent)" />
      <line x1="10" y1="21.6" x2="22" y2="21.6" stroke="var(--on-accent)" strokeWidth="1.6" strokeLinecap="round" strokeDasharray="0.1 3" opacity="0.85" />
    </svg>
  );
}
