"""Guest accounts for open sign-up: short-lived, with a small storage quota.

Guest mode is on when METRICGUARD_FULL_ACCESS_EMAILS (or the older METRICGUARD_INVITE_EMAILS)
lists at least one email. A new account whose verified email is not on that list becomes a
guest: everything it owns is erased METRICGUARD_GUEST_HOURS (default 8) after sign-up, and its
workspace may hold METRICGUARD_GUEST_STORAGE_MB (default 25) in total. Accounts on the list, and
every account created before guest mode, keep full access and are never erased.
"""
from __future__ import annotations

import os
import sys
import threading
import time

SWEEP_SECONDS = 300


def full_access_emails() -> set[str]:
    raw = os.environ.get('METRICGUARD_FULL_ACCESS_EMAILS') or os.environ.get('METRICGUARD_INVITE_EMAILS', '')
    return {e.strip().casefold() for e in raw.split(',') if e.strip()}


def _positive(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, '').strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def guest_terms(email: str) -> dict | None:
    """None for full access; otherwise the guest lifetime and storage quota."""
    allowed = full_access_emails()
    if not allowed or email.strip().casefold() in allowed:
        return None
    return {'seconds': _positive('METRICGUARD_GUEST_HOURS', 8) * 3600,
            'storage_bytes': _positive('METRICGUARD_GUEST_STORAGE_MB', 25) * 1024 * 1024}


_last = {'at': None, 'erased': 0, 'waiting': 0}


def last_sweep() -> dict:
    """The latest sweep's time (Unix seconds) and counts, for the health check; no user ids."""
    return dict(_last)


def sweep(store, data_dir) -> list[str]:
    """Erase expired guest accounts; a busy one (job still running) is retried next sweep."""
    from .account_deletion import delete_account
    erased, waiting = [], 0
    allowed = full_access_emails()
    for user_id in store.expired_guests():
        try:
            # Added to the full-access list after sign-up: promote instead of erasing.
            if allowed and store.get_user(user_id)['email'].strip().casefold() in allowed:
                store.promote_guest(user_id)
                continue
            hashes = store.guest_identity_hashes(user_id)  # read before the identities are deleted
            delete_account(store, user_id, data_dir)
            erased.append(user_id)
            store.remember_guest_erasure(hashes)
        except Exception as exc:  # retried on the next sweep
            waiting += 1
            print(f'guests: could not erase {user_id} yet: {getattr(exc, "code", type(exc).__name__)}', file=sys.stderr, flush=True)
    _last.update(at=time.time(), erased=len(erased), waiting=waiting)
    if erased:
        print(f'guests: erased {len(erased)} expired account{"s" if len(erased) != 1 else ""}', file=sys.stderr, flush=True)
    return erased


def start_sweeper(store, data_dir) -> threading.Event:
    stop = threading.Event()

    def loop():
        while not stop.wait(SWEEP_SECONDS):
            try:
                sweep(store, data_dir)
            except Exception as exc:
                print(f'guests: sweep failed: {type(exc).__name__}', file=sys.stderr, flush=True)

    threading.Thread(target=loop, name='guest-sweeper', daemon=True).start()
    return stop
