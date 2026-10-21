import type { AttemptAnswerOut, QuestionOut } from "../../lib/types";
import { PrintMarkdown } from "./PrintMarkdown";

const questionTypeLabels: Record<QuestionOut["qtype"], string> = {
  single_choice: "单选题",
  multi_choice: "多选题",
  true_false: "判断题",
  cloze: "填空题",
  short_answer: "简答题",
};

type PublicPrintQuestion = Pick<QuestionOut, "id" | "seq" | "qtype" | "stem_md" | "options">;

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "0";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function optionText(question: QuestionOut, keys: string[]): string {
  return keys
    .map((key) => {
      const option = question.options.find((item) => item.key === key);
      return option ? `${key}. ${option.text}` : key;
    })
    .join("；");
}

function mineText(question: QuestionOut, answer: AttemptAnswerOut | null | undefined): string {
  if (!answer) return "（未作答）";
  if (question.qtype === "single_choice" || question.qtype === "multi_choice") {
    return optionText(question, strings(answer.answer.selected)) || "（未作答）";
  }
  if (question.qtype === "true_false") {
    return typeof answer.answer.selected_bool === "boolean"
      ? answer.answer.selected_bool ? "正确" : "错误"
      : "（未作答）";
  }
  return typeof answer.answer.text === "string" && answer.answer.text.trim()
    ? answer.answer.text
    : "（未作答）";
}

function correctText(question: QuestionOut): string {
  if (question.qtype === "single_choice" || question.qtype === "multi_choice") {
    return optionText(question, strings(question.answer?.correct_options)) || "—";
  }
  if (question.qtype === "true_false") {
    return typeof question.answer?.correct_bool === "boolean"
      ? question.answer.correct_bool ? "正确" : "错误"
      : "—";
  }
  if (question.qtype === "cloze") {
    return strings(question.answer?.accept_texts).join(" / ") || "—";
  }
  return typeof question.answer?.rubric === "string" ? question.answer.rubric : "—";
}

export function BlankPrintQuestion({ question }: { question: PublicPrintQuestion }) {
  return (
    <section className={`print-question print-question-${question.qtype}`}>
      <div className="print-question-heading">
        <strong>第 {question.seq} 题</strong>
        <span>{questionTypeLabels[question.qtype]}</span>
      </div>
      <PrintMarkdown content={question.stem_md} />
      {(question.qtype === "single_choice" || question.qtype === "multi_choice") && (
        <div className="print-options">
          {question.options.map((option) => (
            <div key={option.key}><strong>{option.key}.</strong><span>{option.text}</span></div>
          ))}
        </div>
      )}
      {question.qtype === "true_false" && <div className="print-judgement">（　　）</div>}
      {question.qtype === "cloze" && <div className="print-answer-lines print-answer-lines-cloze" />}
      {question.qtype === "short_answer" && <div className="print-answer-lines print-answer-lines-short" />}
    </section>
  );
}

export function GradedPrintQuestion({
  question,
  answer,
}: {
  question: QuestionOut;
  answer: AttemptAnswerOut | null | undefined;
}) {
  const wrong = answer?.is_correct === false || (
    answer?.score !== null && answer?.score !== undefined && answer.score < 1
  );
  const sources = question.source_refs?.map((source) => source.wiki_slug).filter(Boolean) ?? [];

  return (
    <section className={`print-question print-graded-question ${wrong ? "print-question-wrong" : ""}`}>
      <div className="print-question-heading">
        <div>
          <strong>{wrong ? "✗ " : ""}第 {question.seq} 题</strong>
          <span>{questionTypeLabels[question.qtype]}</span>
        </div>
        <strong>{formatScore(answer?.score)}/1 分</strong>
      </div>
      <PrintMarkdown content={question.stem_md} />
      <div className="print-answer-comparison">
        <div>
          <span>我的答案</span>
          <p>{mineText(question, answer)}</p>
        </div>
        <div>
          <span>{question.qtype === "short_answer" ? "评分要点" : "正确答案"}</span>
          <p>{correctText(question)}</p>
        </div>
      </div>
      {answer?.feedback_md && (
        <div className="print-feedback">
          <strong>AI 反馈</strong>
          <PrintMarkdown content={answer.feedback_md} />
        </div>
      )}
      {question.explanation_md && (
        <div className="print-explanation">
          <strong>题目解析</strong>
          <PrintMarkdown content={question.explanation_md} />
        </div>
      )}
      {sources.length > 0 && <div className="print-sources">来源：{sources.join("、")}</div>}
    </section>
  );
}
