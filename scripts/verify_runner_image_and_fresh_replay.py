import sys
from pathlib import Path
import json
import time
import subprocess
import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DATA_DIR = Path('data/v8_final_acceptance')
keys = json.loads((DATA_DIR / '.auth_keys.json').read_text(encoding='utf-8'))
meta = json.loads((DATA_DIR / 'test_metadata.json').read_text(encoding='utf-8'))

pid = 'ws-relational-final'
API_URL = 'http://127.0.0.1:8001'

c_analyst = httpx.Client(base_url=API_URL, headers={'Authorization': f"Bearer {keys['analyst']['api_key']}", 'X-Project-Id': pid}, timeout=60.0)

def main():
    print("=================================================================")
    print("VERIFY RUNNER IMAGE IDENTITY AND FRESH REPLAY")
    print("=================================================================")

    # 1. Capture docker image inspect metricguard-runner:1.0
    res = subprocess.run(['docker', 'image', 'inspect', 'metricguard-runner:1.0'], capture_output=True, text=True, check=True)
    inspect_data = json.loads(res.stdout)[0]
    image_id = inspect_data['Id']
    print(f"Docker Image ID: {image_id}")
    print(f"Created: {inspect_data.get('Created')}")
    print(f"Size: {inspect_data.get('Size')} bytes")
    print(f"Entrypoint: {inspect_data.get('Config', {}).get('Entrypoint')}")

    expected_image_id = "sha256:1ed2112cd9d1f524b9c13e26b1b13c18c1a4c8b2ca2e15cf99e2e2d271c29b28"
    assert image_id == expected_image_id, f"Expected {expected_image_id}, got {image_id}"

    # Historical context:
    # Older v8_live_retest runs used sha256:15cbd74c34794fec24a372df6cb46ce451e64626f977184c8943d17bb69b12ac
    # The current metricguard-runner:1.0 image is sha256:1ed2112cd9d1f524b9c13e26b1b13c18c1a4c8b2ca2e15cf99e2e2d271c29b28
    historical_image_id = "sha256:15cbd74c34794fec24a372df6cb46ce451e64626f977184c8943d17bb69b12ac"

    # 2. Run one fresh comparison with the running supervisor confirmed to use the current image
    fresh_idem = f"fresh-comparison-verified-runner-{int(time.time())}"
    print(f"\nSubmitting fresh comparison with idempotency key: {fresh_idem}...")
    comparison_body = {
        'snapshot_id': meta['snapshot_id'],
        'baseline_version_id': meta['base_ver_id'],
        'target_version_id': meta['target_ver_id'],
        'context': {
            'mode': 'relational_snapshot',
            'scope': 'entire_supplied_snapshot',
            'unit_label': 'points',
            'values_comparable': True,
            'contract_hash': meta['contract_hash']
        }
    }
    r_comp = c_analyst.post('/api/v1/relational/comparisons', json=comparison_body, headers={'Idempotency-Key': fresh_idem})
    assert r_comp.status_code == 202, f"Failed to create comparison: {r_comp.text}"
    fresh_run = r_comp.json()
    fresh_run_id = fresh_run['id']
    print(f"Fresh run ID: {fresh_run_id}, initial state: {fresh_run['state']}")

    # 3. Poll for completion
    completed_run = None
    for _ in range(60):
        r_poll = c_analyst.get(f'/api/v1/comparisons/{fresh_run_id}')
        assert r_poll.status_code == 200
        poll_data = r_poll.json()
        if poll_data['state'] in ('succeeded', 'failed'):
            completed_run = poll_data
            break
        time.sleep(1)
    assert completed_run is not None, "Comparison timed out"
    assert completed_run['state'] == 'succeeded', f"Comparison failed: {completed_run.get('error')}"

    result = completed_run['result']
    print(f"Baseline: {result['baseline_value']}, Target: {result['target_value']}, Delta: {result['delta_target_minus_baseline']}, Residual: {result['residual']}")
    assert result['baseline_value'] == '65.00'
    assert result['target_value'] == '10.00'
    assert result['delta_target_minus_baseline'] == '-55.00'
    assert result['residual'] == '0.00'

    # Check join evidence artifact SHA-256
    artifact_meta = result['evidence_artifact']
    evidence_sha256 = artifact_meta['sha256']
    expected_evidence_sha = "36c87d2315eeae7cabf28709e4971cdf461909d8ca9b76a57813aaf295aa2365"
    print(f"Fresh comparison evidence SHA-256: {evidence_sha256}")
    assert evidence_sha256 == expected_evidence_sha, f"Expected {expected_evidence_sha}, got {evidence_sha256}"

    # 4. Save investigation and replay
    r_inv = c_analyst.post('/api/v1/investigations', json={
        'title': 'Fresh Runner Verified Investigation',
        'run_id': fresh_run_id,
        'decision': 'Verification with current confirmed runner image',
        'actor_label': 'Analyst Beta'
    })
    assert r_inv.status_code in (200, 201), f"Failed to save investigation: {r_inv.text}"
    fresh_inv_id = r_inv.json()['id']

    replay_idem = f"fresh-replay-verified-runner-{int(time.time())}"
    r_rep = c_analyst.post(f'/api/v1/investigations/{fresh_inv_id}/replays', headers={'Idempotency-Key': replay_idem})
    assert r_rep.status_code in (200, 202), f"Failed to queue replay: {r_rep.text}"
    replay_run_id = r_rep.json()['id']

    completed_replay = None
    for _ in range(60):
        r_poll = c_analyst.get(f'/api/v1/comparisons/{replay_run_id}')
        assert r_poll.status_code == 200
        poll_data = r_poll.json()
        if poll_data['state'] in ('succeeded', 'failed'):
            completed_replay = poll_data
            break
        time.sleep(1)
    assert completed_replay is not None and completed_replay['state'] == 'succeeded'
    replay_evidence_sha256 = completed_replay['result']['evidence_artifact']['sha256']
    print(f"Replay evidence SHA-256: {replay_evidence_sha256}")
    assert replay_evidence_sha256 == expected_evidence_sha

    # 5. Write Receipt
    receipt = {
        "gate": "RUNNER_IDENTITY_AND_FRESH_REPLAY",
        "status": "PASS",
        "current_runner_image": {
            "name": "metricguard-runner:1.0",
            "id": image_id,
            "created": inspect_data.get('Created'),
            "size": inspect_data.get('Size'),
            "digest": inspect_data.get('Descriptor', {}).get('digest'),
            "entrypoint": inspect_data.get('Config', {}).get('Entrypoint')
        },
        "historical_runner_images": [
            {
                "image": "metricguard-runner:1.0",
                "id": historical_image_id,
                "context": "Initial build used in earlier v8_live_retest investigation"
            }
        ],
        "fresh_run": {
            "run_id": fresh_run_id,
            "idempotency_key": fresh_idem,
            "baseline_value": result['baseline_value'],
            "target_value": result['target_value'],
            "delta_target_minus_baseline": result['delta_target_minus_baseline'],
            "residual": result['residual'],
            "evidence_sha256": evidence_sha256
        },
        "fresh_replay": {
            "investigation_id": fresh_inv_id,
            "replay_run_id": replay_run_id,
            "replay_evidence_sha256": replay_evidence_sha256,
            "reproducibility": "BITWISE_IDENTICAL"
        }
    }

    out_file = DATA_DIR / 'receipts' / 'receipt_runner_identity_and_replay.json'
    out_file.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f"Saved receipt to {out_file}")

if __name__ == '__main__':
    main()
