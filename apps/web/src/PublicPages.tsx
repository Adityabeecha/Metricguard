import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, Clock, Database, FileQuestion, Fingerprint, GitCompareArrows, LockKeyhole, Repeat2, Sigma } from 'lucide-react';
import { api } from './api';
import { Logo } from './Logo';
import { ThemeSwitch } from './ThemeSwitch';
import { readThemeChoice } from './themeChoice';
import { homeRedirect, type SitePage } from './router';
import './public.css';

type PublicPage = Exclude<SitePage, 'workspace' | 'signin' | 'signup'>;

export function PublicPages({ page }: { page: PublicPage }) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 8);
    update();
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, []);
  useEffect(() => {
    const titles = { home: 'MetricGuard — Explain the difference. Keep the proof.', privacy: 'Privacy — MetricGuard', terms: 'Terms — MetricGuard', 'not-found': 'Page not found — MetricGuard' };
    document.title = titles[page];
    if (page !== 'home') return;
    let active = true;
    // Public pages make no workspace/bootstrap/provider requests. Identity supplies
    // an authorised destination; never guess a workspace from a user's id.
    api.identity().then(identity => {
      const destination = homeRedirect(identity);
      if (active && destination) {
        window.history.replaceState(null, '', destination);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }
    }).catch(() => { /* A signed-out visitor or unavailable API still gets the homepage. */ });
    return () => { active = false; };
  }, [page]);

  return <div className={`public-site ${page === 'home' ? 'public-home' : ''}`}>
    <a className="skip-link" href="#public-content">Skip to content</a>
    <header className={`public-header ${scrolled ? 'is-scrolled' : ''}`}><div className="public-header-inner public-width">
      <a className="public-brand" href="/" aria-label="MetricGuard home"><Logo size={32} /><strong>MetricGuard</strong></a>
      <nav className="public-anchors" aria-label="On this page"><a href={`${page === 'home' ? '' : '/'}#how`}>How it works</a><a href={`${page === 'home' ? '' : '/'}#evidence`}>Evidence</a><a href={`${page === 'home' ? '' : '/'}#security`}>Security</a></nav>
      <div className="public-header-actions"><ThemeSwitch compact /><a className="public-signin" href="/signin">Sign in</a><a className="button primary" href="/signup">Sign up</a></div>
    </div></header>
    <main id="public-content" className={page === 'home' ? 'public-home-main' : 'public-width'}>
      {page === 'home' ? <Homepage /> : page === 'not-found' ? <NotFound /> : <LegalPage kind={page} />}
    </main>
    <footer className="public-footer public-width"><div><span>© MetricGuard</span><span>Built for data analysts</span></div><nav aria-label="Legal"><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="https://github.com/Adityabeecha/Metricguard" rel="noreferrer">GitHub</a></nav></footer>
  </div>;
}

function Homepage() {
  const [choice, setChoice] = useState(readThemeChoice);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const update = () => setChoice(readThemeChoice());
    window.addEventListener('mg-theme-change', update);
    return () => window.removeEventListener('mg-theme-change', update);
  }, []);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const items = element.querySelectorAll<HTMLElement>('[data-reveal]');
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) {
      items.forEach(item => { item.dataset.visible = 'true'; });
      return;
    }
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          (entry.target as HTMLElement).dataset.visible = 'true';
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    element.dataset.motion = 'ready';
    items.forEach(item => observer.observe(item));
    return () => observer.disconnect();
  }, []);
  const darkMedia = choice === 'system' ? '(prefers-color-scheme: dark)' : choice === 'dark' ? 'all' : 'not all';
  const mobileDarkMedia = choice === 'system' ? '(max-width: 759px) and (prefers-color-scheme: dark)' : choice === 'dark' ? '(max-width: 759px)' : 'not all';
  return <div className="homepage" ref={root}>
    <section className="home-hero home-perforation" aria-labelledby="home-title"><div className="public-width hero-grid">
      <div className="hero-copy" data-reveal>
        <h1 id="home-title">Your metric changed. See exactly which records changed it.</h1>
        <p className="public-lead">Compare two calculations, follow the record-level difference, and hand over sealed evidence anyone with access can replay.</p>
        <div className="hero-actions"><a className="button primary public-cta" href="/signup">Sign up — free beta <ArrowRight size={18} aria-hidden="true" /></a><a className="home-how-link" href="#how">See how it works <ArrowRight size={16} aria-hidden="true" /></a></div>
        <p className="public-beta">Beta · by invitation</p>
      </div>
      <div className="hero-demo"><ReconciliationCard /><p className="demo-caption">Synthetic demo orders. The records add up exactly.</p></div>
    </div></section>

    <section className="home-section home-panel" id="problem" aria-labelledby="problem-title"><div className="public-width" data-reveal>
      <h2 id="problem-title">The same metric. Two different stories.</h2>
      <div className="dashboard-disagreement" role="img" aria-label="Illustrative disagreement: Finance reports Revenue 9.87 billion; Product reports Revenue 2.47 billion.">
        <div className="dashboard-tile"><span>Finance</span><div>Revenue<strong>9.87B</strong></div><div className="tile-lines" aria-hidden="true"><i /><i /><i /><i /><i /></div></div>
        <span className="disagreement-mark" aria-hidden="true">?</span>
        <div className="dashboard-tile"><span>Product</span><div>Revenue<strong>2.47B</strong></div><div className="tile-lines alternate" aria-hidden="true"><i /><i /><i /><i /><i /></div></div>
      </div>
      <p className="illustration-note">Illustrative totals, not customer data.</p>
      <div className="home-pains"><p><GitCompareArrows aria-hidden="true" />Same metric. Different answers.</p><p><Clock aria-hidden="true" />Hours spent reconciling records.</p><p><FileQuestion aria-hidden="true" />No evidence to hand over.</p></div>
    </div></section>

    <section className="home-section" id="how" aria-labelledby="how-title"><div className="public-width">
      <h2 id="how-title" data-reveal>Three steps. A defensible answer.</h2>
      <div className="home-stepper">
        <article className="home-step" data-reveal><span className="step-number">01</span><h3>Choose and freeze your data</h3><p>Fix the inputs before comparing.</p><div className="step-vignette dataset-vignette"><Database size={19} aria-hidden="true" /><div><strong>orders</strong><span>1,000,000 rows</span></div><span className="frozen-label"><LockKeyhole size={12} aria-hidden="true" />frozen</span></div></article>
        <article className="home-step" data-reveal><span className="step-number">02</span><h3>Define the current and proposed</h3><p>Change the rule, keep the data.</p><div className="step-vignette sql-vignette"><code>SUM(amount)</code><code>SUM(amount)<br /><mark>WHERE status = 'completed'</mark></code></div></article>
        <article className="home-step" data-reveal><span className="step-number">03</span><h3>Read, explain and seal it</h3><p>Keep the answer and its proof.</p><div className="step-vignette answer-vignette"><strong>−340.00 <small>INR</small></strong><span className="mini-seal"><Fingerprint size={14} aria-hidden="true" />SEALED</span></div></article>
      </div>
      <p className="illustration-note">Workflow illustrations. The case below uses eight synthetic orders.</p>
    </div></section>

    <section className="home-section home-panel" id="evidence" aria-labelledby="evidence-title"><div className="public-width">
      <div className="evidence-heading" data-reveal><h2 id="evidence-title">See the evidence.</h2><p>The answer, the contributing records and the seal. One case.</p></div>
      <figure className="public-case" data-reveal>
        <div className="browser-frame"><div className="browser-bar" aria-hidden="true"><span className="browser-dots"><i /><i /><i /></span><span>MetricGuard / Demo case</span><LockKeyhole size={13} /></div>
          <div className="evidence-image"><picture>
            <source media={mobileDarkMedia} srcSet="/images/case-mobile-dark.webp" width="358" height="570" />
            <source media="(max-width: 759px)" srcSet="/images/case-mobile-light.webp" width="358" height="570" />
            <source media={darkMedia} srcSet="/images/case-dark.webp" />
            <img src="/images/case-light.webp" width="1366" height="1000" loading="lazy" decoding="async" alt="Real MetricGuard demo case: current 750.75 INR, proposed 410.75 INR. R003, R007 and R004 explain the 340.00 INR difference, next to the evidence seal." />
          </picture><span className="shot-pin pin-answer" aria-hidden="true">1</span><span className="shot-pin pin-records" aria-hidden="true">2</span><span className="shot-pin pin-seal" aria-hidden="true">3</span></div>
        </div>
        <figcaption><span><b>1</b>The answer</span><span><b>2</b>The records that explain it</span><span><b>3</b>The evidence seal</span><small className="mobile-crop-note">Mobile detail: the answer and seal. Records are below this crop in the full case.</small></figcaption>
      </figure>
    </div></section>

    <section className="home-section" aria-labelledby="support-title"><div className="public-width" data-reveal>
      <h2 id="support-title">Works with your data.</h2>
      <div className="home-chip-row" aria-label="Supported sources">{['CSV', 'Parquet', 'Excel', 'Two connected tables', 'PostgreSQL', 'Snowflake', 'BigQuery'].map(text => <span key={text}>{text}</span>)}</div>
      <div className="home-chip-row calculations" aria-label="Supported calculations">{['SUM', 'COUNT', 'COUNT DISTINCT', 'AVG', 'Ratios', 'Rates', 'Shares', 'Margins'].map(text => <span key={text}>{text}</span>)}</div>
      <p className="home-limit">Snowflake and BigQuery: stand-in servers verified; live-service checks pending. Supported SQL only.</p>
    </div></section>

    <section className="home-section home-panel" id="security" aria-labelledby="security-title"><div className="public-width">
      <h2 id="security-title" data-reveal>Security &amp; trust.</h2>
      <div className="home-trust-grid">
        <article data-reveal><LockKeyhole aria-hidden="true" /><div><h3>Frozen inputs</h3><p>Both calculations use the same fixed data.</p></div></article>
        <article data-reveal><Sigma aria-hidden="true" /><div><h3>Exact arithmetic</h3><p>Decimal values and exact fractions keep every contribution precise.</p></div></article>
        <article data-reveal><Fingerprint aria-hidden="true" /><div><h3>Tamper-evident hashes</h3><p>Hashes make changed inputs or evidence detectable.</p><code>sha256 761d…90a7</code></div></article>
        <article data-reveal><Repeat2 aria-hidden="true" /><div><h3>Byte-for-byte replay</h3><p>Re-run the saved inputs and check the evidence matches.</p></div></article>
      </div>
    </div></section>

    <section className="home-closing" aria-labelledby="closing-title"><div className="public-width" data-reveal><h2 id="closing-title">Stop guessing why the number moved.</h2><div><a className="button primary public-cta" href="/signup">Sign up — free beta <ArrowRight size={18} aria-hidden="true" /></a><a href="/signin">Sign in</a></div></div></section>
  </div>;
}

function ReconciliationCard() {
  return <div className="reconciliation seal-card" data-reveal role="img" aria-label="Synthetic demo reconciliation: current revenue 750.75 INR, proposed 410.75 INR, a difference of minus 340 INR. R003 contributes minus 200, R007 minus 90, R004 minus 50. Evidence case MG-F3A3.">
    <div aria-hidden="true"><div className="seal-strip recon-seal"><span>Sealed</span><span className="seal-no">MG-F3A3</span></div>
      <div className="recon-metrics"><div><span>Current · Revenue</span><strong>750.75 <small>INR</small></strong></div><div><span>Proposed</span><strong>410.75 <small>INR</small></strong></div></div>
      <div className="recon-connector" /><div className="recon-answer">−340.00 <span>INR</span></div>
      <div className="recon-records"><div className="recon-record-header"><span>Record</span><span>Change · INR</span></div>{[['R003', '−200.00'], ['R007', '−90.00'], ['R004', '−50.00']].map(([key, value]) => <div data-record key={key}><span>{key}</span><span>{value}</span></div>)}</div>
      <p className="recon-match"><Check size={16} />Replay matched byte for byte</p>
    </div>
  </div>;
}

// Draft — review before launch. Replace [region] and [contact email] before publishing.
function LegalPage({ kind }: { kind: 'privacy' | 'terms' }) {
  return <article className="public-legal">
    <h1>{kind === 'privacy' ? 'Privacy' : 'Terms of use'}</h1>
    <p className="public-lead">MetricGuard is an invitation-only beta for investigating metric differences.</p>
    {kind === 'privacy' ? <>
      <h2>What we store</h2><p>Your account email and name, workspace membership, uploaded datasets, calculation definitions, comparison results, supporting evidence and saved conclusions. Sign-in uses Google or GitHub; MetricGuard does not store your provider password.</p>
      <h2>Where data is stored</h2><p>The hosted setup uses Neon (Postgres) for account and application records, Cloudflare R2 for datasets and evidence, and Northflank to run the application. Region: [region]. Local installations store data on the computer running MetricGuard.</p>
      <h2>Retention and deletion</h2><p>The beta has no automatic retention period yet. Data remains until you delete it. In Settings, choose Delete my account and type DELETE. This removes your account and workspaces you own with no other members, including their uploaded data and results.</p><p>Shared workspace records remain for other members, and your membership is removed. Required audit attribution is anonymised. Downloaded reports and copies held by other people cannot be removed by MetricGuard. Active jobs must finish before deletion; a storage failure requires retrying the deletion.</p>
      <h2>Access and contact</h2><p>Workspace roles control access. Session cookies keep you signed in; your theme choice is stored in your browser. For privacy questions or help deleting data, contact [contact email].</p>
    </> : <>
      <h2>Using the beta</h2><p>Use MetricGuard only with data you are authorised to process and share with your workspace. Protect your account and API keys. The beta may change or be unavailable; keep independent copies of anything you need.</p>
      <h2>What a result means</h2><p>MetricGuard compares supported calculations on the frozen inputs you provide. A matching replay verifies reproducibility, not the business meaning of a metric or the accuracy of its source data. Review the definitions, context and evidence before relying on a result.</p>
      <h2>Your information</h2><p>We store your account email and name, uploaded datasets and results. The hosted setup uses Neon (Postgres), Cloudflare R2 and Northflank in [region]. See the <a href="/privacy">privacy page</a> for details.</p>
      <h2>Keeping and deleting data</h2><p>There is no automatic expiry during this beta. Use Settings → Delete my account to remove your account and sole-member owned workspaces. Shared workspace records remain for other members. Downloaded or independently retained copies remain outside this deletion.</p>
      <h2>Questions</h2><p>Contact [contact email] for support or questions about these terms.</p>
    </>}
  </article>;
}

function NotFound() {
  return <section className="public-not-found"><p className="public-error-code">404</p><h1>This page does not exist.</h1><p>The address may be incomplete, or the page may have moved.</p><a className="button secondary" href="/">Go to the homepage <ArrowRight size={16} aria-hidden="true" /></a></section>;
}
