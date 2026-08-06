import { Steps, Tag } from "antd";
import type { RunJob } from "../types";

const ORDER = ["curriculum", "lesson_plan", "exercises", "slides", "blackboard", "done"];

type Props = {
  job: RunJob | null;
};

function currentIndex(step: string) {
  const idx = ORDER.indexOf(step);
  return idx < 0 ? 0 : idx;
}

export function AgentProgress({ job }: Props) {
  if (!job) {
    return (
      <div className="progress-empty">
        <div className="panel-kicker">教研团队</div>
        <h2 className="panel-title">等待开始</h2>
        <p className="muted">填写左侧课时信息后，五位 Agent 将依次完成备课。</p>
        <ol className="agent-roster">
          <li>课标解读员</li>
          <li>教案设计师</li>
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

  return (
    <div>
      <div className="panel-kicker">教研团队</div>
      <div className="progress-head">
        <h2 className="panel-title">生成进度</h2>
        {statusTag}
      </div>
      <p className="muted">{job.message}</p>
      <Steps
        direction="vertical"
        size="small"
        current={job.status === "done" ? 5 : idx}
        status={job.status === "error" ? "error" : undefined}
        items={[
          { title: "课标解读员", description: "提取核心素养与内容要点" },
          { title: "教案设计师", description: "目标 / 重难点 / 环节" },
          { title: "习题组卷师", description: "难度梯度与知识点覆盖" },
          { title: "课件生成师", description: "PPT 大纲与素材关键词" },
          { title: "板书设计师", description: "主板书结构与书写顺序" },
        ]}
      />
      {job.error ? <pre className="error-box">{job.error.slice(0, 800)}</pre> : null}
    </div>
  );
}
