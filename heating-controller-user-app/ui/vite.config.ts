import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

const backend = process.env.HEATING_BACKEND_URL || 'http://127.0.0.1:5055';
// Preserve Host and Origin together. Flask can validate same-origin requests
// without disabling its origin checks or restarting the existing backend.
const proxy = {
  '/api': { target: backend, changeOrigin: false },
  '/ws': { target: backend, changeOrigin: false, ws: true },
};
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], restoreMocks: true },
});
