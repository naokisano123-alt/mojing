"""Small, explicit character state; proposals are never executable instructions."""
from dataclasses import asdict, dataclass


class StateError(ValueError):
    pass


@dataclass(frozen=True)
class CharacterState:
    name: str = '旅人'
    stamina: int = 100
    spirit: int = 100
    coins: int = 0
    elapsed_minutes: int = 0
    location: str = '起点'


def character(value):
    if not isinstance(value, dict) or set(value) - set(CharacterState.__dataclass_fields__):
        raise StateError('角色字段无效。')
    state = CharacterState(**value)
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
    if not isinstance(proposal, dict) or set(proposal) - {'stamina_delta','spirit_delta','coins_delta','minutes','location'}:
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
    return character(values)
