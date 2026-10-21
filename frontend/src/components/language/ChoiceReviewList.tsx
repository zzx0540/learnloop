import { CheckCircle2, XCircle } from "lucide-react";

import type { ChoiceReviewOut } from "../../lib/types";
import { Markdown } from "../Markdown";
import { Card } from "../ui";

interface ReviewQuestion {
  id: string;
  label: string;
  options: string[];
  meta?: string;
}

export function ChoiceReviewList({
  title,
  questions,
  reviews,
}: {
  title: string;
  questions: ReviewQuestion[];
  reviews: ChoiceReviewOut[];
}) {
  const reviewById = new Map(reviews.map((review) => [review.item_id, review]));

  return (
    <Card>
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      <div className="mt-5 space-y-5">
        {questions.map((question, index) => {
          const review = reviewById.get(question.id);
          if (!review) return null;
          return (
            <section key={question.id} className="rounded-xl border border-slate-200 p-4 sm:p-5">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="font-medium leading-6 text-slate-800">
                  {index + 1}. {question.label}
                  {question.meta && <span className="ml-2 text-xs font-normal text-slate-400">{question.meta}</span>}
                </div>
                <span className={`inline-flex w-fit shrink-0 items-center gap-1 text-sm font-semibold ${review.is_correct ? "text-emerald-600" : "text-red-600"}`}>
                  {review.is_correct ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                  {review.is_correct ? "回答正确" : "回答错误"}
                </span>
              </div>
              <div className="mt-3 grid gap-2">
                {question.options.map((option, optionIndex) => {
                  const isCorrect = review.correct_index === optionIndex;
                  const isMine = review.selected_index === optionIndex;
                  const className = isCorrect
                    ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                    : isMine
                      ? "border-red-300 bg-red-50 text-red-800"
                      : "border-slate-200 text-slate-500";
                  return (
                    <div key={optionIndex} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${className}`}>
                      <span className="min-w-0 flex-1">{option}</span>
                      {isCorrect && <span className="shrink-0 text-xs font-medium">正确答案</span>}
                      {isMine && !isCorrect && <span className="shrink-0 text-xs font-medium">我的选择</span>}
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 rounded-lg bg-slate-50 p-3">
                <div className="mb-1 text-xs font-medium text-slate-500">题目解析</div>
                <Markdown content={review.explanation_md} />
              </div>
            </section>
          );
        })}
      </div>
    </Card>
  );
}
