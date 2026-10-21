"""Exact ratio metrics N / D over one flat snapshot (policy flat-ratio-v1).

N and D are each an additive part, SUM(amount), COUNT(*) or COUNT(amount), with an
optional part filter, under one shared WHERE. AVG(amount) is lowered to
SUM(amount) / COUNT(amount). On contract v8 the two parts may read different amount
columns (margin % = SUM(profit) / SUM(revenue)). The division never runs in SQL: N and
D are exact decimals added up in Python and the ratio is an exact fraction.

The change R1 - R0 is split by a two-player Shapley over (numerator, denominator):

    numerator_effect   =  dN * (D0 + D1) / (2 * D0 * D1)
    denominator_effect = -dD * (N0 + N1) / (2 * D0 * D1)

Both are linear in dN = sum(dn_i) and dD = sum(dd_i), so each record gets an exact
decimal share g_i = sigma * (dn_i * (D0 + D1) - dd_i * (N0 + N1)), with
sigma = sign(D0 * D1). The shares add up to G = 2 * sigma * (N1 * D0 - N0 * D1) and
the ratio change is G / Q with Q = 2 * |D0 * D1| > 0. Only Q is shared, so the
evidence stays a list of exact decimals that add up to a verified total.
"""
from __future__ import annotations

from fractions import Fraction

from sqlglot import exp

from .common import CoreError, digest
from .flat_boolean import PREDICATE_POLICY, compile_boolean, lower_boolean, validate_boolean
from .numeric import ScaledInteger, parse_fixed_point

RATIO_POLICY = 'flat-ratio-v1'
MAX_RATIO_LEAVES = 24
MAX_DISPLAY_PLACES = 12
IR_FIELDS = {'mode', 'policy', 'contract_hash', 'table', 'aggregate', 'aggregate_policy',
             'numerator', 'denominator', 'predicate_policy', 'predicate_tree', 'ir_hash'}
PART_FIELDS = {'aggregate', 'measure', 'minus', 'filter'}
UNDEFINED_REASONS = ('null_numerator', 'null_denominator', 'zero_denominator')


def _error(code, message):
    raise CoreError(code, message)


# ---------- exact number helpers ----------

def to_fraction(value: str | None) -> Fraction | None:
    if value is None:
        return None
    coeff, scale = parse_fixed_point(value)
    return Fraction(coeff, 10 ** scale)


def fixed(value: Fraction, scale: int) -> str:
    """Exact fixed-point string; refuses any value that is not exact at this scale."""
    scaled = value * 10 ** scale
    if scaled.denominator != 1:
        raise CoreError('arithmetic_overflow', 'A ratio evidence value is not exact at its declared scale')
    return ScaledInteger(scaled.numerator, scale).to_fixed_string()


def fraction_text(value: Fraction | None) -> str | None:
    if value is None:
        return None
    return str(value.numerator) if value.denominator == 1 else f'{value.numerator}/{value.denominator}'


def parse_fraction_text(text: str) -> Fraction:
    if not isinstance(text, str) or len(text) > 400:
        raise ValueError('EVIDENCE_INVALID')
    numerator, _, denominator = text.partition('/')
    try:
        value = Fraction(int(numerator), int(denominator) if denominator else 1)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError('EVIDENCE_INVALID') from error
    if fraction_text(value) != text:
        raise ValueError('EVIDENCE_INVALID')  # must be the reduced canonical form
    return value


def display(value: Fraction | None, places: int) -> str | None:
    """Round half to even at `places` decimals (Python rounds Fractions half-even)."""
    if value is None:
        return None
    return ScaledInteger(round(value * 10 ** places), places).to_fixed_string()


# ---------- contract helpers ----------

def amount_specs(contract):
    """Registered amount columns: one for contract v5, two to eight for v8."""
    if contract['version'] == 8:
        return contract['decimal_columns']
    return [{'name': contract['measure'], 'precision': contract['precision'], 'scale': contract['scale'],
             'unit_label': contract['unit_label']}]


def _spec(contract, name):
    return next(c for c in amount_specs(contract) if c['name'] == name)


def _check_subtraction_capacity(contract, measure, minus):
    a, b = _spec(contract, measure), _spec(contract, minus)
    scale = max(a['scale'], b['scale'])
    required = max(a['precision'] - a['scale'], b['precision'] - b['scale']) + scale + 1
    if required > 38:
        _error('unsupported_precision', 'Subtracting these amount columns needs more than 38 decimal digits; prepare a bounded exact difference externally.')


def _ratio_contract(contract):
    return contract['version'] in (5, 8)


# ---------- parsing ----------

def _plain_column(node):
    if (not isinstance(node, exp.Column) or
            any(v for k, v in node.args.items() if k != 'this') or
            not isinstance(node.this, exp.Identifier) or node.this.args.get('quoted')):
        _error('unsupported_sql', 'Ratio parts use a registered amount column by its plain name')
    return node.this.this.lower()


def _strip(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _amount_name(column, contract, purpose):
    names = [c['name'] for c in amount_specs(contract)]
    if column not in names:
        if len(names) == 1:
            _error('unsupported_sql', f'{purpose} must use the amount column {names[0]}. '
                   'Dividing two different amount columns needs a dataset registered with several amount columns.')
        _error('unsupported_sql', f'{purpose} must use one of the amount columns: {", ".join(names)}')
    return column


def _part(node, contract, lower_leaf):
    node = _strip(node)
    part_filter = None
    if isinstance(node, exp.Filter):
        where = node.expression
        if not isinstance(where, exp.Where) or any(v for k, v in node.args.items() if k not in {'this', 'expression'}):
            _error('unsupported_sql', 'FILTER must be written as FILTER (WHERE <condition>)')
        part_filter = lower_boolean(where.this, lower_leaf)
        node = node.this
    if isinstance(node, exp.Sum):
        if any(v for k, v in node.args.items() if k != 'this'):
            _error('unsupported_sql', 'SUM in a ratio takes only an amount column')
        inner = _strip(node.this)
        if isinstance(inner, exp.Sub):
            # SUM(a - b), e.g. margin = SUM(revenue - cost): additive per record, NULL if either is NULL.
            if contract['version'] != 8 or any(v for k, v in inner.args.items() if k not in {'this', 'expression'}):
                _error('unsupported_sql', 'SUM(a - b) inside a ratio needs a dataset registered with several amount columns')
            measure = _amount_name(_plain_column(inner.this), contract, 'SUM(a - b)')
            minus = _amount_name(_plain_column(inner.expression), contract, 'SUM(a - b)')
            if measure == minus:
                _error('unsupported_sql', 'SUM(a - b) needs two different amount columns')
            if _spec(contract, measure)['unit_label'] != _spec(contract, minus)['unit_label']:
                _error('unsupported_sql', 'SUM(a - b) needs two amount columns in the same unit')
            _check_subtraction_capacity(contract, measure, minus)
            return {'aggregate': 'sum', 'measure': measure, 'minus': minus, 'filter': part_filter}
        return {'aggregate': 'sum', 'measure': _amount_name(_plain_column(inner), contract, 'SUM in a ratio'),
                'minus': None, 'filter': part_filter}
    if isinstance(node, exp.Count):
        if any(v for k, v in node.args.items() if k not in ('this', 'big_int')):
            _error('unsupported_sql', 'COUNT in a ratio is COUNT(*) or COUNT(amount)')
        if isinstance(node.this, exp.Star):
            return {'aggregate': 'count', 'measure': '*', 'minus': None, 'filter': part_filter}
        if isinstance(node.this, exp.Distinct):
            _error('unsupported_sql', 'Ratios of distinct counts are not supported yet')
        return {'aggregate': 'count', 'measure': _amount_name(_plain_column(node.this), contract, 'COUNT(column) in a ratio'),
                'minus': None, 'filter': part_filter}
    if isinstance(node, (exp.Div, exp.Avg)):
        _error('unsupported_sql', 'A ratio part cannot itself be a ratio or an average')
    _error('unsupported_sql', 'Each side of a ratio must be SUM(amount), COUNT(*) or COUNT(amount), '
           'optionally followed by FILTER (WHERE ...). Percentages are shown automatically; do not multiply by 100.')


def is_ratio_aggregate(node) -> bool:
    node = _strip(node)
    return isinstance(node, (exp.Div, exp.Avg))


def parse_ratio(aggregate, where, contract, lower_leaf, check_leaf):
    """SQL aggregate node (Div or Avg) plus shared WHERE -> ratio IR (FlatContract v5 or v8)."""
    if not _ratio_contract(contract):
        _error('unsupported_sql', "This dataset was added before averages were supported. Use 'Enable averages and ratios' on the dataset page.")
    aggregate = _strip(aggregate)
    if isinstance(aggregate, exp.Avg):
        if any(v for k, v in aggregate.args.items() if k != 'this'):
            _error('unsupported_sql', 'AVG takes only an amount column')
        column = _amount_name(_plain_column(aggregate.this), contract, 'AVG')
        numerator = {'aggregate': 'sum', 'measure': column, 'minus': None, 'filter': None}
        denominator = {'aggregate': 'count', 'measure': column, 'minus': None, 'filter': None}
    else:
        if any(v for k, v in aggregate.args.items() if k not in {'this', 'expression', 'typed', 'safe'}):
            _error('unsupported_sql', 'Unsupported division options')
        numerator = _part(aggregate.this, contract, lower_leaf)
        denominator = _part(aggregate.expression, contract, lower_leaf)
    ir = {'mode': 'flat_snapshot', 'policy': 'flat-snapshot-v1', 'contract_hash': contract['contract_hash'],
          'table': 'snapshot', 'aggregate': 'ratio', 'aggregate_policy': RATIO_POLICY,
          'numerator': numerator, 'denominator': denominator,
          'predicate_policy': PREDICATE_POLICY,
          'predicate_tree': lower_boolean(where.this if where else None, lower_leaf)}
    try:
        _validate_trees(ir, check_leaf)
    except CoreError as error:
        raise CoreError('unsupported_sql', error.message) from error
    return {**ir, 'ir_hash': digest(ir)}


def _validate_trees(ir, check_leaf):
    leaves = 0
    for tree in (ir['predicate_tree'], ir['numerator']['filter'], ir['denominator']['filter']):
        leaves += validate_boolean(tree, check_leaf)[1]
    if leaves > MAX_RATIO_LEAVES:
        _error('invalid_ir', f'A ratio may use at most {MAX_RATIO_LEAVES} conditions in total')


def check_ratio_ir(ir, contract, check_leaf):
    if not _ratio_contract(contract):
        _error('invalid_ir', 'Ratio IR requires FlatContract v5 or v8')
    if not isinstance(ir, dict) or set(ir) != IR_FIELDS:
        _error('invalid_ir', 'Ratio IR shape is invalid')
    if (ir['mode'] != 'flat_snapshot' or ir['policy'] != 'flat-snapshot-v1' or
            ir['contract_hash'] != contract['contract_hash'] or ir['table'] != 'snapshot' or
            ir['aggregate'] != 'ratio' or ir['aggregate_policy'] != RATIO_POLICY or
            ir['predicate_policy'] != PREDICATE_POLICY):
        _error('invalid_ir', 'Ratio IR policy or contract differs')
    names = [c['name'] for c in amount_specs(contract)]
    allowed = [('count', '*')] + [(kind, name) for name in names for kind in ('sum', 'count')]
    for side in ('numerator', 'denominator'):
        part = ir[side]
        if not isinstance(part, dict) or set(part) != PART_FIELDS:
            _error('invalid_ir', 'Ratio part shape is invalid')
        if (part['aggregate'], part['measure']) not in allowed:
            _error('invalid_ir', 'Ratio part aggregate is invalid')
        if part['minus'] is not None and (
                contract['version'] != 8 or part['aggregate'] != 'sum' or part['minus'] not in names or
                part['minus'] == part['measure'] or
                _spec(contract, part['minus'])['unit_label'] != _spec(contract, part['measure'])['unit_label']):
            _error('invalid_ir', 'Ratio part subtraction is invalid')
        if part['minus'] is not None:
            _check_subtraction_capacity(contract, part['measure'], part['minus'])
    _validate_trees(ir, check_leaf)
    if digest({k: v for k, v in ir.items() if k != 'ir_hash'}) != ir['ir_hash']:
        _error('invalid_ir', 'Flat IR hash differs')
    return ir


def unit_family(part, contract=None) -> str:
    """'records' for counts; for sums the amount's unit, so INR / kg is never compared with INR / INR."""
    if part['aggregate'] != 'sum':
        return 'records'
    return 'amount' if contract is None else 'amount:' + _spec(contract, part['measure'])['unit_label']


def check_comparable(baseline, target, contract=None):
    if (baseline['numerator']['aggregate'], baseline['numerator']['measure'], baseline['numerator']['minus']) != \
            (target['numerator']['aggregate'], target['numerator']['measure'], target['numerator']['minus']):
        _error('different_ratio_shape', 'Both ratios must have the same numerator calculation (filters may differ)')
    if unit_family(baseline['denominator'], contract) != unit_family(target['denominator'], contract):
        _error('different_ratio_shape', 'Both ratios must divide by the same kind of total: '
               'both amounts in the same unit or both record counts')
    if (contract is not None and contract['version'] == 8 and baseline['denominator']['aggregate'] == 'sum' and
            any(baseline['denominator'][k] != target['denominator'][k] for k in ('aggregate', 'measure', 'minus'))):
        _error('different_ratio_shape', 'Multi-amount ratios must use the same denominator amount expression; its filters may differ')


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _input(part):
    """The columns a part reads per record (NULL for COUNT(*); the subtracted column, or NULL)."""
    return ('NULL' if part['measure'] == '*' else _quote(part['measure']),
            'NULL' if part['minus'] is None else _quote(part['minus']))


def build_query(ir, key, compile_leaf):
    """One row per record: shared filter, part filters and each part's input. No division."""
    shared, shared_params = compile_boolean(ir['predicate_tree'], compile_leaf)
    select, params = [f'{shared} AS included'], list(shared_params)
    for name in ('numerator', 'denominator'):
        part_filter = ir[name]['filter']
        if part_filter is None:
            select.append(f'{shared} AS in_{name}')
            params += shared_params
        else:
            sql, part_params = compile_boolean(part_filter, compile_leaf)
            select.append(f'({shared} AND {sql}) AS in_{name}')
            params += shared_params + part_params
    (num, num_minus), (den, den_minus) = _input(ir['numerator']), _input(ir['denominator'])
    sql = (f'SELECT {_quote(key)}, {", ".join(select)}, {num} AS numerator_input, {num_minus} AS numerator_minus, '
           f'{den} AS denominator_input, {den_minus} AS denominator_minus FROM snapshot ORDER BY {_quote(key)}')
    return sql, params


def check_query(ir, compile_leaf):
    """DuckDB's own totals for each part (never their quotient), as a cross-check."""
    shared, shared_params = compile_boolean(ir['predicate_tree'], compile_leaf)
    select, params = [], []
    for name in ('numerator', 'denominator'):
        part = ir[name]
        condition, condition_params = shared, list(shared_params)
        if part['filter'] is not None:
            sql, part_params = compile_boolean(part['filter'], compile_leaf)
            condition, condition_params = f'({shared} AND {sql})', condition_params + part_params
        summed = _quote(part['measure']) if part['minus'] is None else f'({_quote(part["measure"])} - {_quote(part["minus"])})'
        expression = ('SUM(' + summed + ')' if part['aggregate'] == 'sum' else
                      'COUNT(*)' if part['measure'] == '*' else 'COUNT(' + _quote(part['measure']) + ')')
        select.append(f'{expression} FILTER (WHERE {condition})')
        params += condition_params
    return f'SELECT {", ".join(select)} FROM snapshot', params


# ---------- endpoint values ----------
#
# Per-record arithmetic uses integer coefficients at fixed decimal scales (exact, and
# far faster than Fractions on hundreds of thousands of records). A value with
# coefficient c at scale s is c / 10**s.

def _coeff(raw, scale):
    """A database DECIMAL (or None) as an integer coefficient at `scale`; refuses any rounding."""
    if raw is None:
        return None
    # Decimal.scaleb/multiply obey the ambient context and can round a valid
    # 38-digit input before the integer check. Read the immutable tuple instead.
    if not raw.is_finite():
        raise CoreError('arithmetic_overflow', 'A ratio amount must be finite')
    sign, digits, exponent = raw.as_tuple()
    value = 0
    for digit in digits:
        value = value * 10 + digit
    shift = exponent + scale
    if shift >= 0:
        value *= 10 ** shift
        remainder = 0
    else:
        value, remainder = divmod(value, 10 ** -shift)
    if remainder:
        raise CoreError('arithmetic_overflow', 'An amount has more decimal places than its declared scale')
    return -value if sign else value


def _reads_amount(part) -> bool:
    return part['measure'] != '*'


def _input_coeff(raw, minus_raw, part, contract):
    """A part's per-record input at its input scale: the column, or column minus column (NULL if either is NULL)."""
    if part['measure'] == '*':
        return None
    scale = input_scale(part, contract)
    if raw is None or (part['minus'] is not None and minus_raw is None):
        return None
    value = _coeff(raw, scale)
    return value - _coeff(minus_raw, scale) if part['minus'] is not None else value


def part_coeff(part, inside, value):
    """A record's term in one part, as a coefficient at the part's scale."""
    if not inside:
        return 0
    if part['aggregate'] == 'sum':
        return value if value is not None else 0
    if part['measure'] == '*':
        return 1
    return 1 if value is not None else 0


class _Endpoint:
    """Per-record values and running exact totals for one side; part scales computed once."""

    def __init__(self, ir, contract):
        self.parts = [(name, ir[name], input_scale(ir[name], contract)) for name in ('numerator', 'denominator')]
        self.totals = {'numerator': 0, 'denominator': 0}
        self.seen = {'numerator': False, 'denominator': False}  # a SUM part is NULL until a non-NULL term matches
        self.contract = contract

    def record(self, row):
        """row: (key, included, in_numerator, in_denominator, numerator_input, numerator_minus,
        denominator_input, denominator_minus) -> (included, in_n, in_d, n, d, null_term, n_input, d_input)."""
        _, included, in_num, in_den, num_raw, num_minus, den_raw, den_minus = row
        raws = {'numerator': (num_raw, num_minus, in_num is True), 'denominator': (den_raw, den_minus, in_den is True)}
        values, inputs, null_term = {}, {}, False
        for name, part, scale in self.parts:
            raw, minus_raw, inside = raws[name]
            if part['measure'] == '*' or raw is None or (part['minus'] is not None and minus_raw is None):
                value = None
            else:
                value = _coeff(raw, scale)
                if part['minus'] is not None:
                    value -= _coeff(minus_raw, scale)
            inputs[name] = value
            values[name] = part_coeff(part, inside, value)
            self.totals[name] += values[name]
            if inside and (part['aggregate'] != 'sum' or value is not None):
                self.seen[name] = True
            null_term = null_term or (inside and part['measure'] != '*' and value is None)
        return (included is True, raws['numerator'][2], raws['denominator'][2], values['numerator'],
                values['denominator'], null_term, inputs['numerator'], inputs['denominator'])

    def result(self):
        return {name: (Fraction(self.totals[name], 10 ** part_scale(part, self.contract))
                       if part['aggregate'] != 'sum' or self.seen[name] else None)
                for name, part, _ in self.parts}


def endpoint(rows, ir, contract):
    """rows: (key, included, in_numerator, in_denominator, numerator_input, numerator_minus,
    denominator_input, denominator_minus). Returns (totals as Fractions or None, per-record values)."""
    from .disk_evidence import DiskRows
    disk = isinstance(rows, DiskRows)
    records = DiskRows(budget=rows.budget) if disk else {}
    side = _Endpoint(ir, contract)
    for row in rows:
        record = side.record(row)
        if disk:
            records.append([row[0], record])
        else:
            records[row[0]] = record
    return side.result(), records


def undefined_reason(numerator, denominator):
    if denominator is None:
        return 'null_denominator'
    if denominator == 0:
        return 'zero_denominator'
    if numerator is None:
        return 'null_numerator'
    return None


def input_scale(part, contract) -> int:
    """Scale of a part's per-record input (the amount column, or the difference of two)."""
    if part['measure'] == '*':
        return 0
    scale = _spec(contract, part['measure'])['scale']
    return max(scale, _spec(contract, part['minus'])['scale']) if part['minus'] is not None else scale


def input_scales(ir, contract):
    return {name: input_scale(ir[name], contract) for name in ('numerator', 'denominator')}


def part_scale(part, contract) -> int:
    return input_scale(part, contract) if part['aggregate'] == 'sum' else 0


def display_places(contract) -> int:
    return min(contract['scale'] + 4, MAX_DISPLAY_PLACES)


def two_part_shapley(n0, n1, d0, d1):
    """Exact numerator and denominator effects; they add up to n1/d1 - n0/d0."""
    corner = {'n0d0': n0 / d0, 'n1d0': n1 / d0, 'n0d1': n0 / d1, 'n1d1': n1 / d1}
    numerator_effect = ((corner['n1d0'] - corner['n0d0']) + (corner['n1d1'] - corner['n0d1'])) / 2
    denominator_effect = ((corner['n0d1'] - corner['n0d0']) + (corner['n1d1'] - corner['n1d0'])) / 2
    if numerator_effect + denominator_effect != corner['n1d1'] - corner['n0d0']:
        raise CoreError('reconciliation_failure', 'Ratio effects do not add up to the change')
    return numerator_effect, denominator_effect, corner


def scales(baseline_ir, target_ir, contract):
    s_n = part_scale(baseline_ir['numerator'], contract)
    s_d = max(part_scale(baseline_ir['denominator'], contract), part_scale(target_ir['denominator'], contract))
    return s_n, s_d, s_n + s_d


def _unit(baseline_ir, contract):
    top, bottom = unit_family(baseline_ir['numerator'], contract), unit_family(baseline_ir['denominator'], contract)
    if top == bottom:
        return 'share'
    if top == 'records':
        return 'records_per_amount'
    return 'amount_per_record' if bottom == 'records' else 'amount_per_amount'


def _part_label(part, contract):
    return {k: part[k] for k in ('aggregate', 'measure', 'minus')} | (
        {'unit_label': _spec(contract, part['measure'])['unit_label']} if part['aggregate'] == 'sum' else {})


def summarize(n0, d0, n1, d1, baseline_ir, target_ir, contract):
    """The ratio block and record weights, derived only from the four exact totals.

    Returns (ratio, r0, r1, weight_n, weight_d); a record's share is
    dn * weight_n - dd * weight_d. The evidence verifier calls this too.
    """
    places = display_places(contract)
    s_n, s_d, s_g = scales(baseline_ir, target_ir, contract)
    reasons = {'baseline': undefined_reason(n0, d0), 'target': undefined_reason(n1, d1)}
    r0 = None if reasons['baseline'] else n0 / d0
    r1 = None if reasons['target'] else n1 / d1
    defined = r0 is not None and r1 is not None
    weight_n, weight_d = Fraction(0), Fraction(0)

    ratio = {
        'policy': RATIO_POLICY,
        'numerator': _part_label(baseline_ir['numerator'], contract),
        'denominator': _part_label(baseline_ir['denominator'], contract),
        'display_places': places, 'numerator_scale': s_n, 'denominator_scale': s_d, 'share_scale': s_g,
        'baseline': _side(n0, d0, r0, reasons['baseline'], s_n, s_d, places),
        'target': _side(n1, d1, r1, reasons['target'], s_n, s_d, places),
        'numerator_change': fixed(n1 - n0, s_n) if n0 is not None and n1 is not None else None,
        'denominator_change': fixed(d1 - d0, s_d) if d0 is not None and d1 is not None else None,
        'status': 'defined' if defined else 'components_only',
        'sigma': None, 'scale_divisor': None, 'scaled_gap': None, 'gap_exact': None,
        'effects': [], 'corners': None, 'pooled_average': None,
        'unit': _unit(baseline_ir, contract),
    }
    if defined:
        sigma = 1 if d0 * d1 > 0 else -1
        q = 2 * abs(d0 * d1)
        g = 2 * sigma * (n1 * d0 - n0 * d1)
        numerator_effect, denominator_effect, corners = two_part_shapley(n0, n1, d0, d1)
        if g / q != r1 - r0:
            raise CoreError('reconciliation_failure', 'Ratio change does not reconcile')
        ratio.update(sigma=str(sigma), scale_divisor=fixed(q, 2 * s_d), scaled_gap=fixed(g, s_g),
                     gap_exact=fraction_text(r1 - r0),
                     effects=[{'rule': 'numerator_effect', 'exact': fraction_text(numerator_effect),
                               'display': display(numerator_effect, places)},
                              {'rule': 'denominator_effect', 'exact': fraction_text(denominator_effect),
                               'display': display(denominator_effect, places)}],
                     corners={k: fraction_text(v) for k, v in corners.items()},
                     pooled_average=fraction_text((n0 + n1) / (d0 + d1)) if d0 + d1 != 0 else None)
        weight_n, weight_d = sigma * (d0 + d1), sigma * (n0 + n1)
    return ratio, r0, r1, weight_n, weight_d


def _coefficient_of(value: Fraction | None, scale: int) -> int:
    scaled = value * 10 ** scale
    if scaled.denominator != 1:
        raise CoreError('arithmetic_overflow', 'A ratio total is not exact at its declared scale')
    return scaled.numerator


def weights(ratio, n0, d0, n1, d1, s_n, s_d):
    """Integer record weights: share = dn * weight_n - dd * weight_d, at scale s_n + s_d."""
    if ratio['status'] != 'defined':
        return 0, 0
    sigma = int(ratio['sigma'])
    return (sigma * (_coefficient_of(d0, s_d) + _coefficient_of(d1, s_d)),
            sigma * (_coefficient_of(n0, s_n) + _coefficient_of(n1, s_n)))


def compare_endpoints(baseline_ir, target_ir, contract, baseline_rows, target_rows):
    """Exact ratio comparison from per-record query rows. Returns (ratio block, evidence rows, endpoints)."""
    (b_tot, b_rec) = endpoint(baseline_rows, baseline_ir, contract)
    (t_tot, t_rec) = endpoint(target_rows, target_ir, contract)
    n0, d0, n1, d1 = b_tot['numerator'], b_tot['denominator'], t_tot['numerator'], t_tot['denominator']
    ratio, r0, r1, _, _ = summarize(n0, d0, n1, d1, baseline_ir, target_ir, contract)
    s_n, s_d, s_g = scales(baseline_ir, target_ir, contract)
    weight_n, weight_d = weights(ratio, n0, d0, n1, d1, s_n, s_d)
    # Denominators of the two sides may use different amount columns (and scales); align to s_d.
    align_b = 10 ** (s_d - part_scale(baseline_ir['denominator'], contract))
    align_t = 10 ** (s_d - part_scale(target_ir['denominator'], contract))
    in_b = input_scales(baseline_ir, contract)
    in_t = input_scales(target_ir, contract)
    from .disk_evidence import DiskRows
    disk = isinstance(b_rec, DiskRows)
    evidence, total = (DiskRows(budget=b_rec.budget) if disk else []), 0
    if disk:
        def paired():
            from itertools import zip_longest
            for b, t in zip_longest(b_rec, t_rec):
                if b is None or t is None or b[0] != t[0]:
                    raise CoreError('reconciliation_failure', 'Ratio endpoints must read the same ordered snapshot')
                yield b[0], b[1], t[1]
        pairs = paired()
    else:
        pairs = ((key, b_rec[key], t_rec[key]) for key in sorted(b_rec.keys() | t_rec.keys()))
    row = _row_builder(weight_n, weight_d, align_b, align_t, in_b, in_t, s_n, s_d, s_g)
    for key, b_record, t_record in pairs:
        item, share = row(key, b_record, t_record)
        total += share
        evidence.append(item)
    if ratio['status'] == 'defined' and Fraction(total, 10 ** s_g) != to_fraction(ratio['scaled_gap']):
        raise CoreError('reconciliation_failure', 'Ratio record shares do not reconcile')
    return ratio, evidence, (r0, r1)


def _row_builder(weight_n, weight_d, align_b, align_t, in_b, in_t, s_n, s_d, s_g):
    text = lambda coeff, scale: None if coeff is None else ScaledInteger(coeff, scale).to_fixed_string()

    def row(key, b_record, t_record):
        bi, b_in_n, b_in_d, bn, bd, b_null, b_ni, b_di = b_record
        ti, t_in_n, t_in_d, tn, td, t_null, t_ni, t_di = t_record
        bd, td = bd * align_b, td * align_t
        share = (tn - bn) * weight_n - (td - bd) * weight_d
        membership = ('included_in_both' if bi and ti else 'baseline_only' if bi else 'target_only' if ti else 'included_in_neither')
        return {
            'order_id': key, 'baseline_included': bi, 'target_included': ti, 'membership': membership,
            'baseline_numerator_input': text(b_ni, in_b['numerator']), 'target_numerator_input': text(t_ni, in_t['numerator']),
            'baseline_denominator_input': text(b_di, in_b['denominator']), 'target_denominator_input': text(t_di, in_t['denominator']),
            'baseline_in_numerator': b_in_n, 'target_in_numerator': t_in_n,
            'baseline_in_denominator': b_in_d, 'target_in_denominator': t_in_d,
            'baseline_numerator': text(bn, s_n), 'target_numerator': text(tn, s_n),
            'baseline_denominator': text(bd, s_d), 'target_denominator': text(td, s_d),
            'baseline_null_term': b_null, 'target_null_term': t_null,
            'delta': text(share, s_g)}, share
    return row


def compare_streaming(con, baseline_ir, target_ir, contract, leaf, key):
    """Large snapshots: one ordered pass over both calculations' rows.

    Exact totals are accumulated over every record, but only records whose inclusion or
    contribution differs are kept: every other record has the same numerator and
    denominator terms on both sides, so its share (dn * weight_n - dd * weight_d) is
    exactly 0. Returns (ratio, evidence, (r0, r1), totals per side, coverage, query hashes).
    """
    from .disk_evidence import DiskRows
    sides = [_Endpoint(baseline_ir, contract), _Endpoint(target_ir, contract)]
    s_n, s_d, s_g = scales(baseline_ir, target_ir, contract)
    align_b = 10 ** (s_d - part_scale(baseline_ir['denominator'], contract))
    align_t = 10 ** (s_d - part_scale(target_ir['denominator'], contract))
    queries = [build_query(ir, key, leaf) for ir in (baseline_ir, target_ir)]
    cursors = [con.cursor().execute(sql, params) for sql, params in queries]
    changed = DiskRows()
    total = both = neither = 0
    try:
        while True:
            b_batch, t_batch = cursors[0].fetchmany(10_000), cursors[1].fetchmany(10_000)
            if len(b_batch) != len(t_batch):
                raise CoreError('reconciliation_failure', 'Ratio endpoints must read the same ordered snapshot')
            if not b_batch:
                break
            for b_row, t_row in zip(b_batch, t_batch):
                if b_row[0] != t_row[0]:
                    raise CoreError('reconciliation_failure', 'Ratio endpoints must read the same ordered snapshot')
                b, t = sides[0].record(b_row), sides[1].record(t_row)
                total += 1
                if b[0] == t[0] and b[3] == t[3] and b[4] * align_b == t[4] * align_t:
                    both += b[0]
                    neither += not b[0]
                else:
                    changed.append([b_row[0], b, t])
    finally:
        for cursor in cursors:
            cursor.close()
    b_tot, t_tot = sides[0].result(), sides[1].result()
    n0, d0, n1, d1 = b_tot['numerator'], b_tot['denominator'], t_tot['numerator'], t_tot['denominator']
    ratio, r0, r1, _, _ = summarize(n0, d0, n1, d1, baseline_ir, target_ir, contract)
    weight_n, weight_d = weights(ratio, n0, d0, n1, d1, s_n, s_d)
    row = _row_builder(weight_n, weight_d, align_b, align_t, input_scales(baseline_ir, contract),
                       input_scales(target_ir, contract), s_n, s_d, s_g)
    evidence, share_total = DiskRows(budget=changed.budget), 0
    for item_key, b, t in changed:
        item, share = row(item_key, tuple(b), tuple(t))
        share_total += share
        evidence.append(item)
    changed.close()
    if ratio['status'] == 'defined' and Fraction(share_total, 10 ** s_g) != to_fraction(ratio['scaled_gap']):
        raise CoreError('reconciliation_failure', 'Ratio record shares do not reconcile')
    coverage = {'mode': 'changed_records', 'total_records': total, 'listed_records': len(evidence),
                'unchanged_records': both + neither, 'unchanged_included_in_both': both,
                'unchanged_included_in_neither': neither}
    return ratio, evidence, (r0, r1), (b_tot, t_tot), coverage, [digest({'sql': s, 'params': p}) for s, p in queries]


def _side(n, d, r, reason, s_n, s_d, places):
    return {'numerator': None if n is None else fixed(n, s_n), 'denominator': None if d is None else fixed(d, s_d),
            'exact': fraction_text(r), 'display': display(r, places), 'undefined_reason': reason}


def result_fields(ratio, r0, r1):
    """Top-level display values: rounded endpoints, and their exact difference as the shown change."""
    places = ratio['display_places']
    b, t = display(r0, places), display(r1, places)
    delta = None
    if b is not None and t is not None:
        delta = (ScaledInteger.parse(t) - ScaledInteger.parse(b)).to_fixed_string()
    return b, t, delta
