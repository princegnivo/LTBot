class MartingaleEngine:
    def __init__(self, base_stake, multiplier, max_steps):
        self.base_stake = base_stake
        self.multiplier = multiplier
        self.max_steps = max_steps
        self.reset()

    def reset(self):
        self.current_step = 1
        self.current_stake = self.base_stake

    def get_current_stake(self):
        return round(self.current_stake, 2)

    def register_result(self, won: bool):
        """Retourne dict: done, won, step, next_stake (si done=False)."""
        if won:
            result = {"done": True, "won": True, "step": self.current_step}
            self.reset()
            return result

        if self.current_step >= self.max_steps:
            result = {"done": True, "won": False, "step": self.current_step}
            self.reset()
            return result

        self.current_step += 1
        self.current_stake *= self.multiplier
        return {
            "done": False,
            "won": False,
            "step": self.current_step,
            "next_stake": self.get_current_stake(),
        }
