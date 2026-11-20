// Base URL of the backend. Empty string means same origin: in development
// Vite proxies /api to the FastAPI server (see vite.config.ts).
export const API_BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '').replace(/\/$/, '');

export type ApiErrorBody = { code: string; message: string; node_id?: string | null; field?: string | null };

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...rest,
      headers: json !== undefined ? { 'Content-Type': 'application/json', ...headers } : headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    throw new ApiError(0, { code: 'NETWORK_ERROR', message: 'Бэкенд недоступен. Проверьте, что API запущен.' });
  }
  if (!response.ok) {
    let body: ApiErrorBody = { code: `HTTP_${response.status}`, message: `Ошибка API: ${response.status}` };
    try {
      const payload = await response.json();
      if (payload?.error?.message) body = payload.error;
    } catch { /* not JSON */ }
    throw new ApiError(response.status, body);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
