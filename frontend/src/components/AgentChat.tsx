import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookUp, Loader2, LockKeyhole, MessageCircle, Send } from "lucide-react";
import { FormEvent, KeyboardEvent, PointerEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../lib/api";
import type { ChatContext, ChatMessageOut, ChatOut } from "../lib/types";
import { Markdown } from "./Markdown";
import { Toast } from "./Toast";
import { Button } from "./ui";

type Agent = "planner" | "companion";
type ScopeClass = "in_syllabus" | "adjacent" | "off_topic";

interface DoneEvent {
  message_id?: string;
  scope_class?: ScopeClass;
  tangent_item_id?: string | null;
}

class LockedError extends Error {}
class RateLimitError extends Error {}

async function responseDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

async function streamAgentReply(
  chatId: string,
  content: string,
  handlers: {
    onScope: (scope: ScopeClass) => void;
    onDelta: (text: string) => void;
    onDone: (data: DoneEvent) => void;
  },
): Promise<void> {
  const response = await fetch(`/api/v1/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (response.status === 403) {
    throw new LockedError(await responseDetail(response, "正式考试进行中，聊天功能已锁定"));
  }
  if (response.status === 429) {
    throw new RateLimitError(await responseDetail(response, "提问太快了，休息一下再来"));
  }
  if (!response.ok) throw new Error(await responseDetail(response, "发送失败，请重试"));
  if (!response.body) throw new Error("发送失败，请重试");

  function handleFrame(frame: string) {
    const lines = frame.split(/\r?\n/);
    const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
    const data = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (event === "scope") {
      try {
        const parsed = JSON.parse(data) as { scope_class?: ScopeClass };
        if (parsed.scope_class) handlers.onScope(parsed.scope_class);
      } catch {
        return;
      }
    }
    if (event === "delta") handlers.onDelta(data);
    if (event === "done") {
      try {
        handlers.onDone(JSON.parse(data) as DoneEvent);
      } catch {
        handlers.onDone({});
      }
    }
    if (event === "error") throw new Error(data || "AI 回复中断，请重试");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    frames.filter(Boolean).forEach(handleFrame);
    if (done) {
      if (buffer.trim()) handleFrame(buffer);
      break;
    }
  }
}

function ScopeBadge({ scope }: { scope: string | null | undefined }) {
  if (scope === "adjacent") {
    return <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">拓展话题</span>;
  }
  if (scope === "off_topic") {
    return <span className="inline-flex rounded-full bg-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-600">超纲</span>;
  }
  return null;
}

function contextsMatch(actual: ChatContext | undefined, expected: ChatContext | undefined): boolean {
  return (
    (actual?.mode ?? "general") === (expected?.mode ?? "general") &&
    (actual?.plan_item_id ?? null) === (expected?.plan_item_id ?? null)
  );
}

export function AgentChat({
  courseId,
  agent,
  context,
  header,
  emptyHint,
  prefill,
  action,
}: {
  courseId: string;
  agent: Agent;
  context?: ChatContext;
  header: { title: string; subtitle: string };
  emptyHint: string;
  prefill?: string;
  action?: ReactNode;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [chatId, setChatId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pendingUser, setPendingUser] = useState("");
  const [streamText, setStreamText] = useState("");
  const [streamScope, setStreamScope] = useState<ScopeClass | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState("");
  const [lastSent, setLastSent] = useState("");
  const [toast, setToast] = useState("");
  const [actionMessageId, setActionMessageId] = useState<string | null>(null);
  const messageBox = useRef<HTMLDivElement>(null);
  const inputBox = useRef<HTMLTextAreaElement>(null);
  const longPressTimer = useRef<number | null>(null);
  const contextKey = `${context?.mode ?? "general"}:${context?.plan_item_id ?? ""}`;

  const { data: chats, isPending: chatsPending } = useQuery<ChatOut[]>({
    queryKey: ["agent-chats", courseId, agent],
    queryFn: () => api.get(`/api/v1/courses/${courseId}/chats?agent=${agent}`),
  });
  const reusableChat = useMemo(
    () =>
      agent === "planner"
        ? chats?.[0]
        : chats?.find((candidate) => contextsMatch(candidate.context, context)),
    [agent, chats, context],
  );
  const activeChatId = chatId ?? reusableChat?.id ?? null;
  const { data: messages } = useQuery<ChatMessageOut[]>({
    queryKey: ["chat-messages", activeChatId],
    queryFn: () => api.get(`/api/v1/chats/${activeChatId}/messages`),
    enabled: Boolean(activeChatId),
  });
  const visibleMessages = (messages ?? []).filter((message) => message.content_md.trim());

  useEffect(() => setChatId(null), [agent, courseId, contextKey]);

  useEffect(() => {
    if (prefill === undefined) return;
    setInput(prefill);
    window.requestAnimationFrame(() => inputBox.current?.focus());
  }, [prefill]);

  useEffect(() => {
    if (messageBox.current) messageBox.current.scrollTop = messageBox.current.scrollHeight;
  }, [messages, pendingUser, streamText, streamScope]);

  useEffect(
    () => () => {
      if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current);
    },
    [],
  );

  const backfill = useMutation({
    mutationFn: (messageId: string) => api.post<{ job_id: string }>(`/api/v1/chat-messages/${messageId}/backfill`),
    onSuccess: () => setToast("已提交回填任务"),
    onError: (e) => setToast(e instanceof ApiError ? e.message : "提交回填失败，请重试"),
  });

  async function ensureChat(): Promise<string> {
    if (activeChatId) return activeChatId;
    const chat = await api.post<ChatOut>(`/api/v1/courses/${courseId}/chats`, {
      agent,
      ...(agent === "companion" ? { context: context ?? { mode: "general" } } : {}),
    });
    setChatId(chat.id);
    queryClient.setQueryData<ChatOut[]>(["agent-chats", courseId, agent], (current = []) => [chat, ...current]);
    return chat.id;
  }

  async function send(content: string) {
    const text = content.trim();
    if (!text || isStreaming || chatsPending) return;
    setInput("");
    setPendingUser(text);
    setStreamText("");
    setStreamScope(null);
    setError("");
    setLocked("");
    setLastSent(text);
    setIsStreaming(true);
    let id: string | null = null;
    try {
      id = await ensureChat();
      await streamAgentReply(id, text, {
        onScope: setStreamScope,
        onDelta: (delta) => setStreamText((current) => current + delta),
        onDone: (data) => {
          if (data.tangent_item_id) {
            setToast("已加入拓展清单");
            void queryClient.invalidateQueries({ queryKey: ["tangents", courseId] });
          }
        },
      });
    } catch (e) {
      if (e instanceof LockedError) setLocked(e.message);
      else if (e instanceof RateLimitError) setToast(e.message);
      else setError(e instanceof Error ? e.message : "网络断开，请重试");
    } finally {
      if (id) await queryClient.invalidateQueries({ queryKey: ["chat-messages", id] });
      setPendingUser("");
      setStreamText("");
      setStreamScope(null);
      setIsStreaming(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(input);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(input);
    }
  }

  function startLongPress(event: PointerEvent, messageId: string) {
    if (event.pointerType === "mouse") return;
    if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current);
    longPressTimer.current = window.setTimeout(() => setActionMessageId(messageId), 550);
  }

  function cancelLongPress() {
    if (longPressTimer.current !== null) window.clearTimeout(longPressTimer.current);
    longPressTimer.current = null;
  }

  const wikiLink = (slug: string) => navigate(`/courses/${courseId}/wiki/${slug}`);

  return (
    <div className="overflow-hidden rounded-xl border border-indigo-100 bg-white">
      <div className="flex items-center justify-between gap-3 border-b border-indigo-100 bg-indigo-50/70 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <MessageCircle className="h-5 w-5 shrink-0 text-indigo-600" />
          <div className="min-w-0">
            <div className="text-sm font-semibold text-indigo-950">{header.title}</div>
            <div className="text-xs text-indigo-500">{header.subtitle}</div>
          </div>
        </div>
        {action}
      </div>

      <div
        ref={messageBox}
        className={`${agent === "planner" ? "max-h-[26rem] min-h-64" : "max-h-[34rem] min-h-72"} space-y-3 overflow-y-auto bg-slate-50/50 p-4`}
      >
        {visibleMessages.length === 0 && !pendingUser && (
          <div className="mx-auto max-w-md rounded-lg bg-white p-4 text-sm leading-6 text-slate-500 shadow-sm">
            {emptyHint}
          </div>
        )}
        {visibleMessages.map((message) => (
          <div
            key={message.id}
            className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
          >
            <div
              className={`group relative ${message.role === "user" ? "max-w-[88%]" : "max-w-[92%]"}`}
              onPointerDown={(event) => message.role === "assistant" && startLongPress(event, message.id)}
              onPointerUp={cancelLongPress}
              onPointerCancel={cancelLongPress}
            >
              <div
                className={
                  message.role === "user"
                    ? "rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2.5 text-sm text-white"
                    : "rounded-2xl rounded-bl-md bg-white px-4 py-2.5 shadow-sm"
                }
              >
                {message.role === "assistant" ? (
                  <Markdown content={message.content_md} onWikiLink={wikiLink} />
                ) : (
                  <>
                    <span className="whitespace-pre-wrap">{message.content_md}</span>
                    {message.scope_class && (
                      <div className="mt-2">
                        <ScopeBadge scope={message.scope_class} />
                      </div>
                    )}
                  </>
                )}
              </div>
              {agent === "companion" && message.role === "assistant" && (
                <div
                  className={`mt-1 flex justify-start transition-opacity ${
                    actionMessageId === message.id ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                  }`}
                >
                  <button
                    className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-500 shadow-sm hover:border-indigo-200 hover:text-indigo-600"
                    disabled={backfill.isPending}
                    onClick={() => backfill.mutate(message.id)}
                  >
                    {backfill.isPending && backfill.variables === message.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <BookUp className="h-3 w-3" />
                    )}
                    回填到 Wiki
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
        {pendingUser && (
          <div className="flex justify-end">
            <div className="max-w-[88%] rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2.5 text-sm text-white">
              {pendingUser}
            </div>
          </div>
        )}
        {isStreaming && (
          <div className="flex justify-start">
            <div className="max-w-[92%] rounded-2xl rounded-bl-md bg-white px-4 py-2.5 shadow-sm">
              {streamScope && (
                <div className="mb-2">
                  <ScopeBadge scope={streamScope} />
                </div>
              )}
              {streamText ? (
                <Markdown content={streamText} onWikiLink={wikiLink} />
              ) : (
                <Loader2 className="h-4 w-4 animate-spin text-indigo-500" />
              )}
            </div>
          </div>
        )}
      </div>

      {locked && (
        <div className="flex items-start gap-2 border-t border-red-200 bg-red-600 px-4 py-3 text-sm font-medium text-white">
          <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{locked}</span>
        </div>
      )}

      {error && (
        <div className="flex items-center justify-between gap-3 border-t border-red-100 bg-red-50 px-4 py-2 text-sm text-red-700">
          <span>{error}</span>
          <button className="shrink-0 font-medium hover:underline" onClick={() => void send(lastSent)}>
            重试
          </button>
        </div>
      )}

      <form onSubmit={onSubmit} className="flex items-end gap-2 border-t border-slate-100 p-3">
        <textarea
          ref={inputBox}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={isStreaming || chatsPending}
          rows={2}
          placeholder={
            agent === "planner"
              ? "输入你的情况，Enter 发送，Shift+Enter 换行"
              : "输入问题，Enter 发送，Shift+Enter 换行"
          }
          className="min-h-11 min-w-0 flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
        />
        <Button type="submit" disabled={isStreaming || chatsPending || !input.trim()} className="h-11 w-11 shrink-0 px-0">
          {isStreaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </form>
      {toast && <Toast key={toast} message={toast} tone="info" onClose={() => setToast("")} />}
    </div>
  );
}
