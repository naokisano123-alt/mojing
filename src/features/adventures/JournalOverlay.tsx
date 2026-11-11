import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

/** 按需打开的阅读辅助面板，关闭后回到原按钮和正文位置。 */
export function JournalOverlay({ title, onClose, children }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (typeof element.showModal === "function" && !element.open) element.showModal();
    else element.setAttribute("open", "");
    element.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      document.body.style.overflow = overflow;
      previousFocus?.focus();
    };
  }, []);
  return <dialog ref={dialog} className="journal-dialog" aria-label={title}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}
    onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); onClose(); } }}>
    <div className="journal-dialog-content">
      <header><h2>{title}</h2><button type="button" className="icon-button" aria-label={`关闭${title}`} onClick={onClose}><X size={21} /></button></header>
      {children}
    </div>
  </dialog>;
}
