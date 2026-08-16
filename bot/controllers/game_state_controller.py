from typing import TYPE_CHECKING

from bot.controllers.controller import Controller
from sc2.ids.unit_typeid import UnitTypeId

if TYPE_CHECKING:
    from bot.main import WilldZergBot


class GameStateController(Controller):
    def __init__(
        self,
        ai: "WilldZergBot",
    ) -> None:
        self.ai = ai

        self._was_rushed = False
        self._cleanup = False
        self._enemy_late_nat = 0
        self._being_spine_rushed = False

    async def start(self) -> None:
        pass

    def _set_was_rushed(self) -> None:
        if not self._was_rushed:
            being_rushed = self.ai.mediator.get_did_enemy_rush
            if being_rushed:
                self._was_rushed = True
                print("Detected that we're being rushed")

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
        self._set_cleanup_state()
        self._set_enemy_late_nat()
        self._set_being_spine_rushed()

    def being_rushed(self) -> bool:
        return self._was_rushed and self.ai.time < 270

    def was_rushed(self) -> bool:
        return self._was_rushed

    def cleanup(self) -> bool:
        return self._cleanup

    def enemy_late_nat(self) -> int:
        return self._enemy_late_nat

    def being_spine_rushed(self) -> bool:
        return self._being_spine_rushed
