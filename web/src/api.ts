import axios from "axios";
import type { LessonInput, RunJob } from "./types";

const client = axios.create({
  baseURL: "/api",
  timeout: 30000,
});

export async function fetchHealth() {
  const { data } = await client.get("/health");
  return data as { ok: boolean; mock_llm: boolean; model: string };
}

export async function createRun(payload: LessonInput) {
  const { data } = await client.post<RunJob>("/runs", payload);
  return data;
}

export async function getRun(runId: string) {
  const { data } = await client.get<RunJob>(`/runs/${runId}`);
  return data;
}
