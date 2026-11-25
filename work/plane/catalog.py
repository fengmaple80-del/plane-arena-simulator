"""Versioned plane configuration; loading a skill never enables its execution.

Parameter lists retain their source order and length. In particular, awakened
passives are not necessarily (attribute, base, growth) triples.
"""
import hashlib
import json
from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import sqlite3


TABLES = {'Hero', 'Skill', 'Buff', 'JueXing', 'ShenQiList'}


def load_catalog_file(path):
    """Load a standalone calculation dataset without opening the report database."""
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw)
        if data['format'] != 1 or set(data['tables']) != TABLES or not data['tables']['Hero']:
            raise ValueError('Invalid catalog format')
        tables = {name: {int(key): row for key, row in rows.items()} for name, rows in data['tables'].items()}
        if any(not isinstance(row, dict) for table in tables.values() for row in table.values()):
            raise ValueError('Invalid record')
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        raise ValueError(f'无法读取角色数据 {Path(path).name}。请放入本工具生成的 JSON。') from error
    catalog = object.__new__(PlaneCatalog)
    digest = hashlib.sha256(raw).hexdigest()
    catalog.sources = {name: f'runtime:plane:{name}:portable:{digest}' for name in TABLES}
    catalog.tables = tables
    catalog.dataset_label = {'demo-catalog.json': '演示角色', 'plane-catalog.json': '内置位面角色'}.get(Path(path).name, '本地位面角色')
    return catalog


def load_current_catalog(sources=None):
    """Load the selected version; explicit historical sources remain supported."""
    root = Path(__file__).resolve().parents[2]
    for name in ('local-catalog.json', 'plane-catalog.json', 'demo-catalog.json'):
        path = root / 'data' / name
        if path.is_file():
            catalog = load_catalog_file(path)
            if sources is not None and catalog.sources != sources:
                raise ValueError('角色数据已变更，请重新开始模拟。')
            return catalog
    if sources is None:
        selected = root / 'outputs/plane-active-catalog.json'
        if not selected.exists():
            selected = root / 'outputs/plane-replay-20261022/corpus-summary.json'
        sources = json.loads(selected.read_text(encoding='utf-8'))['catalog_sources']
    with closing(sqlite3.connect((root / 'outputs/ancient_war.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
        return PlaneCatalog(db, sources)


def segments(raw):
    return tuple((int(parts[0]), tuple(parts[1:]))
                 for item in raw.split('#') if item and item != '0|0'
                 for parts in (item.split('|'),))


def ids(raw):
    return tuple(int(value) for value in raw.split('#') if value and value != '0')


@dataclass(frozen=True)
class PlaneSkill:
    skill_id: int
    name: str
    description: str
    source_id: str
    activation_type: int
    enemy_target: tuple
    ally_target: tuple
    enemy_effects: tuple
    ally_effects: tuple
    passives: tuple
    execution_supported: bool = False


@dataclass(frozen=True)
class PlaneHero:
    template_id: int
    name: str
    skill_ids: tuple
    active_ids: tuple
    artifact_ids: tuple
    supplemental_ids: tuple
    awakening: bool


class PlaneCatalog:
    def __init__(self, db, sources):
        required = TABLES
        if not required.issubset(sources):
            raise ValueError('Explicit versioned sources required for all five plane tables')
        self.sources = dict(sources)
        self.tables = {}
        for table in required:
            source = sources[table]
            if not source.startswith(f'runtime:plane:{table}:'):
                raise ValueError(f'Invalid versioned plane source: {table}')
            rows = db.execute('SELECT record_id,fields_json FROM config_record '
                              'WHERE source_id=? AND table_name=?', (source, table))
            self.tables[table] = {int(key):json.loads(raw) for key,raw in rows}
            if not self.tables[table]:
                raise ValueError(f'Empty plane source: {table}')

    def without_awakenings(self, template_ids):
        """Isolated historical release view; caller must supply version evidence."""
        disabled=set(template_ids)
        if not disabled.issubset(self.tables['JueXing']):
            raise ValueError('Historical profile names an unknown awakening')
        view=object.__new__(PlaneCatalog)
        view.sources=dict(self.sources)
        view.tables=dict(self.tables)
        view.tables['JueXing']={k:v for k,v in self.tables['JueXing'].items() if k not in disabled}
        # Do not copy instance caches: their bound hero() would read self.
        view.disabled_awakenings=tuple(sorted(disabled))
        return view

    def skill(self, skill_id):
        row = self.tables['Skill'][skill_id]
        # Via's awakened combat record is cast directly in reports; its raw active flag is zero.
        activation=1 if skill_id==61120001 else int(row['是否是主动技能'])
        return PlaneSkill(skill_id, row.get('技能名称', str(skill_id)), row.get('技能描述', ''), self.sources['Skill'],
                          activation,
                          tuple(map(int, row['技能对敌目标类型'].split('|'))),
                          tuple(map(int, row['技能对己方目标类型'].split('|'))),
                          segments(row['对敌方效果']), segments(row['对己方效果']),
                          segments(row['技能被动属性']))

    def hero_for(self, unit):
        return self.hero(unit.template_id, unit.awakened)

    def hero(self, template_id, awakened=None):
        row = self.tables['Hero'][template_id]
        skills = list(ids(row['携带技能ID']))
        awakening = self.tables['JueXing'].get(template_id)
        if awakened is False:
            awakening = None
        elif awakened is True and awakening is None:
            raise ValueError(f'Awakening configuration missing for {template_id}')
        supplemental = ()
        if awakening:
            for operation, values in segments(awakening['技能ID']):
                old, new = map(int, values)
                if operation == 1:
                    if old not in skills:
                        raise ValueError(f'Awakening replacement missing base skill {old}')
                    skills[skills.index(old)] = new
                elif operation == 2:
                    skills.append(new)
                else:
                    raise ValueError(f'Unknown awakening operation {operation}')
            supplemental = ids(awakening['补充技能ID'])
        artifact = self.tables['ShenQiList'].get(template_id)
        artifact_ids = ids(artifact['技能ID']) if artifact else ()
        # Resolve every reference now, keeping supplemental skills separately.
        for skill_id in (*skills, *artifact_ids, *supplemental):
            self.skill(skill_id)
        return PlaneHero(template_id, row['短命名'] or row['英雄名称'], tuple(skills),
                         tuple(i for i in skills if self.skill(i).activation_type == 1),
                         artifact_ids, supplemental, bool(awakening))
