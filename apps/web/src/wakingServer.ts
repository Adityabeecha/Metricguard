/** Track slow GET requests independently, including overlapping requests. */
export function createWakingTracker(notify: (visible: boolean) => void, schedule = setTimeout, cancel = clearTimeout) {
  let slow = 0;
  return () => {
    let active = true, visible = false;
    const timer = schedule(() => { if (active) { visible = true; slow++; if (slow === 1) notify(true); } }, 3000);
    return () => { if (!active) return; active = false; cancel(timer); if (visible && --slow === 0) notify(false); };
  };
}
const listeners = new Set<(visible: boolean) => void>();
let visible = false;
export const trackGet = createWakingTracker(value => { visible = value; listeners.forEach(listener => listener(value)); });
export function onWakingChange(listener: (visible: boolean) => void) { listeners.add(listener); listener(visible); return () => { listeners.delete(listener); }; }
