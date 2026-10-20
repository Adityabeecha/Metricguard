import { useState, useEffect } from 'react';
import { type Bootstrap } from '../api';
import { Evaluation } from '../Evaluation';
import { MetadataImports } from '../MetadataImports';
import { Terminal } from 'lucide-react';
import { ThemeSwitch } from '../ThemeSwitch';
import { UsagePanel } from './UsagePanel';
import { AccountSettings } from './AccountSettings';

interface SettingsViewProps {
  data: Bootstrap;
  refresh: () => Promise<Bootstrap | null>;
  activeTab?: string;
  onTabChange?: (tab: string) => void;
}

export function SettingsView({
  data,
  refresh,
  activeTab = 'system',
  onTabChange,
}: SettingsViewProps) {
  // Gating mechanism for developer and evaluation tooling:
  // Developer UI should ONLY exist when an external development/build configuration explicitly permits it:
  // - Local Vite dev server: (import.meta as any).env?.DEV is true
  // - Explicit external runtime configuration: (window as any).__METRICGUARD_DEV__ === true
  // In a normal production build, supplying ?dev=true or ?tab=evaluation will NEVER activate developer tools.
  const [developerMode] = useState<boolean>(() => {
    try {
      if (typeof window === 'undefined') return false;
      const devEnvPermitted = Boolean((import.meta as any).env?.DEV) || Boolean((window as any).__METRICGUARD_DEV__);
      if (!devEnvPermitted) {
        return false;
      }
      // Within an explicitly permitted developer environment, evaluate query parameters or default to true
      const params = new URLSearchParams(window.location.search);
      const devNavRequested = params.get('dev') === 'true' || params.get('tab') === 'evaluation' || activeTab === 'evaluation';
      return devNavRequested || Boolean((import.meta as any).env?.DEV);
    } catch {
      return false;
    }
  });

  const [currentTab, setCurrentTab] = useState(() => {
    if (activeTab === 'evaluation' && !developerMode) {
      return 'system';
    }
    return activeTab || 'system';
  });

  useEffect(() => {
    if (activeTab === 'evaluation' && developerMode) {
      setCurrentTab('evaluation');
    } else if (activeTab === 'evaluation' && !developerMode) {
      setCurrentTab('system');
    } else if (activeTab && activeTab !== 'evaluation') {
      setCurrentTab(activeTab);
    }
  }, [activeTab, developerMode]);

  const handleTabClick = (tab: string) => {
    setCurrentTab(tab);
    if (onTabChange) onTabChange(tab);
  };

  return (
    <div>
      <div className="page-heading">
        <div>
          <h1>Settings</h1>

        </div>
      </div>

      <div className="section-nav-tabs">
        <button
          type="button"
          className={`section-tab-button ${currentTab === 'system' ? 'active' : ''}`}
          onClick={() => handleTabClick('system')}
        >
          System & Workspace
        </button>
        <button
          type="button"
          className={`section-tab-button ${currentTab === 'integrations' ? 'active' : ''}`}
          onClick={() => handleTabClick('integrations')}
        >
          Integrations
        </button>
        <button type="button" className={`section-tab-button ${currentTab === 'usage' ? 'active' : ''}`} onClick={() => handleTabClick('usage')}>Usage</button>
        {developerMode && (
          <button
            type="button"
            className={`section-tab-button ${currentTab === 'evaluation' ? 'active' : ''}`}
            onClick={() => handleTabClick('evaluation')}
          >
            Developer tools & Evaluation
          </button>
        )}
      </div>

      {currentTab === 'usage' && <UsagePanel workspaceId={data.project.id} />}
      {currentTab === 'system' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <AccountSettings />
          <section className="panel">
            <div className="panel-header">
              <h2>Appearance</h2>
              <p>System follows your computer's light or dark setting. Saved in this browser.</p>
            </div>
            <div style={{ padding: '16px 20px' }}><ThemeSwitch /></div>
          </section>
          <div className="two-col">
            <section className="panel">
              <div className="panel-header">
                <h2>Workspace information</h2>
              </div>
              <div style={{ padding: '16px 20px' }}>
                <div className="form-stack">
                  <div>
                    <label className="form-label">Workspace name</label>
                    <strong style={{ fontSize: '14px', display: 'block', marginTop: '2px', color: 'var(--ink)' }}>
                      {data.project.name}
                    </strong>
                  </div>
                  <div>
                    <label className="form-label">Workspace ID</label>
                    <code style={{ fontSize: '12px', background: 'var(--panel-2)', padding: '3px 8px', borderRadius: '4px', border: '1px solid var(--line)', color: 'var(--text-2)' }}>
                      {data.project.id}
                    </code>
                  </div>
                </div>
              </div>
            </section>

            <section className="panel">
              <div className="panel-header">
                <h2>Comparison engine</h2>
              </div>
              <div style={{ padding: '16px 20px' }}>
                <div className="form-stack">
                  <div>
                    <label className="form-label">Engine status</label>
                    <div style={{ marginTop: '4px' }}>
                      {(() => {
                        const isReady = data.mode === 'demo' ? true : Boolean(data.runner_available);
                        return (
                          <span className={`badge ${isReady ? 'ready' : 'warn'}`}>
                            {isReady ? 'Ready' : 'Processing unavailable'}
                          </span>
                        );
                      })()}
                    </div>
                  </div>
                  <div>
                    <label className="form-label">Processing capability</label>
                    <p style={{ margin: '4px 0 0', fontSize: '13px', color: 'var(--text-2)' }}>
                      Secure processing: Available
                    </p>
                  </div>
                </div>
              </div>
            </section>
          </div>

          {/* Developer Environment Indicator (only visible when started with external dev config) */}
          {developerMode && (
            <section className="panel">
              <div className="panel-header">
                <h2>Developer mode active</h2>
                <p>Internal diagnostics, performance benchmarks, and synthetic test suites</p>
              </div>
              <div style={{ padding: '16px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
                <div style={{ maxWidth: '640px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Terminal size={16} color="var(--link)" />
                    <strong style={{ fontSize: '13px', color: 'var(--ink)' }}>
                      Developer tooling enabled via external configuration
                    </strong>
                    <span className="badge ready">Active</span>
                  </div>
                  <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'var(--muted)', lineHeight: 1.4 }}>
                    Engineering release evidence, synthetic test fixtures, and internal Python benchmarks are available in the dedicated tab above.
                  </p>
                </div>
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => handleTabClick('evaluation')}
                >
                  Open Evaluation Workbench
                </button>
              </div>
            </section>
          )}
        </div>
      )}

      {currentTab === 'integrations' && (
        <MetadataImports
          imports={data.metadata_imports || []}
          onImported={() => void refresh()}
        />
      )}

      {developerMode && currentTab === 'evaluation' && <Evaluation />}
    </div>
  );
}
