import { Button, Space, Steps, Tag } from "antd";
import type { ParallelLane, PipelineStep, PrepResult, RunJob } from "../types";

const ORDER = [
  "curriculum",
  "lesson_plan",
  "lesson_plan_review",
  "materials",
  "consistency",
  "done",
];

const LANE_ORDER = ["slides", "exercises", "blackboard"] as const;

const RERUN_OPTIONS: { step: PipelineStep; label: string }[] = [
  { step: "curriculum", label: "从课标重跑" },
  { step: "lesson_plan", label: "从教案重跑" },
  { step: "materials", label: "并行重跑三者" },
  { step: "exercises", label: "只重跑习题" },
  { step: "slides", label: "只重跑课件" },
  { step: "blackboard", label: "只重跑板书" },
  { step: "consistency", label: "重跑一致性检查" },
];

type Props = {
  job: RunJob | null;
  draftResult?: PrepResult | null;
  onRerun?: (fromStep: PipelineStep) => void;
  rerunning?: boolean;
};

function currentIndex(step: string) {
  if (["exercises", "slides", "blackboard"].includes(step)) return ORDER.indexOf("materials");
  const idx = ORDER.indexOf(step);
  return idx < 0 ? 0 : idx;
}

function laneTagColor(status: string) {
  if (status === "done") return "success";
  if (status === "error") return "error";
  if (status === "running") return "processing";
  return "default";
}

function laneTagText(status: string) {
  if (status === "done") return "完成";
  if (status === "error") return "失败";
  if (status === "running") return "进行中";
  return "等待";
}

function materialsDescription(lanes: Record<string, ParallelLane> | null | undefined) {
  if (!lanes || !Object.keys(lanes).length) {
    return "课件生成师 · 习题组卷师 · 板书设计师";
  }
  const parts = LANE_ORDER.filter((k) => lanes[k]).map((k) => {
    const lane = lanes[k];
    const name = lane.label || k;
    return `${name}(${laneTagText(lane.status)})`;
  });
  return parts.join(" · ");
}

export function AgentProgress({ job, draftResult, onRerun, rerunning }: Props) {
  if (!job) {
    return (
      <div className="progress-empty">
        <div className="panel-kicker">教研团队</div>
        <h2 className="panel-title">等待开始</h2>
        <p className="muted">教案审核通过后，课件 / 习题 / 板书并行生成，再由一致性检查员把关。</p>
        <ol className="agent-roster">
          <li>课标解读员</li>
          <li>教案设计师 → 教案审核员</li>
          <li>课件 / 习题 / 板书（并行）</li>
          <li>一致性检查员</li>
        </ol>
      </div>
    );
  }

  const idx = currentIndex(job.step);
  const lanes =
    job.parallel_lanes ||
    (draftResult?.materials_lanes as Record<string, ParallelLane> | undefined) ||
    (job.result?.materials_lanes as Record<string, ParallelLane> | undefined) ||
    null;

  const statusTag =
    job.status === "done" ? (
      <Tag color="success">已完成</Tag>
    ) : job.status === "error" ? (
      <Tag color="error">失败</Tag>
    ) : (
      <Tag color="processing">进行中</Tag>
    );

  const planQa = (draftResult?.lesson_plan_qa || job.result?.lesson_plan_qa) as
    | PrepResult["lesson_plan_qa"]
    | undefined;
  const consistencyQa = (draftResult?.consistency_qa || job.result?.consistency_qa) as
    | PrepResult["consistency_qa"]
    | undefined;
  const showRerun = (job.status === "done" || job.status === "error") && onRerun;
  const failedLanes = LANE_ORDER.filter((k) => lanes?.[k]?.status === "error");

  const renderQa = (
    title: string,
    qa: PrepResult["lesson_plan_qa"] | PrepResult["consistency_qa"] | undefined,
  ) => {
    if (!qa) return null;
    const modules = Array.isArray((qa as PrepResult["consistency_qa"])?.conflict_modules)
      ? (qa as PrepResult["consistency_qa"])!.conflict_modules!
      : [];
    return (
      <div style={{ marginTop: 12 }}>
        <div className="panel-kicker">{title}</div>
        <Space wrap size={6} style={{ marginBottom: 6 }}>
          <Tag color={qa.passed ? "success" : "warning"}>
            {qa.passed ? "通过" : "未完全通过"}
          </Tag>
          {qa.revised ? <Tag color="blue">曾打回修改</Tag> : null}
          {modules.map((m) => (
            <Tag key={m}>{m}</Tag>
          ))}
        </Space>
        {qa.notes ? <p className="muted">{qa.notes}</p> : null}
        {Array.isArray(qa.issues) && qa.issues.length ? (
          <ul className="muted" style={{ paddingLeft: 18, margin: "6px 0" }}>
            {qa.issues.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  };

  return (
    <div>
      <div className="panel-kicker">教研团队</div>
      <div className="progress-head">
        <h2 className="panel-title">生成进度</h2>
        {statusTag}
      </div>
      <p className="muted">{job.message}</p>
      {job.failed_step ? (
        <p className="muted">
          失败节点：
          <Tag color="error">{job.step_label || job.failed_step}</Tag>
        </p>
      ) : null}
      <Steps
        direction="vertical"
        size="small"
        current={job.status === "done" ? 5 : idx}
        status={job.status === "error" ? "error" : undefined}
        items={[
          { title: "课标解读员", description: "按需检索课标并提取要点" },
          { title: "教案设计师", description: "撰写教案；被打回时修改" },
          {
            title: "教案审核员",
            description: "通过 → 并行下游；不通过 → 打回设计师",
          },
          {
            title: "并行生成",
            description: materialsDescription(lanes),
          },
          {
            title: "一致性检查员",
            description: "一致则完成；冲突则打回对应设计师",
          },
        ]}
      />

      {lanes && Object.keys(lanes).length ? (
        <div style={{ marginTop: 12 }}>
          <div className="panel-kicker">并行分路</div>
          <Space direction="vertical" size={6} style={{ width: "100%" }}>
            {LANE_ORDER.filter((k) => lanes[k]).map((key) => {
              const lane = lanes[key];
              return (
                <div key={key}>
                  <Space wrap size={6}>
                    <span>{lane.label || key}</span>
                    <Tag color={laneTagColor(lane.status)}>{laneTagText(lane.status)}</Tag>
                    {job.status === "error" && lane.status === "error" && onRerun ? (
                      <Button
                        size="small"
                        type="link"
                        disabled={rerunning}
                        onClick={() => onRerun(key as PipelineStep)}
                      >
                        只重跑这一路
                      </Button>
                    ) : null}
                  </Space>
                  {lane.status === "error" && lane.error ? (
                    <pre className="error-box" style={{ marginTop: 4, maxHeight: 80 }}>
                      {lane.error.slice(0, 400)}
                    </pre>
                  ) : null}
                </div>
              );
            })}
          </Space>
          {failedLanes.length ? (
            <p className="muted" style={{ marginTop: 8 }}>
              失败分路可单独重跑，无需整单重来。
            </p>
          ) : null}
        </div>
      ) : null}

      {renderQa("教案审核员", planQa)}
      {renderQa("一致性检查员", consistencyQa)}

      {showRerun ? (
        <div style={{ marginTop: 14 }}>
          <div className="panel-kicker">节点重跑</div>
          <p className="muted" style={{ marginBottom: 8 }}>
            可并行重跑三者，或只重跑某一设计师后再做一致性检查。
          </p>
          <Space wrap size={[8, 8]}>
            {RERUN_OPTIONS.map((opt) => (
              <Button
                key={opt.step}
                size="small"
                type={
                  job.failed_step === opt.step || failedLanes.includes(opt.step as typeof LANE_ORDER[number])
                    ? "primary"
                    : "default"
                }
                loading={rerunning}
                disabled={rerunning}
                onClick={() => onRerun?.(opt.step)}
              >
                {opt.label}
              </Button>
            ))}
          </Space>
        </div>
      ) : null}

      {job.error ? <pre className="error-box">{job.error.slice(0, 800)}</pre> : null}
    </div>
  );
}
