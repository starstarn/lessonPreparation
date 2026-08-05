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
  slides?: Record<string, unknown>;
  blackboard?: Record<string, unknown>;
  retrieved_context?: string;
};
