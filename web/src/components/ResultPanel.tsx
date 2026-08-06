import { Card, Empty, List, Tabs, Tag, Timeline, Typography } from "antd";
import type { PrepResult } from "../types";

type Props = {
  result: PrepResult | null;
};

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export function ResultPanel({ result }: Props) {
  if (!result) {
    return (
      <div className="result-empty">
        <div className="panel-kicker">交付物</div>
        <h2 className="panel-title">备课结果</h2>
        <Empty description="生成完成后，课标解读、教案、课件与板书将显示在这里" />
      </div>
    );
  }

  const curriculum = (result.curriculum_analysis || {}) as Record<string, unknown>;
  const plan = (result.lesson_plan || {}) as Record<string, unknown>;
  const slides = (result.slides || {}) as Record<string, unknown>;
  const board = (result.blackboard || {}) as Record<string, unknown>;
  const stages = Array.isArray(plan.stages) ? (plan.stages as Record<string, unknown>[]) : [];
  const pages = Array.isArray(slides.pages) ? (slides.pages as Record<string, unknown>[]) : [];
  const mainBoard = Array.isArray(board.main_board)
    ? (board.main_board as Record<string, unknown>[])
    : [];
  const sideBoard = Array.isArray(board.side_board)
    ? (board.side_board as Record<string, unknown>[])
    : [];

  return (
    <div>
      <div className="panel-kicker">交付物</div>
      <h2 className="panel-title">备课结果</h2>
      <Tabs
        items={[
          {
            key: "curriculum",
            label: "课标解读",
            children: (
              <div className="result-block">
                <Typography.Paragraph>
                  <Tag>置信度 {String(curriculum.confidence || "-")}</Tag>
                </Typography.Paragraph>
                <Section title="核心素养" items={asStringList(curriculum.core_competencies)} />
                <Section title="学业要求" items={asStringList(curriculum.academic_requirements)} />
                <Section title="内容要点" items={asStringList(curriculum.content_points)} />
                <Section
                  title="教学提示"
                  items={asStringList(curriculum.teaching_tips_from_standard)}
                />
              </div>
            ),
          },
          {
            key: "plan",
            label: "教案",
            children: (
              <div className="result-block">
                {!asStringList(plan.teaching_objectives).length && !stages.length ? (
                  <Empty description="教案内容为空（模型可能返回了空字段，请重试生成）" />
                ) : null}
                <Section title="教学目标" items={asStringList(plan.teaching_objectives)} />
                <Section title="重点" items={asStringList(plan.key_points)} />
                <Section title="难点" items={asStringList(plan.difficult_points)} />
                <Section title="练习意图" items={asStringList(plan.practice_intents)} />
                <Typography.Title level={5}>环节设计</Typography.Title>
                {stages.length ? (
                  <Timeline
                    items={stages.map((s) => ({
                      children: (
                        <Card size="small" title={`${s.name} · ${s.duration_minutes} 分钟`}>
                          <p>
                            <strong>师：</strong>
                            {String(s.teacher_activity || "")}
                          </p>
                          <p>
                            <strong>生：</strong>
                            {String(s.student_activity || "")}
                          </p>
                          <p className="muted">{String(s.purpose || "")}</p>
                        </Card>
                      ),
                    }))}
                  />
                ) : (
                  <Empty description="暂无环节设计" />
                )}
              </div>
            ),
          },
          {
            key: "slides",
            label: "课件大纲",
            children: (
              <div className="result-block">
                {slides.design_notes ? (
                  <Typography.Paragraph type="secondary">
                    {String(slides.design_notes)}
                  </Typography.Paragraph>
                ) : null}
                <List
                  dataSource={pages}
                  renderItem={(page) => (
                    <List.Item>
                      <Card
                        size="small"
                        title={`P${page.index} ${String(page.title || "")}`}
                        style={{ width: "100%" }}
                      >
                        <ul>
                          {asStringList(page.bullets).map((b) => (
                            <li key={b}>{b}</li>
                          ))}
                        </ul>
                        {page.interaction ? (
                          <p>
                            <strong>互动：</strong>
                            {String(page.interaction)}
                          </p>
                        ) : null}
                        <div>
                          {asStringList(page.visual_keywords).map((k) => (
                            <Tag key={k}>{k}</Tag>
                          ))}
                        </div>
                      </Card>
                    </List.Item>
                  )}
                />
              </div>
            ),
          },
          {
            key: "board",
            label: "板书",
            children: (
              <div className="result-block chalkboard">
                <Typography.Paragraph>
                  布局：<Tag>{String(board.layout || "main_side")}</Tag>
                </Typography.Paragraph>
                <div className="board-grid">
                  <div>
                    <h4>主板书</h4>
                    <ol>
                      {mainBoard
                        .slice()
                        .sort((a, b) => Number(a.order) - Number(b.order))
                        .map((item) => (
                          <li
                            key={`${item.order}-${item.text}`}
                            style={{ marginLeft: (Number(item.level) - 1) * 12 }}
                          >
                            {String(item.text)}
                          </li>
                        ))}
                    </ol>
                  </div>
                  <div>
                    <h4>副板书</h4>
                    <ol>
                      {sideBoard.map((item) => (
                        <li key={`${item.order}-${item.text}`}>{String(item.text)}</li>
                      ))}
                    </ol>
                  </div>
                </div>
                <Section title="书写顺序" items={asStringList(board.writing_sequence)} />
                <Section title="关键句" items={asStringList(board.key_sentences)} />
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="section-block">
      <Typography.Title level={5}>{title}</Typography.Title>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
