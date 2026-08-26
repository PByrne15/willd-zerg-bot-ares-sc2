from typing import TYPE_CHECKING

import numpy as np
from ares.behaviors.combat.combat_maneuver import CombatManeuver
from ares.consts import (
    COMMON_UNIT_IGNORE_TYPES,
    LOSS_DECISIVE_OR_WORSE,
    LOSS_MARGINAL_OR_BETTER,
    TIE_OR_BETTER,
    VICTORY_DECISIVE_OR_BETTER,
    WORKER_TYPES,
    EngagementResult,
    UnitRole,
)
from bot.behaviour_overwrite import (
    AMove,
    KeepUnitSafe,
    PathUnitToTarget,
)
from bot.controllers.controller import Controller
from cython_extensions.units_utils import (
    cy_find_units_center_mass,
)
from sc2.units import Point2, Unit, Units, UnitTypeId

if TYPE_CHECKING:
    from bot.main import WilldZergBot


class DefendController(Controller):
    def __init__(
        self,
        ai: "WilldZergBot",
    ) -> None:
        self.ai = ai

        self._defend_point: Point2 = Point2((0, 0))
        self._engaging: dict[int, bool] = {}

    def defend_point(self) -> Point2:
        return self._defend_point

    async def start(self) -> None:
        self._defend_point = self.ai.expansion_entrance

    def _set_defend_point(self) -> None:
        if not self.ai.townhalls:
            self._defend_point = self.ai.start_location
        elif (
            self.ai.structures(UnitTypeId.SPINECRAWLER).amount > 0
            and self.ai.townhalls.amount < 4
        ):
            pos, _ = cy_find_units_center_mass(
                self.ai.structures(UnitTypeId.SPINECRAWLER), 5
            )
            self._defend_point = Point2(pos)
        elif self.ai.townhalls.amount < 3:
            self._defend_point = self.ai.expansion_entrance
        else:
            self._defend_point = self.ai._position_facing_enemy_base(
                self.ai.townhalls.closest_to(self.ai.enemy_start_locations[0]).position
            )

    def _get_close_units(self) -> Units:
        if self.ai.townhalls:
            close_units: Units = self.ai.enemy_units.in_distance_of_group(
                self.ai.townhalls, 40
            ).filter(
                lambda u: (
                    not u.is_flying
                    and not u.is_cloaked
                    and not u.is_hallucination
                    and not u.type_id in COMMON_UNIT_IGNORE_TYPES
                    and u.can_be_attacked
                )
            )
            close_structs: Units = self.ai.enemy_structures.in_distance_of_group(
                self.ai.townhalls, 30
            ).filter(
                lambda s: (
                    s.type_id
                    in {
                        UnitTypeId.BUNKER,
                        UnitTypeId.PLANETARYFORTRESS,
                        UnitTypeId.SPINECRAWLER,
                        UnitTypeId.SPINECRAWLERUPROOTED,
                        UnitTypeId.PHOTONCANNON,
                    }
                )
            )
            close_units = close_units + close_structs
        else:
            # We've almost certainly lost so just have some behaviour to not crash
            close_units = self.ai.enemy_units
        return close_units

    def _get_proxy_buildings(self) -> Units:
        if self.ai.townhalls:
            proxy_buildings: Units = self.ai.enemy_structures.closer_than(
                50, self.ai.start_location
            ).filter(
                lambda u: (
                    not u.type_id
                    in {
                        UnitTypeId.BUNKER,
                        UnitTypeId.PLANETARYFORTRESS,
                        UnitTypeId.SPINECRAWLER,
                        UnitTypeId.SPINECRAWLERUPROOTED,
                        UnitTypeId.PHOTONCANNON,
                    }
                )
            )
        else:
            # We've almost certainly lost so just have some behaviour to not crash
            proxy_buildings = self.ai.enemy_structures
        return proxy_buildings

    def _default_defensive_behaviour(
        self, defender: Unit, ground_grid: np.ndarray
    ) -> None:
        maneuver: CombatManeuver = CombatManeuver()
        maneuver.add(KeepUnitSafe(unit=defender, grid=ground_grid))
        maneuver.add(
            PathUnitToTarget(unit=defender, grid=ground_grid, target=self._defend_point)
        )
        self.ai.register_behavior(maneuver)

    def _revert_attackers_to_defenders(
        self, defenders: Units, combat_sim_result: EngagementResult, enemy_units: Units
    ) -> None:
        attackers = self.ai.mediator.get_units_from_role(
            role=UnitRole.ATTACKING_MAIN_SQUAD
        )
        if (
            attackers
            and defenders.amount >= 10
            and combat_sim_result
            in [EngagementResult.LOSS_MARGINAL, EngagementResult.LOSS_CLOSE]
        ):
            all_units = attackers + defenders
            new_combat_sim_result: EngagementResult = self.ai.mediator.can_win_fight(
                own_units=all_units, enemy_units=enemy_units
            )
            if new_combat_sim_result is VICTORY_DECISIVE_OR_BETTER:
                print("Setting attackers to defend")
                self.ai.mediator.batch_assign_role(
                    tags={a.tag for a in attackers}, role=UnitRole.DEFENDING
                )

    def _defensive_behaviour(
        self,
        defender: Unit,
        defenders: Units,
        close_units: Units,
        combat_sim_result: EngagementResult,
        ground_grid: np.ndarray,
    ) -> None:
        tag = defender.tag
        if tag not in self._engaging:
            self._engaging[tag] = False
        engaging = self._engaging[tag]

        maneuver: CombatManeuver = CombatManeuver()
        nearby_friendlies = defenders.closer_than(
            20, close_units.closest_to(defender)
        ).amount
        # Triple count spine crawlers to encourage engaging near them
        nearby_friendlies += (
            self.ai.structures(UnitTypeId.SPINECRAWLER)
            .filter(
                lambda s: (
                    s.is_ready
                    and s.position.distance_to(close_units.closest_to(defender)) < 7
                )
            )
            .amount
            * 3
        )
        nearby_enemies_units = close_units.closer_than(
            10, close_units.closest_to(defender)
        )
        nearby_enemies = nearby_enemies_units.amount

        cannons = nearby_enemies_units.filter(
            lambda u: u.type_id == UnitTypeId.PHOTONCANNON
        ).amount

        defense_location = self._defend_point
        if (
            not self.ai.townhalls
            or (
                combat_sim_result in TIE_OR_BETTER
                and defender.position.distance_to_closest(self.ai.townhalls) <= 50
            )
            or (
                combat_sim_result in LOSS_MARGINAL_OR_BETTER
                and defender.position.distance_to_closest(self.ai.townhalls) <= 50
                and engaging
            )
            or nearby_friendlies >= nearby_enemies * 2
        ) and (
            nearby_friendlies >= cannons * 8
            or (engaging and nearby_friendlies >= cannons * 4)
        ):
            if close_units:
                self._engaging[tag] = True
                defense_location = close_units.closest_to(defender).position
        else:
            self._engaging[tag] = False
            maneuver.add(KeepUnitSafe(unit=defender, grid=ground_grid))

        maneuver.add(AMove(unit=defender, target=defense_location))
        self.ai.register_behavior(maneuver)

    async def _check_for_overwhelming_enemy(self, defenders: Units) -> None:
        if self.ai.controllers.attacks >= 2:
            return
        enemy_units = self.ai.enemy_units.filter(
            lambda u: u.type_id not in WORKER_TYPES
        )
        combat_sim_result: EngagementResult = self.ai.mediator.can_win_fight(
            own_units=defenders, enemy_units=enemy_units
        )
        if (
            combat_sim_result in LOSS_DECISIVE_OR_WORSE
            and not self.ai.controllers.under_attack_timer
        ):
            self.ai.controllers.set_under_attack_timer(1)
            if not self.ai.actual_iteration % 50:
                print(
                    f"Cutting workers as overwhelming enemy scouted @ {self.ai.time_formatted}"
                )

    def _manage_worker_defense(self) -> None:
        defending_workers = self.ai.mediator.get_units_from_role(
            role=UnitRole.DEFENDING, unit_type=UnitTypeId.DRONE
        )
        if not self.ai.controllers.being_spine_rushed:
            if defending_workers:
                self.ai.mediator.batch_assign_role(
                    tags={dw.tag for dw in defending_workers}, role=UnitRole.GATHERING
                )
                print("Workers returning to gathering")
            return

        num_workers_to_defend = 8

        if not self.ai.enemy_structures:
            return

        if defending_workers.amount < num_workers_to_defend:
            proxy_building = self.ai.enemy_structures.closest_to(self.ai.start_location)
            worker = self.ai.mediator.select_worker(target_position=proxy_building)
            if worker:
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.DEFENDING)

    async def update(self) -> None:
        ground_grid: np.ndarray = self.ai.mediator.get_ground_grid
        defenders: Units = self.ai.mediator.get_units_from_role(role=UnitRole.DEFENDING)
        interval = self.ai.controllers.ling_micro_interval
        iteration_mod = self.ai.actual_iteration % interval
        defenders_this_iteration = [
            a for a in defenders if a.tag % interval == iteration_mod
        ]

        self._manage_worker_defense()

        self._set_defend_point()
        close_units = self._get_close_units()

        if not close_units:
            for defender in defenders_this_iteration:
                self._default_defensive_behaviour(defender, ground_grid)
            return

        combat_sim_result: EngagementResult = self.ai.mediator.can_win_fight(
            own_units=defenders, enemy_units=close_units
        )
        # self._revert_attackers_to_defenders(defenders, combat_sim_result, close_units)

        for defender in defenders_this_iteration:
            self._defensive_behaviour(
                defender, defenders, close_units, combat_sim_result, ground_grid
            )

        await self._check_for_overwhelming_enemy(defenders)
