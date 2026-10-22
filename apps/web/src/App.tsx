import React, { useEffect, useRef, useState } from 'react';
import {
  Database,
  FileCode2,
  GitCompareArrows,
  FilePlus2,
  FolderOpen,
  Menu,
  MessageSquare,
  Settings,
  X,
} from 'lucide-react';
import { api, ApiError, hasApiCredential, type Bootstrap, type Run } from './api';
import { GateScreen, restoreSession, WorkspaceChooser } from './AuthScreens';
import { chooseWorkspaceAfterConnect, classifyLoadError, readReloadEnded, type GateState } from './authState';
import { AccountMenu, GuestNotice, describeAuthError, useIdentityEpoch } from './identity';
import { ThemeSwitch } from './ThemeSwitch';
import { Logo } from './Logo';
import { ErrorBoundary } from './ErrorBoundary';
import {
  buildRouteUrl,
  parseRoute,
  type Page,
  type RouteState,
} from './router';
import { CaseReport } from './views/CaseReport';
import { ChangesView } from './views/ChangesView';
import { DataSourcesView } from './views/DataSourcesView';
import { InvestigationLibrary } from './views/InvestigationLibrary';
import { MetricsView } from './views/MetricsView';
import { SettingsView } from './views/SettingsView';
import { StartComparison } from './views/StartComparison';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { SignInScreen } from './SignInScreen';
import { loadAuthProviders } from './authApi';
import { shouldRedirectToSignIn } from './authFlow';
import { sitePage, sharedCaseToken } from './router';
import { PublicCasePage } from './PublicCasePage';
import { PublicPages } from './PublicPages';
import { useDialog } from './useDialog';
import { FeedbackDialog } from './views/FeedbackDialog';


const NAV_ITEMS: Array<{ id: Page; label: string; icon: React.ReactNode }> = [
  { id: 'investigations', label: 'Cases', icon: <FolderOpen size={17} /> },
  { id: 'start', label: 'New comparison', icon: <FilePlus2 size={17} /> },
  { id: 'data', label: 'Datasets', icon: <Database size={17} /> },
  { id: 'metrics', label: 'Metrics', icon: <FileCode2 size={17} /> },
  { id: 'changes', label: 'Changes', icon: <GitCompareArrows size={17} /> },
];

export function App() {
  const [pathname, setPathname] = useState(window.location.pathname);
  useEffect(() => {
    const sync = () => setPathname(window.location.pathname);
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  const page = sitePage(pathname);
  if (page === 'shared-case') return <PublicCasePage token={sharedCaseToken(pathname)!} />;
  if (page === 'signin' || page === 'signup') return <SignInScreen mode={page} />;
  if (page !== 'workspace') return <PublicPages page={page} />;
  return <WorkspaceApp />;
}

function WorkspaceApp() {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [route, setRoute] = useState<RouteState>(() => {
    const initial = parseRoute(window.location.pathname, window.location.search);
    api.setActiveWorkspace(initial.workspaceId);
    return initial;
  });
  const [data, setData] = useState<Bootstrap | null>(null);
  const [error, setError] = useState('');
  const [gate, setGate] = useState<GateState>({ kind: 'loading' });
  const dataRef = useRef<Bootstrap | null>(null);
  dataRef.current = data;
  const [chooserOpen, setChooserOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(() => window.matchMedia('(max-width: 900px)').matches);
  const prevWorkspaceRef = useRef(route.workspaceId);
  const identityEpoch = useIdentityEpoch();
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const navigationRef = useDialog<HTMLElement>(mobileMenuOpen, () => setMobileMenuOpen(false));

  useEffect(() => {
    const media = window.matchMedia('(max-width: 900px)');
    const sync = () => {
      setMobileNavigation(media.matches);
      if (!media.matches) setMobileMenuOpen(false);
    };
    media.addEventListener('change', sync);
    return () => media.removeEventListener('change', sync);
  }, []);

  useEffect(() => {
    let active = true;
    loadAuthProviders().then(value => { if (active) setAuthRequired(value.auth_required); }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!shouldRedirectToSignIn(gate, authRequired === true, hasApiCredential())) return;
    const returnTo = window.location.pathname + window.location.search;
    window.location.replace(`/signin?return_to=${encodeURIComponent(returnTo)}`);
  }, [gate, authRequired, identityEpoch]);

  // Synchronize route with browser history
  const navigate = (
    to:
      | string
      | {
          workspaceId?: string;
          page: Page;
          tab?: string;
          query?: Record<string, string>;
        }
  ) => {
    let url: string;
    if (typeof to === 'string') {
      url = to;
    } else {
      const ws = to.workspaceId || route.workspaceId;
      api.setActiveWorkspace(ws);
      url = buildRouteUrl(ws, to.page, to.tab, to.query);
    }

    if (window.location.pathname + window.location.search !== url) {
      window.history.pushState(null, '', url);
    }
    const nextRoute = parseRoute(window.location.pathname, window.location.search, route.workspaceId);
    api.setActiveWorkspace(nextRoute.workspaceId);
    setRoute(nextRoute);
    setMobileMenuOpen(false);
    window.scrollTo(0, 0);
  };

  // Popstate listener for back / forward navigation
  useEffect(() => {
    const handlePopState = () => {
      const nextRoute = parseRoute(
        window.location.pathname,
        window.location.search,
        route.workspaceId
      );
      api.setActiveWorkspace(nextRoute.workspaceId);
      setRoute(nextRoute);
    };

    window.addEventListener('popstate', handlePopState);
    return () => {
      window.removeEventListener('popstate', handlePopState);
    };
  }, [route.workspaceId]);

  // A "keep me signed in" session survives reloads; pick it up once on load.
  useEffect(() => { void restoreSession(); }, []);

  // Initial redirect if path was legacy or missing workspace
  useEffect(() => {
    const expectedUrl = buildRouteUrl(route.workspaceId, route.page, route.tab, route.query);
    const currentFull = window.location.pathname + window.location.search;
    if (currentFull !== expectedUrl) {
      window.history.replaceState(null, '', expectedUrl);
    }
  }, []);

  // Fetch workspace bootstrap data
  const loadWorkspace = async (workspaceId: string) => {
    try {
      const res = await api.bootstrap(workspaceId);
      setData(res);
      setError('');
      setGate({ kind: 'ready' });
      return res;
    } catch (err: unknown) {
      if (err instanceof ApiError && err.code === 'IDENTITY_CHANGED') return null;
      const connected = hasApiCredential();
      let reloadEnded = false;
      try { reloadEnded = readReloadEnded(window.sessionStorage, connected); } catch { /* storage blocked */ }
      const next = classifyLoadError(err, { connected, reloadEnded });
      // A refresh failure while data is on screen stays a dismissible notice;
      // a failure to open the workspace at all gets a dedicated screen.
      if (dataRef.current && next.kind !== 'signin' && next.kind !== 'denied') {
        setError(describeAuthError(err, workspaceId));
        return null;
      }
      setData(null);
      setGate(next);
      return null;
    }
  };

  // After a key is connected: stay if it can open this workspace, enter a sole
  // workspace automatically, otherwise ask. Authorization is still enforced server-side.
  const settleAfterConnect = async () => {
    try {
      const allowed = await api.listWorkspaces();
      const decision = chooseWorkspaceAfterConnect(route.workspaceId, allowed);
      if (decision.action === 'go') { navigate({ workspaceId: decision.workspaceId, page: 'dashboard' }); return; }
      if (decision.action === 'choose') { setGate({ kind: 'denied', reason: 'not_member', message: `No active role in '${route.workspaceId}'.` }); setChooserOpen(true); return; }
      if (decision.action === 'none') { setGate({ kind: 'denied', reason: 'not_member', message: 'This key has no workspace roles yet.' }); return; }
    } catch (err) {
      // A network failure means the service is down: say so now instead of
      // retrying more requests that will each take seconds to fail.
      if (err instanceof TypeError) { setData(null); setGate({ kind: 'unavailable', message: err.message }); return; }
      /* otherwise fall through: bootstrap classifies invalid keys */
    }
    void loadWorkspace(route.workspaceId);
  };

  useEffect(() => {
    api.setActiveWorkspace(route.workspaceId);
    if (prevWorkspaceRef.current !== route.workspaceId) {
      prevWorkspaceRef.current = route.workspaceId;
      setData(null);
      setGate({ kind: 'loading' });
      // If there are stale object IDs in query from previous workspace, reset them
      if (
        route.query.investigationId ||
        route.query.metricId ||
        route.query.changeId ||
        route.query.left ||
        route.query.right
      ) {
        navigate({ workspaceId: route.workspaceId, page: route.page });
        return;
      }
    }
    void loadWorkspace(route.workspaceId);
  }, [route.workspaceId]);

  // Identity connected / replaced / disconnected: drop all previously authorized
  // server data (bootstrap, opened runs, cached reports) and reload under the new identity.
  const firstEpochRef = useRef(identityEpoch);
  useEffect(() => {
    if (firstEpochRef.current === identityEpoch) return;
    firstEpochRef.current = identityEpoch;
    setData(null);
    setError('');
    setGate({ kind: 'loading' });
    if (hasApiCredential()) void settleAfterConnect();
    else void loadWorkspace(route.workspaceId);
  }, [identityEpoch]);

  const refresh = async () => {
    return loadWorkspace(route.workspaceId);
  };

  const handleSelectWorkspace = (newWorkspaceId: string) => {
    setChooserOpen(false);
    if (newWorkspaceId === route.workspaceId) { setGate({ kind: 'loading' }); void loadWorkspace(newWorkspaceId); return; }
    setData(null);
    setGate({ kind: 'loading' });
    navigate({ workspaceId: newWorkspaceId, page: 'dashboard' });
  };

  // The old Dashboard and Investigations pages are both Cases now; their links keep working.
  const navPage = route.page === 'dashboard' ? 'investigations' : route.page;
  const activeNavLabel =
    NAV_ITEMS.find((item) => item.id === navPage)?.label ||
    (route.page === 'settings' ? 'Settings' : 'Cases');
  const navCounts: Partial<Record<Page, number>> = data ? {
    investigations: (data.investigations || []).length,
    data: (data.snapshots || []).length,
    metrics: (data.metrics || []).length,
  } : {};

  const runnerReady = data ? (data.mode === 'demo' ? true : Boolean(data.runner_available)) : true;
  // Investigations: no id → unified library; saved id → report; 'new' or edit=1 → guided flow.
  const invId = route.query.investigationId;
  const showLibrary = (route.page === 'investigations' && !invId) || route.page === 'dashboard';
  const showReport = route.page === 'investigations' && !!invId && invId !== 'new' && route.query.edit !== '1';
  const showEditor = route.page === 'investigations' && !!invId && !showReport;
  const openRun = (run: Run) => navigate({ page: 'investigations', query: { investigationId: run.id } });
  const startWith = (query: Record<string, string>) => navigate({ page: 'start', query });
  const guided = (prefill: { from?: string; left?: string; right?: string; dataset?: string }) => (
    <StartComparison
      key={`${route.workspaceId}:${prefill.from || ''}:${prefill.left || ''}:${prefill.right || ''}:${prefill.dataset || ''}`}
      data={data!}
      refresh={refresh}
      prefill={prefill}
      onNavigate={(page, tab, query) => navigate({ page, tab, query })}
      onOpenInvestigation={(id) => navigate({ page: 'investigations', query: { investigationId: id } })}
    />
  );

  const chooser = (
    <WorkspaceChooser
      open={chooserOpen}
      currentWorkspaceId={route.workspaceId}
      onClose={() => setChooserOpen(false)}
      onSelect={handleSelectWorkspace}
    />
  );

  // Signed out: a dedicated screen instead of an app shell that looks stuck.
  if (!data && gate.kind === 'signin') {
    return (
      <div className="signed-out-shell">
        <header className="signed-out-brand">
          <Logo size={30} />
          <strong>MetricGuard</strong>
        </header>
        <main id="main-content">
          <GateScreen state={gate} workspaceId={route.workspaceId} onRetry={() => void refresh()} onChooseWorkspace={() => setChooserOpen(true)} />
        </main>
        {chooser}
      </div>
    );
  }

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      {chooser}
      {/* Sidebar matching Final v3 */}
      {mobileMenuOpen && <div className="mobile-navigation-backdrop" aria-hidden="true" onClick={() => setMobileMenuOpen(false)} />}
      <aside ref={navigationRef} id="workspace-navigation" className={`sidebar ${mobileMenuOpen ? 'open' : ''}`}
        inert={mobileNavigation && !mobileMenuOpen}
        role={mobileMenuOpen ? 'dialog' : undefined} aria-modal={mobileMenuOpen ? true : undefined} aria-label={mobileMenuOpen ? 'Workspace navigation' : undefined}>
        <div className="brand">
          <Logo size={30} />
          <div>
            <strong>MetricGuard</strong>
          </div>
          <button
            type="button"
            className="mobile-close icon-button"
            onClick={() => setMobileMenuOpen(false)}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
        </div>

        {/* Real Workspace Switcher */}
        <WorkspaceSwitcher
          key={identityEpoch}
          currentWorkspaceId={route.workspaceId}
          currentWorkspaceName={data?.project?.name || route.workspaceId}
          onSelectWorkspace={handleSelectWorkspace}
        />

        {/* Primary Navigation */}
        <div className="nav-label">NAVIGATION</div>
        <nav aria-label="Main navigation">
          {NAV_ITEMS.map((item) => {
            const isActive = navPage === item.id;
            return (
              <button
                type="button"
                key={item.id}
                className={`nav-item ${isActive ? 'active' : ''}`}
                onClick={() => navigate({ page: item.id })}
              >
                {item.icon}
                <span>{item.label}</span>
                {navCounts[item.id] != null && <span className="nav-count">{navCounts[item.id]}</span>}
              </button>
            );
          })}
        </nav>

        {/* Sidebar Footer */}
        <div className="sidebar-footer">
          <button
            type="button"
            className={`nav-item ${route.page === 'settings' ? 'active' : ''}`}
            onClick={() => navigate({ page: 'settings' })}
          >
            <Settings size={17} />
            <span>Settings</span>
          </button>

          {authRequired && <button type="button" className="nav-item" onClick={() => setFeedbackOpen(true)}>
            <MessageSquare size={17} />
            <span>Feedback</span>
          </button>}

          <div className={`status-indicator ${runnerReady ? 'ready' : 'warning'}`} role="status">
            <span className="status-dot" />
            <span>{runnerReady ? 'Engine ready' : 'Comparison engine not running'}</span>
          </div>
        </div>
      </aside>
      {feedbackOpen && <FeedbackDialog workspaceId={route.workspaceId} onClose={() => setFeedbackOpen(false)} />}

      {/* Main Content */}
      <main className="main" id="main-content">
        {/* Topbar */}
        <header className="topbar">
          <button
            type="button"
            className="mobile-menu icon-button"
            onClick={() => setMobileMenuOpen(true)}
            aria-label="Open navigation"
            aria-expanded={mobileMenuOpen}
            aria-controls="workspace-navigation"
          >
            <Menu size={20} />
          </button>

          <div className="breadcrumbs">
            {data?.project?.name || 'Workspace'} <span>/</span> <b>{activeNavLabel}</b>
          </div>

          <div className="topbar-right">
            <ThemeSwitch compact />
            <AccountMenu workspaceId={route.workspaceId} authRequired={authRequired} onSettings={() => navigate({ page: 'settings', tab: 'account' })} />
          </div>
        </header>

        {/* Dynamic Route Content */}
        <div className="content" key={identityEpoch}>
          <GuestNotice workspaceId={route.workspaceId} />
          {error && (
            <div className="alert error" role="alert">
              <span>
                <strong>System notice:</strong> {error}
              </span>
              <button onClick={() => setError('')} aria-label="Dismiss error">
                <X size={15} />
              </button>
            </div>
          )}

          <ErrorBoundary resetKey={window.location.pathname + window.location.search} onHome={() => navigate({ page: 'investigations' })}>
          {!data ? (
            <GateScreen
              state={gate.kind === 'ready' ? { kind: 'loading' } : gate}
              workspaceId={route.workspaceId}
              onRetry={() => { setGate({ kind: 'loading' }); void refresh(); }}
              onChooseWorkspace={() => setChooserOpen(true)}
            />
          ) : (
            <>
              {route.page === 'metrics' && (
                <MetricsView
                  key={route.workspaceId}
                  data={data}
                  refresh={refresh}
                  activeTab={route.tab || 'implementations'}
                  metricId={route.query.metricId}
                  onTabChange={(tab) => navigate({ page: 'metrics', tab, query: route.query })}
                  onOpenRun={openRun}
                  onSelectMetric={(mId) => {
                    if (mId) {
                      navigate({ page: 'metrics', query: { ...route.query, metricId: mId } });
                    } else {
                      const newQuery = { ...route.query };
                      delete newQuery.metricId;
                      navigate({ page: 'metrics', query: newQuery });
                    }
                  }}
                  onComparePair={(leftId, rightId, _relationship, snapshotId) => startWith({ left: leftId, right: rightId, ...(snapshotId ? {dataset: snapshotId} : {}) })}
                  onStartReplay={(_defId, verId) => startWith({ left: verId, right: verId })}
                />
              )}

              {route.page === 'start' && guided({ from: route.query.from, left: route.query.left, right: route.query.right, dataset: route.query.dataset })}

              {route.page === 'data' && (
                <DataSourcesView key={route.workspaceId} data={data} refresh={refresh} openAdd={route.query.add === '1'}
                  onCompare={id => startWith({ dataset: id })}
                  onCheckMetrics={(earlier, later) => navigate({page:'changes', query:{baseline_load:earlier, target_load:later, check_load:'1'}})}
                  onCreatedReturn={route.query.return === 'start' ? (id => navigate({ page: 'start', query: { dataset: id } })) : undefined} />
              )}

              {route.page === 'changes' && (
                <ChangesView
                  key={route.workspaceId}
                  data={data}
                  refresh={refresh}
                  changeId={route.query.changeId}
                  baselineLoadId={route.query.baseline_load}
                  targetLoadId={route.query.target_load}
                  onSelectChange={(changeId) => {
                    if (changeId) {
                      navigate({ page: 'changes', query: { ...route.query, changeId } });
                    } else {
                      const newQuery = { ...route.query };
                      delete newQuery.changeId;
                      navigate({ page: 'changes', query: newQuery });
                    }
                  }}
                  onNavigate={(page, tab, query) => navigate({ page, tab, query })}
                  onOpenRun={openRun}
                />
              )}

              {route.notFound && (
                <div className="alert error" role="alert">
                  <span>There is no page at <code>{route.rawPath}</code>. Showing Cases instead; use the navigation to find the page you wanted.</span>
                  <button type="button" onClick={() => navigate({ page: 'investigations' })} aria-label="Dismiss message">×</button>
                </div>
              )}
              {showLibrary && (
                <InvestigationLibrary
                  key={route.workspaceId}
                  data={data}
                  workspaceId={route.workspaceId}
                  onStart={() => navigate({ page: 'start' })}
                  onAddData={() => navigate({ page: 'data', query: { add: '1', return: 'start' } })}
                  onSampleReady={async id => { await refresh(); startWith({ dataset: id }); }}
                  onChanged={refresh}
                  onOpenRun={openRun}
                  onOpen={(item) => item.source === 'saved'
                    ? navigate({ page: 'investigations', query: { investigationId: item.id } })
                    : startWith({ from: item.id })}
                />
              )}
              {showReport && (
                <CaseReport
                  key={`${route.workspaceId}:${invId}`}
                  data={data}
                  investigationId={invId!}
                  refresh={refresh}
                  onBack={() => navigate({ page: 'investigations' })}
                  onEdit={() => startWith({ from: invId! })}
                  onOpenMetric={metricId => navigate({ page: 'metrics', query: { metricId } })}
                  onNotSaved={() => {
                    // Drafts and bare runs open in the guided flow instead of the report.
                    const url = buildRouteUrl(route.workspaceId, 'start', undefined, { from: invId! });
                    window.history.replaceState(null, '', url);
                    setRoute(parseRoute(window.location.pathname, window.location.search, route.workspaceId));
                  }}
                />
              )}
              {showEditor && guided({ from: invId !== 'new' ? invId : undefined, left: route.query.left, right: route.query.right })}

              {route.page === 'settings' && (
                <SettingsView
                  key={route.workspaceId}
                  data={data}
                  refresh={refresh}
                  activeTab={route.tab || 'system'}
                  onTabChange={(tab) => navigate({ page: 'settings', tab })}
                />
              )}
            </>
          )}
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
