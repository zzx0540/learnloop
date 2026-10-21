import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { PrintLayout } from "../../components/print/PrintLayout";
import { BlankPrintQuestion, GradedPrintQuestion } from "../../components/print/PrintQuestions";
import { api } from "../../lib/api";
import type { AttemptDetailOut, CourseOut, QuizOut } from "../../lib/types";

const kindLabels: Record<QuizOut["kind"], string> = {
  daily: "每日试卷",
  stage: "阶段试卷",
  formal_exam: "正式考试",
};

function score(value: number | null | undefined): string {
  if (value === null || value === undefined) return "0";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export default function QuizPrintPage() {
  const { courseId, quizId } = useParams();
  const [searchParams] = useSearchParams();
  const mode = searchParams.get("mode") === "graded" ? "graded" : "blank";
  const courseQuery = useQuery<CourseOut>({
    queryKey: ["course", courseId],
    queryFn: () => api.get(`/api/v1/courses/${courseId}`),
    enabled: Boolean(courseId),
  });
  const quizQuery = useQuery<QuizOut>({
    queryKey: ["quiz", quizId],
    queryFn: () => api.get(`/api/v1/quizzes/${quizId}`),
    enabled: Boolean(quizId),
  });
  const quiz = quizQuery.data;
  const attemptId = quiz?.my_attempt?.id;
  const attemptQuery = useQuery<AttemptDetailOut>({
    queryKey: ["attempt-detail", attemptId],
    queryFn: () => api.get(`/api/v1/attempts/${attemptId}`),
    enabled: mode === "graded" && Boolean(attemptId),
  });
  const attempt = attemptQuery.data;
  const answers = useMemo(
    () => new Map((attempt?.answers ?? []).map((answer) => [answer.question_id, answer])),
    [attempt?.answers],
  );
  const hasPrivateQuestionFields = quiz?.questions.some((question) =>
    "answer" in question || "explanation_md" in question || "my_answer" in question || "source_refs" in question,
  ) ?? false;
  const blankAllowed = mode === "blank" && quiz?.kind !== "formal_exam" && (
    !quiz?.my_attempt || quiz.my_attempt.status === "in_progress"
  ) && !hasPrivateQuestionFields;
  const gradedAllowed = mode === "graded" && quiz?.my_attempt?.status === "graded" && Boolean(attempt);
  const loading = courseQuery.isPending || quizQuery.isPending || (mode === "graded" && attemptQuery.isPending);
  const failed = courseQuery.isError || quizQuery.isError || (mode === "graded" && attemptQuery.isError);
  const canPrint = !loading && !failed && (blankAllowed || gradedAllowed);
  const minutes = quiz?.time_limit_seconds ? Math.ceil(quiz.time_limit_seconds / 60) : null;
  const backTo = quiz?.kind === "formal_exam"
    ? `/courses/${courseId}/exam/${quizId}`
    : `/courses/${courseId}/quiz/${quizId}`;

  let unavailable = "";
  if (!loading && !failed && quiz) {
    if (mode === "blank" && quiz.kind === "formal_exam") unavailable = "正式考试不提供空白卷打印。";
    else if (mode === "blank" && hasPrivateQuestionFields) unavailable = "试卷数据不符合公开 schema，已禁止打印。";
    else if (mode === "blank" && !blankAllowed) unavailable = "该试卷已交卷，不能再打印空白卷。";
    else if (mode === "graded" && quiz.my_attempt?.status !== "graded") unavailable = "试卷尚未完成批阅，暂不可打印。";
  }

  return (
    <PrintLayout
      courseName={courseQuery.data?.name ?? "加载中…"}
      templateName={mode === "graded" ? "已批阅试卷" : "空白试卷"}
      date={quiz?.scheduled_date ?? new Date().toISOString().slice(0, 10)}
      backTo={backTo}
      canPrint={canPrint}
      headerExtra={mode === "blank" ? (
        <div className="print-header-fields">
          <span>用时：____________</span>
          <span>得分：____________</span>
        </div>
      ) : undefined}
    >
      {loading ? (
        <div className="print-empty-state">正在加载试卷…</div>
      ) : failed || !quiz ? (
        <div className="print-empty-state">试卷加载失败，请返回后重试。</div>
      ) : unavailable ? (
        <div className="print-empty-state">{unavailable}</div>
      ) : (
        <>
          <section className="print-quiz-heading">
            <div>
              <h2>{quiz.title}</h2>
              <div className="print-quiz-meta">
                <span>{kindLabels[quiz.kind]}</span>
                <span>共 {quiz.questions.length} 题</span>
                {minutes && <span>建议时长 {minutes} 分钟</span>}
              </div>
            </div>
            {mode === "graded" && (
              <div className="print-total-score">
                {score(attempt?.total_score ?? quiz.my_attempt?.total_score)}
                <span>/{score(attempt?.max_score ?? quiz.my_attempt?.max_score)}</span>
              </div>
            )}
          </section>
          {mode === "blank"
            ? quiz.questions.map((question) => <BlankPrintQuestion key={question.id} question={question} />)
            : quiz.questions.map((question) => (
              <GradedPrintQuestion
                key={question.id}
                question={question}
                answer={answers.get(question.id) ?? question.my_answer}
              />
            ))}
        </>
      )}
    </PrintLayout>
  );
}
