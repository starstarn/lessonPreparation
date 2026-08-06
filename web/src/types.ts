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

export type RunStatus = "pending" | "running" | "done" | "error";

export type RunJob = {
  id: string;
  status: RunStatus;
  step: string;
  step_label: string;
  message: string;
  input: LessonInput;
  result: PrepResult | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type PrepResult = {
  input?: LessonInput;
  curriculum_analysis?: Record<string, unknown>;
  lesson_plan?: Record<string, unknown>;
  exercise_paper?: Record<string, unknown>;
  slides?: Record<string, unknown>;
  blackboard?: Record<string, unknown>;
  retrieved_context?: string;
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
