"""Exact fixed-snapshot reconciliation from revalidated IR only."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
import threading
import time
import duckdb
import sqlglot
from .free_tier import check_rows
from .common import CoreError, digest
from .parser import revalidate, render_expression
from .snapshot import inspect_snapshot
from .shapley import exact_three_group_shapley

D=Decimal

def number(value): return None if value is None else format(value,'f')

def validate_context(database,baseline,target,context):
    baseline,target=revalidate(baseline),revalidate(target)
    manifest=inspect_snapshot(database)
    check_rows(manifest["row_count"])
    required={'start','end_exclusive','timezone','currency','unit','grain','scope'}
    if not isinstance(context,dict) or any(not context.get(k) for k in required): raise CoreError('needs_context','Period, timezone, currency, unit, grain and scope are required')
    try:
        start=date.fromisoformat(context['start']); end=date.fromisoformat(context['end_exclusive'])
        if start>=end: raise ValueError()
    except (ValueError,TypeError): raise CoreError('invalid_period','Start must precede exclusive end, using YYYY-MM-DD dates')
    for key in ('timezone','currency','grain','scope'):
        if context[key]!=manifest[key]: raise CoreError('not_comparable',f'Context {key} differs from snapshot')
    for key in ('contract_hash', 'mapping_hash'):
        if context.get(key) != manifest.get(key):
            raise CoreError('not_comparable',f'Context {key} differs from the frozen mapping policy')
    if context.get('source_hash') and context['source_hash']!=manifest['source_hash']: raise CoreError('snapshot_integrity','Context source hash differs')
    if context.get('manifest_hash') and context['manifest_hash']!=manifest['manifest_hash']: raise CoreError('snapshot_integrity','Context manifest hash differs')
    if baseline['aggregate']!=target['aggregate']: raise CoreError('not_comparable','Aggregate families differ')
    expected='currency' if baseline['aggregate']=='sum' else 'count'
    if context['unit']!=expected: raise CoreError('not_comparable',f'This aggregate requires unit {expected}')
    for ir in (baseline,target):
        if not ir['time_column']: raise CoreError('needs_context','Confirm a business date column before execution')
        if ir['period'] and ir['period']!={k:context[k] for k in ('start','end_exclusive')}: raise CoreError('period_conflict','Literal period differs from comparison context')
    return manifest

def _evaluate(con,ir,context):
    expression='1' if ir['aggregate']=='count' else render_expression(ir['expression'])
    predicate=render_expression(ir['predicate'])
    period='"'+ir['time_column']+'" >= ? AND "'+ir['time_column']+'" < ?'
    sql='SELECT order_id, customer_id, (('+predicate+') AND '+period+') AS included, CASE WHEN included THEN '+expression+' ELSE NULL END AS term FROM orders ORDER BY order_id'
    timer=threading.Timer(15,con.interrupt); timer.daemon=True; timer.start()
    try: rows=con.execute(sql,[date.fromisoformat(context['start']),date.fromisoformat(context['end_exclusive'])]).fetchall()
    except duckdb.Error as e: raise CoreError('execution_failed','Validated analytical query failed or exceeded its resource limit') from e
    finally: timer.cancel()
    if ir['aggregate']=='count_distinct':
        universe={row[1] for row in rows if row[1] is not None}
        included={row[3] for row in rows if row[2] is True and row[3] is not None}
        records={entity:(entity in included,D(int(entity in included)),False) for entity in sorted(universe)}
        total=D(len(included))
    else:
        records={key:(included is True,D(term) if included is True and term is not None else D(0),included is True and term is None) for key,customer,included,term in rows}
        terms=[D(term) for key,customer,included,term in rows if included is True and term is not None]
        total=sum(terms,D(0)) if terms or ir['aggregate']=='count' else None
    if total is not None and abs(total)>=D('1e38'): raise CoreError('numeric_overflow','Endpoint exceeds 38 decimal digits')
    return total,records,digest({'sql':sql,'parameters':[context['start'],context['end_exclusive']]})

def compare(database,baseline,target,context):
    baseline,target=revalidate(baseline),revalidate(target)
    manifest=validate_context(database,baseline,target,context)
    started=time.monotonic()
    with localcontext() as decimal_context:
        decimal_context.prec=100
        return _compare(database,baseline,target,context,manifest,started)

def _compare(database,baseline,target,context,manifest,started):
    con=duckdb.connect(str(database),read_only=True,config={'enable_external_access':'false','autoinstall_known_extensions':'false','autoload_known_extensions':'false','memory_limit':'512MB','threads':'1'})
    try:
        con.execute('SET lock_configuration = true')
        av,ar,aq=_evaluate(con,baseline,context); bv,br,bq=_evaluate(con,target,context)
        gap=bv-av if av is not None and bv is not None else None
        evidence=[]
        for key in sorted(ar.keys()|br.keys()):
            ai,ac,an=ar.get(key,(False,D(0),False)); bi,bc,bn=br.get(key,(False,D(0),False))
            evidence.append({'order_id':key,'baseline_included':ai,'target_included':bi,'baseline_contribution':number(ac),'target_contribution':number(bc),'delta':number(bc-ac),'baseline_null_term':an,'target_null_term':bn,'membership':'included_in_both' if ai and bi else 'baseline_only' if ai else 'target_only' if bi else 'included_in_neither'})
        if gap is not None and sum((D(row['delta']) for row in evidence),D(0))!=gap: raise CoreError('reconciliation_failure','Evidence does not reconcile with endpoints')
        groups=[('amount_expression',['expression']),('eligibility_predicate',['predicate']),('time_attribution',['time_column','period'])]
        differences=[{'rule':name,'baseline':{k:baseline[k] for k in fields},'target':{k:target[k] for k in fields},'changed':any(baseline[k]!=target[k] for k in fields)} for name,fields in groups]
        steps=[]; hybrid=deepcopy(baseline); previous=av; partial=False
        hybrid_values={0:number(av),7:number(bv)}
        if gap is not None:
            for position,(name,fields) in enumerate(groups,1):
                for field in fields: hybrid[field]=deepcopy(target[field])
                hybrid['ir_hash']=digest({k:v for k,v in hybrid.items() if k!='ir_hash'})
                revalidate(hybrid)
                value,_,query_hash=_evaluate(con,hybrid,context)
                hybrid_values[(1 << position)-1]=number(value)
                if value is None: partial=True; break
                steps.append({'position':position,'rule':name,'previous_value':number(previous),'next_value':number(value),'contribution':number(value-previous),'intermediate_ir':deepcopy(hybrid),'query_hash':query_hash})
                previous=value
            if not partial and (hybrid!=target or previous!=bv): raise CoreError('reconciliation_failure','Bridge did not reconstruct target')
        residual=gap-sum((D(s['contribution']) for s in steps),D(0)) if gap is not None else None
        tolerance=D('0.01') if baseline['aggregate']=='sum' else D(0)
        relative=gap/abs(av)*100 if gap is not None and av!=0 else None
        value_status='not_run' if gap is None else 'equal_on_snapshot' if gap==0 else 'within_tolerance' if abs(gap)<=tolerance else 'different'
        limitations=['Observed equality applies only to this frozen snapshot.','Ordered rule effects depend on amount, eligibility, time sequence and are not causal claims.','Business DATE semantics only; no timestamp conversion or currency conversion.']
        if gap is None: limitations.append('SUM endpoint is undefined because no non-NULL terms matched; no arithmetic gap or bridge is available.')
        if partial: limitations.append('An intermediate SUM was undefined; remaining attribution is unavailable.')
        if baseline['aggregate']=='count_distinct': limitations.append('Evidence order_id contains customer_id: distinct entities are the evidence unit, not orders.')
        shapley_queries=0
        if gap is not None:
            try:
                for mask in range(1,7):
                    if mask in hybrid_values:
                        continue
                    mixed=deepcopy(baseline)
                    for index,(_,fields) in enumerate(groups):
                        if mask & (1 << index):
                            for field in fields: mixed[field]=deepcopy(target[field])
                    mixed['ir_hash']=digest({k:v for k,v in mixed.items() if k!='ir_hash'})
                    revalidate(mixed)
                    value,_,_=_evaluate(con,mixed,context)
                    shapley_queries+=1
                    hybrid_values[mask]=number(value)
                shapley=exact_three_group_shapley(hybrid_values,tuple(name for name,_ in groups))
            except (CoreError,ArithmeticError,ValueError):
                shapley={'status':'unavailable','method':'exact_three_group_shapley','contributions':[],
                         'limitation':'A hybrid could not be evaluated safely; ordered bridge and endpoints remain available.'}
        else:
            shapley={'status':'unavailable','method':'exact_three_group_shapley','contributions':[],
                     'limitation':'An undefined endpoint has no exact Shapley allocation.'}
        return {'baseline_value':number(av),'target_value':number(bv),'delta_target_minus_baseline':number(gap),'absolute_gap':number(abs(gap)) if gap is not None else None,'relative_change_percent_display':number(relative.quantize(D('0.000000001'))) if relative is not None else None,'residual':number(residual),'currency':context['currency'],'numeric_tolerance':number(tolerance),'method':'ordered_rule_bridge','definition_status':'same_supported_representation' if baseline==target else 'different','value_status':value_status,'explanation_status':'unavailable' if gap is None else 'partial' if partial else 'complete_ordered_bridge','steps':steps,'shapley':shapley,'evidence':evidence,'definition_differences':differences,'provenance':{'baseline_ir_hash':baseline['ir_hash'],'target_ir_hash':target['ir_hash'],'snapshot_manifest_hash':manifest['manifest_hash'],'database_hash':manifest['database_hash'],'source_hash':manifest['source_hash'],'contract_hash':manifest.get('contract_hash'),'mapping_hash':manifest.get('mapping_hash'),'parser_version':sqlglot.__version__,'duckdb_version':duckdb.__version__,'engine_version':'metricguard-core-v1','context':context,'baseline_query_hash':aq,'target_query_hash':bq,'query_count':2+len(steps)+(1 if partial else 0)+shapley_queries,'elapsed_ms':round((time.monotonic()-started)*1000),'evidence_unit':'customer_id' if baseline['aggregate']=='count_distinct' else 'order_id'},'limitations':limitations}
    finally: con.close()


