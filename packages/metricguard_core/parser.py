"""Closed recursive AST lowering. Every accepted node checks all populated arguments."""
from datetime import date
from decimal import Decimal, InvalidOperation
import sqlglot
from sqlglot import exp
from .common import CoreError, SCHEMA, NUMERIC, DATES, POLICY, digest, ALL_COLUMNS, ALL_NUMERIC, ALL_DATES, ALL_BOOLEANS

def fail(message): raise CoreError('unsupported_sql', message)

def checked(node, allowed):
    if any(value is not None and value is not False and value != [] for key,value in node.args.items() if key not in allowed):
        fail(f'Unsupported modifier on {type(node).__name__}')

def identifier(node):
    if not isinstance(node, exp.Identifier): fail('Expected plain identifier')
    checked(node, {'this','quoted'})
    name=node.this if node.args.get('quoted') else node.this.lower()
    return name

def parse_definition(sql, time_column=None):
    if not isinstance(sql,str) or not sql.strip() or len(sql)>50000: fail('SQL must contain 1 to 50000 characters')
    try: statements=sqlglot.parse(sql,read='duckdb')
    except Exception as e: raise CoreError('unsupported_sql','SQL could not be parsed') from e
    if len(statements)!=1 or not isinstance(statements[0],exp.Select): fail('Exactly one SELECT is required')
    tree=statements[0]
    if tree.args.get('with_') or list(tree.find_all(exp.CTE)):
        from .normalization import normalize_level1_cte
        ok, normalized_sql, diag, _ = normalize_level1_cte(sql)
        if not ok:
            fail(diag or 'Unsupported CTE structure')
        return parse_definition(normalized_sql, time_column)
    checked(tree,{'expressions','from_','where'})
    if len(list(tree.walk()))>1000: fail('Expression exceeds 1000 nodes')
    source=tree.args.get('from_')
    if not isinstance(source,exp.From): fail('FROM orders is required')
    checked(source,{'this'})
    table=source.this
    if not isinstance(table,exp.Table): fail('Only the registered orders table is allowed')
    checked(table,{'this','alias'})
    if identifier(table.this)!='orders': fail('Only the registered orders table is allowed')
    alias='orders'
    if table.args.get('alias'):
        checked(table.args['alias'],{'this'}); alias=identifier(table.args['alias'].this)
    def lower(node, depth=0):
        if depth>50: fail('Expression nesting exceeds 50')
        child=lambda n:lower(n,depth+1)
        if isinstance(node,exp.Paren): checked(node,{'this'}); return child(node.this)
        if isinstance(node,exp.Column):
            checked(node,{'this','table'})
            if node.args.get('table') and identifier(node.args['table'])!=alias: fail('Unbound table qualifier')
            name=identifier(node.this)
            if name not in ALL_COLUMNS: fail(f'Unknown column {name}')
            col_type = 'decimal' if name in ALL_NUMERIC else 'date' if name in ALL_DATES else 'bool' if name in ALL_BOOLEANS else 'string'
            return {'op':'column','name':name,'type':col_type}
        if isinstance(node,exp.Literal):
            checked(node,{'this','is_string'})
            if node.is_string: return {'op':'literal','value':node.this,'type':'string'}
            try:
                value=Decimal(node.this)
                if not value.is_finite() or abs(value)>=Decimal('1e18') or value.as_tuple().exponent < -8: fail('Numeric literal exceeds supported precision')
            except InvalidOperation: fail('Invalid decimal literal')
            return {'op':'literal','value':format(value,'f'),'type':'decimal'}
        if isinstance(node,exp.Boolean):
            checked(node,{'this'})
            return {'op':'literal','value':bool(node.this),'type':'bool'}
        if isinstance(node,exp.Null): checked(node,set()); return {'op':'literal','value':None,'type':'null'}
        if isinstance(node,exp.Cast):
            checked(node,{'this','to'})
            typ=node.args['to']; checked(typ,{'this','nested'})
            if typ.this!=exp.DataType.Type.DATE or not isinstance(node.this,exp.Literal) or not node.this.is_string: fail('Only typed DATE literals are supported')
            try: value=date.fromisoformat(node.this.this).isoformat()
            except ValueError: fail('Invalid DATE literal')
            return {'op':'literal','value':value,'type':'date'}
        binary={exp.Add:'add',exp.Sub:'sub',exp.Mul:'mul',exp.And:'and',exp.Or:'or',exp.EQ:'eq',exp.NEQ:'neq',exp.GT:'gt',exp.GTE:'gte',exp.LT:'lt',exp.LTE:'lte'}
        if type(node) in binary:
            checked(node,{'this','expression'}); left,right=child(node.this),child(node.expression); op=binary[type(node)]
            if op in {'add','sub','mul'}:
                if left['type']!='decimal' or right['type']!='decimal': fail('Arithmetic requires decimal operands')
                kind='decimal'
            elif op in {'and','or'}:
                if left['type']!='bool' or right['type']!='bool': fail('Boolean operators require predicates')
                if op == 'or':
                    def _cols_in(n):
                        if isinstance(n, dict):
                            return ({n['name']} if n.get('op') == 'column' else set()).union(*(_cols_in(v) for v in n.values()))
                        if isinstance(n, list): return set().union(*(_cols_in(v) for v in n))
                        return set()
                    left_cols = _cols_in(left)
                    right_cols = _cols_in(right)
                    if not left_cols or left_cols != right_cols or len(left_cols) > 1:
                        fail('Inter-field OR disjunction is not supported')
                kind='bool'
            else:
                if left['op'] == 'column' and right['op'] == 'column':
                    fail('Cross-column comparisons are not supported')
                if left['type']!=right['type'] and 'null' not in {left['type'],right['type']}: fail('Comparison types must agree without implicit casts')
                if op in {'gt', 'gte', 'lt', 'lte'}:
                    if left['type'] not in {'decimal', 'date', 'timestamp'}:
                        fail(f'{op.upper()} comparison is only supported on numeric, date, or timestamp columns')
                kind='bool'
            return {'op':op,'left':left,'right':right,'type':kind}
        if isinstance(node,exp.Between):
            checked(node,{'this','low','high','is_not','symmetric'})
            if node.args.get('is_not'): fail('NOT BETWEEN is not supported')
            if node.args.get('symmetric'): fail('BETWEEN SYMMETRIC is not supported')
            arg=child(node.this); low=child(node.args['low']); high=child(node.args['high'])
            if arg['type'] not in {'decimal','date','timestamp'}: fail('BETWEEN requires numeric, date, or timestamp column')
            if low['type']!=arg['type'] or high['type']!=arg['type']: fail('BETWEEN bounds must match column type')
            if low['op']!='literal' or high['op']!='literal': fail('BETWEEN bounds must be literal constants')
            if arg['type']=='decimal':
                if Decimal(low['value']) > Decimal(high['value']): fail('INVALID_RANGE_BOUNDS: Lower bound exceeds upper bound in BETWEEN')
            elif arg['type']=='date':
                if date.fromisoformat(low['value']) > date.fromisoformat(high['value']): fail('INVALID_RANGE_BOUNDS: Lower bound exceeds upper bound in BETWEEN')
            return {'op':'between','arg':arg,'low':low,'high':high,'type':'bool'}
        if isinstance(node,exp.Neg):
            checked(node,{'this'}); term=child(node.this)
            if term['type']!='decimal': fail('Negation requires a number')
            return {'op':'neg','arg':term,'type':'decimal'}
        if isinstance(node,exp.Not):
            checked(node,{'this'})
            if isinstance(node.this,exp.Is):
                checked(node.this,{'this','expression'})
                arg=child(node.this.this)
                if isinstance(node.this.expression,exp.Null):
                    return {'op':'is_not_null','arg':arg,'type':'bool'}
                if isinstance(node.this.expression,exp.Boolean):
                    if arg['type']!='bool': fail('IS NOT TRUE/FALSE requires boolean column')
                    return {'op':'is_not_true' if node.this.expression.this else 'is_not_false','arg':arg,'type':'bool'}
                fail('Only IS NOT NULL and IS NOT TRUE/FALSE are supported')
            term=child(node.this)
            if term['type']!='bool': fail('NOT requires a predicate')
            return {'op':'not','arg':term,'type':'bool'}
        if isinstance(node,exp.Is):
            checked(node,{'this','expression'})
            arg=child(node.this)
            if isinstance(node.expression,exp.Null):
                return {'op':'is_null','arg':arg,'type':'bool'}
            if isinstance(node.expression,exp.Boolean):
                if arg['type']!='bool': fail('IS TRUE/FALSE requires boolean column')
                return {'op':'is_true' if node.expression.this else 'is_false','arg':arg,'type':'bool'}
            fail('Only IS NULL and IS TRUE/FALSE are supported')
        if isinstance(node,exp.In):
            checked(node,{'this','expressions'}); term=child(node.this); values=[child(v) for v in node.expressions]
            if not values or any(v['op']!='literal' or v['type'] not in {term['type'],'null'} for v in values): fail('IN requires type-compatible literals')
            return {'op':'in','arg':term,'values':values,'type':'bool'}
        if isinstance(node,exp.Coalesce):
            checked(node,{'this','expressions'}); terms=[child(node.this)]+[child(v) for v in node.expressions]
            if len(terms)!=2 or any(v['type']!='decimal' for v in terms): fail('COALESCE requires two numeric operands')
            return {'op':'coalesce','args':terms,'type':'decimal'}
        fail(f'Unsupported expression: {type(node).__name__}')
    if len(tree.expressions)!=1: fail('Exactly one scalar aggregate is required')
    aggregate=tree.expressions[0]
    if isinstance(aggregate,exp.Alias): checked(aggregate,{'this','alias'}); identifier(aggregate.args['alias']); aggregate=aggregate.this
    checked(aggregate,{'this','big_int'})
    if isinstance(aggregate,exp.Sum):
        expression=lower(aggregate.this); kind='sum'
        if expression['type']!='decimal': fail('SUM requires numeric expression')
    elif isinstance(aggregate,exp.Count):
        if isinstance(aggregate.this,exp.Star): checked(aggregate.this,set()); expression=None; kind='count'
        elif isinstance(aggregate.this,exp.Distinct):
            checked(aggregate.this,{'expressions'})
            if len(aggregate.this.expressions)!=1: fail('COUNT DISTINCT requires one column')
            expression=lower(aggregate.this.expressions[0]); kind='count_distinct'
            if expression.get('name')!='customer_id': fail('COUNT DISTINCT supports customer_id only')
        else: fail('Only COUNT(*) or COUNT(DISTINCT customer_id) is supported')
    else: fail('Only SUM and COUNT are supported')
    where=tree.args.get('where'); predicate=None
    if where: checked(where,{'this'}); predicate=lower(where.this)
    if predicate and predicate['type']!='bool': fail('WHERE must be a predicate')
    def flatten(p): return flatten(p['left'])+flatten(p['right']) if p and p['op']=='and' else [p] if p else []
    def date_columns(p):
        if isinstance(p,dict):
            return ({p['name']} if p.get('op')=='column' and p.get('name') in ALL_DATES else set()).union(*(date_columns(v) for v in p.values()))
        if isinstance(p,list): return set().union(*(date_columns(v) for v in p))
        return set()
    def is_period_boundary_candidate(clause):
        return (isinstance(clause, dict) and
                clause.get('op') in {'gte', 'lt'} and
                clause.get('left', {}).get('op') == 'column' and
                clause.get('left', {}).get('name') in ALL_DATES and
                clause.get('right', {}).get('op') == 'literal' and
                clause.get('right', {}).get('type') == 'date')

    clauses = flatten(predicate)
    bounds = {}; business = []; detected = set()
    if time_column:
        for clause in clauses:
            cols = date_columns(clause)
            if time_column in cols:
                if not is_period_boundary_candidate(clause) or clause['left']['name'] != time_column:
                    fail('Period must be a separable half-open DATE interval')
                key = 'start' if clause['op'] == 'gte' else 'end_exclusive'
                if key in bounds: fail('Competing period boundaries')
                bounds[key] = clause['right']['value']
                detected.add(time_column)
            else: business.append(clause)
    else:
        date_candidates = {}
        for clause in clauses:
            if is_period_boundary_candidate(clause):
                date_candidates.setdefault(clause['left']['name'], []).append(clause)
        both = [c for c, l in date_candidates.items() if {'gte','lt'}.issubset({x['op'] for x in l})]
        if len(both) == 1:
            period_col = both[0]
            for clause in clauses:
                cols = date_columns(clause)
                if period_col in cols:
                    if not is_period_boundary_candidate(clause) or clause['left']['name'] != period_col:
                        fail('Period must be a separable half-open DATE interval')
                    key = 'start' if clause['op'] == 'gte' else 'end_exclusive'
                    if key in bounds: fail('Competing period boundaries')
                    bounds[key] = clause['right']['value']
                    detected.add(period_col)
                else: business.append(clause)
            time_column = period_col
        elif len(both) > 1: fail('Competing period boundaries')
        else:
            if any(is_period_boundary_candidate(c) for c in clauses): fail('A single complete half-open period is required')
            business = list(clauses)

    if detected:
        if bounds and set(bounds) != {'start', 'end_exclusive'}: fail('A single complete half-open period is required')
        if bounds and bounds['start'] >= bounds['end_exclusive']: fail('Period start must precede end')
    if time_column is not None and time_column not in ALL_DATES: fail('Unknown business date column')
    predicate = None
    for p in business: predicate = p if predicate is None else {'op': 'and', 'left': predicate, 'right': p, 'type': 'bool'}
    ir = {'policy': POLICY, 'table': 'orders', 'aggregate': kind, 'expression': expression, 'predicate': predicate, 'time_column': time_column, 'period': bounds or None}
    return {**ir, 'ir_hash': digest(ir)}


def render_expression(node):
    if node is None: return 'TRUE'
    op=node['op']
    if op=='column': return '"'+node['name']+'"'
    if op=='literal':
        if node['value'] is None: return 'NULL'
        if node['type']=='decimal': return node['value']
        if node['type']=='bool': return 'TRUE' if node['value'] else 'FALSE'
        return ('DATE ' if node['type']=='date' else '')+"'"+str(node['value']).replace("'","''")+"'"
    if op in {'add','sub','mul','and','or','eq','neq','gt','gte','lt','lte'}:
        symbols={'add':'+','sub':'-','mul':'*','and':'AND','or':'OR','eq':'=','neq':'<>','gt':'>','gte':'>=','lt':'<','lte':'<='}
        return '('+render_expression(node['left'])+' '+symbols[op]+' '+render_expression(node['right'])+')'
    if op=='between':
        return '('+render_expression(node['arg'])+' BETWEEN '+render_expression(node['low'])+' AND '+render_expression(node['high'])+')'
    if op in {'neg','not'}: return '('+('-' if op=='neg' else 'NOT ')+render_expression(node['arg'])+')'
    if op=='is_null': return '('+render_expression(node['arg'])+' IS NULL)'
    if op=='is_not_null': return '('+render_expression(node['arg'])+' IS NOT NULL)'
    if op=='is_true': return '('+render_expression(node['arg'])+' IS TRUE)'
    if op=='is_not_true': return '('+render_expression(node['arg'])+' IS NOT TRUE)'
    if op=='is_false': return '('+render_expression(node['arg'])+' IS FALSE)'
    if op=='is_not_false': return '('+render_expression(node['arg'])+' IS NOT FALSE)'
    if op=='in': return '('+render_expression(node['arg'])+' IN ('+', '.join(render_expression(v) for v in node['values'])+'))'
    if op=='coalesce': return 'COALESCE('+', '.join(render_expression(v) for v in node['args'])+')'
    fail(f'Invalid IR operation: {op}')

def revalidate(ir):
    """Reject forged or stale payloads before execution; renderer is not the validator."""
    try:
        if not isinstance(ir,dict) or len(str(ir))>200000: fail('Invalid IR')
        if set(ir)!={'policy','table','aggregate','expression','predicate','time_column','period','ir_hash'}: fail('Invalid IR fields')
        if ir['policy']!=POLICY or ir['table']!='orders': fail('Unsupported IR policy/table')
        # Structural whitelist checks precede any SQL rendering, including identifiers.
        def check(n,depth=0):
            if n is None: return
            if depth>50 or not isinstance(n,dict): fail('Invalid IR tree')
            op=n.get('op')
            shapes = {
                'column': {'name'}, 'literal': {'value'}, 'neg': {'arg'}, 'not': {'arg'},
                'is_null': {'arg'}, 'is_not_null': {'arg'}, 'is_true': {'arg'}, 'is_not_true': {'arg'},
                'is_false': {'arg'}, 'is_not_false': {'arg'}, 'in': {'arg', 'values'},
                'between': {'arg', 'low', 'high'}, 'coalesce': {'args'}
            }
            fields=shapes.get(op,{'left','right'} if op in {'add','sub','mul','and','or','eq','neq','gt','gte','lt','lte'} else None)
            if fields is None or set(n)!={'op','type'}|fields: fail(f'Invalid IR node: {op}')
            if op=='column' and n['name'] not in ALL_COLUMNS: fail('Unregistered column')
            if op=='literal':
                if n['type']=='decimal':
                    import re
                    if not re.fullmatch(r'-?\d+(\.\d+)?',n['value'] or ''): fail('Invalid numeric literal')
                elif n['type']=='bool':
                    if not isinstance(n['value'], bool): fail('Invalid boolean literal')
                elif n['value'] is not None and not isinstance(n['value'],str):
                    fail('Invalid literal')
            for k in ('arg','left','right','low','high'):
                if k in n: check(n[k],depth+1)
            for k in ('args','values'):
                if k in n:
                    if not isinstance(n[k],list) or len(n[k])>1000: fail('Invalid IR list')
                    for v in n[k]: check(v,depth+1)
        check(ir['expression']); check(ir['predicate'])
        if ir['aggregate'] not in {'sum','count','count_distinct'} or ir['time_column'] not in ALL_DATES|{None}: fail('Invalid aggregate/time')
        agg={'sum':lambda:'SUM('+render_expression(ir['expression'])+')','count':lambda:'COUNT(*)','count_distinct':lambda:'COUNT(DISTINCT '+render_expression(ir['expression'])+')'}[ir['aggregate']]()
        sql='SELECT '+agg+' FROM orders'
        terms=[]
        if ir['predicate']: terms.append(render_expression(ir['predicate']))
        if ir['period']:
            if set(ir['period'])!={'start','end_exclusive'}: fail('Invalid period')
            for key,symbol in [('start','>='),('end_exclusive','<')]:
                value=date.fromisoformat(ir['period'][key]).isoformat()
                terms.append('"'+ir['time_column']+'" '+symbol+" DATE '"+value+"'")
        if terms: sql+=' WHERE '+' AND '.join(terms)
        validated=parse_definition(sql,ir['time_column'])
        if validated!=ir: fail('IR fingerprint or typed structure mismatch')
        return validated
    except CoreError: raise
    except Exception as e: raise CoreError('invalid_ir','IR validation failed') from e
