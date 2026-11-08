"""Small, explicit character state; proposals are never executable instructions."""
from dataclasses import asdict, dataclass
import unicodedata


class StateError(ValueError):
    pass


MAX_ITEMS = 40
MAX_QUANTITY = 9999


def item_name(value):
    if not isinstance(value, str):
        raise StateError('物品名称须为文字。')
    name = unicodedata.normalize('NFC', value.strip())
    if not name or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise StateError('物品名称须为1–80字且不能含控制字符。')
    try:
        name.encode('utf-16-le')
    except UnicodeEncodeError:
        raise StateError('物品名称包含不完整字符。') from None
    return name


@dataclass(frozen=True)
class InventoryItem:
    name: str
    quantity: int


def inventory(value):
    if not isinstance(value, (list, tuple)) or len(value) > MAX_ITEMS:
        raise StateError('背包最多容纳40种物品。')
    result, names = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'name', 'quantity'}:
            raise StateError('物品只能包含名称和数量。')
        name, quantity = item_name(item['name']), item['quantity']
        if name in names or type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
            raise StateError('物品名称不能重复，数量须为1–9999的整数。')
        names.add(name)
        result.append(InventoryItem(name, quantity))
    return tuple(result)


@dataclass(frozen=True)
class CharacterState:
    name: str = '旅人'
    stamina: int = 100
    spirit: int = 100
    coins: int = 0
    elapsed_minutes: int = 0
    location: str = '起点'
    inventory: tuple[InventoryItem, ...] = ()


def character(value):
    if not isinstance(value, dict) or set(value) - set(CharacterState.__dataclass_fields__):
        raise StateError('角色字段无效。')
    state = CharacterState(**{**value, 'inventory': inventory(value.get('inventory', ()))})
    for name in ('name', 'location'):
        text = getattr(state, name)
        if not isinstance(text, str) or not text.strip() or len(text) > 80:
            raise StateError('角色名称和位置须为1–80字。')
        try:
            text.encode('utf-16-le')
        except UnicodeEncodeError:
            raise StateError('角色文字包含不完整字符。') from None
    for name, maximum in [('stamina',100),('spirit',100),('coins',1000000000),('elapsed_minutes',1000000000)]:
        number = getattr(state,name)
        if type(number) is not int or not 0 <= number <= maximum:
            raise StateError('角色数值超出范围。')
    return state


def project(state, proposal):
    if not isinstance(proposal, dict) or set(proposal) - {'stamina_delta','spirit_delta','coins_delta','minutes','location','inventory_changes'}:
        raise StateError('状态提议含未授权字段。')
    values = asdict(state)
    for key, target, lower, upper in [('stamina_delta','stamina',-100,100),('spirit_delta','spirit',-100,100),
                                       ('coins_delta','coins',-1000,1000),('minutes','elapsed_minutes',0,1440)]:
        amount = proposal.get(key,0)
        if type(amount) is not int or not lower <= amount <= upper:
            raise StateError('状态变化幅度无效。')
        values[target] += amount
    if 'location' in proposal:
        values['location'] = proposal['location']
    if 'inventory_changes' in proposal:
        changes = proposal['inventory_changes']
        if not isinstance(changes, list) or len(changes) > MAX_ITEMS:
            raise StateError('单次物品提议最多40项。')
        items = {item.name: item.quantity for item in state.inventory}
        seen = set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {'name','delta'}:
                raise StateError('物品提议只能包含名称和数量增减。')
            name, delta = item_name(change['name']), change['delta']
            if name in seen or type(delta) is not int or delta == 0 or not -99 <= delta <= 99:
                raise StateError('同一物品不能重复提议，增减须为非零整数且幅度不超过99。')
            seen.add(name)
            amount = items.get(name, 0) + delta
            if not 0 <= amount <= MAX_QUANTITY:
                raise StateError('物品不足或数量超过9999，整份提议被拒绝。')
            if amount:
                items[name] = amount
            else:
                items.pop(name, None)
        values['inventory'] = [{'name': name, 'quantity': amount} for name, amount in items.items()]
    return character(values)
