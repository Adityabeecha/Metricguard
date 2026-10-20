export type Page = 'dashboard' | 'start' | 'metrics' | 'data' | 'changes' | 'investigations' | 'settings';

export interface RouteState {
  workspaceId: string;
  page: Page;
  tab?: string;
  query: Record<string, string>;
  rawPath: string;
  /** The path named no known page; the app says so instead of quietly showing Cases. */
  notFound?: boolean;
}

export function parseRoute(pathname: string, search: string = '', defaultWorkspaceId: string = 'local-retail'): RouteState {
  const params = new URLSearchParams(search);
  const query: Record<string, string> = {};
  params.forEach((v, k) => { query[k] = v; });

  const clean = pathname.replace(/\/+$/, '') || '/';
  
  // Format: /w/:workspaceId/:page(/:subId)
  const match = clean.match(/^\/w\/([a-zA-Z0-9_-]+)(?:\/([a-zA-Z0-9_-]+))?(?:\/([a-zA-Z0-9_.-]+))?$/);
  if (match) {
    const workspaceId = match[1] || defaultWorkspaceId;
    const rawPage = match[2] || 'dashboard';
    const subId = match[3];
    let page: Page = 'dashboard';
    let tab = query.tab;
    if (subId && !['metrics', 'changes', 'investigations'].includes(rawPage)) {
      return { workspaceId, page, query, rawPath: clean, notFound: true };
    }

    if (rawPage === 'metrics' && subId && !query.metricId) {
      query.metricId = subId;
    }
    if (rawPage === 'changes' && subId && !query.changeId) {
      query.changeId = subId;
    }
    if (rawPage === 'investigations' && subId && !query.investigationId) {
      query.investigationId = subId;
    }

    switch (rawPage) {
      case 'dashboard':
      case 'overview':
        page = 'dashboard';
        break;
      case 'metrics':
      case 'inventory':
      case 'definitions':
        page = 'metrics';
        break;
      case 'data':
      case 'datasets':
      case 'data-sources':
        page = 'data';
        break;
      case 'changes':
        page = 'changes';
        break;
      case 'start':
      case 'new-comparison':
        page = 'start';
        break;
      case 'investigations':
      case 'compare':
        page = 'investigations';
        break;
      case 'flat':
        page = 'investigations';
        if (!query.mode) query.mode = 'flat';
        break;
      case 'history':
        page = 'metrics';
        tab = tab || 'history';
        break;
      case 'evidence':
        page = 'investigations';
        tab = tab || 'evidence';
        break;
      case 'adapters':
        page = 'settings';
        tab = tab || 'integrations';
        break;
      case 'evaluation':
        page = 'settings';
        tab = tab || 'evaluation';
        break;
      case 'settings':
        page = 'settings';
        break;
      default:
        page = 'dashboard';
        return { workspaceId, page, tab, query, rawPath: clean, notFound: true };
    }
    return { workspaceId, page, tab, query, rawPath: clean };
  }

  // Legacy root-level routes (e.g. /compare, /flat, /overview)
  let page: Page = 'dashboard';
  let tab = query.tab;
  const legacySeg = clean.slice(1).split('/')[0];
  if (clean.slice(1).includes('/')) {
    return { workspaceId: defaultWorkspaceId, page, query, rawPath: clean, notFound: true };
  }

  switch (legacySeg) {
    case 'compare':
    case 'investigations':
      page = 'investigations';
      break;
    case 'flat':
      page = 'investigations';
      query.mode = 'flat';
      break;
    case 'overview':
      page = 'dashboard';
      break;
    case 'start':
      page = 'start';
      break;
    case 'inventory':
    case 'definitions':
      page = 'metrics';
      break;
    case 'datasets':
      page = 'data';
      break;
    case 'history':
      page = 'metrics';
      tab = 'history';
      break;
    case 'evidence':
      page = 'investigations';
      tab = 'evidence';
      break;
    case 'adapters':
      page = 'settings';
      tab = 'integrations';
      break;
    case 'evaluation':
      page = 'settings';
      tab = 'evaluation';
      break;
    case 'settings':
      page = 'settings';
      break;
    case '':
      page = 'dashboard';
      break;
    default:
      page = 'dashboard';
      return { workspaceId: defaultWorkspaceId, page, tab, query, rawPath: clean, notFound: true };
  }

  return { workspaceId: defaultWorkspaceId, page, tab, query, rawPath: clean };
}

export type SitePage = 'home' | 'signin' | 'signup' | 'privacy' | 'terms' | 'demo' | 'workspace' | 'not-found';

/** Public routes never mount workspace components or fetch their data. */
export function sitePage(pathname: string): SitePage {
  const clean = pathname.replace(/\/+$/, '') || '/';
  if (clean === '/') return 'home';
  if (/^\/demo(\/mg-f3a3)?$/i.test(clean)) return 'demo';
  if (clean === '/signin' || clean === '/signup' || clean === '/privacy' || clean === '/terms') {
    return clean.slice(1) as SitePage;
  }
  return parseRoute(pathname).notFound ? 'not-found' : 'workspace';
}

export function homeRedirect(identity: { verified?: boolean; home_workspace_id?: string | null } | null): string | null {
  const id = identity?.home_workspace_id;
  return identity?.verified === true && typeof id === 'string' && /^[a-zA-Z0-9_-]+$/.test(id)
    ? buildRouteUrl(id, 'investigations') : null;
}

export function buildRouteUrl(workspaceId: string, page: Page, tab?: string, query?: Record<string, string>): string {
  let url = `/w/${encodeURIComponent(workspaceId)}/${page}`;
  if (page === 'metrics' && query?.metricId) {
    url += `/${encodeURIComponent(query.metricId)}`;
  } else if (page === 'changes' && query?.changeId) {
    url += `/${encodeURIComponent(query.changeId)}`;
  } else if (page === 'investigations' && query?.investigationId) {
    url += `/${encodeURIComponent(query.investigationId)}`;
  }
  const params = new URLSearchParams();
  if (tab) params.set('tab', tab);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (
        k !== 'tab' &&
        !(page === 'metrics' && k === 'metricId') &&
        !(page === 'changes' && k === 'changeId') &&
        !(page === 'investigations' && k === 'investigationId') &&
        v
      ) {
        params.set(k, v);
      }
    }
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}
