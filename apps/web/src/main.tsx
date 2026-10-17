import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { installGlobalDialogSupport } from './useDialog';
import './style.css';
import './closeout.css';

installGlobalDialogSupport();

const rootElement = document.getElementById('root');
if (rootElement) {
  createRoot(rootElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
