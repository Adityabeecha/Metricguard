import { useState, useRef } from 'react';
import { UploadCloud, X, Database, Layers } from 'lucide-react';
import { api } from './api';

export type MetadataArtifact = {
  id: string;
  created_at: string;
  filename: string;
  format: string;
  source_hash: string;
  capabilities: {
    import_metadata: boolean;
    parse_logic: boolean;
    execute_natively: boolean;
    reconcile_quantitatively: boolean;
  };
  resources?: Array<{
    unique_id: string;
    resource_type: string;
    name: string;
    compiled_sql_present: boolean;
    lineage: string[];
    support_state: string;
  }>;
  tables?: string[];
  measures?: Array<{
    table: string;
    name: string;
    expression: string;
    support_state: string;
  }>;
  relationships?: Array<{
    name: string;
    from_column: string | null;
    to_column: string | null;
  }>;
  limitations: string[];
};

interface FileUploadControlProps {
  label: string;
  helperText?: string;
  accept: string;
  file: File | null;
  onFileSelect: (file: File | null) => void;
  id: string;
}

function FileUploadControl({
  label,
  helperText,
  accept,
  file,
  onFileSelect,
  id,
}: FileUploadControlProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="form-group" style={{ marginBottom: 16 }}>
      <label
        className="form-label"
        htmlFor={id}
        style={{ fontSize: 13, fontWeight: 600, color: '#344054', marginBottom: 6 }}
      >
        {label}
      </label>
      <div
        style={{
          border: '1px solid #D0D5DD',
          borderRadius: 6,
          padding: '8px 12px',
          background: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1 }}>
          <button
            type="button"
            className="button secondary small"
            onClick={() => fileInputRef.current?.click()}
            style={{ flexShrink: 0 }}
          >
            <UploadCloud size={14} />
            <span>Choose file</span>
          </button>
          <span
            style={{
              fontSize: 13,
              color: file ? '#101828' : '#667085',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontFamily: file ? 'monospace' : 'inherit',
            }}
          >
            {file ? file.name : 'No file selected'}
          </span>
        </div>
        {file && (
          <button
            type="button"
            className="icon-button"
            onClick={() => {
              onFileSelect(null);
              if (fileInputRef.current) fileInputRef.current.value = '';
            }}
            aria-label="Remove selected file"
            style={{ padding: 4 }}
          >
            <X size={15} />
          </button>
        )}
      </div>
      <input
        ref={fileInputRef}
        id={id}
        type="file"
        accept={accept}
        onChange={(e) => onFileSelect(e.target.files?.[0] || null)}
        style={{ display: 'none' }}
      />
      {helperText && (
        <p style={{ margin: '6px 0 0', fontSize: 12, color: '#667085' }}>
          {helperText}
        </p>
      )}
    </div>
  );
}

export function MetadataImports({
  imports,
  onImported,
}: {
  imports: MetadataArtifact[];
  onImported: () => void;
}) {
  const [dbtFile, setDbtFile] = useState<File | null>(null);
  const [tmdlFile, setTmdlFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string | null>(null);

  async function upload(kind: 'dbt' | 'tmdl', file: File | null) {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const body = await api.uploadMetadata(kind, file);
      setSelected(body.id);
      if (kind === 'dbt') setDbtFile(null);
      if (kind === 'tmdl') setTmdlFile(null);
      onImported();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  }

  const current = imports.find((item) => item.id === selected) || imports[0];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}

      {/* Two Integrations Columns: dbt & Power BI */}
      <div className="two-col">
        {/* dbt Integration Card */}
        <section className="panel">
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Layers size={18} color="#245BFF" />
              <h2 style={{ margin: 0 }}>dbt</h2>
            </div>
            <p>Version-aware models, metrics, compiled SQL presence, and lineage.</p>
          </div>
          <div style={{ padding: '20px' }}>
            <FileUploadControl
              id="dbt-manifest-upload"
              label="Select manifest"
              helperText="Upload compiled target/manifest.json to inspect dbt nodes and relationships."
              accept=".json,application/json"
              file={dbtFile}
              onFileSelect={setDbtFile}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginTop: 16 }}>
              <button
                type="button"
                className="button primary small"
                disabled={!dbtFile || busy}
                onClick={() => void upload('dbt', dbtFile)}
              >
                {busy ? 'Importing…' : 'Import dbt manifest'}
              </button>
            </div>
          </div>
        </section>

        {/* Power BI TMDL Card */}
        <section className="panel">
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Database size={18} color="#245BFF" />
              <h2 style={{ margin: 0 }}>Power BI TMDL</h2>
            </div>
            <p>Bounded tables, measures, columns, and relationships from TMDL definitions.</p>
          </div>
          <div style={{ padding: '20px' }}>
            <FileUploadControl
              id="pbi-tmdl-upload"
              label="Select TMDL document"
              helperText="Upload a .tmdl file to extract tabular model measures and DAX expressions."
              accept=".tmdl"
              file={tmdlFile}
              onFileSelect={setTmdlFile}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginTop: 16 }}>
              <button
                type="button"
                className="button primary small"
                disabled={!tmdlFile || busy}
                onClick={() => void upload('tmdl', tmdlFile)}
              >
                {busy ? 'Importing…' : 'Import TMDL document'}
              </button>
            </div>
          </div>
        </section>
      </div>

      {/* Imported Artifacts List */}
      <section className="panel">
        <div className="panel-header">
          <h2>Imported artifacts</h2>
          <p>{imports.length} local artifacts · metadata-only capability</p>
        </div>
        <div className="list">
          {imports.map((item) => (
            <button
              type="button"
              className={`list-row ${item.id === current?.id ? 'active' : ''}`}
              key={item.id}
              onClick={() => setSelected(item.id)}
            >
              <div>
                <strong>{item.filename}</strong>
                <small>
                  {item.format.replaceAll('_', ' ')} · {new Date(item.created_at).toLocaleString()} · metadata only
                </small>
              </div>
            </button>
          ))}
          {!imports.length && (
            <p className="panel-note" style={{ padding: '16px 20px', margin: 0 }}>
              No metadata artifacts imported yet.
            </p>
          )}
        </div>
      </section>

      {/* Selected Artifact Details */}
      {current && (
        <section className="panel">
          <div className="panel-header">
            <h2>{current.filename}</h2>
            <p>SHA-256 {current.source_hash}</p>
          </div>
          <div className="status-strip">
            <div>
              <span>METADATA</span>
              <strong>Imported</strong>
            </div>
            <div>
              <span>LOGIC PARSE</span>
              <strong>Unavailable</strong>
            </div>
            <div>
              <span>NATIVE EXECUTION</span>
              <strong>Unavailable</strong>
            </div>
            <div>
              <span>QUANTITATIVE RECONCILIATION</span>
              <strong>Unavailable</strong>
            </div>
          </div>

          {current.resources && current.resources.length > 0 && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>DBT RESOURCE</th>
                    <th>TYPE</th>
                    <th>COMPILED SQL</th>
                    <th>LINEAGE</th>
                  </tr>
                </thead>
                <tbody>
                  {current.resources.map((item) => (
                    <tr key={item.unique_id}>
                      <td className="weight">{item.name}</td>
                      <td>{item.resource_type}</td>
                      <td>{item.compiled_sql_present ? 'Present, not executed' : 'Absent'}</td>
                      <td>{item.lineage.join(', ') || 'None declared'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {current.measures && current.measures.length > 0 && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>TABLE</th>
                    <th>MEASURE</th>
                    <th>DAX EXPRESSION</th>
                    <th>STATE</th>
                  </tr>
                </thead>
                <tbody>
                  {current.measures.map((item, index) => (
                    <tr key={index}>
                      <td>{item.table}</td>
                      <td className="weight">{item.name}</td>
                      <td className="mono-cell">{item.expression}</td>
                      <td>{item.support_state.replaceAll('_', ' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {current.limitations?.map((limit, index) => (
            <p className="panel-note" key={index}>
              {limit}
            </p>
          ))}
        </section>
      )}
    </div>
  );
}
