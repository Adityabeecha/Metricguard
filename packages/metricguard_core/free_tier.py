"""Optional deployment admission limits, independent of pinned execution policies."""
import os

from .common import CoreError


class FreeTierLimit(CoreError):
    status = 413


def configured_limit(full_env_name: str) -> int | None:
    value = os.environ.get(full_env_name, '').strip()
    if not value:
        return None
    try:
        limit = int(value)
    except ValueError as error:
        raise ValueError(f'{full_env_name} must be a positive integer') from error
    if limit <= 0:
        raise ValueError(f'{full_env_name} must be a positive integer')
    return limit


def check_rows(row_count: int, *, exact: bool = True) -> None:
    limit = configured_limit('METRICGUARD_MAX_ROWS')
    if limit is not None and row_count > limit:
        observed = f'{row_count:,}' if exact else f'more than {limit:,}'
        raise FreeTierLimit('FREE_TIER_ROWS',
                            f'Free beta limit: {limit:,} rows per dataset (this file has {observed}).')


def check_upload_size(byte_count: int) -> None:
    limit = configured_limit('METRICGUARD_MAX_UPLOAD_MB')
    if limit is not None and byte_count > limit * 1024 * 1024:
        raise FreeTierLimit('FREE_TIER_UPLOAD',
                            f'Free beta limit: {limit:,} MB per upload (this file has {byte_count:,} bytes).')
