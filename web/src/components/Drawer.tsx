import { useEffect, type ReactNode } from "react";

export function Drawer({ open, title, onClose, children }: { open: boolean; title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} aria-hidden />
      <aside role="dialog" aria-modal="true" className="relative flex h-full w-[640px] max-w-full flex-col border-l border-neutral-300 bg-white shadow-xl">
        <header className="flex items-center gap-2 border-b border-neutral-200 px-3 py-2">
          <div className="min-w-0 flex-1 truncate text-sm font-semibold">{title}</div>
          <button type="button" onClick={onClose} className="rounded border border-neutral-300 px-2 py-0.5 text-xs hover:bg-neutral-100">
            Close (Esc)
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-3 text-xs">{children}</div>
      </aside>
    </div>
  );
}

/** Small centred dialog. */
export function Modal({ open, title, onClose, children }: { open: boolean; title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} aria-hidden />
      <div role="dialog" aria-modal="true" className="relative w-full max-w-md rounded border border-neutral-300 bg-white text-xs shadow-xl">
        <header className="border-b border-neutral-200 px-3 py-2 text-sm font-semibold">{title}</header>
        <div className="space-y-2 p-3">{children}</div>
      </div>
    </div>
  );
}
