import {
  Button,
  Card,
  Dropdown,
  Empty,
  Input,
  InputNumber,
  List,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import { exportPrep, getVersion, listVersions, saveVersion, updateRunResult } from "../api";
import type { LessonInput, PrepResult, VersionItem } from "../types";

type Props = {
  result: PrepResult | null;
  lessonInput?: LessonInput | Record<string, unknown> | null;
  runId?: string | null;
  onResultChange?: (next: PrepResult) => void;
};

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function cloneResult(result: PrepResult): PrepResult {
  return JSON.parse(JSON.stringify(result)) as PrepResult;
}

export function ResultPanel({ result, lessonInput, runId, onResultChange }: Props) {
  const [draft, setDraft] = useState<PrepResult | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [versions, setVersions] = useState<VersionItem[]>([]);
  const [note, setNote] = useState("");
  const [activeTab, setActiveTab] = useState("curriculum");

  useEffect(() => {
    setDraft(result ? cloneResult(result) : null);
    setEditing(false);
  }, [result]);

  useEffect(() => {
    refreshVersions();
  }, []);

  const refreshVersions = async () => {
    try {
      setVersions(await listVersions());
    } catch {
      // 后端未开时忽略
    }
  };

  if (!draft) {
    return (
      <div className="result-empty">
        <div className="panel-kicker">交付物</div>
        <h2 className="panel-title">备课结果</h2>
        <Empty description="生成完成后，课标解读、教案、习题、课件与板书将显示在这里" />
        {versions.length > 0 ? (
          <div className="version-box">
            <Typography.Text type="secondary">或从已保存版本加载：</Typography.Text>
            <Select
              style={{ width: "100%", marginTop: 8 }}
              placeholder="选择历史版本"
              options={versions.map((v) => ({
                value: v.id,
                label: `${v.title}（${v.updated_at || v.created_at || ""}）`,
              }))}
              onChange={async (id) => {
                try {
                  const detail = await getVersion(id);
                  setDraft(cloneResult(detail.result));
                  onResultChange?.(detail.result);
                  message.success("已加载版本");
                } catch {
                  message.error("加载版本失败");
                }
              }}
            />
          </div>
        ) : null}
      </div>
    );
  }

  const curriculum = (draft.curriculum_analysis || {}) as Record<string, unknown>;
  const plan = (draft.lesson_plan || {}) as Record<string, unknown>;
  const exercises = (draft.exercise_paper || {}) as Record<string, unknown>;
  const slides = (draft.slides || {}) as Record<string, unknown>;
  const board = (draft.blackboard || {}) as Record<string, unknown>;
  const stages = Array.isArray(plan.stages) ? (plan.stages as Record<string, unknown>[]) : [];
  const exerciseItems = Array.isArray(exercises.items)
    ? (exercises.items as Record<string, unknown>[])
    : [];
  const dist = (exercises.difficulty_distribution || {}) as Record<string, unknown>;
  const pages = Array.isArray(slides.pages) ? (slides.pages as Record<string, unknown>[]) : [];
  const mainBoard = Array.isArray(board.main_board)
    ? (board.main_board as Record<string, unknown>[])
    : [];
  const sideBoard = Array.isArray(board.side_board)
    ? (board.side_board as Record<string, unknown>[])
    : [];

  const difficultyLabel = (d: unknown) =>
    ({ easy: "易", medium: "中", hard: "难" } as Record<string, string>)[String(d)] || String(d || "");
  const typeLabel = (t: unknown) =>
    (
      {
        choice: "选择",
        fill: "填空",
        short: "简答",
        calculation: "计算",
        application: "应用",
      } as Record<string, string>
    )[String(t)] || String(t || "");

  const patch = (path: string[], value: unknown) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const next = cloneResult(prev);
      let cursor: Record<string, unknown> = next as Record<string, unknown>;
      for (let i = 0; i < path.length - 1; i += 1) {
        const key = path[i];
        const child = cursor[key];
        if (!child || typeof child !== "object") {
          cursor[key] = {};
        }
        cursor = cursor[key] as Record<string, unknown>;
      }
      cursor[path[path.length - 1]] = value;
      onResultChange?.(next);
      return next;
    });
  };

  const onSaveVersion = async () => {
    setSaving(true);
    try {
      if (runId) {
        await updateRunResult(runId, draft);
      }
      await saveVersion({
        input: lessonInput || draft.input || {},
        result: draft,
        note,
      });
      setNote("");
      await refreshVersions();
      message.success("已保存版本");
    } catch (err) {
      message.error(`保存失败：${String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const onExport = async (
    format: "docx" | "pdf" | "pptx",
    module: "all" | "curriculum" | "plan" | "exercises" | "slides" | "board" = "all",
  ) => {
    const labels = {
      all: "全部",
      curriculum: "课标解读",
      plan: "教案",
      exercises: "习题卷",
      slides: "课件大纲",
      board: "板书",
    };
    try {
      await exportPrep({
        format,
        module,
        input: lessonInput || draft.input || {},
        result: draft,
      });
      message.success(`已导出${labels[module]}（${format.toUpperCase()}）`);
    } catch (err) {
      message.error(`导出失败：${String(err)}`);
    }
  };

  const moduleExportItems = (
    module: "curriculum" | "plan" | "exercises" | "slides" | "board",
  ) => {
    const items = [
      { key: "docx", label: "导出 Word", onClick: () => onExport("docx", module) },
      { key: "pdf", label: "导出 PDF", onClick: () => onExport("pdf", module) },
    ];
    items.push({
      key: "pptx",
      label: module === "slides" ? "导出 PPT" : "导出 PPT（本模块）",
      onClick: () => onExport("pptx", module),
    });
    return items;
  };

  return (
    <div>
      <div className="result-toolbar">
        <div>
          <div className="panel-kicker">交付物</div>
          <h2 className="panel-title">备课结果</h2>
        </div>
        <Space wrap>
          <span className="edit-switch">
            编辑
            <Switch checked={editing} onChange={setEditing} size="small" />
          </span>
          <Button onClick={onSaveVersion} loading={saving}>
            保存版本
          </Button>
          <Dropdown
            menu={{
              items: [
                { key: "docx", label: "导出全部 Word", onClick: () => onExport("docx", "all") },
                { key: "pdf", label: "导出全部 PDF", onClick: () => onExport("pdf", "all") },
                { key: "pptx", label: "导出全部 PPT", onClick: () => onExport("pptx", "all") },
              ],
            }}
          >
            <Button>导出全部</Button>
          </Dropdown>
        </Space>
      </div>

      <div className="version-box compact">
        <Input
          placeholder="版本备注（可选）"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          style={{ marginBottom: 8 }}
        />
        <Select
          allowClear
          style={{ width: "100%" }}
          placeholder="加载历史版本"
          options={versions.map((v) => ({
            value: v.id,
            label: `${v.title}（${v.updated_at || v.created_at || ""}）`,
          }))}
          onChange={async (id) => {
            if (!id) return;
            try {
              const detail = await getVersion(id);
              setDraft(cloneResult(detail.result));
              onResultChange?.(detail.result);
              message.success("已加载版本");
            } catch {
              message.error("加载版本失败");
            }
          }}
        />
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        tabBarExtraContent={
          <Dropdown
            menu={{
              items: moduleExportItems(
                (activeTab as "curriculum" | "plan" | "exercises" | "slides" | "board") ||
                  "curriculum",
              ),
            }}
          >
            <Button type="primary" size="small">
              导出本模块
            </Button>
          </Dropdown>
        }
        items={[
          {
            key: "curriculum",
            label: "课标解读",
            children: (
              <div className="result-block">
                <Typography.Paragraph>
                  <Tag>置信度 {String(curriculum.confidence || "-")}</Tag>
                </Typography.Paragraph>
                <EditableSection
                  title="核心素养"
                  editing={editing}
                  items={asStringList(curriculum.core_competencies)}
                  onChange={(items) => patch(["curriculum_analysis", "core_competencies"], items)}
                />
                <EditableSection
                  title="学业要求"
                  editing={editing}
                  items={asStringList(curriculum.academic_requirements)}
                  onChange={(items) =>
                    patch(["curriculum_analysis", "academic_requirements"], items)
                  }
                />
                <EditableSection
                  title="内容要点"
                  editing={editing}
                  items={asStringList(curriculum.content_points)}
                  onChange={(items) => patch(["curriculum_analysis", "content_points"], items)}
                />
                <EditableSection
                  title="教学提示"
                  editing={editing}
                  items={asStringList(curriculum.teaching_tips_from_standard)}
                  onChange={(items) =>
                    patch(["curriculum_analysis", "teaching_tips_from_standard"], items)
                  }
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
                <EditableSection
                  title="教学目标"
                  editing={editing}
                  items={asStringList(plan.teaching_objectives)}
                  onChange={(items) => patch(["lesson_plan", "teaching_objectives"], items)}
                />
                <EditableSection
                  title="重点"
                  editing={editing}
                  items={asStringList(plan.key_points)}
                  onChange={(items) => patch(["lesson_plan", "key_points"], items)}
                />
                <EditableSection
                  title="难点"
                  editing={editing}
                  items={asStringList(plan.difficult_points)}
                  onChange={(items) => patch(["lesson_plan", "difficult_points"], items)}
                />
                <EditableSection
                  title="练习意图"
                  editing={editing}
                  items={asStringList(plan.practice_intents)}
                  onChange={(items) => patch(["lesson_plan", "practice_intents"], items)}
                />
                <Typography.Title level={5}>环节设计</Typography.Title>
                {stages.length ? (
                  <Timeline
                    items={stages.map((s, idx) => ({
                      children: editing ? (
                        <Card size="small" title={`环节 ${idx + 1}`}>
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Input
                              value={String(s.name || "")}
                              onChange={(e) => {
                                const next = stages.map((item, i) =>
                                  i === idx ? { ...item, name: e.target.value } : item,
                                );
                                patch(["lesson_plan", "stages"], next);
                              }}
                              placeholder="环节名"
                            />
                            <InputNumber
                              min={1}
                              max={90}
                              value={Number(s.duration_minutes || 0)}
                              onChange={(v) => {
                                const next = stages.map((item, i) =>
                                  i === idx ? { ...item, duration_minutes: v || 0 } : item,
                                );
                                patch(["lesson_plan", "stages"], next);
                              }}
                              addonAfter="分钟"
                              style={{ width: "100%" }}
                            />
                            <Input.TextArea
                              rows={2}
                              value={String(s.teacher_activity || "")}
                              onChange={(e) => {
                                const next = stages.map((item, i) =>
                                  i === idx ? { ...item, teacher_activity: e.target.value } : item,
                                );
                                patch(["lesson_plan", "stages"], next);
                              }}
                              placeholder="教师活动"
                            />
                            <Input.TextArea
                              rows={2}
                              value={String(s.student_activity || "")}
                              onChange={(e) => {
                                const next = stages.map((item, i) =>
                                  i === idx ? { ...item, student_activity: e.target.value } : item,
                                );
                                patch(["lesson_plan", "stages"], next);
                              }}
                              placeholder="学生活动"
                            />
                            <Input.TextArea
                              rows={2}
                              value={String(s.purpose || "")}
                              onChange={(e) => {
                                const next = stages.map((item, i) =>
                                  i === idx ? { ...item, purpose: e.target.value } : item,
                                );
                                patch(["lesson_plan", "stages"], next);
                              }}
                              placeholder="目的"
                            />
                          </Space>
                        </Card>
                      ) : (
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
            key: "exercises",
            label: "习题卷",
            children: (
              <div className="result-block">
                {!exerciseItems.length ? (
                  <Empty description="暂无习题（请重新生成）" />
                ) : null}
                {editing ? (
                  <Space direction="vertical" style={{ width: "100%", marginBottom: 12 }}>
                    <Input
                      value={String(exercises.title || "")}
                      onChange={(e) => patch(["exercise_paper", "title"], e.target.value)}
                      placeholder="试卷标题"
                    />
                    <Space wrap>
                      <InputNumber
                        min={1}
                        value={Number(exercises.total_score || 0)}
                        onChange={(v) => patch(["exercise_paper", "total_score"], v || 0)}
                        addonBefore="总分"
                      />
                      <InputNumber
                        min={1}
                        value={Number(exercises.time_limit_minutes || 0)}
                        onChange={(v) =>
                          patch(["exercise_paper", "time_limit_minutes"], v || 0)
                        }
                        addonBefore="用时"
                        addonAfter="分钟"
                      />
                    </Space>
                    <Input.TextArea
                      rows={2}
                      value={String(exercises.design_notes || "")}
                      onChange={(e) => patch(["exercise_paper", "design_notes"], e.target.value)}
                      placeholder="设计说明"
                    />
                    <Input.TextArea
                      rows={2}
                      value={asStringList(exercises.knowledge_coverage).join("\n")}
                      onChange={(e) =>
                        patch(
                          ["exercise_paper", "knowledge_coverage"],
                          e.target.value
                            .split("\n")
                            .map((x) => x.trim())
                            .filter(Boolean),
                        )
                      }
                      placeholder="知识点覆盖（每行一条）"
                    />
                  </Space>
                ) : (
                  <>
                    <Typography.Title level={5}>
                      {String(exercises.title || "随堂练习")}
                    </Typography.Title>
                    <Typography.Paragraph>
                      <Tag>总分 {String(exercises.total_score || "-")}</Tag>
                      <Tag>建议用时 {String(exercises.time_limit_minutes || "-")} 分钟</Tag>
                      <Tag>
                        难度 易{String(dist.easy ?? 0)} / 中{String(dist.medium ?? 0)} / 难
                        {String(dist.hard ?? 0)}
                      </Tag>
                    </Typography.Paragraph>
                    {exercises.design_notes ? (
                      <Typography.Paragraph type="secondary">
                        {String(exercises.design_notes)}
                      </Typography.Paragraph>
                    ) : null}
                    <div style={{ marginBottom: 12 }}>
                      {asStringList(exercises.knowledge_coverage).map((k) => (
                        <Tag key={k}>{k}</Tag>
                      ))}
                    </div>
                  </>
                )}
                <List
                  dataSource={exerciseItems}
                  renderItem={(item, idx) => (
                    <List.Item>
                      <Card
                        size="small"
                        title={
                          editing ? (
                            <Space wrap>
                              <span>第 {idx + 1} 题</span>
                              <Select
                                size="small"
                                value={String(item.difficulty || "medium")}
                                style={{ width: 72 }}
                                options={[
                                  { value: "easy", label: "易" },
                                  { value: "medium", label: "中" },
                                  { value: "hard", label: "难" },
                                ]}
                                onChange={(v) => {
                                  const next = exerciseItems.map((row, i) =>
                                    i === idx ? { ...row, difficulty: v } : row,
                                  );
                                  patch(["exercise_paper", "items"], next);
                                }}
                              />
                              <Select
                                size="small"
                                value={String(item.question_type || "calculation")}
                                style={{ width: 88 }}
                                options={[
                                  { value: "choice", label: "选择" },
                                  { value: "fill", label: "填空" },
                                  { value: "short", label: "简答" },
                                  { value: "calculation", label: "计算" },
                                  { value: "application", label: "应用" },
                                ]}
                                onChange={(v) => {
                                  const next = exerciseItems.map((row, i) =>
                                    i === idx ? { ...row, question_type: v } : row,
                                  );
                                  patch(["exercise_paper", "items"], next);
                                }}
                              />
                              <InputNumber
                                size="small"
                                min={1}
                                value={Number(item.score || 0)}
                                onChange={(v) => {
                                  const next = exerciseItems.map((row, i) =>
                                    i === idx ? { ...row, score: v || 0 } : row,
                                  );
                                  patch(["exercise_paper", "items"], next);
                                }}
                                addonAfter="分"
                              />
                            </Space>
                          ) : (
                            `第${item.index ?? idx + 1}题 · ${difficultyLabel(item.difficulty)} · ${typeLabel(item.question_type)} · ${item.score ?? ""}分`
                          )
                        }
                        style={{ width: "100%" }}
                      >
                        {editing ? (
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Input
                              value={String(item.knowledge_point || "")}
                              onChange={(e) => {
                                const next = exerciseItems.map((row, i) =>
                                  i === idx ? { ...row, knowledge_point: e.target.value } : row,
                                );
                                patch(["exercise_paper", "items"], next);
                              }}
                              placeholder="知识点"
                            />
                            <Input.TextArea
                              rows={3}
                              value={String(item.stem || "")}
                              onChange={(e) => {
                                const next = exerciseItems.map((row, i) =>
                                  i === idx ? { ...row, stem: e.target.value } : row,
                                );
                                patch(["exercise_paper", "items"], next);
                              }}
                              placeholder="题干"
                            />
                            <Input.TextArea
                              rows={2}
                              value={asStringList(item.options).join("\n")}
                              onChange={(e) => {
                                const options = e.target.value
                                  .split("\n")
                                  .map((x) => x.trim())
                                  .filter(Boolean);
                                const next = exerciseItems.map((row, i) =>
                                  i === idx ? { ...row, options } : row,
                                );
                                patch(["exercise_paper", "items"], next);
                              }}
                              placeholder="选项（选择题，每行一个）"
                            />
                            <Input
                              value={String(item.answer || "")}
                              onChange={(e) => {
                                const next = exerciseItems.map((row, i) =>
                                  i === idx ? { ...row, answer: e.target.value } : row,
                                );
                                patch(["exercise_paper", "items"], next);
                              }}
                              placeholder="答案"
                            />
                            <Input.TextArea
                              rows={2}
                              value={String(item.analysis || "")}
                              onChange={(e) => {
                                const next = exerciseItems.map((row, i) =>
                                  i === idx ? { ...row, analysis: e.target.value } : row,
                                );
                                patch(["exercise_paper", "items"], next);
                              }}
                              placeholder="解析"
                            />
                          </Space>
                        ) : (
                          <>
                            <Space size={4} wrap style={{ marginBottom: 6 }}>
                              {item.source === "bank" ? (
                                <Tag color="blue">
                                  题库{item.source_id ? ` · ${String(item.source_id)}` : ""}
                                </Tag>
                              ) : item.source === "generated" ? (
                                <Tag>补生成</Tag>
                              ) : null}
                            </Space>
                            {item.knowledge_point ? (
                              <p className="muted">知识点：{String(item.knowledge_point)}</p>
                            ) : null}
                            <p>{String(item.stem || "")}</p>
                            {asStringList(item.options).length ? (
                              <ul>
                                {asStringList(item.options).map((o) => (
                                  <li key={o}>{o}</li>
                                ))}
                              </ul>
                            ) : null}
                            <p>
                              <strong>答案：</strong>
                              {String(item.answer || "")}
                            </p>
                            {item.analysis ? (
                              <p className="muted">解析：{String(item.analysis)}</p>
                            ) : null}
                          </>
                        )}
                      </Card>
                    </List.Item>
                  )}
                />
              </div>
            ),
          },
          {
            key: "slides",
            label: "课件大纲",
            children: (
              <div className="result-block">
                {editing ? (
                  <Input.TextArea
                    rows={2}
                    style={{ marginBottom: 12 }}
                    value={String(slides.design_notes || "")}
                    onChange={(e) => patch(["slides", "design_notes"], e.target.value)}
                    placeholder="设计说明"
                  />
                ) : slides.design_notes ? (
                  <Typography.Paragraph type="secondary">
                    {String(slides.design_notes)}
                  </Typography.Paragraph>
                ) : null}
                <List
                  dataSource={pages}
                  renderItem={(page, idx) => (
                    <List.Item>
                      <Card
                        size="small"
                        title={
                          editing ? (
                            <Input
                              value={String(page.title || "")}
                              onChange={(e) => {
                                const next = pages.map((item, i) =>
                                  i === idx ? { ...item, title: e.target.value } : item,
                                );
                                patch(["slides", "pages"], next);
                              }}
                            />
                          ) : (
                            `P${page.index} ${String(page.title || "")}`
                          )
                        }
                        style={{ width: "100%" }}
                      >
                        {editing ? (
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Input.TextArea
                              rows={3}
                              value={asStringList(page.bullets).join("\n")}
                              onChange={(e) => {
                                const bullets = e.target.value
                                  .split("\n")
                                  .map((x) => x.trim())
                                  .filter(Boolean);
                                const next = pages.map((item, i) =>
                                  i === idx ? { ...item, bullets } : item,
                                );
                                patch(["slides", "pages"], next);
                              }}
                              placeholder="每行一个要点"
                            />
                            <Input
                              value={String(page.interaction || "")}
                              onChange={(e) => {
                                const next = pages.map((item, i) =>
                                  i === idx ? { ...item, interaction: e.target.value } : item,
                                );
                                patch(["slides", "pages"], next);
                              }}
                              placeholder="互动提示"
                            />
                          </Space>
                        ) : (
                          <>
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
                            {page.image_id ? (
                              <div style={{ marginTop: 12 }}>
                                <img
                                  src={`/api/media/${encodeURIComponent(String(page.image_id))}`}
                                  alt={String(page.image_caption || page.title || "配图")}
                                  style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid #e8e8e8" }}
                                  onError={(e) => {
                                    (e.target as HTMLImageElement).style.display = "none";
                                  }}
                                />
                                <Typography.Paragraph type="secondary" style={{ marginTop: 6 }}>
                                  配图：{String(page.image_caption || page.image_id)}
                                  {page.image_source ? ` · ${String(page.image_source)}` : ""}
                                </Typography.Paragraph>
                              </div>
                            ) : null}
                          </>
                        )}
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
                    {editing ? (
                      <Input.TextArea
                        rows={8}
                        value={mainBoard
                          .slice()
                          .sort((a, b) => Number(a.order) - Number(b.order))
                          .map((item) => String(item.text || ""))
                          .join("\n")}
                        onChange={(e) => {
                          const lines = e.target.value.split("\n");
                          const next = lines.map((text, i) => ({
                            order: i + 1,
                            text,
                            level: 1,
                          }));
                          patch(["blackboard", "main_board"], next);
                        }}
                      />
                    ) : (
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
                    )}
                  </div>
                  <div>
                    <h4>副板书</h4>
                    {editing ? (
                      <Input.TextArea
                        rows={8}
                        value={sideBoard.map((item) => String(item.text || "")).join("\n")}
                        onChange={(e) => {
                          const lines = e.target.value.split("\n").filter((x) => x.trim());
                          const next = lines.map((text, i) => ({
                            order: i + 1,
                            text,
                            level: 1,
                          }));
                          patch(["blackboard", "side_board"], next);
                        }}
                      />
                    ) : (
                      <ol>
                        {sideBoard.map((item) => (
                          <li key={`${item.order}-${item.text}`}>{String(item.text)}</li>
                        ))}
                      </ol>
                    )}
                  </div>
                </div>
                <EditableSection
                  title="书写顺序"
                  editing={editing}
                  items={asStringList(board.writing_sequence)}
                  onChange={(items) => patch(["blackboard", "writing_sequence"], items)}
                />
                <EditableSection
                  title="关键句"
                  editing={editing}
                  items={asStringList(board.key_sentences)}
                  onChange={(items) => patch(["blackboard", "key_sentences"], items)}
                />
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}

function EditableSection({
  title,
  items,
  editing,
  onChange,
}: {
  title: string;
  items: string[];
  editing: boolean;
  onChange: (items: string[]) => void;
}) {
  if (!editing && !items.length) return null;
  return (
    <div className="section-block">
      <Typography.Title level={5}>{title}</Typography.Title>
      {editing ? (
        <Input.TextArea
          rows={Math.max(3, Math.min(8, items.length + 1))}
          value={items.join("\n")}
          onChange={(e) =>
            onChange(
              e.target.value
                .split("\n")
                .map((x) => x.trimEnd())
                .filter((x, idx, arr) => x.length > 0 || idx < arr.length - 1)
                .map((x) => x.trim())
                .filter(Boolean),
            )
          }
          placeholder="每行一条"
        />
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
