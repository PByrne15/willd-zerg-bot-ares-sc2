from typing import TYPE_CHECKING

from ares.behaviors.combat.combat_maneuver import CombatManeuver
from ares.consts import TOWNHALL_TYPES, WORKER_TYPES, UnitRole
from bot.behaviour_overwrite import (
    KeepUnitSafe,
    PathUnitToTarget,
)
from bot.controllers.controller import Controller
from sc2.constants import IS_CARRYING_MINERALS
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

if TYPE_CHECKING:
    from bot.main import WilldZergBot


class ScoutController(Controller):
    def __init__(self, ai: "WilldZergBot") -> None:
        self.ai = ai

        self._first_iteration: bool = True
        self._scouting_natural: bool = False
        self._enemy_nat_taken: bool = False

        self._nat_scout_unit: int = 0
        self._nat_scout_attempts = 0
        self._scouted_lack_of_natural = False

        self._expansion_scout_targets: list[Point2] = []
        self._expansion_scout_units: dict[Point2, int] = {}
        self._expansion_scouting_started = False
        self._scouted_expansions: list[Point2] = []

    async def start(self):
        pass

    def scout_for_natural(self) -> None:
        print("Sending scout to natural")
        self._scouting_natural = True

    def cancel_scout_for_natural(self) -> None:
        self._scouting_natural = False

    def scouted_expansions(self) -> list[Point2]:
        return self._scouted_expansions.copy()

    def enemy_nat_taken(self) -> bool:
        if not self._enemy_nat_taken:
            self._enemy_nat_taken = (
                self.ai.mediator.get_enemy_expanded
                or sum(
                    [
                        IS_CARRYING_MINERALS in worker.buffs
                        for worker in self.ai.enemy_units(WORKER_TYPES).closer_than(
                            10, self.ai.mediator.get_enemy_nat
                        )
                    ]
                )
                > 1
            )
            if self._enemy_nat_taken:
                print(
                    f"Scouted a natural: {self.ai.mediator.get_enemy_expanded=}, {
                        sum(
                            [
                                IS_CARRYING_MINERALS in worker.buffs
                                for worker in self.ai.enemy_units(
                                    WORKER_TYPES
                                ).closer_than(10, self.ai.mediator.get_enemy_nat)
                            ]
                        )
                    } @ {self.ai.time_formatted}"
                )

        return self._enemy_nat_taken

    def _scout_for_natural(self) -> None:
        if not self._enemy_nat_taken and self.ai.time > 420:
            self._enemy_nat_taken = True
            self._scouting_natural = False

        enemy_nat = self.ai.mediator.get_enemy_nat
        if self.enemy_nat_taken():
            scouting_unit = self.ai.unit_tag_dict.get(self._nat_scout_unit)
            if scouting_unit and scouting_unit.type_id == UnitTypeId.OVERLORD:
                self.ai.mediator.assign_role(
                    tag=scouting_unit.tag, role=UnitRole.SCOUTING
                )

            self._scouting_natural = False
            return

        if self._nat_scout_unit and self.ai.is_visible(enemy_nat):
            # Successfully scouted a lack of natural so add an extra retry when the unit is killed
            self._scouted_lack_of_natural = True

        # Assign a new scout if we don't have one and can't already see the natural location
        if not self._nat_scout_unit and not self.ai.is_visible(enemy_nat):
            if scout_ols := self.ai.mediator.get_units_from_role(
                role=UnitRole.SCOUTING, unit_type=UnitTypeId.OVERLORD
            ):
                self._nat_scout_unit = scout_ols.first.tag
            elif scout_ling := self.ai.mediator.get_units_from_roles(
                roles=(UnitRole.DEFENDING, UnitRole.ATTACKING_MAIN_SQUAD),
                unit_type=UnitTypeId.ZERGLING,
            ):
                self._nat_scout_unit = scout_ling.first.tag
            else:
                # No units available to scout with, try again next time
                if not self.ai.actual_iteration % 10:
                    print(
                        f"No units available to scout with  @ {self.ai.time_formatted}"
                    )
                return

            self.ai.mediator.assign_role(
                tag=self._nat_scout_unit, role=UnitRole.CONTROL_GROUP_ONE
            )

        # If we don't have one at this point we must have visibility from another unit
        if not self._nat_scout_unit:
            return

        if scouting_unit := self.ai.unit_tag_dict.get(self._nat_scout_unit):
            if scouting_unit.type_id in [UnitTypeId.OVERLORD, UnitTypeId.OVERSEER]:
                grid = self.ai.mediator.get_air_grid
                retreat_spot = self.ai.mediator.get_ol_spot_near_enemy_nat
            else:
                grid = self.ai.mediator.get_ground_grid
                retreat_spot = self.ai.mediator.get_map_data_object.pathfind(
                    enemy_nat,
                    self.ai.start_location,
                    grid,
                )
                if not retreat_spot:
                    retreat_spot = enemy_nat  # This really shouldn't be possible
                elif len(retreat_spot) <= 10:
                    retreat_spot = retreat_spot[-1]
                else:
                    retreat_spot = retreat_spot[10]

            if not self.ai.is_visible(enemy_nat) and not self._enemy_nat_taken:
                self.ai.register_behavior(
                    PathUnitToTarget(unit=scouting_unit, grid=grid, target=enemy_nat)
                )
            else:
                self.ai.register_behavior(
                    PathUnitToTarget(unit=scouting_unit, grid=grid, target=retreat_spot)
                )
        else:
            # Scouting unit must have died
            self._nat_scout_unit = 0
            self._nat_scout_attempts += 1

            # If the scout saw no natural, don't count this attempt
            if self._scouted_lack_of_natural:
                self._scouted_lack_of_natural = False
                self._nat_scout_attempts -= 1
            print(
                f"Scouting unit died, attempts =  {self._nat_scout_attempts} @ {self.ai.time_formatted}"
            )

            # If the scout died without reaching the nat a few times
            # then we will assume it has been taken
            if self._nat_scout_attempts >= 3:
                if not self.ai.controllers.was_rushed:
                    print(
                        "Reached limit for scouting natural, assuming it has been taken"
                    )
                    self._scouting_natural = False
                    self._enemy_nat_taken = True
                else:
                    print(
                        "Reached limit for scouting natural, but were being rushed. Will not assume it was taken until 7 minutes"
                    )
                    self._scouting_natural = False

    def _start_expansion_scouting(self) -> None:
        if self._expansion_scouting_started:
            return

        self._expansion_scouting_started = True

        enemy_start = self.ai.enemy_start_locations[0]
        enemy_nat = self.ai.mediator.get_enemy_nat
        expansion_paths: list[tuple[Point2, int]] = []
        for expansion in self.ai.expansion_locations_list:
            if (
                expansion.distance_to(enemy_start) < 15
                or expansion.distance_to(enemy_nat) < 8
            ):
                continue
            if path := self.ai.mediator.get_map_data_object.pathfind(
                enemy_start, expansion, self.ai.mediator.get_ground_grid
            ):
                expansion_paths.append((expansion, len(path)))

        expansion_paths.sort(key=lambda expansion_path: expansion_path[1])
        self._expansion_scout_targets = [
            expansion for expansion, _ in expansion_paths[:5]
        ]
        if self._expansion_scout_targets:
            print(
                f"Starting expansion scouting at {self.ai.time_formatted}: "
                f"{self._expansion_scout_targets}"
            )

    def _scout_enemy_expansions(self) -> None:
        self._start_expansion_scouting()
        enemy_townhalls = self.ai.enemy_structures(TOWNHALL_TYPES)
        for expansion in self._scouted_expansions.copy():
            if not enemy_townhalls.closer_than(8, expansion):
                self._scouted_expansions.remove(expansion)

        if len(self._scouted_expansions) == len(self._expansion_scout_targets):
            return

        not_scouted_expansions = [
            expansion
            for expansion in self._expansion_scout_targets
            if expansion not in self._scouted_expansions
        ][:2]

        for target in not_scouted_expansions:
            if self.ai.enemy_structures(TOWNHALL_TYPES).closer_than(8, target):
                self._expansion_scout_units.pop(target, None)
                self._scouted_expansions.append(target)
                print(f"Scouted enemy expansion at {target}")
                continue

            scout_tag = self._expansion_scout_units.get(target)
            scouting_unit = self.ai.unit_tag_dict.get(scout_tag) if scout_tag else None

            if scouting_unit is None:
                self._expansion_scout_units.pop(target, None)
                assigned_tags = set(self._expansion_scout_units.values())
                available_scouts = self.ai.mediator.get_units_from_roles(
                    roles=(UnitRole.DEFENDING, UnitRole.ATTACKING_MAIN_SQUAD),
                    unit_type=UnitTypeId.ZERGLING,
                ).filter(
                    lambda unit, assigned_tags=assigned_tags: (
                        unit.tag not in assigned_tags
                    )
                )
                if not available_scouts:
                    continue

                scouting_unit = available_scouts.first
                self._expansion_scout_units[target] = scouting_unit.tag

            if (
                scouting_unit.tag
                not in self.ai.mediator.get_unit_role_dict[UnitRole.CONTROL_GROUP_ONE]
            ):
                self.ai.mediator.assign_role(
                    tag=scouting_unit.tag, role=UnitRole.CONTROL_GROUP_ONE
                )

            maneuver = CombatManeuver()
            maneuver.add(
                PathUnitToTarget(
                    unit=scouting_unit,
                    grid=self.ai.mediator.get_ground_grid,
                    target=target,
                    success_at_distance=4,
                )
            )
            maneuver.add(
                KeepUnitSafe(unit=scouting_unit, grid=self.ai.mediator.get_ground_grid)
            )
            self.ai.register_behavior(maneuver)

    def _defending_overseer(self) -> None:
        if UpgradeId.ZERGMELEEWEAPONSLEVEL1 in self.ai.completed_researches:
            count = 1
            if self.ai.time > 480:
                count = 2

            self._morph_overseers_in_role(
                UnitRole.DEFENDING, count, self.ai.controllers.defend_point
            )

    def _attacking_overseer(self) -> None:
        if self.ai.supply_used == 200 and self.ai.controllers.attacks >= 2:
            self._morph_overseers_in_role(
                UnitRole.ATTACKING_MAIN_SQUAD, 2, self.ai.controllers.attacker_com
            )

    def _morph_overseers_in_role(
        self, role: UnitRole, max_count: int, location: Point2 | None = None
    ) -> None:
        if not location:
            location = self.ai.start_location
        if (
            self.ai.mediator.get_units_from_role(
                role=role,
                unit_type={
                    UnitTypeId.OVERLORD,
                    UnitTypeId.OVERSEER,
                    UnitTypeId.OVERLORDCOCOON,
                },
            ).amount
            < max_count
            and self.ai.can_afford(UnitTypeId.OVERSEER)
            and self.ai.minerals > 200
        ):
            # print(
            #     f"Spawning overseer for role {role} @ {self.ai.time_formatted}")
            if not self.ai.units(UnitTypeId.OVERLORD):
                return
            overlord = self.ai.units(UnitTypeId.OVERLORD).closest_to(location)
            overlord(AbilityId.MORPH_OVERSEER, subtract_cost=True)
            self.ai.mediator.assign_role(tag=overlord.tag, role=role)

    def _first_overlord(self) -> None:
        if self._first_iteration:
            ol = self.ai.units(UnitTypeId.OVERLORD).first
            self.ai.mediator.assign_role(tag=ol.tag, role=UnitRole.SCOUTING)
            self._first_iteration = False

        ols = self.ai.mediator.get_units_from_role(
            role=UnitRole.SCOUTING, unit_type=UnitTypeId.OVERLORD
        )
        for ol in ols:
            maneuver = CombatManeuver()
            maneuver.add(
                PathUnitToTarget(
                    ol,
                    self.ai.mediator.get_air_grid,
                    self.ai.mediator.get_ol_spot_near_enemy_nat,
                )
            )
            self.ai.register_behavior(maneuver)

    async def update(self) -> None:
        self._first_overlord()

        if self._scouting_natural:
            self._scout_for_natural()
        elif self.enemy_nat_taken():
            self._scout_enemy_expansions()

        self._defending_overseer()
        self._attacking_overseer()
