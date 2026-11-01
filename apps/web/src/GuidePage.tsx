import { useEffect } from 'react';
// The repository's own user guide, bundled at build time so the site and docs/ never drift.
import guide from '../../../docs/user-guide.md?raw';
import { renderMarkdown } from './markdownLite';

const html = renderMarkdown(guide);

/** The end-to-end user guide at /guide; the Markdown is MetricGuard's own and is escaped before rendering. */
export function GuidePage() {
  useEffect(() => {
    if (window.location.hash) document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView();
  }, []);
  return <article className="public-legal public-guide" dangerouslySetInnerHTML={{ __html: html }} />;
}
