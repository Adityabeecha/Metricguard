import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { installGlobalDialogSupport } from './useDialog';
import { applyTheme, readThemeChoice, watchSystemTheme } from './themeChoice';
import './style.css';
import './closeout.css';
import './theme.css';
import './seal.css';
import './evidence.css';

applyTheme(readThemeChoice());
watchSystemTheme(readThemeChoice);
installGlobalDialogSupport();

const rootElement = document.getElementById('root');
if (rootElement) {
  createRoot(rootElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
