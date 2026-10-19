"""Run the golden CSV and Parquet workflows through the real Docker launcher."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import duckdb
from fastapi.testclient import TestClient
import sqlglot
from sqlglot import exp

from packages.metricguard_core import ingest_csv
from packages.metricguard_core.common import SCHEMA
from scripts.seed_demo import seed
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.worker import DockerLauncher, Supervisor


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_CSV = ROOT / 'MetricGuard_Planning_Pack/examples/orders_golden.csv'


def verify_isolation(image: str) -> dict:
    """Check the daemon enforces the launcher policy on the fixed image."""
    probe = '''import json, os, socket
from pathlib import Path
try:
    Path('/app/isolation-probe').write_text('unexpected')
    root_writable = True
except OSError:
    root_writable = False
try:
    socket.create_connection(('1.1.1.1', 443), timeout=2)
    network_available = True
except OSError:
    network_available = False
status = Path('/proc/self/status').read_text()
print(json.dumps({
    'uid': os.getuid(), 'root_writable': root_writable,
    'root_read_only': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
    'network_available': network_available,
    'no_new_privileges': next(line.split()[1] for line in status.splitlines() if line.startswith('NoNewPrivs:')),
    'effective_capabilities': next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')),
    'memory_max': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
    'cpu_max': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
    'pids_max': Path('/sys/fs/cgroup/pids.max').read_text().strip(),
}))'''
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only',
               '--user', '10001:10001', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--memory', '1g',
               '--cpus', '1', '--pids-limit', '64',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
               '--entrypoint', 'python', image, '-c', probe]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    checks = json.loads(result.stdout)
    assert checks == {
        'uid': 10001, 'root_writable': False, 'root_read_only': True,
        'network_available': False,
        'no_new_privileges': '1', 'effective_capabilities': '0000000000000000',
        'memory_max': '1073741824', 'cpu_max': '100000 100000', 'pids_max': '64',
    }, checks
    return checks


def verify_scratch(launcher: DockerLauncher, work: Path) -> dict:
    """Large comparisons spool evidence with tempfile; it must fit beyond the 64 MiB in-memory /tmp."""
    probe = '''import json, tempfile
with tempfile.TemporaryFile() as f:
    block = b'x' * (1024 * 1024)
    for _ in range(200):
        f.write(block)
    f.flush()
    print(json.dumps({'tempdir': tempfile.gettempdir(), 'written_mib': f.tell() // (1024 * 1024)}))'''
    (work / 'scratch-check' / 'input').mkdir(parents=True)
    (work / 'scratch-check' / 'output').mkdir()
    args, _ = launcher.arguments(work / 'scratch-check' / 'input', work / 'scratch-check' / 'output',
                                 ['--entrypoint', 'python', launcher.image, '-c', probe])
    volumes = lambda: set(subprocess.run(['docker', 'volume', 'ls', '-q'], check=True, capture_output=True,
                                         text=True).stdout.split())
    before = volumes()
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)
    checks = json.loads(result.stdout)
    assert checks == {'tempdir': '/job/scratch', 'written_mib': 200}, checks
    checks['scratch_volume_removed'] = not (volumes() - before)
    assert checks['scratch_volume_removed'], 'the per-job scratch volume outlived its container'
    return checks


def verify_live_runner() -> dict:
    (ROOT / '.test-tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='live-runner-', dir=ROOT / '.test-tmp',
                                     ignore_cleanup_errors=True) as folder:
        work = Path(folder)
        url = f"sqlite:///{work / 'app.db'}"
        seed(url, work)
        source_db = work / 'source.duckdb'
        ingest_csv(GOLDEN_CSV, source_db)
        parquet = work / 'orders.parquet'
        with duckdb.connect(str(source_db)) as con:
            con.execute('COPY orders TO ? (FORMAT PARQUET)', [str(parquet)])

        launcher = DockerLauncher()
        worker = Supervisor(Store(url), launcher, work / 'jobs', 'live-runner-check', data_dir=work)
        checks = {}
        with TestClient(create_app(database_url=url, data_dir=work, mode='isolated')) as client:
            definitions = client.get('/api/v1/bootstrap').json()['definitions']
            for label, source in (('csv', GOLDEN_CSV), ('parquet', parquet)):
                content = source.read_bytes()
                scope = f'live_runner_{label}'
                upload = client.post('/api/v1/snapshots', files={'file': (source.name, content)},
                                     data={'name': f'Live {label} golden', 'scope': scope})
                assert upload.status_code == 202, upload.text
                import_id = upload.json()['id']
                assert worker.run_once(), f'{label}: import job not claimed'
                imported = client.get(f'/api/v1/snapshot-imports/{import_id}').json()
                assert imported['state'] == 'validated', imported.get('error')
                snapshot = next(item for item in client.get('/api/v1/bootstrap').json()['snapshots']
                                if item['id'] == imported['snapshot_id'])
                assert snapshot['row_count'] == 8
                assert snapshot['source_hash'] == hashlib.sha256(content).hexdigest()

                context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01',
                           'timezone': 'Asia/Kolkata', 'currency': 'INR', 'unit': 'currency',
                           'grain': 'one_row_per_order_id', 'scope': scope}
                comparison = client.post('/api/v1/comparisons', json={
                    'baseline_version_id': definitions[0]['versions'][0]['id'],
                    'target_version_id': definitions[2]['versions'][0]['id'],
                    'snapshot_id': snapshot['id'], 'context': context,
                    'relationship': 'expected_agreement'},
                    headers={'Idempotency-Key': f'live-{label}'})
                assert comparison.status_code == 202, comparison.text
                assert worker.run_once(), f'{label}: comparison job not claimed'
                run = client.get(f"/api/v1/comparisons/{comparison.json()['id']}").json()
                assert run['state'] == 'succeeded', run.get('error')
                assert run['result']['delta_target_minus_baseline'] == '-6000000.00'
                assert run['result']['residual'] == '0.00'
                checks[label] = {'rows': snapshot['row_count'], 'source_hash': snapshot['source_hash'],
                                 'delta': run['result']['delta_target_minus_baseline'],
                                 'residual': run['result']['residual']}

            invalid = client.post('/api/v1/snapshots', files={
                'file': ('broken.parquet', b'not a parquet file')},
                data={'name': 'Invalid Parquet', 'scope': 'live_invalid_parquet'})
            assert invalid.status_code == 202, invalid.text
            assert worker.run_once(), 'invalid Parquet import job not claimed'
            rejected = client.get(f"/api/v1/snapshot-imports/{invalid.json()['id']}").json()
            assert rejected['state'] == 'failed', rejected
            assert rejected['error']['code'] == 'INVALID_PARQUET', rejected
            checks['invalid_parquet'] = {'state': rejected['state'],
                                         'error_code': rejected['error']['code']}

            mapped_csv = work / 'mapped-sales.csv'
            source_names = [f'source_{name}' for name in SCHEMA]
            with GOLDEN_CSV.open(encoding='utf-8-sig', newline='') as original, \
                    mapped_csv.open('w', encoding='utf-8', newline='') as output:
                reader, writer = csv.reader(original), csv.writer(output)
                next(reader)
                writer.writerow(source_names)
                writer.writerows(reader)
            preview_request = client.post('/api/v1/dataset-previews',
                                          files={'file': (mapped_csv.name, mapped_csv.read_bytes())})
            assert preview_request.status_code == 202, preview_request.text
            assert worker.run_once(), 'mapped preview job not claimed'
            preview = client.get(f"/api/v1/dataset-previews/{preview_request.json()['id']}").json()
            assert preview['state'] == 'previewed', preview.get('error')
            assert preview['preview']['headers'] == source_names
            checks['mapped_preview'] = {'columns': len(source_names),
                                        'sample_rows': len(preview['preview']['sample_rows'])}
            contract = {'version': 2, 'name': 'Mapped golden', 'currency': 'INR',
                        'timezone': 'Asia/Kolkata', 'scope': 'live_runner_mapped',
                        'grain': 'one_row_per_sale', 'cutoff_at': '2026-09-02',
                        'null_policy': 'blank_nullable', 'row_key': 'order_id',
                        'entity_key': 'customer_id', 'decimal_scale': 2, 'schema': SCHEMA}
            approved = client.post('/api/v1/dataset-contracts', json=contract)
            assert approved.status_code == 201, approved.text
            mapping = client.post('/api/v1/column-mappings', json={
                'contract_id': approved.json()['id'],
                'columns': dict(zip(SCHEMA, source_names))})
            assert mapping.status_code == 201, mapping.text
            mapped = client.post('/api/v1/mapped-snapshots',
                                 files={'file': (mapped_csv.name, mapped_csv.read_bytes())},
                                 data={'mapping_id': mapping.json()['id'], 'name': 'Mapped golden'})
            assert mapped.status_code == 202, mapped.text
            assert worker.run_once(), 'mapped import job not claimed'
            accepted_import = client.get(f"/api/v1/snapshot-imports/{mapped.json()['id']}").json()
            assert accepted_import['state'] == 'validated', accepted_import.get('error')
            accepted = client.get(f"/api/v1/snapshots/{accepted_import['snapshot_id']}").json()
            assert accepted['contract_hash'] == approved.json()['contract_hash']
            assert accepted['mapping_hash'] == mapping.json()['mapping_hash']
            mapped_context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01',
                              'timezone': 'Asia/Kolkata', 'currency': 'INR', 'unit': 'currency',
                              'grain': 'one_row_per_sale', 'scope': 'live_runner_mapped',
                              'contract_hash': approved.json()['contract_hash'],
                              'mapping_hash': mapping.json()['mapping_hash']}
            mapped_versions = []
            for index in (0, 2):
                version = definitions[index]['versions'][0]
                tree = sqlglot.parse_one(version['raw_sql'], read='duckdb')
                for column in tree.find_all(exp.Column):
                    column.set('this', exp.to_identifier(f'source_{column.name}', quoted=True))
                imported = client.post('/api/v1/definitions', json={
                    'name': f'Mapped {definitions[index]["name"]}',
                    'raw_sql': tree.sql(dialect='duckdb'),
                    'time_column': f'source_{version["time_column"]}',
                    'mapping_id': mapping.json()['id']})
                assert imported.status_code == 201, imported.text
                mapped_versions.append(imported.json()['versions'][0]['id'])
            mapped_run = client.post('/api/v1/comparisons', json={
                'baseline_version_id': mapped_versions[0],
                'target_version_id': mapped_versions[1],
                'snapshot_id': accepted['id'], 'context': mapped_context,
                'relationship': 'expected_agreement'},
                headers={'Idempotency-Key': 'live-mapped'})
            assert mapped_run.status_code == 202, mapped_run.text
            assert worker.run_once(), 'mapped comparison job not claimed'
            finished = client.get(f"/api/v1/comparisons/{mapped_run.json()['id']}").json()
            assert finished['state'] == 'succeeded', finished.get('error')
            assert finished['result']['delta_target_minus_baseline'] == '-6000000.00'
            assert finished['result']['evidence'] == []
            evidence_rows = finished['result']['evidence_artifact']['row_count']
            evidence_page = client.get(f"/api/v1/comparisons/{finished['id']}/evidence",
                                       params={'offset': 0, 'limit': 3})
            assert evidence_page.status_code == 200, evidence_page.text
            assert evidence_page.json()['total'] == evidence_rows
            assert len(evidence_page.json()['items']) == min(3, evidence_rows)
            exported = client.get(f"/api/v1/comparisons/{finished['id']}/export")
            assert exported.status_code == 200, exported.text
            assert exported.json()['evidence_coverage']['total_rows'] == evidence_rows
            checks['mapped_csv'] = {'rows': accepted['row_count'],
                                    'contract_hash': accepted['contract_hash'],
                                    'mapping_hash': accepted['mapping_hash'],
                                    'delta': finished['result']['delta_target_minus_baseline'],
                                    'evidence_rows': evidence_rows}
            saved = client.post('/api/v1/investigations', json={
                'title': 'Mapped golden investigation', 'run_id': finished['id'],
                'snapshot_id': accepted['id'], 'decision': 'Synthetic fixture reviewed',
                'actor_label': 'Local analyst'})
            assert saved.status_code == 201, saved.text
            reopened = client.get(f"/api/v1/investigations/{saved.json()['id']}").json()
            assert reopened['run']['id'] == finished['id']
            assert reopened['snapshot']['mapping_hash'] == mapping.json()['mapping_hash']
            assert reopened['baseline_version']['id'] == mapped_versions[0]
            assert reopened['target_version']['id'] == mapped_versions[1]
            replay = client.post(f"/api/v1/investigations/{saved.json()['id']}/replays",
                                 headers={'Idempotency-Key': 'live-mapped-replay'})
            assert replay.status_code == 202, replay.text
            assert worker.run_once(), 'mapped case replay not claimed'
            replayed = client.get(f"/api/v1/comparisons/{replay.json()['id']}").json()
            assert replayed['state'] == 'succeeded', replayed.get('error')
            assert replayed['result']['delta_target_minus_baseline'] == '-6000000.00'
            checks['mapped_case_replay'] = {'snapshot_id': replayed['snapshot_id'],
                                            'delta': replayed['result']['delta_target_minus_baseline']}

            mapped_parquet = work / 'mapped-sales.parquet'
            with duckdb.connect(str(source_db)) as con:
                columns = ', '.join(f'"{role}" AS "source_{role}"' for role in SCHEMA)
                con.execute(f'COPY (SELECT {columns} FROM orders) TO ? (FORMAT PARQUET)',
                            [str(mapped_parquet)])
            parquet_contract = {**contract, 'name': 'Mapped Parquet golden',
                                'scope': 'live_runner_mapped_parquet',
                                'null_policy': 'native_null'}
            parquet_approved = client.post('/api/v1/dataset-contracts', json=parquet_contract)
            assert parquet_approved.status_code == 201, parquet_approved.text
            parquet_mapping = client.post('/api/v1/column-mappings', json={
                'contract_id': parquet_approved.json()['id'],
                'columns': dict(zip(SCHEMA, source_names))})
            assert parquet_mapping.status_code == 201, parquet_mapping.text
            parquet_upload = client.post('/api/v1/mapped-snapshots',
                                         files={'file': (mapped_parquet.name, mapped_parquet.read_bytes())},
                                         data={'mapping_id': parquet_mapping.json()['id'],
                                               'name': 'Mapped Parquet golden'})
            assert parquet_upload.status_code == 202, parquet_upload.text
            assert worker.run_once(), 'mapped Parquet import job not claimed'
            parquet_import = client.get(f"/api/v1/snapshot-imports/{parquet_upload.json()['id']}").json()
            assert parquet_import['state'] == 'validated', parquet_import.get('error')
            parquet_snapshot = client.get(f"/api/v1/snapshots/{parquet_import['snapshot_id']}").json()
            assert parquet_snapshot['row_count'] == 8
            checks['mapped_parquet'] = {'rows': parquet_snapshot['row_count'],
                                        'contract_hash': parquet_snapshot['contract_hash'],
                                        'mapping_hash': parquet_snapshot['mapping_hash']}

        worker.store.engine.dispose()
        client.app.state.store.engine.dispose()
        report = {'method': 'live_docker_supervisor', 'image': launcher.image,
                  'checks': checks, 'isolation': verify_isolation(launcher.image),
                  'scratch': verify_scratch(launcher, work)}
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = verify_live_runner()
    encoded = json.dumps(report, sort_keys=True, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded, end='')


if __name__ == '__main__':
    main()
