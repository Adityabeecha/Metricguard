export function isLocalHost(hostname: string): boolean {
  return ['localhost', '127.0.0.1', '[::1]'].includes(hostname.toLowerCase());
}

export function engineDownMessage(hostname: string): string {
  return isLocalHost(hostname)
    ? 'The comparison engine is not running. Start it with scripts\\mg.ps1 start.'
    : 'The comparison engine is busy or restarting. Try again in a minute; if it keeps happening, send Feedback.';
}
