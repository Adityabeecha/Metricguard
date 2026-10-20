/** Scroll to the section a URL hash names (e.g. "#security"); returns whether one was found. */
export function scrollToHash(hash: string, doc: Pick<Document, 'getElementById'>, reducedMotion: boolean): boolean {
  const id = decodeURIComponent(hash.replace(/^#/, ''));
  if (!id || !/^[A-Za-z][\w-]*$/.test(id)) return false;
  const target = doc.getElementById(id);
  if (!target) return false;
  target.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
  return true;
}
