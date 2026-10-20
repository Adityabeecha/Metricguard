import { useEffect, useRef, useState } from 'react';
import { useConnectedIdentity } from './identity';
import { recordedByDefault } from './comparisonSelection';

export function useRecordedBy(workspaceId: string): [string, (value: string) => void] {
  const { identity } = useConnectedIdentity(workspaceId);
  const [actor, setValue] = useState(() => { try { return localStorage.getItem('mg.actor') || ''; } catch { return ''; } });
  const edited = useRef(false);
  useEffect(() => { setValue(current => recordedByDefault(current, identity, edited.current)); }, [identity]);
  return [actor, value => { edited.current = true; setValue(value); }];
}
