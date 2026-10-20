import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Database, Fingerprint, LockKeyhole } from 'lucide-react';
import { api } from './api';
import { Logo } from './Logo';
import { ThemeSwitch } from './ThemeSwitch';
import { readThemeChoice } from './themeChoice';
import { Bridge } from './views/evidence/Bridge';
import { HashPrint } from './views/evidence/HashPrint';
import { Stamp } from './views/evidence/Stamp';
import { homeRedirect, type SitePage } from './router';
import { DemoCasePage } from './DemoCasePage';
import { scrollToHash } from './publicScroll';
import { CONTACT_EMAIL, HOSTING_REGION } from './publicFacts';
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
    const titles = { home: 'MetricGuard — Explain the difference. Keep the proof.', privacy: 'Privacy — MetricGuard', terms: 'Terms — MetricGuard', demo: 'Sample case MG-F3A3 — MetricGuard', 'not-found': 'Page not found — MetricGuard' };
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
      {page === 'home' ? <Homepage /> : page === 'demo' ? <DemoCasePage /> : page === 'not-found' ? <NotFound /> : <LegalPage kind={page} />}
    </main>
    <footer className="public-footer public-width"><div><span>© MetricGuard</span><span>Built for data analysts</span></div><nav aria-label="Legal"><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="https://github.com/Adityabeecha/Metricguard" rel="noreferrer">GitHub</a></nav></footer>
  </div>;
}

function Homepage() {
  const [choice, setChoice] = useState(readThemeChoice);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const go = () => scrollToHash(window.location.hash, document, window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const frame = requestAnimationFrame(go);
    window.addEventListener('hashchange', go);
    return () => { cancelAnimationFrame(frame); window.removeEventListener('hashchange', go); };
  }, []);
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
    <section className="home-hero home-perforation" aria-labelledby="home-title"><div className="public-width hero-stack">
      <div className="hero-copy" data-reveal>
        <h1 id="home-title">Your metric changed. See exactly which records changed it.</h1>
        <p className="public-lead">Compare two calculations, follow the record-level difference, and hand over sealed evidence anyone with access can replay.</p>
        <div className="hero-actions"><a className="button primary public-cta" href="/signup">Sign up — free beta <ArrowRight size={18} aria-hidden="true" /></a><a className="button secondary public-cta" href="/demo/MG-F3A3">Open the sample case <ArrowRight size={18} aria-hidden="true" /></a></div>
        <p className="hero-sub-links"><a className="home-how-link" href="#how">See how it works <ArrowRight size={14} aria-hidden="true" /></a></p>
        <p className="public-beta">Beta · by invitation</p>
      </div>
      <HeroReconciliation />
    </div></section>

    <section className="home-section home-panel" id="problem" aria-labelledby="problem-title"><div className="public-width" data-reveal>
      <h2 id="problem-title">The same metric. Two different stories.</h2>
      <div className="dashboard-disagreement" role="img" aria-label="Illustrative disagreement: Finance reports Revenue 9.87 billion; Product reports Revenue 2.47 billion.">
        <div className="dashboard-tile"><span>Finance</span><div>Revenue<strong>9.87B</strong></div><div className="tile-lines" aria-hidden="true"><i /><i /><i /><i /><i /></div></div>
        <span className="disagreement-mark" aria-hidden="true">?</span>
        <div className="dashboard-tile"><span>Product</span><div>Revenue<strong>2.47B</strong></div><div className="tile-lines alternate" aria-hidden="true"><i /><i /><i /><i /><i /></div></div>
      </div>
      <p className="illustration-note">Illustrative totals.</p>
      <p className="home-problem-line">Two teams, one metric, two numbers, and hours of SQL and spreadsheets to find the records that disagree, with nothing to hand over at the end.</p>
    </div></section>

    <section className="home-section" id="how" aria-labelledby="how-title"><div className="public-width">
      <h2 id="how-title" data-reveal>Three steps. A defensible answer.</h2>
      <div className="home-stepper">
        <article className="home-step" data-reveal><span className="step-number">01</span><h3>Choose and freeze your data</h3><p>Fix the inputs before comparing.</p><div className="step-vignette dataset-vignette"><Database size={19} aria-hidden="true" /><div><strong>orders</strong><span>1,000,000 rows</span></div><span className="frozen-label"><LockKeyhole size={12} aria-hidden="true" />frozen</span></div></article>
        <article className="home-step" data-reveal><span className="step-number">02</span><h3>Define the current and proposed</h3><p>Change the rule, keep the data.</p><div className="step-vignette sql-vignette"><code>SUM(amount)</code><code>SUM(amount)<br /><mark>WHERE status = 'completed'</mark></code></div></article>
        <article className="home-step" data-reveal><span className="step-number">03</span><h3>Read, explain and seal it</h3><p>Keep the answer and its proof.</p><div className="step-vignette answer-vignette"><strong>−340.00 <small>INR</small></strong><span className="mini-seal"><Fingerprint size={14} aria-hidden="true" />SEALED</span></div></article>
      </div>
      <p className="illustration-note">Sample case uses synthetic orders.</p>
    </div></section>

    <section className="home-section home-panel" id="evidence" aria-labelledby="evidence-title"><div className="public-width">
      <div className="evidence-heading" data-reveal><h2 id="evidence-title">See the evidence.</h2><p>The answer, the contributing records and the seal. One case.</p></div>
      <figure className="public-case" data-reveal>
        <a className="browser-frame case-link" href="/demo/MG-F3A3" aria-label="Open sample case MG-F3A3"><div className="browser-bar" aria-hidden="true"><span className="browser-dots"><i /><i /><i /></span><span>MetricGuard / Demo case</span><LockKeyhole size={13} /></div>
          <div className="evidence-image"><picture>
            <source media={mobileDarkMedia} srcSet="/images/case-mobile-dark.webp" width="358" height="570" />
            <source media="(max-width: 759px)" srcSet="/images/case-mobile-light.webp" width="358" height="570" />
            <source media={darkMedia} srcSet="/images/case-dark.webp" />
            <img src="/images/case-light.webp" width="1366" height="1000" loading="lazy" decoding="async" alt="Real MetricGuard demo case: the answer, −340.00 INR; the reconciliation bridge from current 750.75 INR to proposed 410.75 INR, balanced; and the evidence seal MG-F3A3 with its hash fingerprints." />
          </picture><span className="shot-pin pin-answer" aria-hidden="true">1</span><span className="shot-pin pin-records" aria-hidden="true">2</span><span className="shot-pin pin-seal" aria-hidden="true">3</span></div>
        </a>
        <figcaption><span><b>1</b>The answer</span><span><b>2</b>The reconciliation bridge</span><span><b>3</b>The evidence seal</span><a className="case-open-link" href="/demo/MG-F3A3">Open this case — the tabs work there <ArrowRight size={14} aria-hidden="true" /></a><small className="mobile-crop-note">Mobile detail: the answer and seal. The bridge and records follow below in the full case.</small></figcaption>
      </figure>
    </div></section>

    <section className="home-section" aria-labelledby="support-title"><div className="public-width" data-reveal>
      <h2 id="support-title">Works with your data.</h2>
      <div className="home-register">
        <dl><dt>Sources</dt>{['CSV', 'Parquet', 'Excel', 'Two connected tables', 'PostgreSQL'].map(text => <dd key={text}>{text}</dd>)}{['Snowflake', 'BigQuery'].map(text => <dd key={text}>{text} <span className="beta-tag">Beta</span></dd>)}</dl>
        <dl><dt>Calculations</dt>{['SUM', 'COUNT', 'COUNT DISTINCT', 'AVG', 'Ratios', 'Rates', 'Shares', 'Margins'].map(text => <dd key={text}>{text}</dd>)}</dl>
      </div>
    </div></section>

    <section className="home-section home-panel" id="security" aria-labelledby="security-title"><div className="public-width">
      <div className="custody-heading" data-reveal>
        <h2 id="security-title">Every case keeps its custody log.</h2>
        <p>Frozen inputs, exact arithmetic and a hash at every step. Replay re-runs the saved inputs; matching evidence draws the same fingerprint.</p>
      </div>
      <ol className="custody-log home-custody" data-reveal aria-label="Custody log of demo case MG-F3A3">
        <li className="done"><div><div className="what">Dataset frozen</div><div className="when">17 Oct 2026, 21:15</div><div className="detail">Demo sales orders · 8 rows <HashPrint hash={DEMO.dataset} /></div></div></li>
        <li className="done"><div><div className="what">Calculations pinned</div><div className="detail">Current <HashPrint hash={DEMO.current} /> Proposed <HashPrint hash={DEMO.proposed} /></div></div></li>
        <li className="done"><div><div className="what">Compared on the isolated engine</div><div className="when">17 Oct 2026, 21:15 · no network</div><div className="detail">Evidence <HashPrint hash={DEMO.evidence} /></div></div></li>
        <li className="done"><div><div className="what">Sealed as case MG-F3A3</div><div className="when">17 Oct 2026, 21:15</div></div></li>
        <li className="ok"><div><div className="what">Replayed: identical</div><div className="when">20 Oct 2026, 15:57</div><div className="detail">Evidence <HashPrint hash={DEMO.evidence} /> <Stamp kind="replayed" /></div></div></li>
      </ol>
    </div></section>

    <section className="home-closing" aria-labelledby="closing-title"><div className="public-width" data-reveal><h2 id="closing-title">Stop guessing why the number moved.</h2><div><a className="button primary public-cta" href="/signup">Sign up — free beta <ArrowRight size={18} aria-hidden="true" /></a><a href="/signin">Sign in</a></div></div></section>
  </div>;
}

// The demo case shown on the homepage: real values and hashes from the seeded demo workspace.
const DEMO = {
  dataset: '442bd3d056832e6ea3a24bc62635fcffc72c957f4deef1d7e50d28c5990af648',
  current: '6b999209d489aebcf4fb97d3b256bacdf59861d822dea0a4a908135cb5ee64c2',
  proposed: '41f98de6826f493fadbf63d56d07bf74b097be7ab1467224126809c92378afa8',
  evidence: '761d2b86d3f47de44f00929f5ab7053a31fd41143cf6214f4b5fdaf20ae790a7',
};

/** The product's own picture: the demo case's reconciliation bridge, drawn by the same component as the app. */
function HeroReconciliation() {
  return <figure className="hero-reconciliation" data-reveal>
    <figcaption className="hero-case-line">
      <span className="case-no">MG-F3A3</span>
      <span>Demo · Revenue, completed orders only</span>
      <Stamp kind="sealed" />
    </figcaption>
    <Bridge
      start={{ label: 'Current', value: 750.75, text: '750.75 INR' }}
      end={{ label: 'Proposed', value: 410.75, text: '410.75 INR' }}
      steps={[{ key: 'excluded', label: 'Excluded by proposed', value: -340, text: '\u2212340.00 INR', records: 3 }]}
      residualText="0.00 INR" balanced
      description="Demo reconciliation: current revenue 750.75 INR, minus 340.00 INR from 3 records excluded by the proposed calculation, unexplained 0.00, equals proposed 410.75 INR. Balanced." />
    <p className="demo-caption">Synthetic demo orders: R003 −200.00, R007 −90.00 and R004 −50.00 explain the difference exactly.</p>
  </figure>;
}

function LegalPage({ kind }: { kind: 'privacy' | 'terms' }) {
  return <article className="public-legal">
    <h1>{kind === 'privacy' ? 'Privacy' : 'Terms of use'}</h1>
    <p className="public-lead">MetricGuard is an invitation-only beta for investigating metric differences.</p>
    {kind === 'privacy' ? <>
      <h2>What we store</h2><p>Your account email and name, workspace membership, uploaded datasets, calculation definitions, comparison results, supporting evidence and saved conclusions. Sign-in uses Google or GitHub; MetricGuard does not store your provider password.</p>
      <h2>Where data is stored</h2><p>The hosted setup uses Neon (Postgres) for account and application records, Cloudflare R2 for datasets and evidence, and Northflank to run the application. Region: {HOSTING_REGION}. Local installations store data on the computer running MetricGuard.</p>
      <h2>Retention and deletion</h2><p>The beta has no automatic retention period yet. Data remains until you delete it. In Settings, choose Delete my account and type DELETE. This removes your account and workspaces you own with no other members, including their uploaded data and results.</p><p>Shared workspace records remain for other members, and your membership is removed. Required audit attribution is anonymised. Downloaded reports and copies held by other people cannot be removed by MetricGuard. Active jobs must finish before deletion; a storage failure requires retrying the deletion.</p>
      <h2>Access and contact</h2><p>Workspace roles control access. Session cookies keep you signed in; your theme choice is stored in your browser. For privacy questions or help deleting data, contact <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.</p>
    </> : <>
      <h2>Using the beta</h2><p>Use MetricGuard only with data you are authorised to process and share with your workspace. Protect your account and API keys. The beta may change or be unavailable; keep independent copies of anything you need.</p>
      <h2>What a result means</h2><p>MetricGuard compares supported calculations on the frozen inputs you provide. A matching replay verifies reproducibility, not the business meaning of a metric or the accuracy of its source data. Review the definitions, context and evidence before relying on a result.</p>
      <h2>Your information</h2><p>We store your account email and name, uploaded datasets and results. The hosted setup uses Neon (Postgres), Cloudflare R2 and Northflank in {HOSTING_REGION}. See the <a href="/privacy">privacy page</a> for details.</p>
      <h2>Keeping and deleting data</h2><p>There is no automatic expiry during this beta. Use Settings → Delete my account to remove your account and sole-member owned workspaces. Shared workspace records remain for other members. Downloaded or independently retained copies remain outside this deletion.</p>
      <h2>Questions</h2><p>Contact <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> for support or questions about these terms.</p>
    </>}
  </article>;
}

function NotFound() {
  return <section className="public-not-found"><p className="public-error-code">404</p><h1>This page does not exist.</h1><p>The address may be incomplete, or the page may have moved.</p><a className="button secondary" href="/">Go to the homepage <ArrowRight size={16} aria-hidden="true" /></a></section>;
}
