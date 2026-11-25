"""Plane panel candidate translated from captured HeroShuXingFix, not fitted wins.

Star/advancement coefficients are ARMv7 HeroShuXing ctor constants at25f35b8:
SSS60/140, other15/35. AttributeConfig constants and awakening table are explicit.
UI float32 template growth and decimal configuration are separate hypotheses.
"""
from decimal import Decimal as D
import math,struct
from catalog import ids
from panel_conversion import convert_magic_to_hp, convert_physical_to_hp

PRIMARY={'strength':'力量','agility':'敏捷','intellect':'智力','vitality':'体质','tenacity':'韧性'}
STATIC={6:'max_hp',9:'magical_attack',11:'physical_defence',35:'physical_attack',36:'magical_defence'}
def f32(v):return struct.unpack('<f',struct.pack('<f',float(v)))[0]
def floor(v):return math.floor(v)
def compile_panel(catalog,tid,star,advancement,*,battle=False,growth_mode='client_float32',artifact_level=10,wing_conversion_policy='bonus_plus_base'):
    if star is None or advancement is None:raise ValueError('Explicit star and advancement required')
    h=catalog.tables['Hero'][tid]
    coeff=100+(60*star+140*advancement if tid//100000==806 else 15*star+35*advancement)
    primary={}
    for key,source in PRIMARY.items():
        base,growth=h[source].split('|')
        if growth_mode=='float64':initial=floor(float(base)+float(growth)*150)
        elif growth_mode=='native_float32':initial=floor(f32(f32(base)+f32(f32(growth)*150)))
        else:
            growth=D(str(f32(growth))) if growth_mode=='client_float32' else D(growth)
            initial=floor(D(base)+growth*150)
        primary[key]=int((initial*coeff)//100)
    p=dict(primary)
    p.update(max_hp=primary['vitality']*34,
        physical_attack=floor(D(primary['strength'])*D(str(f32('5.7')))),
        magical_attack=floor(D(primary['intellect'])*D(str(f32('5.7')))),
        physical_defence=floor(D(primary['tenacity'])*D(str(f32('2.86')))),
        speed=floor(D(primary['agility'])*D(str(f32('.3')))),
        magical_defence=floor(D(primary['strength'])*D('.2')+D(primary['vitality'])*D('.4')+D(primary['intellect'])*D('1.85')+D(primary['tenacity'])*D('.4')),
        critical_chance=0,critical_resist=0,critical_multiplier=2,critical_damage_resist=0,cast_chance=100)
    for sid in ids(h['携带技能ID']):
        for code,values in catalog.skill(sid).passives:
            if code in STATIC:p[STATIC[code]]+=floor(D(values[0])+D(values[1])*150)
    awake=catalog.tables['JueXing'].get(tid)
    if awake:
        rate=int(awake['属性加成'])
        delta={k:(v*(100+rate))//100-v for k,v in primary.items()}
        for field,key,coef in [('physical_attack','strength','5.7'),('magical_attack','intellect','5.7'),('physical_defence','tenacity','2.86'),('speed','agility','.3')]:
            p[field]+=floor(D(delta[key])*D(str(f32(coef))))
        p['magical_defence']+=floor(D(delta['strength'])*D('.2')+D(delta['vitality'])*D('.4')+D(delta['intellect'])*D('1.85')+D(delta['tenacity'])*D('.4'))
    if battle:
        hero=catalog.hero(tid)
        # Static artifact stat application is not certified by initial HP:
        # Angel's raw HP equals the no-artifact panel in this corpus. Preserve
        # detail-style defences; observed raw HP is supplied by the importer.
        for sid in hero.skill_ids:
            for code,values in catalog.skill(sid).passives:
                level=artifact_level if sid in hero.artifact_ids else 150
                value=D(values[0])+D(values[1])*level if len(values)==2 else None
                if code in (22,54,56):
                    field={22:'max_hp',54:'magical_defence',56:'physical_defence'}[code]
                    p[field]=floor(D(p[field])*(1+value/100))
                elif code==68:convert_magic_to_hp(p,value,policy=wing_conversion_policy)
                elif code==13:p['critical_multiplier']+=float(value/100)
        #68 is a battle conversion in the artifact, not a generic static
        #artifact HP bonus. Other artifact bonuses retain their prior scope.
        for sid in hero.artifact_ids:
            for code,values in catalog.skill(sid).passives:
                if code==13:
                    value=D(values[0])+D(values[1])*artifact_level
                    p['critical_multiplier']+=float(value/100)
                if code==68:
                    percent=float(D(values[0])+D(values[1])*artifact_level)
                    convert_magic_to_hp(p,percent,policy=wing_conversion_policy)
                if code==73 and sid==66000019:
                    convert_physical_to_hp(p,D(values[0])+D(values[1])*artifact_level)
    p['includes_static_passives']=True
    return p
