"""Conservative metadata extraction. Neither adapter executes imported source text."""
from __future__ import annotations

import hashlib
import json
import re


class AdapterError(ValueError):
    pass


CAPABILITIES = {'import_metadata': True, 'parse_logic': False,
                'execute_natively': False, 'reconcile_quantitatively': False}


def _text(value, label, limit=50000):
    if not isinstance(value, str) or len(value) > limit:
        raise AdapterError(f'{label} must be text no longer than {limit} characters')
    return value


MAX_DBT_MANIFEST_BYTES = 10 * 1024 * 1024  # 10 MiB provisional limit
MAX_DBT_RUN_RESULTS_BYTES = 5 * 1024 * 1024  # 5 MiB provisional limit
MAX_DBT_RESOURCES = 500  # 500 nodes provisional limit


def analyze_dbt_sql(compiled_sql: str | None) -> tuple[bool, str, list[str]]:
    """
    Classify dbt compiled SQL.
    Returns (compiled_sql_present, support_state, diagnostics).
    support_state is one of: 'executable', 'needs_context', 'metadata_only'.
    """
    if not compiled_sql or not isinstance(compiled_sql, str) or not compiled_sql.strip():
        return False, 'metadata_only', [
            "MISSING_COMPILED_SQL: Node contains raw Jinja template only. Execute 'dbt compile' or 'dbt build' before importing."
        ]

    sql = compiled_sql.strip()

    # Check for unrendered Jinja syntax
    if any(token in sql for token in ('{{', '}}', '{%', '%}')):
        return False, 'metadata_only', [
            "UNRENDERED_JINJA: Compiled SQL contains unresolved Jinja macro expressions."
        ]

    # Parse with sqlglot
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return True, 'metadata_only', ["DEPENDENCY_MISSING: sqlglot is required for SQL analysis."]

    try:
        statements = sqlglot.parse(sql, read='duckdb')
    except Exception:
        try:
            statements = sqlglot.parse(sql, read='postgres')
        except Exception as exc:
            return True, 'metadata_only', [f"UNPARSEABLE_SQL: Could not parse SQL statement ({exc})."]

    if len(statements) != 1:
        return True, 'metadata_only', ["UNSUPPORTED_SQL: Exactly one SELECT statement is required."]

    stmt = statements[0]
    if isinstance(stmt, (exp.Union, exp.Intersect, exp.Except)):
        return True, 'metadata_only', ["SET_OPERATION_DETECTED: Set operations (UNION/INTERSECT/EXCEPT) are outside single-table analytical boundary."]

    if not isinstance(stmt, exp.Select):
        return True, 'metadata_only', ["UNSUPPORTED_SQL: Exactly one SELECT statement is required."]

    tree = stmt
    diagnostics = []
    has_cte = bool(tree.args.get('with_') or list(tree.find_all(exp.CTE)))
    sql_to_validate = sql

    # Check for CTE (WITH clause)
    if has_cte:
        from packages.metricguard_core.normalization import normalize_level1_cte
        ok, normalized_sql, diag, _ = normalize_level1_cte(sql)
        if ok:
            sql_to_validate = normalized_sql
            try:
                norm_stmts = sqlglot.parse(normalized_sql, read='duckdb')
                tree = norm_stmts[0]
            except Exception:
                pass
        else:
            diagnostics.append(
                diag or "CTE_DETECTED: Common Table Expressions (WITH clause) must be materialized or flattened into a single table before quantitative reconciliation."
            )

    # Check for JOINs
    if list(tree.find_all(exp.Join)):
        diagnostics.append(
            "JOIN_DETECTED: Multi-table JOINs are not supported for quantitative reconciliation without a proven 1:1 grain contract."
        )

    # Check for multiple tables in FROM
    tables = list(tree.find_all(exp.Table))
    if len(tables) > 1 and not list(tree.find_all(exp.Join)):
        diagnostics.append("MULTIPLE_TABLES_DETECTED: Multiple source tables detected in FROM clause.")

    if diagnostics:
        return True, 'metadata_only', diagnostics

    # Attempt closed IR validation using metricguard parser
    try:
        from packages.metricguard_core.parser import parse_definition
        from packages.metricguard_core.common import CoreError
        try:
            ir = parse_definition(sql_to_validate)
            if ir.get('time_column'):
                return True, 'executable', []
            return True, 'needs_context', [
                "CONTEXT_REQUIRED: Confirm an order_date or payment_date business date column before comparison."
            ]
        except CoreError as err:
            if err.code == 'context_required':
                return True, 'needs_context', [f"CONTEXT_REQUIRED: {err.message}"]
            if has_cte:
                return True, 'metadata_only', [f"NORMALIZED_QUERY_UNSUPPORTED: Normalized query fails core IR validation: {err.message}"]
            return True, 'metadata_only', [f"UNSUPPORTED_SQL: {err.message}"]
    except Exception as exc:
        if has_cte:
            return True, 'metadata_only', [f"NORMALIZED_QUERY_UNSUPPORTED: Normalized query fails core IR validation: {str(exc)}"]
        return True, 'metadata_only', [f"UNSUPPORTED_SQL: {str(exc)}"]


def parse_run_results(content: bytes) -> dict[str, dict]:
    """Parse dbt run_results.json and index by unique_id."""
    if len(content) > MAX_DBT_RUN_RESULTS_BYTES:
        raise AdapterError(f'run_results.json exceeds {MAX_DBT_RUN_RESULTS_BYTES // (1024 * 1024)} MiB')
    try:
        data = json.loads(content)
    except (UnicodeError, ValueError) as err:
        raise AdapterError('run_results.json must be valid UTF-8 JSON') from err
    if not isinstance(data, dict):
        raise AdapterError('run_results.json must be a JSON object')
    results = data.get('results', [])
    if not isinstance(results, list):
        raise AdapterError('run_results.json results must be a list')
    by_unique_id = {}
    for res in results:
        if isinstance(res, dict) and 'unique_id' in res and isinstance(res['unique_id'], str):
            by_unique_id[res['unique_id']] = {
                'status': res.get('status'),
                'execution_time': res.get('execution_time'),
                'message': res.get('message'),
                'failures': res.get('failures'),
            }
    return by_unique_id


def parse_dbt_project_artifacts(manifest_content: bytes,
                                run_results_content: bytes | None = None,
                                source_revision: dict | None = None) -> dict:
    """Parse dbt manifest (v7-12) and companion run_results into structured revision resources."""
    if len(manifest_content) > MAX_DBT_MANIFEST_BYTES:
        raise AdapterError(f'dbt manifest exceeds {MAX_DBT_MANIFEST_BYTES // (1024 * 1024)} MiB')
    try:
        artifact = json.loads(manifest_content)
    except (UnicodeError, ValueError) as error:
        raise AdapterError('dbt manifest must be valid UTF-8 JSON') from error
    if not isinstance(artifact, dict) or not isinstance(artifact.get('metadata'), dict):
        raise AdapterError('dbt manifest metadata is required')
    schema = artifact['metadata'].get('dbt_schema_version')
    match = re.search(r'/manifest/v(\d+)\.json$', schema) if isinstance(schema, str) else None
    if not match or int(match.group(1)) not in range(7, 13):
        raise AdapterError('Unsupported dbt manifest schema version; supported versions are 7–12')
    version = int(match.group(1))
    nodes = artifact.get('nodes')
    metrics = artifact.get('metrics', {})
    semantic_models = artifact.get('semantic_models', {})
    if not isinstance(nodes, dict) or not isinstance(metrics, dict):
        raise AdapterError('dbt manifest nodes and metrics must be objects')

    selected = [(key, value) for key, value in nodes.items()
                if isinstance(value, dict) and value.get('resource_type') == 'model']
    selected += [(key, value) for key, value in metrics.items() if isinstance(value, dict)]
    if isinstance(semantic_models, dict):
        selected += [(key, value) for key, value in semantic_models.items() if isinstance(value, dict)]

    if len(selected) > MAX_DBT_RESOURCES:
        raise AdapterError(f'dbt metadata import is limited to {MAX_DBT_RESOURCES} models and metrics')

    run_results_map = parse_run_results(run_results_content) if run_results_content else {}
    child_map = artifact.get('child_map', {}) if isinstance(artifact.get('child_map'), dict) else {}

    resources = []
    for key, value in sorted(selected):
        if not isinstance(key, str) or not isinstance(value, dict):
            raise AdapterError('dbt resource identifiers and records must be valid')
        ident = _text(value.get('unique_id', key), 'unique_id', 300)
        if ident != key:
            raise AdapterError('dbt resource key and unique_id differ')
        resource_type = value.get('resource_type', 'metric' if key.startswith('metric.') else 'model')
        if resource_type not in ('model', 'metric', 'semantic_model'):
            raise AdapterError('dbt collection contains an unsupported resource type')

        # Lineage
        depends_on = value.get('depends_on', {}) if isinstance(value.get('depends_on'), dict) else {}
        upstream = depends_on.get('nodes', []) if isinstance(depends_on, dict) else []
        if not isinstance(upstream, list) or len(upstream) > 100 or any(not isinstance(item, str) or len(item) > 300 for item in upstream):
            raise AdapterError('dbt lineage must be a bounded list of identifiers')

        downstream = child_map.get(ident, []) if isinstance(child_map.get(ident), list) else []
        downstream = [item for item in downstream if isinstance(item, str) and len(item) <= 300][:100]
        observed_lineage = {'upstream_nodes': upstream, 'downstream_nodes': downstream}

        # Compiled SQL extraction
        compiled = value.get('compiled_code') if version >= 9 else value.get('compiled_sql')
        if not compiled:
            compiled = value.get('compiled_sql') if version >= 9 else value.get('compiled_code')
        if compiled is not None:
            compiled = _text(compiled, 'compiled SQL')

        compiled_sql_present, support_state, diagnostics = analyze_dbt_sql(compiled)
        # If compiled was set but unrendered jinja or missing, reset compiled
        clean_compiled = compiled if compiled_sql_present else None

        norm_sql = None
        norm_meta = None
        if clean_compiled and support_state in ('executable', 'needs_context'):
            from packages.metricguard_core.normalization import normalize_level1_cte
            ok, n_sql, _, n_meta = normalize_level1_cte(clean_compiled)
            if ok and n_meta and n_meta.get('normalization_applied'):
                norm_sql = n_sql
                norm_meta = n_meta

        raw_code = value.get('raw_code') or value.get('raw_sql')
        if raw_code is not None:
            raw_code = _text(raw_code, 'raw code', 50000)

        meta = value.get('meta', {}) if isinstance(value.get('meta'), dict) else {}
        tags = [t[:64] for t in value.get('tags', []) if isinstance(t, str)] if isinstance(value.get('tags'), list) else []
        materialized = value.get('config', {}).get('materialized', 'model') if isinstance(value.get('config'), dict) else 'model'

        resources.append({
            'unique_id': ident,
            'resource_type': resource_type,
            'name': _text(value.get('name', key), 'name', 160),
            'original_file_path': _text(value.get('original_file_path', ''), 'original_file_path', 500),
            'raw_code': raw_code,
            'compiled_sql': clean_compiled,
            'normalized_sql': norm_sql,
            'normalization': norm_meta,
            'compiled_sql_present': compiled_sql_present,
            'support_state': support_state,
            'diagnostics': diagnostics,
            'observed_lineage': observed_lineage,
            'run_results_status': run_results_map.get(ident),
            'meta': meta,
            'tags': tags,
            'materialized': str(materialized)[:64],
        })

    manifest_hash = hashlib.sha256(manifest_content).hexdigest()
    return {
        'manifest_hash': manifest_hash,
        'metadata': {
            'schema_version': version,
            'dbt_version': artifact['metadata'].get('dbt_version'),
            'project_name': artifact['metadata'].get('project_name'),
            'generated_at': artifact['metadata'].get('generated_at'),
            'invocation_id': artifact['metadata'].get('invocation_id'),
        },
        'source_revision': source_revision or {},
        'capabilities': CAPABILITIES.copy(),
        'resources': resources,
        'limitations': [
            'Models are not assumed to be KPI definitions.',
            'Uncompiled Jinja and macros are never evaluated.',
            'CTEs and multi-table JOINs degrade to metadata-only until single-grain contracts are defined.',
        ],
    }


def import_dbt_manifest(content: bytes) -> dict:
    """Legacy adapter wrapper for backward compatibility."""
    parsed = parse_dbt_project_artifacts(content)
    return {
        'format': 'dbt_manifest',
        'schema_version': parsed['metadata']['schema_version'],
        'dbt_version': parsed['metadata'].get('dbt_version'),
        'project_name': parsed['metadata'].get('project_name'),
        'source_hash': parsed['manifest_hash'],
        'capabilities': CAPABILITIES.copy(),
        'resources': [
            {
                'unique_id': r['unique_id'],
                'resource_type': r['resource_type'],
                'name': r['name'],
                'original_file_path': r['original_file_path'],
                'lineage': r['observed_lineage']['upstream_nodes'],
                'compiled_sql_present': r['compiled_sql_present'],
                'compiled_sql': r['compiled_sql'],
                'support_state': 'metadata_only',  # Retain legacy metadata_only label for older consumers
                'diagnostics': r['diagnostics'],
            }
            for r in parsed['resources']
        ],
        'limitations': parsed['limitations'],
    }


_NAME = r"(?:'(?:[^']|'')+'|[^=\s]+)"


def _unquote(name: str) -> str:
    return name[1:-1].replace("''", "'") if name.startswith("'") and name.endswith("'") else name


def import_tmdl(content: bytes) -> dict:
    if len(content) > 5 * 1024 * 1024:
        raise AdapterError('TMDL document exceeds 5 MiB')
    try:
        source = content.decode('utf-8-sig')
    except UnicodeError as error:
        raise AdapterError('TMDL document must be UTF-8') from error
    if '```' in source and source.count('```') % 2:
        raise AdapterError('Unclosed TMDL expression fence')
    tables, measures, columns, relationships = [], [], [], []
    table = None
    current = None
    current_type = None
    expression_lines = []
    expression_indent = None
    seen_measures = set()

    def flush_expression():
        nonlocal expression_lines, expression_indent
        if current_type == 'measure' and expression_lines:
            current['expression'] = '\n'.join(expression_lines).strip()
        expression_lines, expression_indent = [], None

    for raw in source.splitlines():
        line = raw.expandtabs(4)
        indent = len(line) - len(line.lstrip(' '))
        stripped = line.strip()
        if not stripped or stripped.startswith('///'):
            continue
        if expression_indent is not None and indent >= expression_indent:
            expression_lines.append(stripped)
            continue
        flush_expression()
        if indent == 0:
            current, current_type = None, None
            if stripped.startswith('table '):
                table = _unquote(stripped[6:].strip())
                if not table or len(table) > 160:
                    raise AdapterError('Invalid TMDL table name')
                tables.append(table)
            elif stripped.startswith('relationship '):
                current = {'name': _unquote(stripped[13:].strip()), 'from_column': None,
                           'to_column': None, 'support_state': 'metadata_only'}
                relationships.append(current)
                current_type, table = 'relationship', None
            elif stripped.startswith(('model ', 'database ', 'ref ', 'annotation ')):
                table = None
            else:
                table = None
            continue
        measure_match = re.match(rf'^measure\s+({_NAME})\s*=\s*(.*)$', stripped)
        column_match = re.match(rf'^column\s+({_NAME})(?:\s|$)', stripped)
        if table and measure_match and indent >= 4:
            name = _unquote(measure_match.group(1))
            if (table, name) in seen_measures:
                raise AdapterError('Duplicate TMDL measure declaration')
            seen_measures.add((table, name))
            current = {'table': table, 'name': name, 'expression': measure_match.group(2).strip(),
                       'lineage_tag': None, 'support_state': 'metadata_only'}
            measures.append(current)
            current_type = 'measure'
            if not current['expression']:
                expression_indent = indent + 8
            continue
        if table and column_match and indent >= 4:
            current = {'table': table, 'name': _unquote(column_match.group(1)),
                       'source_column': None, 'data_type': None, 'support_state': 'metadata_only'}
            columns.append(current)
            current_type = 'column'
            continue
        if current is not None and ':' in stripped:
            key, value = (piece.strip() for piece in stripped.split(':', 1))
            mapping = {'lineageTag': 'lineage_tag'} if current_type == 'measure' else (
                      {'sourceColumn': 'source_column', 'dataType': 'data_type'} if current_type == 'column' else
                      {'fromColumn': 'from_column', 'toColumn': 'to_column'})
            if key in mapping:
                current[mapping[key]] = value
            continue
    flush_expression()
    if not tables and not measures and not columns and not relationships:
        raise AdapterError('No supported TMDL table or relationship metadata found')
    if len(measures) + len(columns) + len(relationships) > 500:
        raise AdapterError('TMDL metadata import is limited to 500 objects')
    return {'format': 'power_bi_tmdl', 'source_hash': hashlib.sha256(content).hexdigest(),
            'capabilities': CAPABILITIES.copy(), 'tables': tables,
            'measures': measures, 'columns': columns, 'relationships': relationships,
            'limitations': ['Subset metadata extraction only; it is not a full TMDL parser.',
                            'DAX and model relationships are not executed; numeric comparisons require a native model and exact filter context.']}
