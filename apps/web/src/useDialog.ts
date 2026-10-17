import { useEffect, useRef } from 'react';

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])', 'textarea:not([disabled])', 'summary', '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter(el => !el.hasAttribute('inert') && el.getClientRects().length > 0);
}

/**
 * Modal dialog keyboard contract: when `open` becomes true, focus moves into the
 * dialog (autofocus target, else first control, else the dialog itself); Tab and
 * Shift+Tab cycle inside it; Escape calls `onClose` unless `canClose` is false;
 * on close, focus returns to the element that was focused when it opened.
 */
export function useDialog<T extends HTMLElement = HTMLDivElement>(
  open: boolean,
  onClose: () => void,
  canClose: boolean = true,
) {
  const ref = useRef<T>(null);
  const closeRef = useRef({ onClose, canClose });
  closeRef.current = { onClose, canClose };

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = ref.current;
    if (root) {
      root.setAttribute('data-dialog-managed', '');
      if (!root.hasAttribute('tabindex')) root.setAttribute('tabindex', '-1');
      const preferred = root.querySelector<HTMLElement>('[autofocus],[data-autofocus]');
      (preferred || focusables(root)[0] || root).focus();
    }

    const onKeyDown = (event: KeyboardEvent) => {
      const dialog = ref.current;
      if (!dialog) return;
      if (event.key === 'Escape') {
        if (closeRef.current.canClose) {
          event.stopPropagation();
          closeRef.current.onClose();
        }
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusables(dialog);
      if (items.length === 0) { event.preventDefault(); dialog.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (active === last || !dialog.contains(active))) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [open]);

  return ref;
}

/**
 * Same keyboard contract for every other `[aria-modal="true"]` dialog in the app,
 * without changing each view: focus moves in when one appears, Tab stays inside the
 * top-most dialog, Escape activates its close control, and focus returns to the
 * control that opened it when it disappears. Dialogs using `useDialog` opt out.
 */
export function installGlobalDialogSupport() {
  if (typeof document === 'undefined' || (window as { __mgDialogs?: boolean }).__mgDialogs) return;
  (window as { __mgDialogs?: boolean }).__mgDialogs = true;
  const SELECTOR = '[aria-modal="true"]:not([data-dialog-managed])';
  const stack: Array<{ el: HTMLElement; opener: HTMLElement | null }> = [];
  let lastOutside: HTMLElement | null = null;

  document.addEventListener('focusin', event => {
    const target = event.target as HTMLElement | null;
    if (target && !target.closest('[aria-modal="true"]')) lastOutside = target;
  });

  const sync = () => {
    for (let i = stack.length - 1; i >= 0; i--) {
      if (!document.contains(stack[i].el)) {
        const [{ opener }] = stack.splice(i, 1);
        const active = document.activeElement;
        const focusLost = !active || active === document.body || !document.contains(active);
        if (opener && document.contains(opener) && focusLost) opener.focus();
      }
    }
    document.querySelectorAll<HTMLElement>(SELECTOR).forEach(el => {
      if (stack.some(s => s.el === el)) return;
      stack.push({ el, opener: lastOutside });
      if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
      if (!el.contains(document.activeElement)) {
        const preferred = el.querySelector<HTMLElement>('[autofocus],[data-autofocus]');
        (preferred || focusables(el).find(f => !f.classList.contains('modal-close-button')) || focusables(el)[0] || el).focus();
      }
    });
  };
  new MutationObserver(sync).observe(document.body, { childList: true, subtree: true });

  document.addEventListener('keydown', event => {
    const top = stack[stack.length - 1]?.el;
    if (!top || !document.contains(top)) return;
    if (event.key === 'Escape') {
      const close = top.querySelector<HTMLButtonElement>('[data-dialog-close], .modal-close-button, button[aria-label^="Close"]');
      if (close && !close.disabled) { event.preventDefault(); close.click(); }
      return;
    }
    if (event.key !== 'Tab') return;
    const items = focusables(top);
    if (items.length === 0) { event.preventDefault(); top.focus(); return; }
    const first = items[0], last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !top.contains(active))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (active === last || !top.contains(active))) { event.preventDefault(); first.focus(); }
  }, true);
}
