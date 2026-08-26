import math
import random
from typing import TYPE_CHECKING

from ares.behaviors.macro import (
    AutoSupply,
    BuildStructure,
    BuildWorkers,
    GasBuildingController,
    MacroPlan,
    Mining,
    SpawnController,
    TechUp,
    UpgradeController,
)
from ares.consts import ID, UnitRole
from bot.controllers.controller import Controller
from bot.expansion_controller import FixedExpansionController
from cython_extensions import (
    cy_center,
    cy_distance_to_squared,
    cy_towards,
)
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.units import Point2, Unit, Units

if TYPE_CHECKING:
    from bot.main import WilldZergBot


class MacroController(Controller):
    def __init__(
        self,
        ai: "WilldZergBot",
    ) -> None:
        self.ai = ai

        self._macro_plan: MacroPlan
        self._hq: Unit

    async def start(self) -> None:
        pass

    def _gas_mining(self) -> None:
        workers_per_gas = 3
        if (
            (
                (
                    self.ai.pending_or_complete_upgrade(
                        UpgradeId.ZERGGROUNDARMORSLEVEL3
                    )
                    and self.ai.pending_or_complete_upgrade(
                        UpgradeId.ZERGMELEEWEAPONSLEVEL3
                    )
                )
                or self.ai.controllers.being_rushed
            )
            and not self.ai.controllers.cleanup
        ) or (self.ai.minerals < 200 and self.ai.vespene > 300):
            workers_per_gas = 1

        if self.ai.supply_workers < 8 or (
            self.ai.supply_workers < 16
            and self.ai.pending_or_complete_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED)
        ):
            workers_per_gas = 0

        self.ai.register_behavior(
            Mining(mineral_boost=True, workers_per_gas=workers_per_gas)
        )

    async def _build_structure(
        self,
        structure_type: UnitTypeId,
        pos: Point2,
        max_distance: int = 20,
        random_alternative: bool = True,
        ignore_danger: bool = False,
    ) -> None:
        build_pos: Point2 | None = await self.ai.find_placement(
            structure_type,
            pos,
            random_alternative=random_alternative,
            max_distance=max_distance,
        )
        if build_pos and (
            ignore_danger
            or self.ai.mediator.is_position_safe(
                grid=self.ai.mediator.get_ground_grid, position=build_pos
            )
        ):
            worker = self.ai.mediator.select_worker(target_position=build_pos)
            if worker:
                self.ai.mediator.build_with_specific_worker(
                    worker=worker, structure_type=structure_type, pos=build_pos
                )
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)

    def _extractor_building(self) -> None:
        if self.ai.controllers.cleanup:
            self.ai.register_behavior(GasBuildingController(to_count=10, max_pending=8))
        elif (
            self.ai.structures(UnitTypeId.SPAWNINGPOOL) and self.ai.supply_workers == 14
        ):
            self.ai.register_behavior(GasBuildingController(to_count=1))
        elif (
            self.ai.controllers.attacks
            and not UpgradeId.ZERGGROUNDARMORSLEVEL1 in self.ai.completed_researches
            and self.ai.townhalls.amount >= 3
            and (
                self.ai.units(UnitTypeId.QUEEN).amount
                + self.ai.already_pending(UnitTypeId.QUEEN)
                >= 3
            )
        ) or UpgradeId.ZERGMELEEWEAPONSLEVEL1 in self.ai.completed_researches:
            self.ai.register_behavior(GasBuildingController(to_count=2))

    def _build_overlords(self) -> None:
        if (
            self.ai.structures(UnitTypeId.SPAWNINGPOOL)
            and self.ai.supply_used == 14
            and (
                self.ai.units(UnitTypeId.OVERLORD).amount
                + self.ai.already_pending(UnitTypeId.OVERLORD)
            )
            < 2
            and self.ai.can_afford(UnitTypeId.OVERLORD)
        ):
            self.ai.larva.first.build(UnitTypeId.OVERLORD)
        elif self.ai.structures(UnitTypeId.SPAWNINGPOOL).ready:
            self._macro_plan.add(AutoSupply(base_location=self.ai.start_location))

    def _build_spawning_pool(self) -> None:
        if self.ai.minerals > 150:
            self.ai.register_behavior(
                BuildStructure(
                    base_location=self.ai.mediator.get_behind_mineral_positions(
                        th_pos=self._hq.position
                    )[0],
                    structure_id=UnitTypeId.SPAWNINGPOOL,
                    to_count=1,
                )
            )

    def _calculate_max_workers(self) -> int:
        if self.ai.controllers.being_rushed:
            if self.ai.controllers.under_attack_timer or self.ai.townhalls.amount == 1:
                worker_count = 16
            elif self.ai.enemy_units.closer_than(40, self.ai.mediator.get_own_nat):
                worker_count = 22
            else:
                worker_count = 26
            return worker_count

        try:
            if not self.ai.structures(UnitTypeId.SPAWNINGPOOL):
                worker_count = 14
            elif (
                not self.ai.controllers.attacks
                and not self.ai.controllers.skip_first_attack
            ):
                worker_count = 16
            elif self.ai.controllers.attacks == 1 or (
                self.ai.controllers.skip_first_attack
                and self.ai.controllers.attacks == 0
            ):
                worker_count = min(
                    self.ai.supply_used
                    - 3 * int(math.log(self.ai.supply_used))
                    - self.ai.units(UnitTypeId.QUEEN).amount * 2,
                    self.ai.townhalls.amount * 19,
                    60,
                )
            else:
                worker_count = min(
                    self.ai.supply_used - 16 * int(math.log(self.ai.supply_used)), 80
                )
        except ValueError:
            # This is possible if we're taking the log of 0
            # We have already lost at this point but catch it to avoid crashing
            worker_count = 14

        return worker_count

    def _zergling_speed(self) -> None:
        if self.ai.can_afford(
            UpgradeId.ZERGLINGMOVEMENTSPEED
        ) and not self.ai.pending_or_complete_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED):
            sp = self.ai.structures(UnitTypeId.SPAWNINGPOOL).ready
            if sp:
                self.ai.research(UpgradeId.ZERGLINGMOVEMENTSPEED)

    def _tech_to_lair(self) -> None:
        if (
            self.ai.pending_or_complete_upgrade(UpgradeId.ZERGMELEEWEAPONSLEVEL1)
            and self.ai.pending_or_complete_upgrade(UpgradeId.ZERGGROUNDARMORSLEVEL1)
            and self.ai.structures(UnitTypeId.SPAWNINGPOOL).ready
        ):
            # await self.chat_send("Upgrading to Lair", True)
            self.ai.register_behavior(
                TechUp(base_location=self._hq.position, desired_tech=UnitTypeId.LAIR)
            )

    def _tech_to_hive(self) -> None:
        if (
            self.ai.already_pending_upgrade(UpgradeId.ZERGMELEEWEAPONSLEVEL1) == 1.0
            and self.ai.already_pending_upgrade(UpgradeId.ZERGGROUNDARMORSLEVEL1) == 1.0
        ):
            self.ai.register_behavior(
                TechUp(base_location=self._hq.position, desired_tech=UnitTypeId.HIVE)
            )

    def _manage_upgrades(self) -> None:
        if (
            self.ai.controllers.attacks
            and self.ai.pending_or_complete_upgrade(UpgradeId.ZERGGROUNDARMORSLEVEL1)
            < 1.0
            and self.ai.townhalls.amount >= 3
            and (
                self.ai.units(UnitTypeId.QUEEN).amount
                + self.ai.already_pending(UnitTypeId.QUEEN)
                >= 3
            )
            and not self.ai.controllers.being_rushed
        ):
            if self.ai.can_afford(UnitTypeId.EVOLUTIONCHAMBER):
                self.ai.register_behavior(
                    BuildStructure(
                        base_location=self._hq.position,
                        structure_id=UnitTypeId.EVOLUTIONCHAMBER,
                        to_count=2,
                    )
                )

            researches = [
                UpgradeId.ZERGMELEEWEAPONSLEVEL1,
                UpgradeId.ZERGGROUNDARMORSLEVEL1,
                UpgradeId.ZERGMELEEWEAPONSLEVEL2,
                UpgradeId.ZERGGROUNDARMORSLEVEL2,
            ]

            self.ai.register_behavior(
                UpgradeController(researches, self._hq.position, False)
            )

        if UpgradeId.ZERGMELEEWEAPONSLEVEL1 in self.ai.completed_researches:
            if self.ai.can_afford(UnitTypeId.EVOLUTIONCHAMBER):
                self.ai.register_behavior(
                    BuildStructure(
                        base_location=self._hq.position,
                        structure_id=UnitTypeId.EVOLUTIONCHAMBER,
                        to_count=2,
                    )
                )

            researches = [
                UpgradeId.ZERGMELEEWEAPONSLEVEL2,
                UpgradeId.ZERGGROUNDARMORSLEVEL2,
                UpgradeId.ZERGMELEEWEAPONSLEVEL3,
                UpgradeId.ZERGGROUNDARMORSLEVEL3,
                UpgradeId.ZERGLINGATTACKSPEED,
            ]

            self.ai.register_behavior(
                UpgradeController(researches, self._hq.position, False)
            )

    def _expansions(self) -> None:
        if (
            self.ai.mediator.get_enemy_worker_rushed and self.ai.time < 150
        ) or self.ai.controllers.being_cannon_rushed:
            if self.ai.townhalls.amount == 1:
                building_orders = self.ai.mediator.get_building_tracker_dict
                building_counter = self.ai.mediator.get_building_counter
                for worker_tag, details in building_orders.items():
                    if details[ID] == UnitTypeId.HATCHERY:
                        building_counter[UnitTypeId.HATCHERY] -= 1
                        building_orders.pop(worker_tag)
                        # ensure worker is correctly reassigned
                        self.ai.mediator.assign_role(
                            tag=worker_tag, role=UnitRole.GATHERING
                        )
                        break

            self.ai.register_behavior(
                FixedExpansionController(to_count=1, max_pending=0)
            )
            return
        if self.ai.controllers.being_rushed:
            if self.ai.cancelled_expansion:
                return
            self.ai.register_behavior(
                FixedExpansionController(to_count=2, max_pending=1)
            )
            return
        if self.ai.time < 900:
            if self.ai.minerals > 1000:
                max_pending = 10
            elif self.ai.townhalls.amount == 1:
                max_pending = 1
            else:
                max_pending = 2
            self.ai.register_behavior(
                FixedExpansionController(to_count=8, max_pending=max_pending)
            )
        else:
            self.ai.register_behavior(
                FixedExpansionController(to_count=20, max_pending=10)
            )

    def _macro_hatcheries(self) -> None:
        if self.ai.minerals > 2000:
            self.ai.register_behavior(
                BuildStructure(
                    base_location=random.choice(self.ai.townhalls).position,
                    structure_id=UnitTypeId.HATCHERY,
                    to_count=15,
                    max_on_route=1,
                )
            )

    def _cleanup(self) -> None:
        if self.ai.controllers.cleanup:
            self.ai.register_behavior(
                BuildStructure(
                    base_location=self._hq.position,
                    structure_id=UnitTypeId.SPIRE,
                    to_count=1,
                    max_on_route=1,
                )
            )

    def _production(self, worker_count: int) -> None:
        under_attack = bool(self.ai.controllers.under_attack_timer)
        if self.ai.controllers.cleanup:
            self._macro_plan.add(
                SpawnController(
                    army_composition_dict={
                        UnitTypeId.MUTALISK: {"proportion": 1.0, "priority": 0}
                    }
                )
            )
        # After first attack stop production until we have 3 hatcheries
        elif (
            self.ai.controllers.attacks != 1
            or self.ai.townhalls.amount >= 3
            or under_attack
            or self.ai.controllers.being_rushed
            or self.ai.controllers.being_cannon_rushed
            or self.ai.supply_workers < 19
        ):
            if self.ai.supply_workers >= worker_count or (
                under_attack and not self.ai.controllers.being_rushed
            ):
                self._macro_plan.add(
                    SpawnController(
                        army_composition_dict={
                            UnitTypeId.ZERGLING: {"proportion": 1.0, "priority": 0}
                        }
                    )
                )
            else:
                self._macro_plan.add(BuildWorkers(to_count=worker_count))

    async def _build_spines(self) -> None:
        if (
            self.ai.mediator.get_building_counter[UnitTypeId.SPINECRAWLER] >= 2
        ) or self.ai.supply_used > 80:
            return

        spine_count: int = 2
        if self.ai.controllers.being_spine_rushed or (
            self.ai.mediator.get_enemy_worker_rushed and self.ai.time < 210
        ):
            enemy_structs = self.ai.enemy_structures()
            if enemy_structs and enemy_structs.closer_than(20, self.ai.start_location):
                enemy_proxy = enemy_structs.closer_than(
                    20, self.ai.start_location
                ).closest_to(self.ai.start_location)
                spine_location: Point2 = Point2(
                    cy_towards(self.ai.start_location, enemy_proxy.position, 3)
                )
            else:
                spine_location: Point2 = Point2(
                    cy_towards(self.ai.start_location, self.ai.mediator.get_own_nat, 3)
                )
        elif self.ai.controllers.being_rushed:
            if (
                self.ai.structures(UnitTypeId.HATCHERY)
                .filter(lambda h: h.is_ready)
                .amount
                > 1
            ):
                spine_location: Point2 = Point2(
                    cy_towards(
                        self.ai.mediator.get_own_nat, self.ai.game_info.map_center, 3
                    )
                )
            else:
                spine_location: Point2 = Point2(
                    cy_towards(self.ai.start_location, self.ai.mediator.get_own_nat, 3)
                )
            spine_count = 3
        elif self.ai.controllers.enemy_late_nat:
            loc = self.ai.mediator.get_closest_creep_tile(
                pos=self.ai.expansion_entrance
            )
            if not loc:
                return
            spine_location: Point2 = loc
            spine_count = 2 + self.ai.controllers.enemy_late_nat
        else:
            return

        existing_spines: list[Unit] = [
            s
            for s in self.ai.mediator.get_own_structures_dict[UnitTypeId.SPINECRAWLER]
            + self.ai.mediator.get_own_structures_dict[UnitTypeId.SPINECRAWLERUPROOTED]
            if cy_distance_to_squared(s.position, spine_location) < 1600.0
        ]
        if (
            len(existing_spines) < spine_count
            and len(
                self.ai.mediator.get_own_structures_dict[UnitTypeId.SPINECRAWLER]
                + self.ai.mediator.get_own_structures_dict[
                    UnitTypeId.SPINECRAWLERUPROOTED
                ]
            )
            < 3 + self.ai.controllers.enemy_late_nat
            and self.ai.can_afford(UnitTypeId.SPINECRAWLER)
            and self.ai.structures(UnitTypeId.SPAWNINGPOOL).ready
            and self.ai.supply_workers >= 14
        ):
            await self._build_structure(UnitTypeId.SPINECRAWLER, spine_location, 9)

    def _move_spines(self) -> None:
        # Spines have a habit of getting in each other's way so move one at a time
        uprooted_spines = [
            s
            for s in self.ai.mediator.get_own_structures_dict[
                UnitTypeId.SPINECRAWLERUPROOTED
            ]
        ]
        spines = Units(
            [
                s
                for s in self.ai.mediator.get_own_structures_dict[
                    UnitTypeId.SPINECRAWLER
                ]
            ],
            self.ai,
        )

        enemy_units = self.ai.enemy_units
        if (
            not any(enemy_units.closer_than(40, th) for th in self.ai.townhalls)
            and not self.ai.controllers.being_spine_rushed
        ):
            pos = self.ai.mediator.get_closest_creep_tile(
                pos=self.ai.expansion_entrance
            )
            if not pos:
                return
            for s in uprooted_spines:
                if (
                    s.is_using_ability(AbilityId.SPINECRAWLERROOT_SPINECRAWLERROOT)
                    or AbilityId.CANCEL_SPINECRAWLERROOT in s.abilities
                ):
                    continue
                if cy_distance_to_squared(s.position, pos) <= 16:
                    # print("Attempting to burrow spinecrawler")
                    s(AbilityId.SPINECRAWLERROOT_SPINECRAWLERROOT, s.position)
                elif cy_distance_to_squared(s.position, pos) >= 16:
                    # print("Trying to move Spinecrawler")
                    s.move(pos)
                return

            if not spines:
                return
            s = spines.furthest_to(pos)
            if (
                not any(
                    cy_distance_to_squared(th.position, pos) < 4
                    for th in self.ai.townhalls
                )
                and cy_distance_to_squared(s.position, pos) > 25
                and s.is_ready
                and not self.ai.controllers.being_rushed
            ):
                print("Trying to uproot Spinecrawler")
                print(f"Distance squared = {cy_distance_to_squared(s.position, pos)=}")
                s(AbilityId.SPINECRAWLERUPROOT_SPINECRAWLERUPROOT)
                return
        else:
            for s in uprooted_spines:
                pos = self.ai.mediator.get_closest_creep_tile(pos=s.position)
                if not pos:
                    return
                if (
                    s.is_using_ability(AbilityId.SPINECRAWLERROOT_SPINECRAWLERROOT)
                    or AbilityId.CANCEL_SPINECRAWLERROOT in s.abilities
                ):
                    continue
                if cy_distance_to_squared(s.position, pos) < 1:
                    print("Attempting to burrow spinecrawler 2")
                    s(AbilityId.SPINECRAWLERROOT_SPINECRAWLERROOT, pos)
                else:
                    print("Trying to move Spinecrawler 2")
                    s.move(pos)
                return

    async def _build_spores(self) -> None:
        if not self.ai.controllers.need_mineral_spores:
            return

        spores_per_base = 1

        th_positions = [th.position for th in self.ai.townhalls]
        for expo in self.ai.expansion_locations_list:
            if expo in th_positions:
                mineral_fields = self.ai.mineral_field.closer_than(10, expo)
                if not mineral_fields:
                    continue
                com = cy_center(mineral_fields)
                spore_position = Point2(com)
                spore_position = Point2(spore_position.towards(expo, 1)).round(0)
                existing_spore = len(
                    [
                        s
                        for s in self.ai.mediator.get_own_structures_dict[
                            UnitTypeId.SPORECRAWLER
                        ]
                        if cy_distance_to_squared(s.position, spore_position) < 100.0
                    ]
                ) + self.ai.not_started_but_in_building_tracker(UnitTypeId.SPORECRAWLER)

                if (
                    existing_spore < spores_per_base
                    and self.ai.can_afford(UnitTypeId.SPORECRAWLER)
                    and self.ai.has_creep(spore_position)
                ):
                    await self._build_structure(
                        UnitTypeId.SPORECRAWLER, spore_position, 4, False, True
                    )

    async def update(self) -> None:
        self._macro_plan = MacroPlan()

        if not self.ai.townhalls:
            return

        self._hq = self.ai.townhalls.closest_to(self.ai.start_location)
        if not self._hq:
            self._hq = self.ai.townhalls.first

        await self._build_spines()
        await self._build_spores()
        self._move_spines()
        self._gas_mining()
        self._extractor_building()
        self._build_spawning_pool()
        self._build_overlords()

        worker_count = self._calculate_max_workers()

        self._production(worker_count)

        self._zergling_speed()
        self._tech_to_lair()
        self._tech_to_hive()
        self._manage_upgrades()
        self._expansions()
        self._macro_hatcheries()
        self._cleanup()

        self.ai.register_behavior(self._macro_plan)
