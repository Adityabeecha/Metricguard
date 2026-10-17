from pathlib import Path

from scripts.seed_demo import seed
from services.api.store import Store


def test_user_definition_with_fixture_label_cannot_replace_trusted_demo(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    store.record_definition('User shadow', 'Synthetic fixture / dashboard_a.sql', 'Local',
                            'SELECT SUM(order_amount) FROM orders', 'order_date')
    ready = seed(url, tmp_path)
    definitions = ready.bootstrap()['definitions']
    fixture = Path(__file__).resolve().parents[1] / 'MetricGuard_Planning_Pack/examples/dashboard_a.sql'
    assert any(d['source_label'] == 'Synthetic fixture / dashboard_a.sql' and
               d['versions'][0]['raw_sql'] == fixture.read_text(encoding='utf-8') for d in definitions)
    assert len(ready.bootstrap()['definitions']) == 5


def test_demo_seed_does_not_duplicate_versions_on_repeat(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    first = seed(url, tmp_path).bootstrap()
    second = seed(url, tmp_path).bootstrap()
    assert len(first['definitions']) == len(second['definitions']) == 4
    dashboard_a = next(item for item in second['definitions'] if item['name'] == 'Dashboard A')
    assert len(dashboard_a['versions']) == 2
