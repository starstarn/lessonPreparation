import { ConfigProvider, message } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useEffect, useRef, useState } from "react";
import { createRun, fetchHealth, getRun, rerunRun } from "./api";
import { AgentProgress } from "./components/AgentProgress";
import { LessonForm } from "./components/LessonForm";
import { ResultPanel } from "./components/ResultPanel";
import type { LessonInput, PipelineStep, PrepResult, RunJob } from "./types";

export default function App() {
  const [job, setJob] = useState<RunJob | null>(null);
  const [draftResult, setDraftResult] = useState<PrepResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [modelInfo, setModelInfo] = useState("检测中…");
  const timer = useRef<number | null>(null);

  useEffect(() => {
    fetchHealth()
      .then((h) => setModelInfo(`${h.model}${h.mock_llm ? " · MOCK" : ""}`))
      .catch(() => setModelInfo("后端未连接"));
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  useEffect(() => {
    if (job?.result && (job.status === "done" || job.status === "error" || job.status === "running")) {
      setDraftResult(job.result);
    }
  }, [job]);

  const startPolling = (runId: string) => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      try {
        const next = await getRun(runId);
        setJob(next);
        if (next.status === "done" || next.status === "error") {
          setLoading(false);
          if (timer.current) window.clearInterval(timer.current);
          if (next.status === "done") message.success("备课完成");
          if (next.status === "error") message.error("生成失败，可从失败节点重跑");
        }
      } catch {
        setLoading(false);
        if (timer.current) window.clearInterval(timer.current);
        message.error("无法获取任务状态");
      }
    }, 1500);
  };

  const onSubmit = async (values: LessonInput) => {
    setLoading(true);
    setJob(null);
    setDraftResult(null);
    try {
      const created = await createRun(values);
      setJob(created);
      startPolling(created.id);
    } catch (err) {
      setLoading(false);
      message.error(`创建任务失败：${String(err)}`);
    }
  };

  const onRerun = async (fromStep: PipelineStep) => {
    if (!job) return;
    setLoading(true);
    try {
      const next = await rerunRun(job.id, fromStep, draftResult);
      setJob(next);
      startPolling(next.id);
      message.info(`已从「${fromStep}」开始重跑`);
    } catch (err) {
      setLoading(false);
      message.error(`重跑失败：${String(err)}`);
    }
  };

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#2F6F6A",
          colorInfo: "#2F6F6A",
          colorSuccess: "#3D8B7A",
          borderRadius: 10,
          fontFamily: '"Noto Sans SC", "PingFang SC", sans-serif',
        },
      }}
    >
      <div className="app-shell">
        <header className="topbar">
          <div>
            <p className="brand-mark">Lesson Atelier</p>
            <h1>智能备课教研工作台</h1>
          </div>
          <div className="topbar-meta">
            <span>服务老师 · 对齐课标</span>
            <span className="chip">{modelInfo}</span>
          </div>
        </header>

        <main className="workbench">
          <section className="panel panel-input">
            <LessonForm loading={loading} onSubmit={onSubmit} />
          </section>
          <section className="panel panel-progress">
            <AgentProgress
              job={job}
              draftResult={draftResult}
              onRerun={onRerun}
              rerunning={loading}
            />
          </section>
          <section className="panel panel-result">
            <ResultPanel
              result={draftResult}
              lessonInput={job?.input}
              runId={job?.status === "done" || job?.status === "error" ? job.id : null}
              onResultChange={setDraftResult}
            />
          </section>
        </main>
      </div>
    </ConfigProvider>
  );
}
