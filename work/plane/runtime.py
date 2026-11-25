"""Public entry point for explicit-panel plane skill components.

This module deliberately has no betting probability or automatic victory API.
Panel construction, complete reactive ordering and random calibration remain gates.
"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3

from catalog import PlaneCatalog, load_current_catalog
from combat import Buff, PlaneCombat, Unit, UnsupportedMechanism
from mechanics import CommonMechanics
from legacy_mechanics import LegacyMechanics
from modern_mechanics import ModernMechanics
from statuses import CHARM_BUFFS, StatusMechanics
from panel_passives import PanelPassives
from ss_adai import SsAdaiMechanics
from jinkai import JinkaiMechanics
from tide_prince import TidePrinceMechanics
from finalists import Finalists
from points_finalists import PointsFinalists
from points_missing import PointsMissing
from moni_early import MoniEarly
from moni_middle import MoniMiddle
from moni_late import MoniLate

ROOT = Path(__file__).resolve().parents[2]
MONI_MECHANICS = (MoniEarly, MoniMiddle, MoniLate)


class PlaneSkillSession:
    rules = dict(level=150,star='per_unit_observed',artifacts_enabled=True,awaken_if_available=True,
                 active_cast_percent=100,active_cast_evidence='captured plane rules clause6')

    def __init__(self, catalog, units, *, artifact_levels=None, seed=0,
                 mechanism_options=None, legacy_rules=None, modern_parameters=None,
                 strict_inputs=False, battle_rules=None):
        self.catalog=catalog
        self.rules=dict(type(self).rules if battle_rules is None else battle_rules)
        self.strict_inputs=strict_inputs
        missing=self.input_requirements(catalog,units,artifact_levels or {})
        if strict_inputs and missing:
            raise UnsupportedMechanism('Incomplete explicit inputs: '+json.dumps(missing,ensure_ascii=False))
        self.mechanics=CommonMechanics(catalog,artifact_levels=artifact_levels)
        self.legacy=LegacyMechanics(catalog,self.mechanics,rules=legacy_rules)
        self.modern=ModernMechanics(catalog,self.mechanics,parameters=modern_parameters)
        self.statuses=StatusMechanics(catalog,self.mechanics)
        self.panels=PanelPassives(catalog,self.mechanics)
        self.ss_adai=SsAdaiMechanics(catalog)
        self.jinkai=JinkaiMechanics(catalog,self.mechanics)
        self.tide_prince=TidePrinceMechanics(catalog,self.mechanics)
        finalists=(Finalists(catalog,self.mechanics,units),) if any(u.template_id in Finalists.heroes for u in units) else ()
        points=(PointsFinalists(catalog,self.mechanics,units),) if any(u.template_id in PointsFinalists.heroes for u in units) else ()
        self.missing_roster=PointsMissing(catalog,self.mechanics,units) if any(u.template_id in PointsMissing.heroes for u in units) else None
        roster = {u.template_id for u in units}
        self.activity_mechanics = tuple(cls(catalog, self.mechanics, units) for cls in MONI_MECHANICS
                                        if roster.intersection(cls.heroes))
        # Legacy proposes hit modifiers, shared status decides critical, modern
        # consumes the resolved critical flags. Lifecycle callbacks run once.
        self.combat=PlaneCombat(catalog,units,seed=seed,
            hooks=(self.panels,self.mechanics,self.legacy,self.statuses,self.modern,self.ss_adai,self.jinkai,self.tide_prince,*points,*finalists,*((self.missing_roster,) if self.missing_roster else ()),*self.activity_mechanics),
            damage_rule=self.mechanics.damage_rule,mechanism_options=mechanism_options)
        self.panels.initialize(self.combat)
        self.mechanics.initialize(self.combat)
        self.legacy.initialize(self.combat)
        self.modern.initialize(self.combat)
        self.round_end_in_progress=False
        self.round_end_failure=None
        self.round_end_stage=None

    @staticmethod
    def input_requirements(catalog,units,artifact_levels):
        result={}
        for unit in units:
            missing=[]
            row=catalog.tables['Hero'][unit.template_id]
            for key in ('critical_chance','critical_resist','critical_multiplier','critical_damage_resist'):
                value=unit.panel.get(key)
                if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
                    missing.append('explicit finite '+key+' required')
            if 'includes_static_passives' not in unit.panel:
                missing.append('explicit includes_static_passives contract required')
            if unit.race!=int(row['种族']) or unit.profession!=int(row['技能类型']):
                missing.append('race/profession disagrees with versioned hero template')
            hero=catalog.hero_for(unit)
            for sid in hero.artifact_ids:
                if not any(key in artifact_levels for key in (unit.uid,unit.template_id,sid)):
                    missing.append(f'artifact {sid} level required')
            if any(c==15 for sid in hero.skill_ids for c,_ in catalog.skill(sid).enemy_effects):
                if 'intellect' not in unit.panel:missing.append('explicit intellect required')
            if missing:result[unit.uid]=missing
        return result

    def execute(self, actor_uid, skill_id=None, *, primary_uid=None, component_only=False, origin=None):
        actor=self.combat.units[actor_uid]
        hero=self.catalog.hero_for(actor)
        if skill_id is None:
            if len(hero.active_ids)!=1:
                raise UnsupportedMechanism('Explicit active required for non-single-active hero')
            skill_id=hero.active_ids[0]
        # Base reactive skills also belong to the actor; supplemental tooltip
        # records are not separately castable.
        allowed={61000001,*hero.skill_ids,*hero.artifact_ids}
        if skill_id not in allowed:
            raise UnsupportedMechanism('Skill does not belong to the awakened plane hero')
        level=150
        if skill_id in hero.artifact_ids:
            level=self.mechanics.artifact_level(actor,skill_id)
            if level is None:
                raise UnsupportedMechanism('Artifact skill level is required; enabled does not determine its level')
        return self.combat.execute(actor_uid,skill_id,skill_level=level,
                                   primary_uid=primary_uid,active_only=component_only,origin=origin)

    def run_opening(self,actor_uid,*,component_only=False):
        """Opening activation belongs to awakening skills as well as artifacts."""
        actor=self.combat.units[actor_uid]
        hero=self.catalog.hero_for(actor)
        start=len(self.combat.events)
        for hook in self.activity_mechanics:
            fn=getattr(hook,'before_opening',None)
            if fn:fn(self.combat,actor)
        events=list(self.combat.events[start:])
        for sid in dict.fromkeys((*hero.skill_ids,*hero.artifact_ids)):
            if actor.alive and self.catalog.skill(sid).activation_type==5:
                events.extend(self.execute(actor_uid,sid,component_only=component_only,origin={'kind':'opening'}))
                events.extend(self.drain_followups(component_only=component_only))
        return tuple(events)

    def run_turn(self,actor_uid,*,primary_uid=None,component_only=False):
        actor=self.combat.units[actor_uid]
        if not actor.alive:return ()
        if any(self.combat.has_buff(actor,b) for b in (6,21,155,220)):
            return (self.combat.emit('skipped_turn',actor,None,0,reason='stunned'),)
        hero=self.catalog.hero_for(actor)
        normal=any(self.combat.has_buff(actor,b) for b in (5,*CHARM_BUFFS)) or not hero.active_ids
        # Retained 217 windows distinguish new ordinary casts from a second hit
        # of 61121011 or a separate 66000103 coordination.
        if 61121011 in hero.active_ids and self.combat.has_buff(actor,217):
            normal=True
        skill=61000001 if normal else self.modern.active_id(actor)
        result=self.execute(actor_uid,skill,primary_uid=primary_uid,
                            component_only=component_only,origin={'kind':'ordinary'})
        return (*result,*self.drain_followups(component_only=component_only))

    def unresolved(self):
        result={}
        for unit in self.combat.units.values():
            hero=self.catalog.hero_for(unit)
            missing={str(sid):list(self.combat.requirements(sid))
                     for sid in (*hero.skill_ids,*hero.artifact_ids)
                     if self.combat.requirements(sid)}
            for sid in hero.artifact_ids:
                if self.mechanics.artifact_level(unit,sid) is None:
                    missing.setdefault(str(sid),[]).append('artifact level not supplied')
            result[unit.uid]=missing
        modern=self.modern.unresolved(self.combat)
        if modern:result['_modern_context']=list(modern)
        return result

    def drain_followups(self, *, component_only=False, limit=64):
        """Resolve explicit queued work; errors leave the failing item pending."""
        result=[]
        for _ in range(limit):
            if not self.combat.followups:
                return tuple(result)
            action=self.combat.followups[0]
            actor=self.combat.units[action['actor_uid']]
            if not actor.alive:
                self.combat.followups.pop(0)
                continue
            if ((action.get('origin') or {}).get('mechanism') == 'dragon_chase'
                    and self.combat.has_buff(actor, 6)):
                # An earlier queued action may have stunned this actor.
                result.append(self.combat.emit('skipped_followup',actor,None,action['skill_id'],
                                               reason='stunned',origin=action['origin']))
                self.combat.followups.pop(0)
                continue
            primary_uid=action.get('primary_uid')
            primary=self.combat.units.get(primary_uid)
            if primary is not None and not primary.alive:
                if action['skill_id']==66000073 and (action.get('origin') or {}).get('mechanism')==150:
                    self.combat.followups.pop(0)
                    continue
                self.combat.emit('followup_primary_expired',actor,primary,action['skill_id'],
                                 origin=action.get('origin'),retarget_policy='ordinary selector')
                primary_uid=None
            origin=action.get('origin') or {}
            extra_dragon_chase=(action.get('check_extra_dragon_chase') and
                                self.mechanics.dragon_can_chase_again(self.combat,actor))
            if isinstance(origin,dict) and origin.get('mechanism')=='copy_active':
                source=self.combat.units.get(origin.get('copied_from_uid'))
                if source is None or source.camp==actor.camp or action['skill_id'] not in self.catalog.hero_for(source).active_ids:
                    raise UnsupportedMechanism('Invalid copied active source')
                events=self.combat.execute(actor.uid,action['skill_id'],skill_level=action['skill_level'],
                                           primary_uid=primary_uid,active_only=component_only,origin=origin)
            else:
                events=self.execute(action['actor_uid'],action['skill_id'],
                                    primary_uid=primary_uid,component_only=component_only,
                                    origin=action.get('origin'))
            self.combat.followups.pop(0)
            if extra_dragon_chase:
                self.combat.queue_followup(actor.uid,action['skill_id'],skill_level=action['skill_level'],
                                           origin=origin,front=True)
            result.extend(events)
        if self.combat.followups:
            raise UnsupportedMechanism('Followup chain limit reached; remaining work preserved')
        return tuple(result)

    def begin_round_end(self):
        """Queue late Dragon actions once, before next-round periodic effects."""
        if self.round_end_failure:
            raise UnsupportedMechanism('Round-end callback failed after possible partial mutation; recreate session: '+self.round_end_failure)
        if not self.round_end_in_progress:
            self.round_end_in_progress=True
            self.round_end_stage='chases'
            try:
                self.mechanics.end_round(self.combat)
            except Exception as error:
                self.round_end_failure=f'{type(error).__name__}: {error}'
                raise

    def end_round(self, *, component_only=False):
        """Finish late actions, periodic settlement and then duration decay."""
        self.begin_round_end()
        events=list(self.drain_followups(component_only=component_only))
        # A terminal tail chase has no next round: do not tick healing,
        # burn, or status expiry after the final kill.
        if not self.combat.alive(1) or not self.combat.alive(2):
            self.round_end_in_progress=False
            self.round_end_stage=None
            return tuple(events)
        if self.round_end_stage=='chases':
            try:
                self.mechanics.heal_round_end(self.combat)
                self.legacy.end_round(self.combat)
                self.statuses.end_round(self.combat)
                self.modern.end_round(self.combat)
                self.jinkai.end_round(self.combat)
                for hook in self.activity_mechanics:
                    callback = getattr(hook, 'end_round', None)
                    if callback:
                        callback(self.combat)
            except Exception as error:
                self.round_end_failure=f'{type(error).__name__}: {error}'
                raise
            self.round_end_stage='periodics'
        events.extend(self.drain_followups(component_only=component_only))
        expiration_hooks = tuple(h.after_status_expiration for h in self.activity_mechanics
                                 if hasattr(h, 'after_status_expiration'))
        expired_by_uid = {}
        for unit in self.combat.units.values():
            for buff in unit.buffs:
                buff.duration-=1
            if expiration_hooks:
                expired_by_uid[unit.uid] = tuple(b for b in unit.buffs if b.duration <= 0)
            expired=[v for v in unit.buffs if v.buff_id==167 and v.duration<=0] if self.missing_roster else []
            unit.buffs[:]=[b for b in unit.buffs if b.duration>0 and b.hits_remaining!=0]
            if expired:self.missing_roster.removed(self.combat,unit,expired)
        self.legacy.after_status_expiration(self.combat)
        if expiration_hooks:
            try:
                for callback in expiration_hooks:
                    callback(self.combat, expired_by_uid)
                events.extend(self.drain_followups(component_only=component_only))
            except Exception as error:
                self.round_end_failure=f'{type(error).__name__}: {error}'
                raise
        self.mechanics.refresh_stats(self.combat)
        self.round_end_in_progress=False
        self.round_end_stage=None
        # Later-round retained Shadow hits and exact bursts require renewed
        # pre-action eligibility; accumulated attack and its total cap remain.
        self.mechanics.has_acted.clear()
        return tuple(events)

    def export(self):
        return dict(scope='explicit_panel_skill_components',rules=self.rules,
                    catalog_sources=dict(self.catalog.sources),
                    full_prediction_ready=False,
                    incomplete_inputs=self.input_requirements(self.catalog,list(self.combat.units.values()),self.mechanics.artifact_levels),
                    strict_inputs=self.strict_inputs,
                    candidate_options=dict(self.combat.mechanism_options),
                    unresolved_static_requirements=self.unresolved(),
                    limits=['Static requirements are not a complete hero coverage certificate.',
                            'Reactions, durations, critical hits and numerical rounding remain partly candidate.',
                            'Queued followups are explicit pending work, not silently executed.'],
                    units=[asdict(u) for u in self.combat.units.values()],
                    round_end_in_progress=self.round_end_in_progress,
                    round_end_failure=self.round_end_failure,
                    events=[asdict(e) for e in self.combat.events],
                    queued_followups=list(self.combat.followups))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    data=json.loads(args.input.read_text(encoding='utf8'))
    catalog=load_current_catalog(data.get('catalog_sources'))
    units=[]
    for row in data['units']:
        row=dict(row)
        row['buffs']=[Buff(**b) for b in row.get('buffs',[])]
        units.append(Unit(**row))
    # UID-keyed levels work consistently in Python and JSON inputs.
    session=PlaneSkillSession(catalog,units,artifact_levels=data.get('artifact_levels'),seed=data.get('seed',0),
        mechanism_options=data.get('mechanism_options'),legacy_rules=data.get('legacy_rules'),
        modern_parameters=data.get('modern_parameters'),strict_inputs=data.get('strict_inputs',False))
    for action in data['actions']:
        session.execute(**action)
    args.output.write_text(json.dumps(session.export(),ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
