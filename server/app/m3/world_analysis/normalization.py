"""Repair low-impact model annotations without promoting guesses into canon."""
from collections.abc import Mapping
from .errors import CrossWorldSourceError
from .types import GROUNDING_TYPE_ALIASES


def _clip(value, units):
    return value.encode('utf-16-le', errors='ignore')[:units * 2].decode('utf-16-le', errors='ignore')


def normalize_model_sources(partial, allowed, foreign_sources, world_id, audit, batch_index):
    def record(raw, path):
        if not isinstance(raw, Mapping):
            return raw
        row = dict(raw)
        supplied = row.get('source_ids')
        candidates = [supplied] if isinstance(supplied, str) else supplied if isinstance(supplied, (list, tuple)) else []
        valid, discarded = [], 0
        for sid in candidates:
            if isinstance(sid, str):
                sid = sid.strip()
                if sid in foreign_sources:
                    raise CrossWorldSourceError(world_id, sid, foreign_sources[sid])
                if sid in allowed and sid.startswith(('chunk:', 'entry:')):
                    if sid not in valid: valid.append(sid)
                    continue
            discarded += 1
        raw_kind = row.get('grounding_type')
        kind = GROUNDING_TYPE_ALIASES.get(str(raw_kind).strip().lower(), 'adventure_supplement')
        downgraded = kind == 'canon' and (not valid or discarded > 0)
        if downgraded:
            kind = 'adventure_supplement'
        row['grounding_type'] = kind
        row['source_ids'] = valid
        if kind == 'adventure_supplement' and (downgraded or not valid or discarded):
            row['supplement_reason'] = '此项未提供可核对的直接资料出处，仅作为本旅程的推演补写，不代表世界观原文已明确记载。'
        if downgraded or discarded or raw_kind is None or not isinstance(supplied, (list, tuple)):
            audit.append({
                'action': 'normalized_model_grounding', 'field': path, 'batch_index': batch_index,
                'grounding_type': kind, 'discarded_reference_count': discarded,
                'reason': '整理模型出处标注；无直接依据的内容标为旅程补写，不伪造资料引文。',
            })
        relations = row.get('relationships')
        if isinstance(relations, (list, tuple)):
            row['relationships'] = [record(rel, f'{path}.relationships[{i}]') for i, rel in enumerate(relations)]
        elif relations is not None:
            row['relationships'] = []
            audit.append({'action': 'omitted_optional_relationship', 'field': path + '.relationships', 'reason': '可选人物关系格式不完整，保留人物与开局。'})
        return row

    cleaned = dict(partial)
    for name in ('locations', 'npcs', 'events', 'relationships'):
        rows = partial.get(name)
        if isinstance(rows, (list, tuple)):
            cleaned[name] = [record(row, f'{name}[{i}]') for i, row in enumerate(rows)]
        elif name == 'relationships' and rows is not None:
            cleaned[name] = []
            audit.append({'action': 'omitted_optional_relationship', 'field': name, 'reason': '可选人物关系格式不完整，保留开局。'})
    return cleaned


def normalize_optional_relationships(npcs, relationships, aliases, audit):
    ids = {str(npc['npc_id']) for npc in npcs}

    def clean(rows, owner=None):
        result = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            source = owner or str(raw.get('from_npc_id') or '').strip()
            target = str(raw.get('to_target_id') or '').strip()
            source, target = aliases.get(source, source), aliases.get(target, target)
            summary = raw.get('summary')
            if source not in ids or target not in ids | {'player'} or source == target or not isinstance(summary, str) or not summary.strip():
                audit.append({'action': 'omitted_optional_relationship', 'reason': '人物关系的对象或描述不完整，先保留可用的人物与故事，不补造关系对象。'})
                continue
            row = dict(raw)
            row.update(from_npc_id=source, to_target_id=target, summary=_clip(summary.strip(), 300),
                       relation_type=_clip(str(raw.get('relation_type') or 'acquaintance').strip(), 60) or 'acquaintance')
            reason = raw.get('supplement_reason')
            row['supplement_reason'] = _clip(reason, 240) if isinstance(reason, str) else ''
            visibility = raw.get('visibility')
            if visibility is not None:
                visibility = visibility.strip() if isinstance(visibility, str) else None
                row['visibility'] = visibility if visibility in {'public', 'local_observable', 'faction_internal', 'secret'} else 'secret'
            result.append(row)
        return result

    for npc in npcs:
        npc['relationships'] = clean(npc.get('relationships') or [], str(npc['npc_id']))
    return clean(relationships)
