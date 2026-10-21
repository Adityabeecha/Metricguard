"""Keep internal validation vocabulary out of analyst-facing refusals.

Codes remain stable for callers. Historical messages arriving from saved jobs also
pass this boundary; calculation structures and evidence hashes are never changed.
"""
import re


def plain_error_message(message):
    text = str(message)
    if re.search(r'compound filters.*contract|ratios?.*contract v5', text, re.I):
        return "This dataset was added before averages were supported. Use 'Enable averages and ratios' on the dataset page."
    if re.search(r'\bIR\b|FlatContract|\bcontract v\d|execution policy|flat[-_]snapshot policy', text, re.I):
        if re.search(r'hash|fingerprint|integrity|validation|invalid|shape', text, re.I):
            return 'The saved calculation could not be verified. Add the SQL again for the chosen dataset and try again.'
        return 'These calculations and data were added with different supported features. Choose SQL linked to the same dataset, or add the SQL again for that dataset.'
    return text
