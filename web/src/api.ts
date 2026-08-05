import axios from "axios";
import localCatalog from "./data/catalog.json";
import type { Catalog, LessonInput, RunJob } from "./types";

const client = axios.create({
  baseURL: "/api",
  timeout: 30000,
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
