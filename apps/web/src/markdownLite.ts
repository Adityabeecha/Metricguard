// A small Markdown renderer for MetricGuard's own guide (headings, paragraphs, lists, tables,
// quotes, rules, code, bold, italics, links and guide images). All text is escaped first, so the output only
// contains the tags written here. Pure, so node tests can import it.

const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Relative links to other docs point at the repository; anything unexpected becomes plain text. */
export function guideHref(href: string, repoDocs = 'https://github.com/Adityabeecha/Metricguard/blob/main/docs/'): string | null {
  if (/^(https:\/\/|mailto:|\/(?!\/)|#)/.test(href)) return href;
  if (/^[A-Za-z0-9_./-]+\.md(#[A-Za-z0-9_-]*)?$/.test(href) && !href.includes('..')) return repoDocs + href;
  return null;
}

/** Heading anchors: lower case, letters/digits/hyphens ("7. Run a comparison" -> "7-run-a-comparison"). */
export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '-');
}

export function inline(text: string): string {
  const codes: string[] = [];
  const images: string[] = [];
  let out = escape(text).replace(/`([^`]+)`/g, (_, code) => { codes.push(code); return `\u0000${codes.length - 1}\u0000`; });
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_whole, alt, src) => {
    if (!/^\/guide\/[A-Za-z0-9_./-]+$/.test(src) || src.split('/').some((part: string) => part === '.' || part === '..')) return alt;
    images.push(`<img src="${src}" alt="${alt}" loading="lazy">`);
    return `\u0001${images.length - 1}\u0001`;
  });
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_whole, label, href) => {
    const safe = guideHref(href.replace(/&amp;/g, '&'));
    return safe ? `<a href="${escape(safe)}"${safe.startsWith('https://') ? ' rel="noreferrer"' : ''}>${label}</a>` : label;
  });
  out = out.replace(/\*\*((?:[^*]|\*(?!\*))+)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\w)/g, '$1<em>$2</em>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`)
    .replace(/\u0001(\d+)\u0001/g, (_, i) => images[Number(i)]);
}

const cells = (row: string) => row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());

export function renderMarkdown(source: string): string {
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  const html: string[] = [];
  let i = 0;
  const isList = (line: string) => /^\s*([-*]|\d+\.)\s+/.test(line);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith('```')) {
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) body.push(lines[i]);
      i++;
      html.push(`<pre><code>${escape(body.join('\n'))}</code></pre>`);
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      html.push(`<h${level} id="${slug(heading[2])}">${inline(heading[2])}</h${level}>`);
      i++; continue;
    }
    if (/^\s*---+\s*$/.test(line)) { html.push('<hr>'); i++; continue; }
    if (line.startsWith('>')) {
      const body: string[] = [];
      for (; i < lines.length && lines[i].startsWith('>'); i++) body.push(lines[i].replace(/^>\s?/, ''));
      html.push(`<blockquote>${renderMarkdown(body.join('\n'))}</blockquote>`);
      continue;
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith('|'); i++) rows.push(cells(lines[i]));
      html.push(`<div class="guide-table"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${
        rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    if (isList(line)) {
      const block: string[] = [];
      for (; i < lines.length && (isList(lines[i]) || (lines[i].trim() && /^\s{2,}/.test(lines[i])) ||
        (!lines[i].trim() && i + 1 < lines.length && /^\s{2,}\S|^\s*([-*]|\d+\.)\s/.test(lines[i + 1]))); i++) block.push(lines[i]);
      html.push(renderList(block));
      continue;
    }
    const para: string[] = [];
    for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|>|```|\s*\||\s*---+\s*$)/.test(lines[i]) && !isList(lines[i]); i++) para.push(lines[i].trim());
    html.push(`<p>${inline(para.join(' '))}</p>`);
  }
  return html.join('\n');
}

/** A list and its nested items; deeper indentation becomes a nested list or a continuation. */
function renderList(block: string[]): string {
  const indent = (s: string) => s.length - s.trimStart().length;
  const base = indent(block.find(l => l.trim()) || '');
  const ordered = /^\s*\d+\./.test(block[0]);
  const items: string[][] = [];
  for (const line of block) {
    if (!line.trim()) continue;
    if (indent(line) <= base + 1 && /^\s*([-*]|\d+\.)\s+/.test(line)) items.push([line.trim().replace(/^([-*]|\d+\.)\s+/, '')]);
    else if (items.length) items[items.length - 1].push(line);
  }
  const listLine = (l: string) => /^\s*([-*]|\d+\.)\s+/.test(l);
  const body = items.map(([first, ...rest]) => {
    // Lines under an item: runs of list lines become nested lists, other runs become paragraphs.
    let lead = first, html = '';
    let k = 0;
    while (k < rest.length && !listLine(rest[k])) lead += ' ' + rest[k++].trim();
    while (k < rest.length) {
      const run: string[] = [];
      const nested = listLine(rest[k]);
      while (k < rest.length && listLine(rest[k]) === nested || (nested && k < rest.length && indent(rest[k]) > indent(run[0] || rest[k]) && !listLine(rest[k]))) run.push(rest[k++]);
      html += nested ? renderList(run) : `<p>${inline(run.map(l => l.trim()).join(' '))}</p>`;
    }
    return `<li>${inline(lead)}${html}</li>`;
  }).join('');
  return ordered ? `<ol>${body}</ol>` : `<ul>${body}</ul>`;
}
