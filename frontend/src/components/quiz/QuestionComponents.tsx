import { CheckCircle2, ExternalLink, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

import type { AttemptAnswerOut, QuestionOut } from "../../lib/types";
import { Markdown } from "../Markdown";
import { Card } from "../ui";

export type AnswerDraft = { selected: string[] } | { selected_bool: boolean } | { text: string };
export type SaveState = { status: "idle" | "saving" | "saved" | "error"; message?: string };

const questionTypeLabels: Record<QuestionOut["qtype"], string> = {
  single_choice: "单选题",
  multi_choice: "多选题",
  true_false: "判断题",
  cloze: "填空题",
  short_answer: "简答题",
};

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "0";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function AutoTextarea({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.max(112, element.scrollHeight)}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      value={value}
      disabled={disabled}
      rows={4}
      placeholder="按要点作答，将由 AI 批改"
      className="w-full resize-none overflow-hidden rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm leading-6 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function SaveIndicator({ state, onRetry }: { state?: SaveState; onRetry: () => void }) {
  if (!state || state.status === "idle") return null;
  if (state.status === "saving") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-400">
        <Loader2 className="h-3 w-3 animate-spin" />保存中…
      </span>
    );
  }
  if (state.status === "saved") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
        <CheckCircle2 className="h-3 w-3" />已保存
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1 text-xs text-red-600">
      {state.message || "保存失败"}
      <button className="font-medium underline" onClick={onRetry}>重试</button>
    </span>
  );
}

export function AnswerQuestion({
  question,
  draft,
  saveState,
  onChange,
  onRetry,
  disabled,
}: {
  question: QuestionOut;
  draft: AnswerDraft | undefined;
  saveState?: SaveState;
  onChange: (draft: AnswerDraft) => void;
  onRetry: () => void;
  disabled?: boolean;
}) {
  const selected = draft && "selected" in draft ? draft.selected : [];
  const text = draft && "text" in draft ? draft.text : "";

  return (
    <Card className="min-w-0 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700">
          第 {question.seq} 题 · {questionTypeLabels[question.qtype]}
        </span>
        <SaveIndicator state={saveState} onRetry={onRetry} />
      </div>
      <div className="mt-4 min-w-0 text-slate-900">
        <Markdown content={question.stem_md} />
      </div>

      {(question.qtype === "single_choice" || question.qtype === "multi_choice") && (
        <div className="mt-5 space-y-2">
          {question.options.map((option) => {
            const checked = selected.includes(option.key);
            return (
              <label
                key={option.key}
                className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-3 text-sm transition-colors ${
                  checked ? "border-indigo-400 bg-indigo-50 text-indigo-900" : "border-slate-200 hover:bg-slate-50"
                }`}
              >
                <input
                  type={question.qtype === "single_choice" ? "radio" : "checkbox"}
                  name={`question-${question.id}`}
                  checked={checked}
                  disabled={disabled}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-indigo-600"
                  onChange={() => {
                    if (question.qtype === "single_choice") {
                      onChange({ selected: [option.key] });
                    } else {
                      onChange({
                        selected: checked
                          ? selected.filter((key) => key !== option.key)
                          : [...selected, option.key],
                      });
                    }
                  }}
                />
                <span className="font-medium text-slate-500">{option.key}</span>
                <span className="min-w-0">{option.text}</span>
              </label>
            );
          })}
        </div>
      )}

      {question.qtype === "true_false" && (
        <div className="mt-5 grid grid-cols-2 gap-3">
          {[
            { value: true, label: "正确" },
            { value: false, label: "错误" },
          ].map((option) => {
            const checked = draft && "selected_bool" in draft && draft.selected_bool === option.value;
            return (
              <button
                key={String(option.value)}
                disabled={disabled}
                className={`min-h-14 rounded-xl border px-3 text-base font-semibold transition-colors ${
                  checked
                    ? "border-indigo-500 bg-indigo-600 text-white"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                }`}
                onClick={() => onChange({ selected_bool: option.value })}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      )}

      {question.qtype === "cloze" && (
        <input
          value={text}
          disabled={disabled}
          placeholder="请输入答案"
          className="mt-5 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100"
          onChange={(event) => onChange({ text: event.target.value })}
        />
      )}

      {question.qtype === "short_answer" && (
        <div className="mt-5">
          <AutoTextarea value={text} disabled={disabled} onChange={(value) => onChange({ text: value })} />
        </div>
      )}
    </Card>
  );
}

function selectedKeys(answer: AttemptAnswerOut | null | undefined): string[] {
  const value = answer?.answer.selected;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function ResultQuestion({
  question,
  answer,
  onWikiLink,
}: {
  question: QuestionOut;
  answer: AttemptAnswerOut | null | undefined;
  onWikiLink: (slug: string) => void;
}) {
  const wrong = answer?.score !== null && answer?.score !== undefined && answer.score < 1;
  const correctOptions = Array.isArray(question.answer?.correct_options)
    ? question.answer.correct_options.filter((item): item is string => typeof item === "string")
    : [];
  const mine = selectedKeys(answer);
  const textAnswer = typeof answer?.answer.text === "string" ? answer.answer.text : "";
  const reference = typeof question.answer?.reference_md === "string" ? question.answer.reference_md : "";
  const rubric = typeof question.answer?.rubric === "string" ? question.answer.rubric : "";
  const accepted = Array.isArray(question.answer?.accept_texts)
    ? question.answer.accept_texts.filter((item): item is string => typeof item === "string")
    : [];

  return (
    <Card className={`min-w-0 p-4 sm:p-6 ${wrong ? "border-l-4 border-l-red-500" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
          第 {question.seq} 题 · {questionTypeLabels[question.qtype]}
        </span>
        {answer?.graded_by === "pending" ? (
          <span className="inline-flex items-center gap-1 text-xs text-amber-600">
            <Loader2 className="h-3 w-3 animate-spin" />AI 批改中
          </span>
        ) : answer?.score !== null && answer?.score !== undefined ? (
          <span className={`text-sm font-semibold ${wrong ? "text-red-600" : "text-emerald-600"}`}>
            {formatScore(answer.score)}/1 分
          </span>
        ) : null}
      </div>
      <div className="mt-4 min-w-0 text-slate-900">
        <Markdown content={question.stem_md} onWikiLink={onWikiLink} />
      </div>

      {(question.qtype === "single_choice" || question.qtype === "multi_choice") && (
        <div className="mt-5 space-y-2">
          {question.options.map((option) => {
            const isCorrect = correctOptions.includes(option.key);
            const isMine = mine.includes(option.key);
            const className = isCorrect
              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
              : isMine
                ? "border-red-300 bg-red-50 text-red-800"
                : "border-slate-200 bg-white text-slate-500";
            return (
              <div key={option.key} className={`flex items-start gap-3 rounded-xl border px-3 py-3 text-sm ${className}`}>
                <span className="font-semibold">{option.key}</span>
                <span className="min-w-0 flex-1">{option.text}</span>
                {isCorrect && <span className="shrink-0 text-xs font-medium">正确答案</span>}
                {isMine && !isCorrect && <span className="shrink-0 text-xs font-medium">我的选择</span>}
              </div>
            );
          })}
        </div>
      )}

      {question.qtype === "true_false" && (
        <div className="mt-5 grid grid-cols-2 gap-3 text-center text-sm font-semibold">
          {[true, false].map((value) => {
            const isCorrect = question.answer?.correct_bool === value;
            const isMine = answer?.answer.selected_bool === value;
            return (
              <div
                key={String(value)}
                className={`rounded-xl border px-3 py-4 ${
                  isCorrect
                    ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                    : isMine
                      ? "border-red-300 bg-red-50 text-red-700"
                      : "border-slate-200 text-slate-400"
                }`}
              >
                {value ? "正确" : "错误"}
                {isCorrect && <span className="mt-1 block text-[11px]">正确答案</span>}
                {isMine && !isCorrect && <span className="mt-1 block text-[11px]">我的选择</span>}
              </div>
            );
          })}
        </div>
      )}

      {(question.qtype === "cloze" || question.qtype === "short_answer") && (
        <div className="mt-5 grid min-w-0 gap-3 sm:grid-cols-2">
          <div className={`min-w-0 rounded-xl p-3 ${wrong ? "bg-red-50" : "bg-slate-50"}`}>
            <div className="text-xs font-medium text-slate-400">我的答案</div>
            <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-700">
              {textAnswer || "（未作答）"}
            </div>
          </div>
          {question.qtype === "cloze" ? (
            <div className="min-w-0 rounded-xl bg-emerald-50 p-3">
              <div className="text-xs font-medium text-emerald-600">正确答案</div>
              <div className="mt-2 break-words text-sm leading-6 text-emerald-900">
                {accepted.join(" / ") || "—"}
              </div>
            </div>
          ) : (
            <div className="min-w-0 rounded-xl bg-indigo-50 p-3">
              <div className="text-xs font-medium text-indigo-600">评分要点</div>
              <div className="mt-2 text-sm leading-6 text-indigo-950">{rubric || "—"}</div>
            </div>
          )}
        </div>
      )}

      {answer?.feedback_md && (
        <div className="mt-4 rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
          <div className="mb-2 text-xs font-medium text-indigo-600">AI 反馈</div>
          <Markdown content={answer.feedback_md} onWikiLink={onWikiLink} />
        </div>
      )}

      {question.explanation_md && (
        <div className="mt-4 rounded-xl bg-slate-50 p-4">
          <div className="mb-2 text-xs font-medium text-slate-500">题目解析</div>
          <Markdown content={question.explanation_md} onWikiLink={onWikiLink} />
        </div>
      )}

      {reference && (
        <details className="mt-4 rounded-xl border border-slate-200 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-600">查看参考答案</summary>
          <div className="mt-3"><Markdown content={reference} onWikiLink={onWikiLink} /></div>
        </details>
      )}

      {(question.source_refs?.length ?? 0) > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400">来源</span>
          {question.source_refs!.map((ref) => (
            <button
              key={ref.wiki_slug}
              className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-1 text-xs text-indigo-700 hover:bg-indigo-100"
              onClick={() => onWikiLink(ref.wiki_slug)}
            >
              <ExternalLink className="h-3 w-3" />{ref.wiki_slug}
            </button>
          ))}
        </div>
      )}
    </Card>
  );
}
