import React, { useEffect, useRef, useState } from 'react';
import {
  Database,
  FileCode2,
  GitCompareArrows,
  Layers3,
  LayoutDashboard,
  Menu,
  PlayCircle,
  Settings,
  X,
} from 'lucide-react';
import { api, ApiError, hasApiCredential, type Bootstrap, type Run } from './api';
import { GateScreen, restoreSession, WorkspaceChooser } from './AuthScreens';
import { chooseWorkspaceAfterConnect, classifyLoadError, readReloadEnded, type GateState } from './authState';
import { ConnectionControl, describeAuthError, useIdentityEpoch } from './identity';
import { Dashboard } from './Dashboard';
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


const NAV_ITEMS: Array<{ id: Page; label: string; icon: React.ReactNode }> = [
  { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={17} /> },
  { id: 'start', label: 'Start a comparison', icon: <PlayCircle size={17} /> },
  { id: 'metrics', label: 'Metrics', icon: <FileCode2 size={17} /> },
  { id: 'data', label: 'Data Sources', icon: <Database size={17} /> },
  { id: 'changes', label: 'Changes', icon: <GitCompareArrows size={17} /> },
  { id: 'investigations', label: 'Investigations', icon: <Layers3 size={17} /> },
];

export function App() {
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
  const prevWorkspaceRef = useRef(route.workspaceId);
  const identityEpoch = useIdentityEpoch();

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
    } catch { /* fall through: bootstrap classifies invalid keys and outages */ }
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

  const activeNavLabel =
    NAV_ITEMS.find((item) => item.id === route.page)?.label ||
    (route.page === 'settings' ? 'Settings' : 'Dashboard');

  const runnerReady = data ? (data.mode === 'demo' ? true : Boolean(data.runner_available)) : true;
  // Investigations: no id → unified library; saved id → report; 'new' or edit=1 → guided flow.
  const invId = route.query.investigationId;
  const showLibrary = route.page === 'investigations' && !invId;
  const showReport = route.page === 'investigations' && !!invId && invId !== 'new' && route.query.edit !== '1';
  const showEditor = route.page === 'investigations' && !!invId && !showReport;
  const openRun = (run: Run) => navigate({ page: 'investigations', query: { investigationId: run.id } });
  const startWith = (query: Record<string, string>) => navigate({ page: 'start', query });
  const guided = (prefill: { from?: string; left?: string; right?: string }) => (
    <StartComparison
      key={`${route.workspaceId}:${prefill.from || ''}:${prefill.left || ''}:${prefill.right || ''}`}
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
          <div className="brand-mark"><Layers3 size={18} /></div>
          <strong>metricguard</strong>
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
      <aside className={`sidebar ${mobileMenuOpen ? 'open' : ''}`}>
        <div className="brand">
          <div className="brand-mark">
            <Layers3 size={18} />
          </div>
          <div>
            <strong>metricguard</strong>
            <small>RECONCILIATION STUDIO</small>
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
            const isActive = route.page === item.id;
            return (
              <button
                type="button"
                key={item.id}
                className={`nav-item ${isActive ? 'active' : ''}`}
                onClick={() => navigate({ page: item.id })}
              >
                {item.icon}
                <span>{item.label}</span>
                {isActive && <span className="nav-dot" />}
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

          {/* System status pill in BLUE (strictly no green) */}
          <div className={`status-indicator ${runnerReady ? 'ready' : 'warning'}`}>
            <span className="status-dot" />
            <span>{runnerReady ? 'System ready' : 'Processing unavailable'}</span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main" id="main-content">
        {/* Topbar */}
        <header className="topbar">
          <button
            type="button"
            className="mobile-menu icon-button"
            onClick={() => setMobileMenuOpen(true)}
            aria-label="Open navigation"
          >
            <Menu size={20} />
          </button>

          <div className="breadcrumbs">
            {data?.project?.name || 'Workspace'} <span>/</span> <b>{activeNavLabel}</b>
          </div>

          <div className="topbar-right">
            <ConnectionControl workspaceId={route.workspaceId} />
            <span className={`topbar-status ${runnerReady ? 'ready' : 'warning'}`}>
              <span className="status-dot" />
              {runnerReady ? 'System ready' : 'Processing unavailable'}
            </span>
            <span className="avatar">MG</span>
          </div>
        </header>

        {/* Dynamic Route Content */}
        <div className="content" key={identityEpoch}>
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
              {route.page === 'dashboard' && (
                <Dashboard
                  key={route.workspaceId}
                  data={data}
                  refresh={refresh}
                  onNavigate={(page, tab, query) => navigate({ page, tab, query })}
                  onOpenRun={openRun}
                  onSelectCandidate={(c) => startWith({ left: c.left_version_id, right: c.right_version_id })}
                />
              )}

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
                  onComparePair={(leftId, rightId) => startWith({ left: leftId, right: rightId })}
                  onStartReplay={(_defId, verId) => startWith({ left: verId, right: verId })}
                />
              )}

              {route.page === 'start' && guided({ from: route.query.from, left: route.query.left, right: route.query.right })}

              {route.page === 'data' && (
                <DataSourcesView key={route.workspaceId} data={data} refresh={refresh} openAdd={route.query.add === '1'} />
              )}

              {route.page === 'changes' && (
                <ChangesView
                  key={route.workspaceId}
                  data={data}
                  refresh={refresh}
                  changeId={route.query.changeId}
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

              {showLibrary && (
                <InvestigationLibrary
                  key={route.workspaceId}
                  data={data}
                  workspaceId={route.workspaceId}
                  onStart={() => navigate({ page: 'start' })}
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
