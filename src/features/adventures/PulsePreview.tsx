import { useEffect, useState } from "react";
import { ApiClientError, request } from "../../api/client";

export function PulsePreview({ adventureId }: { adventureId: string }) {
  const path = "/adventures/" + encodeURIComponent(adventureId) + "/world-pulse";
  const [enabled, setEnabled] = useState<boolean>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    request<{ enabled: boolean }>("GET", path, { signal: controller.signal })
      .then(value => setEnabled(value.enabled))
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(err instanceof ApiClientError ? err.friendlyMessage : "读取脉动状态失败。");
        }
      });
    return () => controller.abort();
  }, [path]);

  async function toggle() {
    setBusy(true);
    setError("");
    try {
      const value = await request<{ enabled: boolean }>("PUT", path, { body: { enabled: !enabled } });
      setEnabled(value.enabled);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.friendlyMessage : "更新脉动状态失败。");
    } finally {
      setBusy(false);
    }
  }

  return <div className="journal-character">
    <p>世界脉动演示 · 无需 API Key。开启后，公告栏会随着已确认的游戏时间更新。</p>
    <button type="button" className="secondary" aria-pressed={enabled}
      disabled={busy || enabled === undefined} onClick={() => void toggle()}>
      {enabled ? "关闭脉动演示" : "开启脉动演示"}
    </button>
    {error && <p role="alert">{error}</p>}
  </div>;
}
