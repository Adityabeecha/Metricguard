from pathlib import Path
from decimal import Decimal
import csv
import pytest

ROOT = Path(__file__).resolve().parents[1]

def api():
    import packages.metricguard_core as core
    assert hasattr(core, 'parse_definition'), 'core parser not implemented'
    return core

CTX = dict(start='2026-08-01', end_exclusive='2026-09-01', timezone='Asia/Kolkata',currency='INR',unit='currency',grain='one_row_per_order_id',scope='synthetic_retail_all_orders')

def definitions():
    c=api()
    return [c.parse_definition(p.read_text()) for p in sorted((ROOT/'MetricGuard_Planning_Pack/examples').glob('*.sql'))]

@pytest.fixture
def snapshot(tmp_path):
    c=api(); db=tmp_path/'snapshot.duckdb'
    c.ingest_csv(ROOT/'MetricGuard_Planning_Pack/examples/orders_golden.csv',db)
    return db

def test_golden_independent_oracle(snapshot):
    c=api(); a,b,f=definitions()
    result=c.compare(snapshot,a,f,CTX)
    rows=list(csv.DictReader((ROOT/'MetricGuard_Planning_Pack/examples/orders_golden.csv').open()))
    expected=sum(Decimal(r['order_amount']) for r in rows if r['order_id'] in ['O01','O02','O03','O04','O05'])
    assert Decimal(result['baseline_value'])==expected==104000000
    assert Decimal(result['target_value'])==98000000
    assert [Decimal(s['contribution']) for s in result['steps']]==[-4320000,-420000,-1260000]
    assert Decimal(result['residual'])==0
    assert sum(Decimal(r['delta']) for r in result['evidence'])==-6000000
    assert Decimal(c.compare(snapshot,a,b,CTX)['delta_target_minus_baseline'])==700000
    assert Decimal(c.compare(snapshot,f,a,CTX)['delta_target_minus_baseline'])==6000000
    assert Decimal(c.compare(snapshot,a,a,CTX)['delta_target_minus_baseline'])==0

@pytest.mark.parametrize('sql',[
 'SELECT SUM(order_amount) FROM orders LIMIT 1','SELECT SUM(order_amount) FROM orders ORDER BY 1',
 'SELECT SUM(order_amount/2) FROM orders','SELECT SUM(order_amount) FROM orders; SELECT 1',
 "SELECT SUM(order_amount) FROM read_csv_auto('secret')",'SELECT SUM(order_amount) FROM orders JOIN orders b USING(order_id)',
 'WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b',
 'SELECT SUM(order_amount) OVER () FROM orders','SELECT SUM(random()) FROM orders',
 'SELECT SUM(order_amount) FROM orders WHERE EXISTS (SELECT 1)',
 'SELECT SUM(order_amount) FROM orders GROUP BY status','SELECT SUM(order_amount) FROM orders TABLESAMPLE 50%',
 'SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE \'2026-08-01\' OR status=\'Paid\'',
])
def test_negative_ast(sql):
    c=api()
    with pytest.raises(c.CoreError): c.parse_definition(sql,'order_date')

def test_cosmetic():
    c=api()
    assert c.parse_definition('SELECT SUM(order_amount) FROM orders','order_date')['ir_hash']==c.parse_definition('select sum(o.order_amount) as revenue from orders o -- cosmetic','order_date')['ir_hash']

def test_context_and_ir_integrity(snapshot):
    c=api(); a,_,f=definitions()
    with pytest.raises(c.CoreError): c.compare(snapshot,a,f,{**CTX,'start':'2026-07-01'})
    with pytest.raises(c.CoreError): c.compare(snapshot,a,f,{**CTX,'timezone':'UTC'})
    with pytest.raises(c.CoreError): c.compare(snapshot,{**a,'expression':{'op':'evil'}},f,CTX)

def test_empty_and_count(snapshot):
    c=api()
    empty=c.parse_definition("SELECT SUM(order_amount) FROM orders WHERE status='absent'",'order_date')
    r=c.compare(snapshot,empty,empty,CTX)
    assert r['baseline_value'] is None and r['absolute_gap'] is None and not r['steps']
    count=c.parse_definition("SELECT COUNT(*) FROM orders WHERE status='absent'",'order_date')
    assert Decimal(c.compare(snapshot,count,count,{**CTX,'unit':'count'})['baseline_value'])==0
    distinct=c.parse_definition('SELECT COUNT(DISTINCT customer_id) FROM orders','order_date')
    r=c.compare(snapshot,distinct,distinct,{**CTX,'unit':'count'})
    assert Decimal(r['baseline_value'])==7 and len(r['evidence'])==7

@pytest.mark.parametrize('old,new',[('O02,C02','O01,C02'),('2026-08-05','2026-99-05'),('60000000.00','999999999999999999.00'),('60000000.00','1.001'),('INR','USD')])
def test_bad_snapshot(tmp_path,old,new):
    c=api(); src=tmp_path/'bad.csv'; src.write_text((ROOT/'MetricGuard_Planning_Pack/examples/orders_golden.csv').read_text().replace(old,new,1))
    with pytest.raises(c.CoreError): c.ingest_csv(src,tmp_path/'bad.duckdb')

def test_snapshot_tamper(snapshot):
    c=api(); a,_,f=definitions()
    with snapshot.open('ab') as stream: stream.write(b'tampered')
    with pytest.raises(c.CoreError): c.compare(snapshot,a,f,CTX)

def custom_snapshot(tmp_path, replacements):
    c=api(); text=(ROOT/'MetricGuard_Planning_Pack/examples/orders_golden.csv').read_text()
    for old,new in replacements: text=text.replace(old,new)
    src=tmp_path/'custom.csv'; src.write_text(text); db=tmp_path/'custom.duckdb'; c.ingest_csv(src,db); return db

def test_null_terms_explicit_coalesce(tmp_path):
    c=api(); db=custom_snapshot(tmp_path,[('2400000.00','')])
    plain=c.parse_definition("SELECT SUM(order_amount-refund_amount) FROM orders WHERE status='Completed'",'order_date')
    fill=c.parse_definition("SELECT SUM(order_amount-COALESCE(refund_amount,0)) FROM orders WHERE status='Completed'",'order_date')
    r=c.compare(db,plain,fill,CTX)
    assert Decimal(r['delta_target_minus_baseline'])==60000000
    assert r['evidence'][0]['baseline_null_term'] is True
    assert r['evidence'][0]['target_null_term'] is False

def test_sql_three_valued_predicates(snapshot):
    c=api()
    queries=[("status <> 'Cancelled'",6),("NOT (status = 'Cancelled')",6),("status IS NULL",1),("status IS NOT NULL",7),("status IN ('Completed',NULL)",5),("status NOT IN ('Completed',NULL)",0),("status='Completed' OR status IS NULL",6)]
    for predicate,expected in queries:
        ir=c.parse_definition('SELECT COUNT(*) FROM orders WHERE '+predicate,'order_date')
        assert Decimal(c.compare(snapshot,ir,ir,{**CTX,'unit':'count'})['baseline_value'])==expected

def test_zero_baseline_percent_undefined(snapshot):
    c=api(); a=c.parse_definition('SELECT SUM(0) FROM orders','order_date'); b=c.parse_definition('SELECT SUM(order_amount) FROM orders','order_date')
    r=c.compare(snapshot,a,b,CTX)
    assert r['relative_change_percent_display'] is None and Decimal(r['absolute_gap'])>0

def test_aggregate_arithmetic_overflow(snapshot):
    c=api(); ir=c.parse_definition('SELECT SUM(order_amount * order_amount * order_amount * order_amount * order_amount) FROM orders','order_date')
    with pytest.raises(c.CoreError): c.compare(snapshot,ir,ir,CTX)

def test_distinct_entity_null_and_duplicate(tmp_path):
    c=api(); db=custom_snapshot(tmp_path,[('O08,C07','O08,')])
    a=c.parse_definition('SELECT COUNT(DISTINCT customer_id) FROM orders','order_date')
    b=c.parse_definition("SELECT COUNT(DISTINCT customer_id) FROM orders WHERE status='Completed'",'order_date')
    r=c.compare(db,a,b,{**CTX,'unit':'count'})
    assert Decimal(r['baseline_value'])==6 and Decimal(r['target_value'])==4
    assert sum(Decimal(row['delta']) for row in r['evidence'])==-2
    assert len(r['evidence'])==6

def test_equal_values_different_definition(snapshot):
    c=api(); a=c.parse_definition("SELECT SUM(order_amount) FROM orders WHERE status='Completed'",'order_date'); b=c.parse_definition("SELECT SUM(order_amount) FROM orders WHERE status IN ('Completed')",'order_date')
    r=c.compare(snapshot,a,b,CTX)
    assert r['definition_status']=='different' and r['value_status']=='equal_on_snapshot'

def test_ir_injection_and_extra_fields(snapshot):
    from copy import deepcopy
    c=api(); a,_,f=definitions()
    for modification in [{'extra':'sql'}, {'ir_hash':'fake'}, {'predicate':{'op':'literal','type':'decimal','value':'0); COPY orders TO \'stolen\'; --'}}]:
        forged={**deepcopy(a),**modification}
        with pytest.raises(c.CoreError): c.compare(snapshot,forged,f,CTX)

def test_snapshot_cannot_overwrite(snapshot):
    c=api()
    with pytest.raises(c.CoreError): c.ingest_csv(ROOT/'MetricGuard_Planning_Pack/examples/orders_golden.csv',snapshot)

def test_context_must_confirm_time(snapshot):
    c=api(); ir=c.parse_definition('SELECT SUM(order_amount) FROM orders')
    with pytest.raises(c.CoreError) as error: c.compare(snapshot,ir,ir,CTX)
    assert error.value.code=='needs_context'

def test_empty_snapshot(tmp_path):
    c=api(); src=tmp_path/'empty.csv'; src.write_text((ROOT/'MetricGuard_Planning_Pack/examples/orders_golden.csv').read_text().splitlines()[0]+'\n'); db=tmp_path/'empty.duckdb'; c.ingest_csv(src,db)
    for aggregate,expected,unit in [('SUM(order_amount)',None,'currency'),('COUNT(*)','0','count'),('COUNT(DISTINCT customer_id)','0','count')]:
        ir=c.parse_definition('SELECT '+aggregate+' FROM orders','order_date'); r=c.compare(db,ir,ir,{**CTX,'unit':unit}); assert r['baseline_value']==expected

@pytest.mark.parametrize('sql',[
 'SELECT SUM(order_amount) FROM orders WHERE order_date > DATE \'2026-08-01\' AND order_date < DATE \'2026-09-01\'',
 'SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE \'2026-08-01\'',
 'SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE \'2026-08-01\' AND payment_date < DATE \'2026-09-01\'',
 'SELECT SUM(order_amount) FROM orders WHERE order_date >= \'2026-08-01\' AND order_date < \'2026-09-01\'',
 'SELECT SUM(order_amount) FILTER(WHERE status=\'Completed\') FROM orders',
 'SELECT DISTINCT SUM(order_amount) FROM orders',
 'SELECT SUM(order_amount) FROM orders PIVOT (SUM(order_amount) FOR status IN (\'Paid\'))',
 'SELECT SUM(order_amount) FROM main.orders',
 'SELECT SUM(order_amount) FROM orders WHERE currency COLLATE NOCASE = \'INR\'',
])
def test_additional_ast_rejections(sql):
    c=api()
    with pytest.raises(c.CoreError): c.parse_definition(sql,'order_date')


def test_excluded_rows_do_not_evaluate_overflowing_terms(tmp_path):
    c=api(); db=custom_snapshot(tmp_path,[('60000000.00','9999999999999999.00')])
    ir=c.parse_definition("SELECT SUM(order_amount * order_amount) FROM orders WHERE order_id='O03'",'order_date')
    r=c.compare(db,ir,ir,CTX)
    assert Decimal(r['baseline_value'])==Decimal('2320000')**2

def test_undefined_hybrid_cannot_claim_complete(tmp_path):
    c=api(); db=custom_snapshot(tmp_path,[('2400000.00','')])
    a=c.parse_definition("SELECT SUM(order_amount) FROM orders WHERE order_id='O01'",'order_date')
    b=c.parse_definition("SELECT SUM(refund_amount) FROM orders WHERE order_id='O02'",'order_date')
    r=c.compare(db,a,b,CTX)
    assert r['baseline_value'] is not None and r['target_value'] is not None
    assert r['explanation_status']=='partial'
    assert Decimal(r['residual'])==Decimal(r['delta_target_minus_baseline'])

