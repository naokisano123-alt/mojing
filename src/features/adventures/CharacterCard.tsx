import type { CharacterState } from "./api";
export function CharacterCard({
  state,
  title,
}: {
  state: CharacterState;
  title: string;
}) {
  return (
    <aside className="journal-character" aria-label={title}>
      <strong>
        {title} · {state.name}
      </strong>
      <dl>
        <div>
          <dt>体力</dt>
          <dd>{state.stamina}/100</dd>
        </div>
        <div>
          <dt>精神</dt>
          <dd>{state.spirit}/100</dd>
        </div>
        <div>
          <dt>铜币</dt>
          <dd>{state.coins}</dd>
        </div>
        <div>
          <dt>累计耗时</dt>
          <dd>{state.elapsed_minutes}分钟</dd>
        </div>
        <div>
          <dt>位置</dt>
          <dd>{state.location}</dd>
        </div>
      </dl>
    </aside>
  );
}
