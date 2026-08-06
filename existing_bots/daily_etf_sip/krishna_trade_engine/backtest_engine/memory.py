import json
from pathlib import Path


MEMORY_FILE = Path("storage/memory/memory.json")
MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)


class StrategyMemory:

    def __init__(self):
        self.memory = self._load()

    def _load(self):
        if not MEMORY_FILE.exists():
            return []

        with open(MEMORY_FILE, "r") as f:
            return json.load(f)

    def _save(self):
        with open(MEMORY_FILE, "w") as f:
            json.dump(self.memory, f, indent=4)

    def _key(self, strategy):
        return str(strategy)

    def exists(self, strategy):
        key = self._key(strategy)
        return any(self._key(s["strategy"]) == key for s in self.memory)

    def add(self, strategy, score, timestamp):
        key = self._key(strategy)

        # update if exists
        for s in self.memory:
            if self._key(s["strategy"]) == key:
                s["score"] = score
                s["timestamp"] = timestamp
                self._save()
                return

        # else add new
        self.memory.append({
            "strategy": strategy,
            "score": score,
            "timestamp": timestamp
        })

        self._save()

    def top_strategies(self, n=10):
        return sorted(self.memory, key=lambda x: x["score"], reverse=True)[:n]
