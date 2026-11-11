/** 仅改变阅读展示；以确认的世界分钟为准，不使用设备时间。 */
export function elapsedAt(start: string | undefined, current: string): number {
  if (!start) return 0;
  const elapsed = (Date.parse(current + 'Z') - Date.parse(start + 'Z')) / 60000;
  return Number.isFinite(elapsed) ? Math.max(0, Math.round(elapsed)) : 0;
}

export function formatStoryTime(start: string | undefined, elapsed: number): string {
  const anchor = start ? Date.parse(start + 'Z') : NaN;
  if (!Number.isFinite(anchor)) return `第 ${Math.floor(Math.max(0, elapsed) / 1440) + 1} 天`;
  const minute = Math.max(0, elapsed);
  const begin = new Date(anchor);
  const current = new Date(anchor + minute * 60000);
  const day = Math.floor((begin.getUTCHours() * 60 + begin.getUTCMinutes() + minute) / 1440) + 1;
  const hour = current.getUTCHours();
  const period = hour < 6 || hour >= 18 ? '夜晚' : hour < 12 ? '上午' : '下午';
  return `第 ${day} 天 · ${period}`;
}
