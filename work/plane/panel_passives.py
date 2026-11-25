"""Static skill bonuses with an explicit already-included panel contract."""
from combat import UnsupportedMechanism, scaled
from panel_conversion import convert_magic_to_hp, convert_physical_to_hp


class PanelPassives:
    supported_passives={6,9,11,13,22,35,36,54,56,68}
    fields={6:'max_hp',9:'magical_attack',11:'physical_defence',35:'physical_attack',
            36:'magical_defence',13:'critical_multiplier',22:'max_hp',
            54:'magical_defence',56:'physical_defence'}
    def __init__(self,catalog,common):self.catalog,self.common=catalog,common

    def records(self,unit):
        hero=self.catalog.hero_for(unit)
        # Replacement awakenings and appended passives are already resolved.
        for sid in (*hero.skill_ids,*hero.artifact_ids):
            for code,values in self.catalog.skill(sid).passives:
                if code not in self.fields and code != 68 and not (code==73 and sid==66000019):continue
                level=self.common.artifact_level(unit,sid) if sid in hero.artifact_ids else 150
                if level is None:raise UnsupportedMechanism(f'Static artifact {sid} level required')
                yield code,scaled(values,level)

    def initialize(self,combat):
        # All computations are prepared before changing any unit.
        prepared=[]
        for unit in combat.units.values():
            if unit.panel.get('includes_static_passives',True):continue
            if unit.hp!=unit.max_hp:
                raise UnsupportedMechanism('Static panel compilation is pre-battle only')
            panel=dict(unit.panel)
            records=list(self.records(unit))
            for code,value in records:
                if code in (68,73):continue
                field=self.fields[code]
                if code==13:panel[field]=panel.get(field,2)+value/100
                elif code in (22,54,56):panel[field]=int(panel[field]*(1+value/100))
                else:panel[field]=int(panel[field]+value)
            for code,value in records:
                if code==68:convert_magic_to_hp(panel,value,policy=combat.mechanism_options.get('wing_conversion_policy','bonus_plus_base'))
                if code==73:convert_physical_to_hp(panel,value)
            panel['includes_static_passives']=True
            prepared.append((unit,panel))
        for unit,panel in prepared:
            unit.panel=panel
            for name in ('max_hp','physical_attack','magical_attack','physical_defence','magical_defence'):
                setattr(unit,name,panel[name])
            unit.hp=unit.max_hp

    def requirements(self,unit):
        # Core attack/HP/defence panels are mandatory Unit inputs. The caller
        # asserts that supplied panels already include the static passives.
        if not unit.panel.get('includes_static_passives',True):
            return ['static panel compilation not performed']
        return []
