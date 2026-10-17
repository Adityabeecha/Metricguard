"""Shared immutable schema and versioned normalization policy."""
import hashlib
import json

POLICY = 'metricguard-ir-v1'
SCHEMA = {'order_id':'VARCHAR','customer_id':'VARCHAR','order_date':'DATE','payment_date':'DATE','status':'VARCHAR','payment_status':'VARCHAR','order_amount':'DECIMAL(18,2)','refund_amount':'DECIMAL(18,2)','currency':'VARCHAR'}
NUMERIC = {'order_amount','refund_amount'}
DATES = {'order_date','payment_date'}

# V6 safe SQL expansion columns
V6_COLUMNS = {
    'deleted_at': 'DATE',
    'amount': 'DECIMAL(18,2)',
    'units': 'DECIMAL(18,2)',
    'is_active': 'BOOLEAN',
}
ALL_COLUMNS = {**SCHEMA, **V6_COLUMNS}
ALL_NUMERIC = NUMERIC | {'amount', 'units'}
ALL_DATES = DATES | {'deleted_at'}
ALL_BOOLEANS = {'is_active'}

class CoreError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()
