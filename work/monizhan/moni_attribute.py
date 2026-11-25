import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moni_evidence import ASSET, load_enemy, sha256

FIELDS = {'strength': '力量', 'agility': '敏捷', 'intellect': '智力', 'vitality': '体质',
          'tenacity': '韧性', 'max_hp': '生命值', 'physical_attack': '物理攻击',
          'magical_attack': '法术攻击', 'physical_defence': '物理防御',
          'magical_defence': '法术防御', 'speed': '速度', 'critical_chance': '暴击率',
          'critical_resist': '抗暴'}


def lookup(hero_id, awakened, asset=ASSET, *, enemy=None):
    if type(awakened) is not bool:
        raise ValueError('必须明确提供普通或觉醒状态')
    rows = [row for row in (enemy if enemy is not None else load_enemy(asset)).values()
            if row['heroId'] == str(hero_id) and int(row['是否觉醒']) == int(awakened)]
    if len(rows) != 1:
        raise ValueError(f'角色及觉醒状态对应 {len(rows)} 条静态记录，不能自动选取')
    row = rows[0]
    skills = []
    for segment in row['技能'].split('#'):
        sid, level = segment.split('|')
        skills.append({'id': sid, 'level': int(level)})
    return {'status': 'candidate_only', 'hero_id': row['heroId'], 'name': row['名称'],
            'enemy_id': int(row['怪物ID']), 'static_awakened': awakened,
            'level': int(row['等级']), 'panel': {key: int(row[value]) for key, value in FIELDS.items()},
            'skills': skills, 'static_position_category': row['前排或后排'],
            'source': {'path': str(asset), 'sha256': sha256(asset)},
            'limitations': ['仅部分角色通过截图核对，静态资源与实际面板的整体一致性尚未核验',
                            '星级、进阶编码和神器基础属性范围尚未确认',
                            '仅凭静态标记不能确认某次对阵的觉醒状态',
                            '缺少其他战斗字段，不可直接当完整模拟器面板']}


