"""Plane-scoped active mechanisms, with explicit panels and auditable partial scope.

This is an action executor, not a calibrated whole-battle simulator.  Selecting
an active does not model its cast probability or grant support for its hero.
"""
from dataclasses import dataclass, field, replace
from decimal import Decimal
from fractions import Fraction
import copy
import math
import random
from all_active import (ADDITIONAL_EFFECTS, ADDITIONAL_SELECTORS, PRIMARY_DAMAGE,
                        BUFF_EFFECTS, effect_arity, active_requirements,
                        preflight_active, select_additional, apply_effects,
                        execute_active)


class UnsupportedMechanism(ValueError):
    pass


def scaled(values, level):
    """Existing active-skill convention: base + growth * explicit skill level."""
    return float(Decimal(str(values[0])) + Decimal(str(values[1])) * level)


@dataclass
class Buff:
    buff_id: int
    source_uid: str
    source_skill: int
    duration: int
    value: float = 0
    stacks: int = 1
    max_stacks: int | None = None
    initial_duration: int | None = None
    hits_remaining: int | None = None
    source_attack: int | None = None
    stat_modifiers: dict = field(default_factory=dict)
    def __post_init__(self):
        if self.initial_duration is None:
            self.initial_duration = self.duration
        if self.duration < 0 or self.stacks < 1:
            raise ValueError('Invalid buff duration or stacks')


@dataclass
class Unit:
    uid: str
    camp: int
    position: int
    template_id: int
    race: int
    profession: int
    max_hp: int
    hp: int
    physical_attack: int
    magical_attack: int
    physical_defence: int
    magical_defence: int
    buffs: list[Buff] = field(default_factory=list)
    speed: int = 0
    is_male: bool | None = None
    panel: dict = field(default_factory=dict)
    awakened: bool | None = None
    def __post_init__(self):
        if self.max_hp <= 0 or not 0 <= self.hp <= self.max_hp:
            raise ValueError('Explicit valid current/max HP required')
        if self.position not in range(6):
            raise ValueError('Position must be 0..5')
        for key in ('physical_attack', 'magical_attack', 'physical_defence',
                    'magical_defence', 'speed', 'max_hp'):
            if getattr(self, key) < 0:
                raise ValueError('Panels cannot be negative')
            self.panel.setdefault(key, getattr(self, key))
    @property
    def alive(self):
        return self.hp > 0


@dataclass(frozen=True)
class Event:
    kind: str
    actor_uid: str
    target_uid: str | None
    skill_id: int
    payload: dict = field(default_factory=dict)


class PlaneCombat:
    # Only pure numeric, independently stackable debuffs; never DoT/control
    # or180 (one180 object represents a whole awakening application).
    COMPACT_STAT_DEBUFFS={52,59,71,72}
    EFFECTS = {1, 2, 3, 4, 5, 6, 12, 19, 43, 52, 54, 56, 60, 62, 63} | ADDITIONAL_EFFECTS
    SELECTORS = {0, 1, 2, 3, 4, 7, 17, 21, 24, 33, 37, 40, 42, 45} | ADDITIONAL_SELECTORS
    # Hela's awakening is implemented locally, not a generic passive claim.
    LOCAL_PASSIVES = {153}
    BUILTIN_BUFFS = {4, 5, 6, 15, 123, 181, 187, 192, 206, 214}

    def __init__(self, catalog, units, *, seed=0, hooks=(), damage_rule=None, mechanism_options=None):
        self.catalog = catalog
        for unit in units:
            if unit.is_male is None:
                fields=catalog.tables.get('Hero',{}).get(unit.template_id,{})
                if fields.get('性别') in ('0','1'):
                    unit.is_male=fields['性别']=='0'
        self.units = {unit.uid: unit for unit in units}
        if len(self.units) != len(units):
            raise ValueError('Unit identities must be unique')
        slots = {(u.camp, u.position) for u in units}
        if len(slots) != len(units):
            raise ValueError('A camp position cannot contain two units')
        self.rng = random.Random(seed)
        self.hooks = tuple(hooks)
        self.extra_effects = {code: (arity, hook) for hook in self.hooks
                              for code, arity in getattr(hook, 'effect_arities', {}).items()}
        self.extra_selectors = {mode: hook for hook in self.hooks
                                for mode in getattr(hook, 'supported_selectors', ())}
        self.effect_rewriters = tuple(hook.rewrite_effects for hook in self.hooks
                                      if hasattr(hook, 'rewrite_effects'))
        self.events = []
        self.followups = []
        self.damage_rule = damage_rule
        self.current_damage = None
        self.current_hit_context = {}
        self.current_action = None
        self.mechanism_options = dict(mechanism_options or {})
        self.active_counts = {}

    def alive(self, camp=None):
        # (camp, position) is unique and fixed, so filtering a list sorted once
        # gives the same order as sorting the filtered units on every call.
        ordered = getattr(self, '_ordered_units', None)
        if ordered is None or len(ordered) != len(self.units):
            ordered = self._ordered_units = sorted(self.units.values(), key=lambda u: (u.camp, u.position))
        return [u for u in ordered if u.alive and (camp is None or u.camp == camp)]

    def active_id_override(self, actor):
        for hook in self.hooks:
            choose = getattr(hook, 'active_id_override', None)
            if choose:
                sid = choose(self, actor)
                if sid is not None:
                    return sid
        return None

    def emit(self, event_kind, actor, target, skill_id, **payload):
        budget=getattr(self,'event_budget',None)
        if budget is not None and len(self.events)>=budget:
            raise UnsupportedMechanism('event budget reached')
        event = Event(event_kind, actor.uid if isinstance(actor, Unit) else actor,
                      target.uid if isinstance(target, Unit) else target, skill_id, payload)
        self.events.append(event)
        for hook in self.hooks:
            callback = getattr(hook, 'on_event', None)
            if callback:
                callback(self, event)
        return event

    def queue_followup(self, actor_uid, skill_id, *, skill_level, primary_uid=None, origin=None,
                       front=False):
        # front=True keeps the second settlement of a two-release skill ahead
        # of reactions that the first settlement already queued (D5-002).
        entry = dict(actor_uid=actor_uid, skill_id=skill_id,
                     skill_level=skill_level, primary_uid=primary_uid,
                     origin=copy.deepcopy(origin))
        if front:
            self.followups.insert(0, entry)
        elif (skill_id==66000100 and (origin or {}).get('mechanism')==222
              and (self.current_action or {}).get('skill_id')==66000100):
            # A chained Shadow burst finishes before the allied Lance assist
            # already queued by this attack (58b047f0cb2a, game 1).
            index=len(self.followups)
            while index:
                prev=self.followups[index-1]
                if (prev['skill_id']!=61109801 or (prev.get('origin') or {}).get('mechanism')!=125
                        or self.units[prev['actor_uid']].camp!=self.units[actor_uid].camp):break
                index-=1
            self.followups.insert(index,entry)
        else:
            self.followups.append(entry)

    def buff_row(self, buff_id):
        try:
            return self.catalog.tables['Buff'][buff_id]
        except KeyError as exc:
            raise UnsupportedMechanism(f'Unknown buff {buff_id}') from exc

    def positive(self, buff):
        return int(self.buff_row(buff.buff_id)['buffordebuff']) == 1

    def seal_bypasses_immunity(self, buff):
        if buff.buff_id != 192:
            return False
        policy=self.mechanism_options.get('copied_seal_immunity_policy','bypass')
        if policy not in ('bypass','respect'):
            raise ValueError('Unknown copied seal immunity policy')
        # Source attribution remains Vik when Mod copies the status. That
        # attribution does not grant the original cast's immunity bypass.
        copied=bool((self.current_action or {}).get('negative_copy_source'))
        return not (copied and policy=='respect')

    def active_buffs(self, unit):
        sealed = any(b.buff_id == 192 and b.duration > 0 for b in unit.buffs)
        return [b for b in unit.buffs if b.duration > 0 and b.hits_remaining != 0 and
                (not sealed or not self.positive(b))]

    def has_buff(self, unit, buff_id):
        return any(b.buff_id == buff_id for b in self.active_buffs(unit))

    def negative_buffs(self, unit, *, include_seal=False):
        return [b for b in unit.buffs if not self.positive(b) and b.duration > 0
                and (include_seal or b.buff_id != 192)]

    def negative_count(self,unit,*,include_seal=False):
        return sum(b.stacks if b.buff_id in self.COMPACT_STAT_DEBUFFS else 1
                   for b in self.negative_buffs(unit,include_seal=include_seal))

    def compact_stat_debuffs(self,unit):
        if not self.mechanism_options.get('compact_stat_debuffs') or len(unit.buffs)<32:return
        merged={};result=[]
        for buff in unit.buffs:
            if buff.buff_id not in self.COMPACT_STAT_DEBUFFS or buff.max_stacks is not None:
                result.append(buff);continue
            key=(buff.buff_id,buff.source_uid,buff.source_skill,buff.duration,buff.initial_duration,
                 buff.value,buff.hits_remaining,buff.source_attack,tuple(sorted(buff.stat_modifiers.items())))
            if key in merged:merged[key].stacks+=buff.stacks
            else:
                merged[key]=buff;result.append(buff)
        unit.buffs[:]=result

    def apply_buff(self, target, buff):
        if not target.alive:
            return False
        if buff.buff_id in self.COMPACT_STAT_DEBUFFS and buff.stacks>1:
            limited=any(getattr(h,'immunity',{}).get(target.uid,0)>0 for h in self.hooks)
            random_immunity=target.race==3 and any(b.buff_id==186 and b.value>0 for b in self.active_buffs(target))
            if limited or random_immunity:
                accepted=False
                for _ in range(buff.stacks):
                    accepted=self.apply_buff(target,replace(buff,stacks=1)) or accepted
                return accepted
        row = self.buff_row(buff.buff_id)
        for hook in self.hooks:
            fn = getattr(hook, 'before_apply_buff', None)
            if fn and fn(self, target, buff) is False:
                self.emit('buff_blocked', buff.source_uid, target, buff.source_skill,
                          buff_id=buff.buff_id)
                return False
        if buff.buff_id in (4, 15):
            self.dispel(target, positive=buff.buff_id == 4,
                        actor=self.units[buff.source_uid], skill_id=buff.source_skill)
            return True
        buff = copy.deepcopy(buff)
        if buff.buff_id == 206 and buff.hits_remaining is None:
            buff.hits_remaining = 2
        same = [b for b in target.buffs if b.buff_id == buff.buff_id]
        stackable = int(row.get('是否可叠加', '0')) == 1 or (buff.buff_id==46 and buff.source_skill==66000023)
        if buff.max_stacks is not None:
            same_source = [b for b in same if (b.source_uid, b.source_skill) ==
                           (buff.source_uid, buff.source_skill)]
            buff.stacks = min(buff.max_stacks, buff.stacks + sum(b.stacks for b in same_source))
            target.buffs[:] = [b for b in target.buffs if b not in same_source]
        elif not stackable and not (buff.buff_id==25 and buff.source_skill==66000058):
            # Sloth's opening and active blessings are separately stolen or
            # removed in reports, but the target-count bonus stays boolean.
            distinct_sources = buff.buff_id in (11, 16, 17, 22, 25, 27, 117)
            target.buffs[:] = [b for b in target.buffs if b.buff_id != buff.buff_id or
                               distinct_sources and (b.source_uid, b.source_skill) !=
                               (buff.source_uid, buff.source_skill)]
        target.buffs.append(buff)
        self.emit('buff', buff.source_uid, target, buff.source_skill,
                  buff_id=buff.buff_id, duration=buff.duration, value=buff.value,
                  stacks=buff.stacks, source_uid=buff.source_uid)
        self.compact_stat_debuffs(target)
        return True

    def dispel(self, target, *, positive, actor, skill_id, bypass_stone_skin=False):
        if positive and (self.has_buff(target, 181) or (self.has_buff(target, 187) and not bypass_stone_skin)):
            self.emit('dispel_blocked', actor, target, skill_id)
            return []
        removed = [b for b in target.buffs if self.positive(b) == positive and
                   int(self.buff_row(b.buff_id)['是否可以被驱散']) == 0]
        target.buffs[:] = [b for b in target.buffs if b not in removed]
        removed=[replace(b,stacks=1) if b.buff_id in self.COMPACT_STAT_DEBUFFS else b
                 for b in removed for _ in range(b.stacks if b.buff_id in self.COMPACT_STAT_DEBUFFS else 1)]
        self.emit('dispel', actor, target, skill_id, buff_ids=[b.buff_id for b in removed], positive=positive)
        if any(v.buff_id==167 for v in removed):
            for hook in self.hooks:
                if hasattr(hook,'removed'):hook.removed(self,target,removed)
        return removed

    def steal(self, target, *, actor, skill_id):
        # 62 transfers every positive buff. The report shows 181/66 rows moving
        # to the thief's ally, so neither the 181 block nor 是否可以被驱散 applies.
        removed = [b for b in target.buffs if self.positive(b)]
        target.buffs[:] = [b for b in target.buffs if b not in removed]
        self.emit('steal', actor, target, skill_id, buff_ids=[b.buff_id for b in removed])
        return removed

    def damage(self, actor, target, amount, skill_id, kind='physical', *, components=None,
               transferred_components=None, lifesteal_components=None, reject_lethal=False,
               bypass_lethal_protection=False):
        """Resolve a hit, with explicit opt-ins for nonlethal shares or permanent kills."""
        previous = self.current_damage
        parts = components if components is not None else [dict(kind=kind, amount=amount)]
        self.current_damage = dict(actor_uid=actor.uid, target_uid=target.uid, skill_id=skill_id,
                                   reject_lethal=reject_lethal,
                                   components=[dict(kind=p['kind'], raw_amount=p['amount'],
                                                    resolved_amount=p['amount'],
                                                    flat_skill_addition=p.get('flat_skill_addition',0),
                                                    **{k:p[k] for k in ('source_buff','source_effect','retained_shield_percent','source_damage_kind') if k in p}) for p in parts])
        if bypass_lethal_protection:
            self.current_damage['bypass_lethal_protection'] = True
        context=self.current_hit_context
        if (context.get('target_source_actor_uid')==actor.uid
                and kind not in ('dot','counter','reflection','heal_reversal')):
            self.current_damage['target_source_buff']=context.get('target_source_buff')
        if transferred_components is not None:
            self.current_damage['transferred_components'] = copy.deepcopy(transferred_components)
        if lifesteal_components is not None:
            self.current_damage['lifesteal_components'] = copy.deepcopy(lifesteal_components)
        try:
            return self._damage(actor, target, amount, skill_id, kind, components=components)
        finally:
            self.current_damage = previous

    def _damage(self, actor, target, amount, skill_id, kind='physical', *, components=None):
        if not target.alive:
            return 0
        if not isinstance(amount, int) or amount < 0:
            raise ValueError('Damage must be a nonnegative integer')
        parts = components if components is not None else [dict(kind=kind, amount=amount)]
        if not parts or any(not isinstance(p['amount'], int) or p['amount'] < 0 for p in parts):
            raise ValueError('Damage components must be nonnegative integers')
        if sum(p['amount'] for p in parts) != amount:
            raise ValueError('Damage components must sum to requested damage')
        resolved = []
        for index,part in enumerate(parts):
            self.current_damage['component_index']=index
            if part.get('requires_surviving_prior_components'):
                remaining=max(0,target.hp-sum(p['resolved_amount'] for p in resolved))
                for hook in self.hooks:
                    fn=getattr(hook,'surviving_component_basis',None)
                    if fn:remaining=fn(self,actor,target,skill_id,resolved,remaining)
                if not isinstance(remaining,int) or remaining<0:
                    raise ValueError('Invalid surviving-component HP basis')
                if remaining==0:continue
            adjusted = part['amount']
            if 'current_hp_after_primary_percent' in part:
                remaining=max(0,target.hp-sum(p['resolved_amount'] for p in resolved))
                for hook in self.hooks:
                    fn=getattr(hook,'current_hp_component_basis',None)
                    if fn:remaining=fn(self,actor,target,skill_id,resolved,remaining)
                if not isinstance(remaining,int) or remaining<0:
                    raise ValueError('Invalid current-HP component basis')
                adjusted=int(Decimal(remaining//100)*Decimal(str(part['current_hp_after_primary_percent'])))
                self.current_damage['components'][index]['raw_amount']=adjusted
                self.current_damage['components'][index]['resolved_amount']=adjusted
            component_raw=adjusted
            blocked=(target.template_id==80600048 and any(h.apollo_blocks(self,actor,target,skill_id,part) for h in self.hooks if hasattr(h,'apollo_blocks')))
            if blocked:adjusted=0
            for hook in self.hooks:
                if blocked:break
                fn = getattr(hook, 'before_damage', None)
                if fn:
                    adjusted = fn(self, actor, target, adjusted, skill_id, part['kind'])
            if not isinstance(adjusted, int) or adjusted < 0:
                raise ValueError('Damage hook returned invalid amount')
            resolved_part=dict(kind=part['kind'],raw_amount=component_raw,resolved_amount=adjusted)
            for key in ('source_buff','source_effect','retained_shield_percent','source_damage_kind'):
                if key in part:resolved_part[key]=part[key]
            resolved.append(resolved_part)
            if part.get('dispel_positive_after_component'):
                self.dispel(target,positive=True,actor=actor,skill_id=skill_id)
        raw_amount = sum(p['raw_amount'] for p in resolved)
        amount = sum(p['resolved_amount'] for p in resolved)
        self.current_damage['components'] = resolved
        if len(resolved) > 1:
            kind = 'composite'
        for hook in self.hooks:
            fn = getattr(hook, 'before_settlement', None)
            if fn:
                amount = fn(self, actor, target, amount, skill_id, kind)
        # Some limits allocate exact component amounts. Keep those separate
        # from changes to the aggregate whose allocation is not known.
        before_settlement_amount = sum(p['resolved_amount'] for p in resolved)
        if not isinstance(amount, int) or amount < 0:
            raise ValueError('Settlement hook returned invalid amount')
        if self.current_damage.get('reject_lethal') and amount >= target.hp:
            self.emit('damage_rejected', actor, target, skill_id,
                      reason='lethal_transfer', requested=amount, hp=target.hp)
            return 0
        before = target.hp
        # Keep component ownership at the HP boundary, after mitigation and
        # transfer. An appended209 cannot take ownership of an already-lethal
        # primary component. Revival still suppresses the eventual death event.
        lethal_component=None
        remaining=before
        kill_parts=self.current_damage.get('jinkai_component_settlements',
            self.current_damage.get('transferred_components')) or resolved
        for part in kill_parts:
            remaining-=part['resolved_amount']
            if remaining<=0:
                lethal_component=dict(part)
                break
        target_source_buff=self.current_damage.get('target_source_buff')
        ordered = None
        resolved_count = len(resolved)
        if resolved_count > 1:
            for hook in self.hooks:
                settle = getattr(hook, 'settle_component_hp', None)
                if settle:
                    ordered = settle(self, actor, target, skill_id, resolved, amount)
                    if ordered is not None:
                        break
        if ordered is None:
            actual = self._apply_damage_hp(actor, target, amount, skill_id)
        else:
            actual, lethal_component = ordered
            if len(resolved) < resolved_count:
                # Ordered settlement stopped before an unexecuted tail.
                raw_amount = sum(p['raw_amount'] for p in resolved)
                before_settlement_amount = sum(p['resolved_amount'] for p in resolved)
                observed = self.current_damage.get('observed_component_amounts')
                amount = before_settlement_amount if observed is None else sum(observed)
                if len(resolved) == 1:
                    kind = resolved[0]['kind']
        directly_killed=not target.alive
        reaction_start=len(self.events)
        damage_event=self.emit('damage', actor, target, skill_id, amount=actual, requested=amount,
                  hp_before=before, hp_after=target.hp, damage_kind=kind, kind=kind,
                  raw_amount=raw_amount, resolved_amount=amount, actual_hp_loss=actual,
                  before_settlement_amount=before_settlement_amount,
                  components=resolved,
                  settled_components=copy.deepcopy(self.current_damage.get('jinkai_component_settlements',
                      self.current_damage.get('transferred_components'))),
                  lifesteal_components=copy.deepcopy(self.current_damage.get('lifesteal_components')),
                  context=copy.deepcopy(self.current_hit_context),
                  critical=self.current_hit_context.get('critical'),
                  killed=not target.alive)
        # Resolve per-hit growth after all on-hit reactions, including charm.
        # Keep the saved event: nested reflection can alter the live context.
        for hook in self.hooks:
            fn=getattr(hook,'after_damage',None)
            if fn:fn(self,damage_event)
        already_reported=any(e.kind=='death' and e.target_uid==target.uid for e in self.events[reaction_start:])
        if directly_killed and not target.alive and not already_reported:
            devour=self.current_damage.get('devour_kill')
            if devour and devour[1]==target.uid:
                self.emit('death',devour[0],target,61111001,lethal_component=None,target_source_buff=None)
            else:
                self.emit('death', actor, target, skill_id,
                          lethal_component=lethal_component,target_source_buff=target_source_buff)
        return actual

    def _apply_damage_hp(self, actor, target, amount, skill_id, *, consume_mark=True, rescue_first=None):
        before = target.hp
        actual = min(amount, before)
        target.hp -= actual
        shield_hit=(self.current_damage or {}).get('ancestral_absorbed_hit_uid')==target.uid
        if consume_mark and (actual or shield_hit):
            for mark in list(target.buffs):
                if mark.buff_id == 206:
                    mark.hits_remaining = (2 if mark.hits_remaining is None else mark.hits_remaining) - 1
                    if mark.hits_remaining <= 0:
                        target.buffs.remove(mark)
        if target.hp == 0 and not self.current_damage.get('bypass_lethal_protection'):
            start=len(self.events)
            self.current_damage['resolving_lethal_uid']=target.uid
            if rescue_first is None or not rescue_first(before, amount):
                for hook in self.hooks:
                    fn = getattr(hook, 'on_lethal', None)
                    if fn:
                        fn(self, actor, target, skill_id)
            self.current_damage.pop('resolving_lethal_uid',None)
            if target.alive:
                for hook in self.hooks:
                    if hasattr(hook,'after_lethal_protection'):hook.after_lethal_protection(self,target,self.events[start:])
            target.hp = max(0, min(target.hp, target.max_hp))
        return actual

    def heal(self, actor, target, amount, skill_id, *, allow_rescue=False, modifiers_applied=False):
        if not isinstance(amount, int) or amount < 0:
            raise ValueError('Healing must be a nonnegative integer')
        if not target.alive and not allow_rescue:
            return 0
        if not modifiers_applied:
            for hook in sorted(self.hooks, key=lambda h: getattr(h, 'healing_priority', 0)):
                fn = getattr(hook, 'before_heal', None)
                if fn:
                    amount = fn(self, actor, target, amount, skill_id)
        if not isinstance(amount, int) or amount < 0:
            raise ValueError('Healing hook returned invalid amount')
        actual = min(amount, max(0,target.max_hp - target.hp))
        target.hp += actual
        self.emit('heal', actor, target, skill_id, amount=actual,
                  requested=amount, overflow=amount-actual, hp_after=target.hp)
        return actual

    def requirements(self, skill_id):
        skill = self.catalog.skill(skill_id)
        reactive_handled=any(skill_id in getattr(h,'supported_reactive_skills',()) for h in self.hooks)
        covered = self.LOCAL_PASSIVES | set().union(
            *(set(getattr(h, 'supported_passives', ())) for h in self.hooks))
        covered_buffs = self.BUILTIN_BUFFS | set().union(
            *(set(getattr(h, 'supported_buffs', ())) for h in self.hooks))
        required = []
        for side, selector, effects in [('enemy', skill.enemy_target, skill.enemy_effects),
                                         ('ally', skill.ally_target, skill.ally_effects)]:
            if selector[0] not in self.SELECTORS and selector[0] not in self.extra_selectors:
                required.append(f'{side} selector {selector[0]}')
            for code, values in effects:
                if code not in self.EFFECTS and code not in self.extra_effects and not (reactive_handled and code==14):
                    required.append(f'{side} effect {code}')
                if code in BUFF_EFFECTS and int(values[0]) not in self.catalog.tables['Buff']:
                    required.append(f'unknown buff {values[0]}')
                elif code in BUFF_EFFECTS and int(values[0]) not in covered_buffs:
                    required.append(f'buff behaviour {values[0]}')
        required.extend(f'passive {code}' for code, _ in skill.passives if code not in covered)
        required.extend(active_requirements(self, skill))
        return tuple(required)

    def preflight(self, actor, skill, skill_level, *, active_only):
        if any(skill.skill_id in getattr(h,'supported_reactive_skills',()) for h in self.hooks):
            raise UnsupportedMechanism('Reactive-only skill must run through its incoming-damage callback')
        if not isinstance(skill_level, int) or isinstance(skill_level, bool) or skill_level < 1:
            raise ValueError('An explicit positive integer skill_level is required')
        if not actor.alive:
            raise ValueError('A dead actor cannot execute a skill')
        missing = self.requirements(skill.skill_id)
        blocking = [r for r in missing if not (active_only and r.startswith(('passive ', 'candidate ', 'buff behaviour ')))]
        if blocking:
            raise UnsupportedMechanism('; '.join(blocking))
        for effects in (skill.enemy_effects, skill.ally_effects):
            primaries = [c for c, _ in effects if c in PRIMARY_DAMAGE | {2}]
            if len(primaries) > 1:
                raise UnsupportedMechanism('Multiple primary effects require ordering evidence')
            for code, values in effects:
                expected = self.extra_effects[code][0] if code in self.extra_effects else effect_arity(code)
                if len(values) != expected:
                    raise UnsupportedMechanism(f'Effect {code} parameter arity')
                for value in values:
                    if not math.isfinite(float(value)):
                        raise ValueError('Non-finite effect parameter')
                if code in (3, 56) and (int(values[3]) < 0 or not 0 <= float(values[4]) <= 100):
                    raise ValueError('Invalid status duration/probability')
        for unit in self.units.values():
            for buff in unit.buffs:
                self.buff_row(buff.buff_id)
        supported_buffs = set().union(*(set(getattr(h, 'supported_buffs', ())) for h in self.hooks))
        for unit in self.alive():
            for buff in self.active_buffs(unit):
                if buff.buff_id in (16, 43) and buff.buff_id not in supported_buffs:
                    raise UnsupportedMechanism(f'Active buff {buff.buff_id} requires an implemented healing/lifesteal hook')
        #53250cfb/game2/round2/action6 casts Son's artifact under5+21+122.
        #b2a67673dac9/game2/action7: Solomon preempts while buff6 remains.
        if any((b.buff_id == 6 and skill.skill_id not in (66000091,66000044,66000045,66000023)) or (b.buff_id == 5 and skill.skill_id not in (61000001,66000103)) for b in self.active_buffs(actor)):
            raise UnsupportedMechanism('Silenced/stunned actor cannot execute an ordinary active; artifact timing requires separate evidence')
        control = set().union(*(set(getattr(h, 'supported_control', ())) for h in self.hooks))
        if any(b.buff_id in (20, 36, 97, 122, 171) and b.buff_id not in control for b in self.active_buffs(actor)):
            raise UnsupportedMechanism('Controlled/taunted action targeting needs an explicit control executor')
        if any(b.buff_id in (117, 209) and b.buff_id not in control for b in self.active_buffs(actor)):
            raise UnsupportedMechanism('Additional target status 117/209 requires target-count ordering evidence')
        for hook in self.hooks:
            fn = getattr(hook, 'preflight', None)
            if fn:
                fn(self, actor, skill)
        preflight_active(self, actor, skill)
        return missing

    def ordinary_primary(self, actor, enemies):
        marked = [u for u in enemies if self.has_buff(u, 206)]
        if marked:
            return min(marked, key=lambda u: u.position)
        return next((u for u in enemies if u.position == actor.position % 3),
                    min(enemies, key=lambda u: u.position) if enemies else None)

    def _highest(self, pool, count, key):
        # Tie selection is seeded and does not inspect the observed target.
        result = []
        for value in sorted({key(u) for u in pool}, reverse=True):
            tied = [u for u in pool if key(u) == value]
            take = min(len(tied), count-len(result))
            if take <= 0:
                break
            result.extend(tied if take == len(tied) else self.rng.sample(tied, take))
        return result

    def select_targets(self, spec, actor, candidates, *, primary=None, ally=False, skill=None):
        candidates = list(candidates)
        if not ally and skill and skill.skill_id in (66000052,66000073) and (self.current_action or {}).get('origin',{}).get('kind')=='counter':
            return [primary] if primary is not None and primary.alive else []
        selected = None
        if not ally and self.current_action and self.current_action.get('origin', {}).get('mechanism') == 125:
            if skill is not None and skill.skill_id == 61109801 and spec == (2, 1):
                # The low-HP reference is retained in the queued action, but raw
                # records regularly hit another unit via 61109801's selector.
                selected = self._select_targets(spec, actor, candidates, primary=primary, skill=skill)
            else:
                selected = [primary] if primary is not None and primary.alive else self._highest(
                    [u for u in candidates if u.alive], 1, lambda u: -Fraction(u.hp, u.max_hp))
        for hook in self.hooks:
            if selected is not None:
                break
            if hook is self.extra_selectors.get(spec[0]):
                continue  # Run custom selectors with the normal count and live-unit checks.
            fn = getattr(hook, 'select_targets_override', None)
            if fn:
                selected = fn(self, spec, actor, candidates, primary, ally)
                if selected is not None:
                    break
        origin = (self.current_action or {}).get('origin')
        if (selected is None and not ally and spec[0] == 1 and primary in candidates
                and primary.alive and isinstance(origin, dict) and origin.get('force_enemy_primary')):
            selected = [primary]
        if selected is None:
            selected = self._select_targets(spec, actor, candidates, primary=primary, ally=ally, skill=skill)
        for hook in self.hooks:
            fn = getattr(hook, 'after_select_targets', None)
            if fn:
                selected = fn(self, spec, actor, selected, candidates, ally)
        return selected

    def _select_targets(self, spec, actor, candidates, *, primary=None, ally=False, skill=None):
        mode, count = spec
        if skill is not None:
            for hook in self.hooks:
                fn = getattr(hook, 'target_count_bonus', None)
                if fn:
                    count += fn(self, actor, skill, ally)
        # Two independent plane reports show66000102 still protecting its
        # complete undead row under123. Do not generalize to all opening buffs:
        # ordinary team-wide opening selectors DO shrink in the same corpus.
        structural_row = ally and mode == 44 and skill is not None and skill.skill_id == 66000102
        if self.has_buff(actor, 123):
            if ally and skill is not None and skill.skill_id == 66000001:
                # Matching openings selected every friendly position, including Sophia.
                mode, count = 2, 1
            elif mode != 0 and not structural_row:
                count = min(1, count)
        pool = sorted((u for u in candidates if u.alive), key=lambda u: u.position)
        if mode not in self.SELECTORS and mode not in self.extra_selectors:
            raise UnsupportedMechanism(f'Selector {mode}')
        if mode == 0 or not pool or count <= 0:
            return []
        if mode in self.extra_selectors:
            selected = self.extra_selectors[mode].select_targets_override(
                self, (mode, count), actor, pool, primary, ally)
            if selected is None:
                raise UnsupportedMechanism(f'No custom selection for mode {mode}')
            return selected
        if mode in ADDITIONAL_SELECTORS:
            return select_additional(self, mode, count, actor, pool, primary, ally, skill)
        marked = [u for u in pool if not ally and self.has_buff(u, 206)]
        if mode == 1:
            if ally:
                return [actor] if actor in pool else []
            return (marked[:1] or [primary or self.ordinary_primary(actor, pool)])[:count]
        if mode == 2:
            return self.rng.sample(pool, min(count, len(pool)))
        if mode == 3:
            if len(marked) >= count:
                return marked if len(marked) == count else self.rng.sample(marked, count)
            primary = primary or self.ordinary_primary(actor, pool)
            # One mark is the column primary; it does not add an off-column hit.
            column = [u for u in pool if u.position % 3 == primary.position % 3]
            # Under Lust123,60 clean first-round reports include29 rear hits
            #while the front is alive. Truncating the ordered column wrongly
            #made all reduced column skills hit the front unit.
            if (self.has_buff(actor,123) or (skill and skill.skill_id==66000094)) and len(column)>count:
                return self.rng.sample(column,count)
            # Nine identical Plague inputs produce both same-column orders.
            # Keep its target set; processing order affects capped lifesteal.
            if actor.template_id==80600093 and len(column)>1:
                return self.rng.sample(column,min(count,len(column)))
            if skill and skill.skill_id in (61020301,61120301,61220301) and count>=2 and len(column)==2:
                # The second hit may inherit the first hit's newly applied DoTs.
                return self.rng.sample(column,2)
            return column[:count]
        if mode == 4:
            front = [u for u in pool if u.position < 3] or pool
            forced = marked[:count]
            remaining = [u for u in front if u not in forced]
            n = min(count-len(forced), len(remaining))
            return forced + (remaining if n == len(remaining) else self.rng.sample(remaining, n))
        if mode == 7:
            return sorted(pool, key=lambda u: (Fraction(u.hp, u.max_hp), u.hp, u.position))[:count]
        if mode == 17:
            ranks = {u.uid:sum(b.stacks for b in u.buffs if b.duration > 0 and self.positive(b)) for u in pool}
            action = self.current_action
            if not ally and skill and skill.skill_id==61119001 and action is not None:
                # 39 unchanged tie windows kept the same victims within one Rage cast.
                signature = (count,tuple(ranks.items()))
                cached = action.get('rage_target_ranking')
                if cached and cached[0]==signature:
                    return [self.units[uid] for uid in cached[1]]
                chosen = self._highest(pool,count,lambda u:ranks[u.uid])
                action['rage_target_ranking'] = (signature,tuple(u.uid for u in chosen))
                return chosen
            return self._highest(pool,count,lambda u:ranks[u.uid])
        if mode == 21:
            # Adai's enemy ordering and Bear's ally cleanse compare the larger
            # attack. A high magical-attack undead can receive Bear's cleanse.
            key = (lambda u: max(u.physical_attack, u.magical_attack)) if (actor.template_id == 80600100 or ally and actor.template_id == 80600095) else (lambda u: u.physical_attack)
            if ally and skill is not None and skill.skill_id==61120001:
                # Via can choose a mage; the King's round-only damage growth does not win a healing slot.
                offset=next((h.via_rank_physical_offset for h in self.hooks if hasattr(h,'via_rank_physical_offset')),lambda u:0)
                key=lambda u:max(u.magical_attack,u.physical_attack-offset(u))
            return self._highest(marked, min(count,len(marked)), key) + self._highest(
                [u for u in pool if u not in marked], max(0, count-len(marked)), key)
        if mode == 24:
            demons=[u for u in pool if u.race == 3]
            #61008301 and its opening blessing66000044 say highest attack,
            #not first formation slot. Full attack ranking still needs panels.
            if spec[1] == 1:
                return self._highest(demons,count,lambda u:max(u.physical_attack,u.magical_attack))
            return demons[:count]
        if mode == 33:
            return self._highest(pool, count, lambda u: self.negative_count(u))
        if mode == 37:
            return [u for u in pool if u.uid != actor.uid and u.profession == 2
                    and u.position % 3 == actor.position % 3][:count]
        if mode == 42:
            # Bloodbone artifact: other Beast allies by highest attack. The
            # second target unlocks when five allied Beast heroes are present.
            if ally and sum(u.race == 1 for u in self.alive(actor.camp)) >= 5:
                count = max(count, 2)
            beasts = [u for u in pool if u.race == 1 and u.uid != actor.uid]
            return self._highest(beasts, count, lambda u: u.physical_attack)
        if mode in (40, 45):
            demons = [u for u in pool if u.race == 3 and (mode != 45 or u.uid != actor.uid)]
            if mode == 40 and not self.has_buff(actor, 123) and sum(u.race == 3 for u in self.alive(actor.camp)) >= 5:
                count += 1
            # Adai45 ranks attack, including Sloth's larger magical value.
            # Plague40 separately specifies panel physical attack.
            key=(lambda u:u.panel['physical_attack']) if mode==40 else (lambda u:max(u.physical_attack,u.magical_attack))
            return self._highest(demons, count, key)
        raise AssertionError(mode)

    def candidate_damage(self, actor, target, kind, multiplier, bonus):
        """Apply the skill rate to attack minus effective defence."""
        if self.damage_rule:
            return self.damage_rule(self, actor, target, kind, multiplier, bonus)
        attack = actor.physical_attack if kind == 'physical' else actor.magical_attack
        defence = target.physical_defence if kind == 'physical' else target.magical_defence
        context = self.current_hit_context
        attack *= context.get('attack_multiplier', 1)
        defence = max(0, defence*max(0,1-context.get('ignore_defence_percent',0)/100)-context.get('defence_ignore_flat',0))
        raw = max(1, int((Decimal(str(attack)) - Decimal(str(defence))) * Decimal(str(multiplier))))
        raw=max(raw,int(context.get('intellect_damage_floor',0)))
        amount = max(1, int(raw * self.rng.uniform(.95, 1.05)))
        if context.get('critical'):
            amount = int(amount * context.get('critical_multiplier', 2))
        return int((amount+bonus) * context.get('damage_multiplier', 1))

    def _hela(self, actor, target, skill, level):
        self.compact_stat_debuffs(target)
        original_instances = list(self.negative_buffs(target))
        copied_count=self.negative_count(target)
        negatives = copy.deepcopy(original_instances)
        others = [u for u in self.alive() if u.camp != actor.camp and u.uid != target.uid]
        rank=next((h.hela_copy_attack for h in self.hooks if hasattr(h,'hela_copy_attack')),
                  lambda u:max(u.physical_attack,u.magical_attack))
        recipients = self._highest(others, 2, rank)
        # Raw packets propagate42 negative instances but not55/59/60.
        #50 is an explicit scenario inside the observed boundary interval,
        #not a decoded server constant. Source refresh still occurs.
        copy_limit=self.mechanism_options.get('hela_copy_source_limit')
        if copy_limit is not None:
            if type(copy_limit) is not int or copy_limit<1:
                raise ValueError('Hela copy source limit must be a positive integer or None')
            if copied_count>copy_limit:
                recipients=[]
                self.emit('candidate_mechanism',actor,target,skill.skill_id,
                          mechanism='hela_copy_source_limit',negative_instances=copied_count,
                          limit=copy_limit,server_verified=False)
        awake = next((v for code, v in skill.passives if code == 153), None)
        reduction = scaled(awake, level) if awake and negatives else None
        # At level 150, a single 7+ effect transfer can reach the plane 54% cap.
        # Keep the six-layer rule for the tooltip scenario and other levels.
        layer_limit = 7 if reduction == 8 and self.mechanism_options.get('hela_defence_cap_percent') == 54 else 6
        # These negative statuses have no intervening numeric-stat consumer.
        # Preserve every application/reaction and refresh once after the batch.
        batchable={1,6,21,22,52,56,59,61,65,71,72,118,120,122,123,180}
        prior=getattr(self,'defer_copy_stats',False)
        defer=all(b.buff_id in batchable for b in negatives)
        self.defer_copy_stats=prior or defer
        try:
            for recipient in [target] + recipients:
                for index, buff in enumerate(negatives):
                    refreshed = replace(buff, duration=buff.initial_duration)
                    if recipient is target:
                        old = original_instances[index]
                        old.duration = old.initial_duration
                        self.emit('refresh', actor, recipient, skill.skill_id, buff_id=buff.buff_id,
                                  source_uid=buff.source_uid)
                    else:
                        action=self.current_action or {}
                        action['copying_existing_negative']=True
                        try:self.apply_buff(recipient, refreshed)
                        finally:action.pop('copying_existing_negative',None)
                if reduction is not None:
                    # One awakening application per refresh/copy operation. Keep
                    # different casts as independent instances with their own
                    # expiry; stat calculation still caps total defence loss.
                    self.apply_buff(recipient, Buff(180, actor.uid, skill.skill_id, 2,
                                               reduction, stacks=min(layer_limit, copied_count)))
        finally:
            self.defer_copy_stats=prior
            if defer and not prior:
                for hook in self.hooks:
                    if type(hook).__name__=='CommonMechanics':hook.refresh_stats(self)

    def _purification(self, actor, target, values, skill, level, *, had_positive=None,defer_dispel=False):
        """Element Mother 60: pre-dispel positive presence drives the true part.

        Point-race corpus 2026-10-25 (66 casts, 167 target rows): every 60
        component had a positive buff before the strike; dispellable positives
        were removed unless 181/187 blocked it, and the true part fired either
        way. Human coefficient stays +10 points per living human (caster
        included).
        """
        if had_positive is None:
            had_positive = any(b.duration > 0 and self.positive(b) for b in target.buffs)
        humans = sum(u.race == 0 for u in self.alive(actor.camp))
        coefficient = scaled(values, level) + 10 * humans
        amount = int(Decimal(actor.panel['magical_attack']//100) * Decimal(str(coefficient))) if had_positive else 0
        self.emit('candidate_timing', actor, target, skill.skill_id,
                  mechanism='element_mother_60', had_positive_before_dispel=had_positive,
                  living_humans=humans, true_damage_percent=coefficient,
                  panel_magic=actor.panel['magical_attack'], true_damage=amount,
                  ordering='resolve primary protection; dispel; mark and surviving purification retain147; settle once' if defer_dispel else 'dispel then standalone purification')
        if not defer_dispel:self.dispel(target, positive=True, actor=actor, skill_id=skill.skill_id)
        return amount

    def _apply(self, actor, target, effects, skill, level, mod_snapshot):
        return apply_effects(self, actor, target, effects, skill, level, mod_snapshot)

    def execute(self, actor_uid, skill_id, *, skill_level, primary_uid=None, active_only=False, origin=None):
        if (self.mechanism_options.get('blocked_reaction_policy')=='skip'
                and isinstance(origin,dict) and origin.get('kind') in ('reaction','counter','preempt','round_end','opening')):
            actor=self.units[actor_uid]
            try:
                self.preflight(actor,self.catalog.skill(skill_id),skill_level,active_only=active_only)
            except UnsupportedMechanism as error:
                # Cancel only the known control-denial cases. Missing effects,
                # parameters and other failures must remain visible.
                if str(error) not in {
                    'Silenced/stunned actor cannot execute an ordinary active; artifact timing requires separate evidence',
                    'Stunned unit must skip its ordinary turn',
                    'Silenced unit must use its normal attack',
                    'Charmed unit must use its normal attack',
                }:raise
                for hook in self.hooks:
                    for name in ('_pending_extra','_pending_double','_pending_coord'):
                        pending=getattr(hook,name,None)
                        if pending is not None:pending.discard(actor_uid)
                return (self.emit('skipped_action',actor,None,skill_id,
                                  reason=str(error),origin=origin,policy='blocked_reaction_policy:skip'),)
        return execute_active(self, actor_uid, skill_id, skill_level=skill_level,
                              primary_uid=primary_uid, active_only=active_only, origin=origin)
