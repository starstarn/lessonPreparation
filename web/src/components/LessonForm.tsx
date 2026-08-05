import { Button, Col, Form, Input, InputNumber, Row, Select, Space, Switch } from "antd";
import { useEffect, useMemo, useState } from "react";
import { fetchCatalog } from "../api";
import type { Catalog, CatalogDomain, CatalogGrade, CatalogTopic, LessonInput } from "../types";

type Props = {
  loading: boolean;
  onSubmit: (values: LessonInput) => void;
};

const CUSTOM = "__custom__";

const initial: LessonInput = {
  stage: "初中",
  subject: "数学",
  textbook_version: "人教版",
  grade: "七年级",
  unit: "有理数",
  lesson_title: "有理数的加法",
  duration_minutes: 45,
  curriculum_year: "2022",
  extra_notes: "突出符号法则，多举数轴例子",
  learning_profile: {
    class_level: "average",
    prior_knowledge: "已认识正负数、数轴",
    known_pain_points: "异号两数相加易错",
    focus: "key_points",
  },
};

function findTopicPath(
  catalog: Catalog,
  stage: string,
  grade: string,
  unitOrTopic: string,
  lessonTitle: string,
): { domain: string; topic: string; unit: string; lesson: string } | null {
  const stageNode = catalog.stages.find((s) => s.stage === stage);
  const gradeNode = stageNode?.grades.find((g) => g.grade === grade);
  if (!gradeNode) return null;

  for (const d of gradeNode.domains) {
    for (const t of d.topics) {
      const unitMatch = t.unit === unitOrTopic || t.name === unitOrTopic;
      const lessonMatch = t.lessons.includes(lessonTitle);
      if (lessonMatch || unitMatch) {
        return {
          domain: d.name,
          topic: t.name,
          unit: t.unit || t.name,
          lesson: lessonMatch ? lessonTitle : t.lessons[0] || "",
        };
      }
    }
  }

  const firstDomain = gradeNode.domains[0];
  const firstTopic = firstDomain?.topics[0];
  if (!firstTopic) return null;
  return {
    domain: firstDomain.name,
    topic: firstTopic.name,
    unit: firstTopic.unit || firstTopic.name,
    lesson: firstTopic.lessons[0] || "",
  };
}

export function LessonForm({ loading, onSubmit }: Props) {
  const [form] = Form.useForm<LessonInput>();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [customMode, setCustomMode] = useState(false);
  const [domain, setDomain] = useState("");
  const [topic, setTopic] = useState("");

  const stage = Form.useWatch("stage", form) || initial.stage;
  const grade = Form.useWatch("grade", form) || initial.grade;
  const lessonTitle = Form.useWatch("lesson_title", form);

  useEffect(() => {
    let cancelled = false;
    fetchCatalog().then((data) => {
      if (cancelled) return;
      setCatalog(data);
      const path = findTopicPath(
        data,
        form.getFieldValue("stage") || initial.stage,
        form.getFieldValue("grade") || initial.grade,
        form.getFieldValue("unit") || initial.unit,
        form.getFieldValue("lesson_title") || initial.lesson_title,
      );
      form.setFieldsValue({
        subject: data.subject || "数学",
        curriculum_year: data.curriculum_year || "2022",
      });
      if (path) {
        setDomain(path.domain);
        setTopic(path.topic);
        form.setFieldsValue({
          unit: path.unit,
          lesson_title: path.lesson,
        });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [form]);

  const stageNode = useMemo(
    () => catalog?.stages.find((s) => s.stage === stage),
    [catalog, stage],
  );

  const gradeNode: CatalogGrade | undefined = useMemo(
    () => stageNode?.grades.find((g) => g.grade === grade),
    [stageNode, grade],
  );

  const domainNode: CatalogDomain | undefined = useMemo(
    () => gradeNode?.domains.find((d) => d.name === domain),
    [gradeNode, domain],
  );

  const topicNode: CatalogTopic | undefined = useMemo(
    () => domainNode?.topics.find((t) => t.name === topic),
    [domainNode, topic],
  );

  const gradeOptions = (stageNode?.grades || []).map((g) => ({
    value: g.grade,
    label: g.grade,
  }));

  const domainOptions = (gradeNode?.domains || []).map((d) => ({
    value: d.name,
    label: d.name,
  }));

  const topicOptions = (domainNode?.topics || []).map((t) => ({
    value: t.name,
    label: t.name,
  }));

  const lessonOptions = useMemo(
    () => [
      ...(topicNode?.lessons || []).map((l) => ({ value: l, label: l })),
      { value: CUSTOM, label: "自定义课时…" },
    ],
    [topicNode],
  );

  const textbookOptions = (catalog?.textbook_versions || ["人教版"]).map((v) => ({
    value: v,
    label: v,
  }));

  const applyTopic = (nextTopic: string, nextDomain?: CatalogDomain) => {
    const dom = nextDomain || domainNode;
    const node = dom?.topics.find((t) => t.name === nextTopic);
    setTopic(nextTopic);
    if (!node) return;
    form.setFieldsValue({
      unit: node.unit || node.name,
      lesson_title: node.lessons[0] || "",
    });
    setCustomMode(false);
  };

  const onStageChange = (nextStage: string) => {
    const next = catalog?.stages.find((s) => s.stage === nextStage);
    const nextGrade = next?.grades[0]?.grade || "";
    const nextDomain = next?.grades[0]?.domains[0];
    const nextTopic = nextDomain?.topics[0]?.name || "";
    form.setFieldsValue({ stage: nextStage, grade: nextGrade });
    setDomain(nextDomain?.name || "");
    if (nextTopic) applyTopic(nextTopic, nextDomain);
  };

  const onGradeChange = (nextGrade: string) => {
    const g = stageNode?.grades.find((x) => x.grade === nextGrade);
    const nextDomain = g?.domains[0];
    const nextTopic = nextDomain?.topics[0]?.name || "";
    form.setFieldsValue({ grade: nextGrade });
    setDomain(nextDomain?.name || "");
    if (nextTopic) applyTopic(nextTopic, nextDomain);
  };

  const onDomainChange = (nextDomainName: string) => {
    const d = gradeNode?.domains.find((x) => x.name === nextDomainName);
    const nextTopic = d?.topics[0]?.name || "";
    setDomain(nextDomainName);
    if (nextTopic) applyTopic(nextTopic, d);
  };

  const onLessonChange = (value: string) => {
    if (value === CUSTOM) {
      setCustomMode(true);
      form.setFieldsValue({ lesson_title: "" });
      return;
    }
    setCustomMode(false);
    form.setFieldsValue({
      lesson_title: value,
      unit: topicNode?.unit || topicNode?.name || form.getFieldValue("unit"),
    });
  };

  const lessonInList = !!(lessonTitle && topicNode?.lessons.includes(lessonTitle));
  const selectValue = customMode ? CUSTOM : lessonInList ? lessonTitle : undefined;

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={initial}
      onFinish={onSubmit}
      requiredMark="optional"
      className="lesson-form"
    >
      <div className="panel-kicker">备课输入</div>
      <h2 className="panel-title">本课时信息</h2>

      <Row gutter={12}>
        <Col span={12}>
          <Form.Item name="stage" label="学段" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "初中", label: "初中" },
                { value: "小学", label: "小学（目录建设中）", disabled: true },
                { value: "高中", label: "高中（目录建设中）", disabled: true },
              ]}
              onChange={onStageChange}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="grade" label="年级" rules={[{ required: true }]}>
            <Select
              options={gradeOptions}
              onChange={onGradeChange}
              placeholder={catalog ? "选择年级" : "目录加载中…"}
            />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={12}>
        <Col span={12}>
          <Form.Item name="subject" label="学科" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "数学", label: "数学" },
                { value: "语文", label: "语文（目录建设中）", disabled: true },
                { value: "英语", label: "英语（目录建设中）", disabled: true },
              ]}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="textbook_version" label="教材版本" rules={[{ required: true }]}>
            <Select options={textbookOptions} showSearch />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item label="学习领域">
        <Select
          value={domain || undefined}
          options={domainOptions}
          onChange={onDomainChange}
          placeholder={catalog ? "数与代数 / 图形与几何…" : "目录加载中…"}
        />
      </Form.Item>

      <Form.Item label="主题 / 单元">
        <Select
          value={topic || undefined}
          options={topicOptions}
          onChange={(v) => applyTopic(v)}
          placeholder={catalog ? "选择主题" : "目录加载中…"}
          showSearch
        />
      </Form.Item>

      <Form.Item label="课时课题" required>
        <Select
          value={selectValue}
          options={lessonOptions}
          onChange={onLessonChange}
          showSearch
          optionFilterProp="label"
          placeholder={topicNode ? "从目录选择课时" : "请先选择主题"}
          disabled={customMode || !topicNode}
        />
      </Form.Item>

      {customMode && (
        <>
          <Form.Item
            name="lesson_title"
            label="自定义课题"
            rules={[{ required: true, message: "请输入课时课题" }]}
          >
            <Input placeholder="输入自定义课时名称" />
          </Form.Item>
          <Form.Item
            name="unit"
            label="自定义单元"
            rules={[{ required: true, message: "请输入单元名" }]}
          >
            <Input placeholder="如：校本拓展单元" />
          </Form.Item>
        </>
      )}

      {!customMode && (
        <Form.Item name="lesson_title" hidden rules={[{ required: true }]}>
          <Input />
        </Form.Item>
      )}
      {!customMode && (
        <Form.Item name="unit" hidden>
          <Input />
        </Form.Item>
      )}

      <div className="custom-toggle">
        <span>手动自定义课题 / 单元</span>
        <Switch
          checked={customMode}
          onChange={(checked) => {
            setCustomMode(checked);
            if (checked) {
              form.setFieldsValue({ lesson_title: "", unit: topicNode?.unit || topic || "" });
            } else if (topicNode?.lessons[0]) {
              form.setFieldsValue({
                lesson_title: topicNode.lessons[0],
                unit: topicNode.unit || topicNode.name,
              });
            }
          }}
        />
      </div>

      <Row gutter={12}>
        <Col span={12}>
          <Form.Item name="duration_minutes" label="课时（分钟）">
            <InputNumber min={20} max={90} style={{ width: "100%" }} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="curriculum_year" label="课标年份">
            <Input disabled />
          </Form.Item>
        </Col>
      </Row>

      <div className="form-section-label">学情卡片（可选）</div>

      <Form.Item name={["learning_profile", "class_level"]} label="班级水平">
        <Select
          options={[
            { value: "weak", label: "偏弱" },
            { value: "average", label: "中等" },
            { value: "strong", label: "较好" },
          ]}
        />
      </Form.Item>

      <Form.Item name={["learning_profile", "focus"]} label="本节侧重">
        <Select
          options={[
            { value: "foundation", label: "夯实基础" },
            { value: "key_points", label: "突破重难点" },
            { value: "extension", label: "拓展提升" },
          ]}
        />
      </Form.Item>

      <Form.Item name={["learning_profile", "prior_knowledge"]} label="已学相关内容">
        <Input.TextArea rows={2} />
      </Form.Item>

      <Form.Item name={["learning_profile", "known_pain_points"]} label="已知易错点">
        <Input.TextArea rows={2} />
      </Form.Item>

      <Form.Item name="extra_notes" label="补充要求">
        <Input.TextArea rows={2} />
      </Form.Item>

      <Space style={{ width: "100%" }} direction="vertical">
        <Button type="primary" htmlType="submit" loading={loading} block size="large">
          开始智能备课
        </Button>
      </Space>
    </Form>
  );
}
