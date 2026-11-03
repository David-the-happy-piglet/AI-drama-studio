import type { ReactNode } from "react";
import { AlertTriangle, Copy, X } from "lucide-react";

export function Modal({
  title,
  eyebrow,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className={wide ? "modal-card wide" : "modal-card"}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭"><X size={17} /></button>
        </div>
        {children}
      </section>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`field ${className}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Callout({ children, tone = "note" }: { children: ReactNode; tone?: "note" | "warning" | "success" }) {
  return <div className={`callout ${tone}`}><AlertTriangle size={17} /> <div>{children}</div></div>;
}

export function CodeBlock({ code, onCopied }: { code: string; onCopied?: () => void }) {
  async function copy() {
    await navigator.clipboard.writeText(code);
    onCopied?.();
  }
  return (
    <div className="code-block">
      <code>{code}</code>
      <button type="button" onClick={() => void copy()} aria-label="复制命令"><Copy size={14} /></button>
    </div>
  );
}

export function Empty({ icon, title, copy, action }: { icon: ReactNode; title: string; copy: string; action?: ReactNode }) {
  return (
    <div className="empty-state page-empty">
      {icon}<strong>{title}</strong><span>{copy}</span>{action}
    </div>
  );
}

export function SubmitButton({ busy, children, className = "primary-button", disabled = false }: { busy?: boolean; children: ReactNode; className?: string; disabled?: boolean }) {
  return <button type="submit" className={className} disabled={disabled || busy}>{busy ? "处理中…" : children}</button>;
}
