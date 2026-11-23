// Backend-Host wird aus der aufgerufenen Adresse abgeleitet, damit es sowohl unter
// localhost als auch über eine LAN-IP (Zugriff vom Tablet / anderen Rechner) funktioniert.
// Überschreibbar über window.__BACKEND__ (z. B. per index.html-Snippet oder Env-Injection).

const host = (window as any).__BACKEND_HOST__ || `${location.hostname}:8080`;
const secure = location.protocol === 'https:';

export const BACKEND = {
  http: secure ? `${location.origin}/api` : `http://${host}`,
  ws: secure ? `wss://${location.host}/api` : `ws://${host}`,
};
