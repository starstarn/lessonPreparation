import { Button, Space, Steps, Tag } from "antd";
import type { PipelineStep, PrepResult, RunJob } from "../types";

const ORDER = ["curriculum", "lesson_plan", "exercises", "slides", "blackboard", "done"];

const RERUN_OPTIONS: { step: PipelineStep; label: string }[] = [
  { step: "curriculum", label: "从课标重跑" },
  { step: "lesson_plan", label: "从教案重跑" },
  { step: "exercises", label: "从习题重跑" },
  { step: "slides", label: "从课件重跑" },
  { step: "blackboard", label: "从板书重跑" },
];

type Props = {
  job: RunJob | null;
  draftResult?: PrepResult | null;
  onRerun?: (fromStep: PipelineStep) => void;
  rerunning?: boolean;
};

function currentIndex(step: string) {
  const idx = ORDER.indexOf(step);
  return idx < 0 ? 0 : idx;
}

export function AgentProgress({ job, draftResult, onRerun, rerunning }: Props) {
  if (!job) {
    return (
      <div className="progress-empty">
        <div className="panel-kicker">教研团队</div>
        <h2 className="panel-title">等待开始</h2>
        <p className="muted">填写左侧课时信息后，五位 Agent 将依次完成备课。</p>
        <ol className="agent-roster">
          <li>课标解读员</li>
          <li>教案设计师（含质检/回修）</li>
          <li>习题组卷师</li>
          <li>课件生成师</li>
          <li>板书设计师</li>
        </ol>
      </div>
    );
  }

  const idx = currentIndex(job.step);
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
  const exerciseQa = (draftResult?.exercise_qa || job.result?.exercise_qa) as
    | PrepResult["exercise_qa"]
    | undefined;
  const slidesQa = (draftResult?.slides_qa || job.result?.slides_qa) as
    | PrepResult["slides_qa"]
    | undefined;
  const showRerun = (job.status === "done" || job.status === "error") && onRerun;

  const renderQa = (title: string, qa: PrepResult["lesson_plan_qa"] | undefined) => {
    if (!qa) return null;
    return (
      <div style={{ marginTop: 12 }}>
        <div className="panel-kicker">{title}</div>
        <Space wrap size={6} style={{ marginBottom: 6 }}>
          <Tag color={qa.passed ? "success" : "warning"}>
            {qa.passed ? "通过" : "未完全通过"}
          </Tag>
          {qa.revised ? <Tag color="blue">已回修一次</Tag> : null}
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
          失败节点：<Tag color="error">{job.failed_step}</Tag>
        </p>
      ) : null}
      <Steps
        direction="vertical"
        size="small"
        current={job.status === "done" ? 5 : idx}
        status={job.status === "error" ? "error" : undefined}
        items={[
          { title: "课标解读员", description: "按需检索课标并提取要点" },
          { title: "教案设计师", description: "生成 → 质检 → 不通过则回修一次" },
          { title: "习题组卷师", description: "组卷 → 对照教案质检 → 回修一次" },
          { title: "课件生成师", description: "生成 → 对照环节质检 → 回修一次" },
          { title: "板书设计师", description: "主板书结构与书写顺序" },
        ]}
      />

      {renderQa("教案质检", planQa)}
      {renderQa("习题质检", exerciseQa)}
      {renderQa("课件质检", slidesQa)}

      {showRerun ? (
        <div style={{ marginTop: 14 }}>
          <div className="panel-kicker">节点重跑</div>
          <p className="muted" style={{ marginBottom: 8 }}>
            保留上游结果，从所选节点重新生成后续内容。若已编辑教案/习题，将带上当前草稿。
          </p>
          <Space wrap size={[8, 8]}>
            {RERUN_OPTIONS.map((opt) => (
              <Button
                key={opt.step}
                size="small"
                type={job.failed_step === opt.step ? "primary" : "default"}
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
