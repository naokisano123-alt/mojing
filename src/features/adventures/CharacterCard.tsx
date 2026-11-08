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
      <div className="inventory-list">
        <strong>背包</strong>
        {state.inventory?.length ? (
          <ul>
            {state.inventory.map((item) => (
              <li key={item.name}>
                <span>{item.name}</span>
                <span>× {item.quantity}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">暂无物品</p>
        )}
      </div>
    </aside>
  );
}
