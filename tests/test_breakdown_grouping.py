"""How the change breakdown groups records: by day for timestamps, and "no match" for missing lookups."""
from services.api import evidence_cache as ec


def test_timestamps_group_by_calendar_day():
    context = {'R1': {'started': '2024-01-05T09:15:00'}, 'R2': {'started': '2024-01-05T23:45:00.000'}}
    row = lambda key: {'row_key': key}
    assert ec._group_value(row('R1'), context, 'started', 'day') == '2024-01-05'
    assert ec._group_value(row('R2'), context, 'started', 'day') == '2024-01-05'
    assert ec._group_value(row('R1'), context, 'started', None) == '2024-01-05T09:15:00'


def test_unmatched_lookup_rows_are_not_blank_values():
    unmatched = {'fact_key': 'O1', 'fact_values': {'status': 'paid'}, 'dimension_values': {'customer': {'segment': None}},
                 'links': {'customer': {'foreign_key': 'C9', 'dimension_key': None, 'matched': False}}}
    matched_blank = {'fact_key': 'O2', 'fact_values': {'status': 'paid'}, 'dimension_values': {'customer': {'segment': None}},
                     'links': {'customer': {'foreign_key': 'C1', 'dimension_key': 'C1', 'matched': True}}}
    assert ec.row_values(unmatched, None)['customer.segment'] == ec.NO_MATCH
    assert ec.row_values(unmatched, None)['link.customer'] == 'C9'
    assert ec.row_values(matched_blank, None)['customer.segment'] is None
    assert ec._public_group({'value': ec.NO_MATCH, 'change': '1'}) == {'value': None, 'no_match': True, 'change': '1'}


def test_one_lookup_format_exposes_its_link_and_no_match():
    row = {'fact_key': 'O1', 'foreign_key': 'S7', 'dimension_matched': False, 'fact_values': {}, 'dimension_values': {'region': None}}
    values = ec.row_values(row, None)
    assert values['link.dimension'] == 'S7' and values['dimension.region'] == ec.NO_MATCH
