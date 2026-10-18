import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Plus, X } from 'lucide-react';
import { api, type Workspace } from './api';
import { useDialog } from './useDialog';

interface WorkspaceSwitcherProps {
  currentWorkspaceId: string;
  currentWorkspaceName: string;
  onSelectWorkspace: (workspaceId: string) => void;
}

export function WorkspaceSwitcher({
  currentWorkspaceId,
  currentWorkspaceName,
  onSelectWorkspace,
}: WorkspaceSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newWsName, setNewWsName] = useState('');
  const [newWsId, setNewWsId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const createDialogRef = useDialog<HTMLDivElement>(showCreateModal, () => setShowCreateModal(false), !busy);

  useEffect(() => {
    let cancelled = false;
    api.listWorkspaces()
      .then((list) => {
        if (!cancelled && Array.isArray(list)) {
          setWorkspaces(list);
        }
      })
      .catch(() => {
        // Fallback to current workspace if listing fails
        if (!cancelled) {
          setWorkspaces([{ id: currentWorkspaceId, name: currentWorkspaceName }]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentWorkspaceId, currentWorkspaceName, showCreateModal]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (open) setOpen(false);
      }
    };
    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const handleNameChange = (val: string) => {
    setNewWsName(val);
    const slug = val
      .toLowerCase()
      .replace(/[^a-z0-9_-]/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
    setNewWsId(slug);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWsName.trim()) return;
    setBusy(true);
    setError('');
    try {
      const created = await api.createWorkspace(newWsName.trim(), newWsId.trim() || undefined);
      setShowCreateModal(false);
      setNewWsName('');
      setNewWsId('');
      setOpen(false);
      onSelectWorkspace(created.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // The built-in default workspace; it may hold real work, so it is not called a sample.
  const isDefault = currentWorkspaceId === 'local-retail';

  return (
    <div className="workspace-switcher-wrapper" ref={dropdownRef}>
      <button
        type="button"
        className={`workspace-trigger ${open ? 'active' : ''}`}
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Select workspace"
      >
        <div className="workspace-avatar">
          {currentWorkspaceName.slice(0, 1).toUpperCase()}
        </div>
        <div className="workspace-info">
          <div className="workspace-name-row">
            <strong>{currentWorkspaceName}</strong>
            {isDefault && <span className="demo-chip">Default</span>}
          </div>
          <small>{isDefault ? 'Default local workspace' : 'Workspace'}</small>
        </div>
        <ChevronDown size={14} className={`chevron-icon ${open ? 'rotated' : ''}`} />
      </button>

      {open && (
        <div className="workspace-dropdown" role="listbox">
          <div className="dropdown-header">
            <span>SWITCH WORKSPACE</span>
          </div>

          <div className="workspace-items-list">
            {workspaces.map((ws) => {
              const selected = ws.id === currentWorkspaceId;
              const isDefaultWs = ws.id === 'local-retail';
              return (
                <button
                  type="button"
                  key={ws.id}
                  className={`workspace-menu-item ${selected ? 'selected' : ''}`}
                  onClick={() => {
                    setOpen(false);
                    onSelectWorkspace(ws.id);
                  }}
                  role="option"
                  aria-selected={selected}
                >
                  <div className="menu-item-avatar">
                    {ws.name.slice(0, 1).toUpperCase()}
                  </div>
                  <div className="menu-item-details">
                    <div className="menu-item-title-row">
                      <span>{ws.name}</span>
                      {isDefaultWs && <span className="demo-chip-small">Default</span>}
                      {ws.role && <span className="demo-chip-small">{ws.role}</span>}
                    </div>
                    <small>{ws.id}</small>
                  </div>
                  {selected && <Check size={15} className="selected-check" />}
                </button>
              );
            })}
          </div>

          <div className="dropdown-footer">
            <button
              type="button"
              className="create-ws-button"
              onClick={() => {
                setOpen(false);
                setShowCreateModal(true);
              }}
            >
              <Plus size={14} /> New workspace
            </button>
          </div>
        </div>
      )}

      {/* Modal for creating a new workspace */}
      {showCreateModal && (
        <div className="modal-backdrop" onClick={() => !busy && setShowCreateModal(false)}>
          <div
            ref={createDialogRef}
            data-dialog-managed=""
            className="modal-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-ws-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h2 id="create-ws-title">Create new workspace</h2>
              <button
                type="button"
                className="modal-close-button"
                onClick={() => setShowCreateModal(false)}
                aria-label="Close dialog"
              >
                <X size={18} />
              </button>
            </div>
            <form onSubmit={handleCreate}>
              <div className="modal-body">
                {error && <div className="alert error">{error}</div>}
                <div className="form-stack">
                  <label>
                    Workspace name
                    <input
                      required
                      autoFocus
                      placeholder="e.g. Finance Analytics"
                      value={newWsName}
                      onChange={(e) => handleNameChange(e.target.value)}
                    />
                  </label>
                  <label>
                    Workspace identifier (slug)
                    <input
                      required
                      placeholder="e.g. finance-analytics"
                      value={newWsId}
                      onChange={(e) => setNewWsId(e.target.value)}
                    />
                  </label>
                  <p className="modal-helper-text">
                    A new workspace starts completely clean with its own snapshots, metrics,
                    and audit records.
                  </p>
                </div>
              </div>
              <div className="modal-footer">
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => setShowCreateModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="button primary"
                  disabled={busy || !newWsName.trim()}
                >
                  {busy ? 'Creating…' : 'Create workspace'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
