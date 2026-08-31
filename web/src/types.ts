export type LearningProfile = {
  class_level: "weak" | "average" | "strong";
  prior_knowledge: string;
  known_pain_points: string;
  focus: "foundation" | "key_points" | "extension";
};

export type LessonInput = {
  stage: string;
  subject: string;
  textbook_version: string;
  grade: string;
  unit: string;
  lesson_title: string;
  duration_minutes: number;
  curriculum_year: string;
  extra_notes: string;
  learning_profile: LearningProfile;
  /** 场景模板 id，如 full / plan_only / homework */
  agent_profile?: string;
  /** 自定义勾选的 Agent id；非空时优先生效 */
  enabled_agents?: string[] | null;
};

export type AgentPluginInfo = {
  id: string;
  label: string;
  description: string;
  phase: "upstream" | "core" | "material" | "qa";
  depends_on: string[];
  parallel: boolean;
  user_toggleable: boolean;
};

export type AgentProfileInfo = {
  id: string;
  name: string;
  description: string;
  agents: string[];
  order: number;
};

export type AgentPlan = {
  profile_id: string;
  agents: string[];
  material_agents?: string[];
  run_curriculum?: boolean;
  run_lesson_plan?: boolean;
  run_lesson_review?: boolean;
  run_materials?: boolean;
  run_consistency?: boolean;
};

export type CatalogTopic = {
  name: string;
  unit: string;
  lessons: string[];
};

export type CatalogDomain = {
  name: string;
  topics: CatalogTopic[];
};

export type CatalogGrade = {
  grade: string;
  domains: CatalogDomain[];
};

export type CatalogStage = {
  stage: string;
  grades: CatalogGrade[];
};

export type Catalog = {
  subject: string;
  curriculum_year: string;
  textbook_versions: string[];
  stages: CatalogStage[];
};

export type RunStatus = "pending" | "running" | "done" | "error" | "awaiting_confirmation";

export type PipelineStep =
  | "curriculum"
  | "lesson_plan"
  | "materials"
  | "consistency"
  | "exercises"
  | "slides"
  | "blackboard";

export type LaneStatus = "pending" | "running" | "done" | "error";

export type ParallelLane = {
  status: LaneStatus;
  label?: string;
  error?: string;
};

export type RunJob = {
  id: string;
  status: RunStatus;
  step: string;
  step_label: string;
  message: string;
  input: LessonInput;
  result: PrepResult | null;
  error: string | null;
  failed_step?: string | null;
  parallel_lanes?: Record<string, ParallelLane> | null;
  created_at: string;
  updated_at: string;
};

export type PrepResult = {
  input?: LessonInput;
  agent_plan?: AgentPlan;
  curriculum_analysis?: Record<string, unknown>;
  lesson_plan?: Record<string, unknown>;
  lesson_plan_qa?: {
    passed?: boolean;
    issues?: string[];
    suggested_fixes?: string[];
    revised?: boolean;
    notes?: string;
  };
  exercise_paper?: Record<string, unknown>;
  exercise_qa?: {
    passed?: boolean;
    issues?: string[];
    suggested_fixes?: string[];
    revised?: boolean;
    notes?: string;
  };
  slides?: Record<string, unknown>;
  slides_qa?: {
    passed?: boolean;
    issues?: string[];
    suggested_fixes?: string[];
    revised?: boolean;
    notes?: string;
  };
  blackboard?: Record<string, unknown>;
  consistency_qa?: {
    passed?: boolean;
    issues?: string[];
    suggested_fixes?: string[];
    conflict_modules?: string[];
    revised?: boolean;
    notes?: string;
  };
  materials_lanes?: Record<string, ParallelLane>;
  materials_failed?: string[];
  awaiting_plan_confirm?: boolean;
  retrieved_context?: string;
  errors?: string[];
};

export type ExportModule = "all" | "curriculum" | "plan" | "exercises" | "slides" | "board";

export type VersionItem = {
  id: string;
  title: string;
  note: string;
  created_at?: string;
  updated_at?: string;
  lesson_title?: string;
  grade?: string;
};

export type VersionDetail = VersionItem & {
  input: LessonInput | Record<string, unknown>;
  result: PrepResult;
};
