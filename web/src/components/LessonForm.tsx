import { Button, Col, Form, Input, InputNumber, Row, Select, Space } from "antd";
import type { LessonInput } from "./types";

type Props = {
  loading: boolean;
  onSubmit: (values: LessonInput) => void;
};

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

export function LessonForm({ loading, onSubmit }: Props) {
  const [form] = Form.useForm<LessonInput>();

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
                { value: "小学", label: "小学" },
                { value: "初中", label: "初中" },
                { value: "高中", label: "高中" },
              ]}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="grade" label="年级" rules={[{ required: true }]}>
            <Input placeholder="如：七年级" />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={12}>
        <Col span={12}>
          <Form.Item name="subject" label="学科" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="textbook_version" label="教材版本" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item name="unit" label="单元">
        <Input placeholder="如：有理数" />
      </Form.Item>

      <Form.Item name="lesson_title" label="课时课题" rules={[{ required: true }]}>
        <Input placeholder="如：有理数的加法" />
      </Form.Item>

      <Row gutter={12}>
        <Col span={12}>
          <Form.Item name="duration_minutes" label="课时（分钟）">
            <InputNumber min={20} max={90} style={{ width: "100%" }} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="curriculum_year" label="课标年份">
            <Input />
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
