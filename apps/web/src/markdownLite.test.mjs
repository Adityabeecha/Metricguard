import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { guideHref, inline, renderMarkdown, slug } from './markdownLite.ts';

test('inline formatting is escaped first and only safe links survive', () => {
  assert.equal(inline('**Save case** and `snapshot` <b>'), '<strong>Save case</strong> and <code>snapshot</code> &lt;b&gt;');
  assert.equal(inline('*Example:* text'), '<em>Example:</em> text');
  assert.equal(inline('[guide](cli-usage.md)'), '<a href="https://github.com/Adityabeecha/Metricguard/blob/main/docs/cli-usage.md" rel="noreferrer">guide</a>');
  assert.equal(inline('[x](javascript:alert(1))'), '[x](javascript:alert(1))'.replace('[x](javascript:alert(1))', 'x)'));
  assert.equal(guideHref('javascript:alert(1)'), null);
  assert.equal(guideHref('../secrets.md'), null);
  assert.equal(guideHref('/demo/MG-F3A3'), '/demo/MG-F3A3');
  assert.equal(slug('7. Run a comparison'), '7-run-a-comparison');
});

test('blocks: headings, tables, nested lists, quotes and rules', () => {
  const html = renderMarkdown('# Title\n\nPara one\nstill one.\n\n| A | B |\n|---|---|\n| 1 | **2** |\n\n- one\n  - nested\n- two\n\n> **Tip:** quote\n\n---\n\n1. first\n2. second');
  assert.match(html, /<h1 id="title">Title<\/h1>/);
  assert.match(html, /<p>Para one still one\.<\/p>/);
  assert.match(html, /<table><thead><tr><th>A<\/th><th>B<\/th><\/tr><\/thead><tbody><tr><td>1<\/td><td><strong>2<\/strong><\/td><\/tr>/);
  assert.match(html, /<ul><li>one<ul><li>nested<\/li><\/ul><\/li><li>two<\/li><\/ul>/);
  assert.match(html, /<blockquote><p><strong>Tip:<\/strong> quote<\/p><\/blockquote>/);
  assert.match(html, /<hr>/);
  assert.match(html, /<ol><li>first<\/li><li>second<\/li><\/ol>/);
});

test('the real user guide renders without leftover Markdown or raw tags', () => {
  const html = renderMarkdown(readFileSync(new URL('../../../docs/user-guide.md', import.meta.url), 'utf8'));
  assert.match(html, /<h1 id="metricguard-user-guide">/);
  assert.ok(!/<script|onerror=/i.test(html));
  assert.ok(!/\*\*|^#{1,4} /m.test(html.replace(/<pre>[\s\S]*?<\/pre>/g, '')), 'unrendered bold or heading markers');
});
