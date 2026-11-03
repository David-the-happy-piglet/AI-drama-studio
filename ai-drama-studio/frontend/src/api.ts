import type { AssetUpload } from "./types";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData
      ? init.headers
      : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json() as { detail?: unknown };
      message = typeof payload.detail === "string"
        ? payload.detail
        : JSON.stringify(payload.detail ?? payload);
    } catch {
      // Keep the HTTP status message when the body is not JSON.
    }
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export function postJson<T>(path: string, payload: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(payload) });
}

export async function uploadAsset(
  projectId: string,
  kind: string,
  file: File,
): Promise<AssetUpload> {
  const form = new FormData();
  form.append("asset_kind", kind);
  form.append("file", file);
  return api<AssetUpload>(`/projects/${projectId}/assets`, {
    method: "POST",
    body: form,
  });
}

export function mediaUrl(path: string | null): string | null {
  if (!path) return null;
  if (/^https?:\/\//.test(path) || path.startsWith("/media/")) return path;
  return path.startsWith("assets/") ? `/media/${path.slice(7)}` : null;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误";
}
