import axios from "axios";
import localCatalog from "./data/catalog.json";
import type { Catalog, LessonInput, PrepResult, RunJob, VersionItem, VersionDetail } from "./types";

const client = axios.create({
  baseURL: "/api",
  timeout: 60000,
});

export async function fetchHealth() {
  const { data } = await client.get("/health");
  return data as { ok: boolean; mock_llm: boolean; model: string };
}

export async function fetchCatalog(): Promise<Catalog> {
  try {
    const { data } = await client.get<Catalog>("/catalog");
    if (data?.stages?.length) return data;
  } catch {
    // 后端未启动或旧进程无 /catalog 时，使用本地目录兜底
  }
  return localCatalog as Catalog;
}

export async function createRun(payload: LessonInput) {
  const { data } = await client.post<RunJob>("/runs", payload);
  return data;
}

export async function getRun(runId: string) {
  const { data } = await client.get<RunJob>(`/runs/${runId}`);
  return data;
}

export async function updateRunResult(runId: string, result: PrepResult) {
  const { data } = await client.put<RunJob>(`/runs/${runId}/result`, { result });
  return data;
}

export async function listVersions() {
  const { data } = await client.get<{ items: VersionItem[] }>("/versions");
  return data.items;
}

export async function saveVersion(payload: {
  input: LessonInput | Record<string, unknown>;
  result: PrepResult;
  note?: string;
  title?: string;
}) {
  const { data } = await client.post<VersionDetail>("/versions", payload);
  return data;
}

export async function getVersion(versionId: string) {
  const { data } = await client.get<VersionDetail>(`/versions/${versionId}`);
  return data;
}

export async function exportPrep(payload: {
  format: "docx" | "pdf" | "pptx";
  module?: "all" | "curriculum" | "plan" | "exercises" | "slides" | "board";
  input: LessonInput | Record<string, unknown>;
  result: PrepResult;
}) {
  const { data, headers } = await client.post(
    "/export",
    { module: "all", ...payload },
    {
      responseType: "blob",
    },
  );
  const disposition = String(headers["content-disposition"] || "");
  const match = disposition.match(/filename="?([^"]+)"?/i);
  const fallback = `lesson.${payload.format}`;
  const filename = match?.[1] || fallback;
  const url = window.URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
