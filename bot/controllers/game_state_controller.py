from typing import TYPE_CHECKING

from bot.controllers.controller import Controller
from cython_extensions import cy_distance_to_squared
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

if TYPE_CHECKING:
    from bot.main import WilldZergBot


class GameStateController(Controller):
    def __init__(
        self,
        ai: "WilldZergBot",
    ) -> None:
        self.ai = ai

        self._was_rushed = False
        self._being_rushed = False
        self._cancel_rush_timer = 0
        self._cleanup = False
        self._enemy_late_nat = 0
        self._being_spine_rushed = False

    async def start(self) -> None:
        pass

    def _is_proxy_zealot_extra(self) -> bool:
        if self.ai.enemy_race != Race["Protoss"] or self.ai.time > 240:
            return False

        proxy_zealots: list[Unit] = [
            z
            for z in self.ai.enemy_units
            if z.type_id == UnitTypeId.ZEALOT
            and cy_distance_to_squared(z.position, self.ai.start_location) < 4225
        ]
        return (self.ai.time < 150.0 and len(proxy_zealots) >= 1) or (
            self.ai.time < 180.0 and len(proxy_zealots) >= 2
        )

    def _set_was_rushed(self) -> None:
        if self.ai.time > 240 or self._was_rushed:
            return

        was_rushed = (
            self.ai.mediator.get_did_enemy_rush or self._is_proxy_zealot_extra()
        )
        if was_rushed:
            self._was_rushed = True
            self._being_rushed = True
            print(f"Detected that we're being rushed @ {self.ai.time_formatted}")

    def _set_being_rushed(self) -> None:
        # Early exit when not being rushed
        if not self._was_rushed or (self._was_rushed and not self._being_rushed):
            return

        # Early exit when being rushed and it's before 4 minutes
        if self.ai.time < 240:
            return

        # Check if rush is still going on after 4 minutes
        if not self.ai.controllers.under_attack_timer:
            if not self._cancel_rush_timer:
                self._cancel_rush_timer = self.ai.time
            else:
                # If the under attack timer has been off for 25s we're no longer being rushed
                if self._cancel_rush_timer + 25 <= self.ai.time:
                    print(f"Cancelling being rushed state @ {self.ai.time_formatted}")
                    self._being_rushed = False
        elif self._cancel_rush_timer:
            self._cancel_rush_timer = 0

    def _set_cleanup_state(self) -> None:
        if self._cleanup:
            return

        if self.ai.supply_used <= 190 or (
            not all(self.ai.is_visible(exp) for exp in self.ai.expansion_locations_list)
            and self.ai.time < 1200
        ):
            self._cleanup = False
            return

        if (
            self.ai.enemy_units.filter(lambda s: not s.is_flying).amount == 0
            and self.ai.enemy_structures.filter(lambda s: not s.is_flying).amount == 0
        ):
            print("Going into cleanup mode")
            self._cleanup = True
        else:
            if not self.ai.actual_iteration % 50:
                print(
                    f"Not going into cleanup {
                        self.ai.enemy_units.filter(lambda s: not s.is_flying)=
                      } and {
                        self.ai.enemy_structures.filter(lambda s: not s.is_flying)=
                      } @ {self.ai.time_formatted}"
                )

    def _set_enemy_late_nat(self) -> None:
        if self.ai.controllers.enemy_nat_taken:
            return
        if self.ai.time >= 420:
            self._enemy_late_nat = 3
        elif self.ai.time >= 360:
            self._enemy_late_nat = 2
        elif self.ai.time >= 300:
            self._enemy_late_nat = 1

    def _set_being_spine_rushed(self) -> None:
        if self.ai.time > 240:
            self._being_spine_rushed = False

        spines = self.ai.enemy_structures(
            [UnitTypeId.SPINECRAWLER, UnitTypeId.SPINECRAWLERUPROOTED]
        )
        if spines and spines.closest_distance_to(self.ai.start_location) < 15:
            self._being_spine_rushed = True
            self._was_rushed = True
        elif self._being_spine_rushed:
            self._being_spine_rushed = False

    async def update(self) -> None:
        self._set_was_rushed()
        self._set_being_rushed()
        self._set_cleanup_state()
        self._set_enemy_late_nat()
        self._set_being_spine_rushed()

    def being_rushed(self) -> bool:
        return self._being_rushed

    def was_rushed(self) -> bool:
        return self._was_rushed

    def cleanup(self) -> bool:
        return self._cleanup

    def enemy_late_nat(self) -> int:
        return self._enemy_late_nat

    def being_spine_rushed(self) -> bool:
        return self._being_spine_rushed
