import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// fs.allow: the /guide page bundles ../../docs/user-guide.md, outside this app's folder.
export default defineConfig({ plugins: [react()], server: { proxy: { '/api': 'http://127.0.0.1:8001' }, fs: { allow: ['.', '../../docs'] } } });
