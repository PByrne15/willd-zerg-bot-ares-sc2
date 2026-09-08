from math import hypot
from typing import TYPE_CHECKING

from ares.behaviors.combat.combat_maneuver import CombatManeuver
from ares.consts import WORKER_TYPES, UnitRole
from bot.behaviour_overwrite import AMove, PathUnitToTarget
from bot.controllers.controller import Controller
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.units import Unit, Units

if TYPE_CHECKING:
    from bot.main import WilldZergBot


class HarassController(Controller):
    """Send short zergling raids to the enemy's most recently scouted expansion."""

    _HARASS_INTERVAL = 560
    _MAX_RAID_SIZE = 10

    def __init__(self, ai: "WilldZergBot") -> None:
        self.ai = ai
        self._raid_detours: dict[Point2, Point2 | None] = {}

    async def start(self) -> None:
        pass

    def _raid_target(self) -> Point2 | None:
        pf = self.ai.enemy_structures.filter(
            lambda s: s.type_id == UnitTypeId.PLANETARYFORTRESS and s.is_ready
        )
        valid_targets = [
            exp
            for exp in self.ai.controllers.scouted_expansions
            if not pf or pf.closest_distance_to(exp) > 8
        ]
        return valid_targets[-1] if valid_targets else None

    def _raid_units(self) -> Units:
        return self.ai.mediator.get_units_from_role(
            role=UnitRole.CONTROL_GROUP_TWO,
            unit_type=UnitTypeId.ZERGLING,
        )

    def _end_raid(self) -> None:
        raid_units = self._raid_units()
        if raid_units:
            self.ai.mediator.batch_assign_role(
                tags={unit.tag for unit in raid_units}, role=UnitRole.DEFENDING
            )

    def _start_raid(self, target: Point2) -> None:
        defenders = self.ai.mediator.get_units_from_role(
            role=UnitRole.DEFENDING,
            unit_type=UnitTypeId.ZERGLING,
        )
        if not defenders:
            return

        raid_units = Units(defenders[: self._MAX_RAID_SIZE], self.ai)
        self.ai.mediator.batch_assign_role(
            tags={unit.tag for unit in raid_units}, role=UnitRole.CONTROL_GROUP_TWO
        )
        print(
            f"Sending expansion harass with {raid_units.amount} lings to {target} "
            f"@ {self.ai.time_formatted}"
        )

    def _raid_detour(self, target: Point2) -> Point2 | None:
        if target in self._raid_detours:
            return self._raid_detours[target]

        own_nat = self.ai.mediator.get_own_nat
        enemy_nat = self.ai.mediator.get_enemy_nat
        grid = self.ai.mediator.get_ground_grid
        direct_path = self.ai.mediator.get_map_data_object.pathfind(
            own_nat, enemy_nat, grid
        )
        if not direct_path:
            self._raid_detours[target] = None
            return None

        midpoint = direct_path[(len(direct_path) * 3) // 4]
        direction_x = enemy_nat.x - own_nat.x
        direction_y = enemy_nat.y - own_nat.y
        direction_length = hypot(direction_x, direction_y)
        if not direction_length:
            self._raid_detours[target] = None
            return None

        perpendicular = (
            -direction_y / direction_length,
            direction_x / direction_length,
        )
        target_offset_x = target.x - midpoint.x
        target_offset_y = target.y - midpoint.y
        target_side = (
            target_offset_x * perpendicular[0] + target_offset_y * perpendicular[1]
        )
        preferred_side = 1.0 if target_side >= 0.0 else -1.0
        map_width, map_height = self.ai.game_info.map_size
        for side in (preferred_side, -preferred_side):
            for offset in (40.0, 32.0, 25.0):
                candidate = Point2(
                    (
                        min(
                            max(midpoint.x + perpendicular[0] * offset * side, 4.0),
                            map_width - 4.0,
                        ),
                        min(
                            max(midpoint.y + perpendicular[1] * offset * side, 4.0),
                            map_height - 4.0,
                        ),
                    )
                )
                if self.ai.mediator.get_map_data_object.pathfind(
                    own_nat, candidate, grid
                ) and self.ai.mediator.get_map_data_object.pathfind(
                    candidate, target, grid
                ):
                    self._raid_detours[target] = candidate
                    return candidate

        self._raid_detours[target] = None
        return None

    @staticmethod
    def _past_raid_detour(unit: Unit, detour: Point2, target: Point2) -> bool:
        detour_to_target_x = target.x - detour.x
        detour_to_target_y = target.y - detour.y
        unit_to_detour_x = unit.position.x - detour.x
        unit_to_detour_y = unit.position.y - detour.y
        return (
            unit_to_detour_x * detour_to_target_x
            + unit_to_detour_y * detour_to_target_y
            >= 0
        )

    def _raid_behaviour(self, unit: Unit, target: Point2) -> None:
        workers = self.ai.enemy_units.filter(
            lambda enemy: (
                enemy.type_id in WORKER_TYPES
                and enemy.position.distance_to(target) < 10
            )
        )
        maneuver = CombatManeuver()
        detour = self._raid_detour(target)
        if detour is not None and not self._past_raid_detour(unit, detour, target):
            maneuver.add(
                PathUnitToTarget(
                    unit=unit,
                    grid=self.ai.mediator.get_ground_grid,
                    target=detour,
                    success_at_distance=5,
                )
            )
        if workers:
            maneuver.add(AMove(unit=unit, target=workers.closest_to(unit)))
        else:
            maneuver.add(
                PathUnitToTarget(
                    unit=unit,
                    grid=self.ai.mediator.get_ground_grid,
                    target=target,
                    success_at_distance=8,
                )
            )
            maneuver.add(AMove(unit=unit, target=target, success_at_distance=8))
        self.ai.register_behavior(maneuver)

    async def update(self) -> None:
        target = self._raid_target()
        if target is None:
            self._end_raid()
            return

        enemies_at_target = self.ai.enemy_units.filter(
            lambda enemy: enemy.position.distance_to(target) < 10
        )
        workers_at_target = enemies_at_target.filter(
            lambda enemy: enemy.type_id in WORKER_TYPES
        )
        if not workers_at_target and enemies_at_target.amount > 2:
            self._end_raid()
            return

        raid_units = self._raid_units()
        if not raid_units and not self.ai.actual_iteration % self._HARASS_INTERVAL:
            self._start_raid(target)
            raid_units = self._raid_units()

        for unit in raid_units:
            self._raid_behaviour(unit, target)
