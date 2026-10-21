import { clsx } from "clsx";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
} from "react";

type ButtonVariant = "primary" | "outline" | "ghost" | "danger";

export function Button({
  variant = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      className={clsx(
        "inline-flex h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium transition-colors",
        "disabled:pointer-events-none disabled:opacity-50",
        {
          primary: "bg-indigo-600 text-white hover:bg-indigo-700",
          outline: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-100",
          ghost: "text-slate-600 hover:bg-slate-100",
          danger: "bg-red-600 text-white hover:bg-red-700",
        }[variant],
        className,
      )}
      {...props}
    />
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={clsx(
        "h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm",
        "placeholder:text-slate-400",
        "focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100",
        className,
      )}
      {...props}
    />
  );
}

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={clsx("mb-1.5 block text-sm font-medium text-slate-700", className)} {...props} />;
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx("rounded-xl border border-slate-200 bg-white p-6 shadow-sm", className)} {...props} />
  );
}

export function Alert({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      {children}
    </div>
  );
}
