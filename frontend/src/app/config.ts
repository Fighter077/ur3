// Backend-Host wird aus der aufgerufenen Adresse abgeleitet, damit es sowohl unter
// localhost als auch über eine LAN-IP (Zugriff vom Tablet / anderen Rechner) funktioniert.
// Überschreibbar über window.__BACKEND__ (z. B. per index.html-Snippet oder Env-Injection).

const host = (window as any).__BACKEND_HOST__ || `${location.hostname}:8080`;

export const BACKEND = {
  http: `http://${host}`,
  ws: `ws://${host}`,
};
