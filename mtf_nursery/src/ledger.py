"""SQLite ledger — positions, EMI schedule, steps, orders (C1, no broker)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from emi import compute_emi_schedule
from emi_verify import EmiStatus, PaidVia, VerifyResult, verify_emi_repaid
from gates import BuyGateInput, GateResult, SellGateInput, evaluate_buy_gate, evaluate_sell_gate

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    step_no INTEGER PRIMARY KEY,
    ticket_amount REAL NOT NULL,
    force_count INTEGER NOT NULL DEFAULT 0,
    advanced INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    qty INTEGER NOT NULL,
    avg_price REAL NOT NULL,
    buy_value REAL NOT NULL,
    initial_margin REAL NOT NULL,
    funded_baseline REAL NOT NULL,
    buffer_10pct REAL NOT NULL,
    broker_remaining0 REAL NOT NULL,
    weekly_emi REAL NOT NULL,
    funded_current REAL,
    status TEXT NOT NULL DEFAULT 'open_mtf',
    step_id INTEGER NOT NULL,
    force_tag TEXT,
    product TEXT NOT NULL DEFAULT 'MTF',
    created_at TEXT NOT NULL,
    FOREIGN KEY (step_id) REFERENCES steps(step_no)
);

CREATE TABLE IF NOT EXISTS emi_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    paid_at TEXT,
    paid_via TEXT,
    funded_snapshot_before REAL,
    funding_order_id TEXT,
    UNIQUE(position_id, installment_no),
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS orders_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    qty INTEGER,
    product TEXT,
    mode TEXT,
    reason TEXT,
    gate_results TEXT,
    broker_order_id TEXT,
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS cash_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    res_date TEXT NOT NULL,
    purpose TEXT NOT NULL,
    amount REAL NOT NULL,
    UNIQUE(res_date, purpose)
);

CREATE INDEX IF NOT EXISTS idx_emi_due ON emi_schedule(due_date, status);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders_log(ts);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _date_iso(d: date) -> str:
    return d.isoformat()


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


@dataclass(frozen=True)
class Position:
    id: int
    symbol: str
    buy_date: date
    qty: int
    avg_price: float
    buy_value: float
    initial_margin: float
    funded_baseline: float
    buffer_10pct: float
    broker_remaining0: float
    weekly_emi: float
    funded_current: float | None
    status: str
    step_id: int
    force_tag: str | None
    product: str


@dataclass(frozen=True)
class EmiRow:
    id: int
    position_id: int
    symbol: str
    installment_no: int
    due_date: date
    amount: float
    status: str
    paid_at: str | None
    paid_via: str | None
    funded_snapshot_before: float | None


@dataclass(frozen=True)
class Step:
    step_no: int
    ticket_amount: float
    force_count: int
    advanced: bool


class Ledger:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._tx() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
                ("version", str(SCHEMA_VERSION)),
            )

    def ensure_step(self, step_no: int, ticket_amount: float) -> Step:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO steps(step_no, ticket_amount, force_count, advanced)
                VALUES (?, ?, 0, 0)
                ON CONFLICT(step_no) DO NOTHING
                """,
                (step_no, ticket_amount),
            )
        step = self.get_step(step_no)
        assert step is not None
        return step

    def get_step(self, step_no: int) -> Step | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM steps WHERE step_no = ?", (step_no,)
            ).fetchone()
        if row is None:
            return None
        return Step(
            step_no=row["step_no"],
            ticket_amount=row["ticket_amount"],
            force_count=row["force_count"],
            advanced=bool(row["advanced"]),
        )

    def current_ticket(self, default: float = 15000.0) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ticket_amount FROM steps ORDER BY step_no DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return default
        return float(row["ticket_amount"])

    def add_position(
        self,
        symbol: str,
        buy_date: date,
        qty: int,
        avg_price: float,
        initial_margin: float,
        *,
        step_id: int = 1,
        buffer_pct: float = 0.10,
        emi_weeks: int = 16,
    ) -> Position:
        buy_value = round(qty * avg_price, 2)
        sched = compute_emi_schedule(
            buy_date, buy_value, initial_margin, buffer_pct=buffer_pct, emi_weeks=emi_weeks
        )
        funded_baseline = round(buy_value - initial_margin, 2)
        created_at = _utc_now_iso()

        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO positions(
                    symbol, buy_date, qty, avg_price, buy_value, initial_margin,
                    funded_baseline, buffer_10pct, broker_remaining0, weekly_emi,
                    funded_current, status, step_id, product, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open_mtf', ?, 'MTF', ?)
                """,
                (
                    symbol,
                    _date_iso(buy_date),
                    qty,
                    avg_price,
                    buy_value,
                    initial_margin,
                    funded_baseline,
                    sched.buffer,
                    sched.broker_remaining,
                    sched.weekly_emi,
                    funded_baseline,
                    step_id,
                    created_at,
                ),
            )
            position_id = cur.lastrowid
            for inst in sched.installments:
                conn.execute(
                    """
                    INSERT INTO emi_schedule(
                        position_id, installment_no, due_date, amount, status
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        position_id,
                        inst.installment_no,
                        _date_iso(inst.due_date),
                        inst.amount,
                        EmiStatus.SCHEDULED.value,
                    ),
                )

        pos = self.get_position(position_id)
        assert pos is not None
        return pos

    def get_position(self, position_id: int) -> Position | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?", (position_id,)
            ).fetchone()
        return _row_to_position(row) if row else None

    def list_positions(self, status: str | None = None) -> list[Position]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status = ? ORDER BY id", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM positions ORDER BY id").fetchall()
        return [_row_to_position(r) for r in rows]

    def remaining_emi_obligation(self, as_of: date | None = None) -> float:
        """Sum all unpaid EMI amounts on open MTF positions (includes overdue)."""
        as_of = as_of or date.today()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(e.amount), 0) AS total
                FROM emi_schedule e
                JOIN positions p ON p.id = e.position_id
                WHERE p.status = 'open_mtf'
                  AND e.status IN (?, ?, ?, ?)
                """,
                (
                    EmiStatus.SCHEDULED.value,
                    EmiStatus.DUE.value,
                    EmiStatus.PENDING_REPAY.value,
                    EmiStatus.OVERDUE.value,
                ),
            ).fetchone()
        return round(float(row["total"]), 2)

    def refresh_emi_statuses(self, as_of: date | None = None) -> int:
        """Mark due/overdue EMIs; return count changed to due+overdue+pending."""
        as_of = as_of or date.today()
        as_of_s = _date_iso(as_of)
        changed = 0
        with self._tx() as conn:
            cur = conn.execute(
                """
                UPDATE emi_schedule
                SET status = ?
                WHERE status = ? AND due_date = ?
                """,
                (EmiStatus.DUE.value, EmiStatus.SCHEDULED.value, as_of_s),
            )
            changed += cur.rowcount
            cur = conn.execute(
                """
                UPDATE emi_schedule
                SET status = ?
                WHERE status IN (?, ?) AND due_date < ?
                """,
                (
                    EmiStatus.OVERDUE.value,
                    EmiStatus.SCHEDULED.value,
                    EmiStatus.DUE.value,
                    as_of_s,
                ),
            )
            changed += cur.rowcount
        return changed

    def list_emis_needing_alert(self, as_of: date | None = None) -> list[EmiRow]:
        """EMIs that are due, pending repay, or overdue (for Telegram nag)."""
        as_of = as_of or date.today()
        self.refresh_emi_statuses(as_of)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.symbol
                FROM emi_schedule e
                JOIN positions p ON p.id = e.position_id
                WHERE p.status = 'open_mtf'
                  AND e.status IN (?, ?, ?)
                ORDER BY e.due_date, e.installment_no
                """,
                (
                    EmiStatus.DUE.value,
                    EmiStatus.PENDING_REPAY.value,
                    EmiStatus.OVERDUE.value,
                ),
            ).fetchall()
        return [_row_to_emi(r) for r in rows]

    def mark_emi_pending_repay(self, emi_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE emi_schedule SET status = ?
                WHERE id = ? AND status IN (?, ?, ?)
                """,
                (
                    EmiStatus.PENDING_REPAY.value,
                    emi_id,
                    EmiStatus.DUE.value,
                    EmiStatus.OVERDUE.value,
                    EmiStatus.SCHEDULED.value,
                ),
            )

    def set_emi_funded_snapshot(self, emi_id: int, funded_before: float) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE emi_schedule SET funded_snapshot_before = ? WHERE id = ?",
                (funded_before, emi_id),
            )

    def confirm_emi_verified(
        self,
        emi_id: int,
        *,
        paid_via: PaidVia,
        funded_after: float | None = None,
    ) -> bool:
        with self._tx() as conn:
            emi = conn.execute(
                "SELECT * FROM emi_schedule WHERE id = ?", (emi_id,)
            ).fetchone()
            if emi is None or emi["status"] == EmiStatus.VERIFIED.value:
                return False
            conn.execute(
                """
                UPDATE emi_schedule
                SET status = ?, paid_at = ?, paid_via = ?
                WHERE id = ?
                """,
                (EmiStatus.VERIFIED.value, _utc_now_iso(), paid_via.value, emi_id),
            )
            if funded_after is not None:
                conn.execute(
                    "UPDATE positions SET funded_current = ? WHERE id = ?",
                    (funded_after, emi["position_id"]),
                )
        return True

    def try_verify_emi_from_funded(
        self,
        emi_id: int,
        funded_now: float,
        *,
        tolerance: float = 50.0,
    ) -> VerifyResult:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT e.*, p.funded_current, p.funded_baseline
                FROM emi_schedule e
                JOIN positions p ON p.id = e.position_id
                WHERE e.id = ?
                """,
                (emi_id,),
            ).fetchone()
        if row is None:
            return VerifyResult(verified=False, funded_drop=0.0, reason="emi_not_found")
        if row["status"] == EmiStatus.VERIFIED.value:
            return VerifyResult(verified=True, funded_drop=0.0, reason="already_verified")

        funded_before = row["funded_snapshot_before"]
        if funded_before is None:
            funded_before = row["funded_current"] or row["funded_baseline"]
        self.set_emi_funded_snapshot(emi_id, float(funded_before))

        result = verify_emi_repaid(
            float(row["amount"]),
            float(funded_before),
            funded_now,
            tolerance=tolerance,
        )
        if result.verified:
            self.confirm_emi_verified(
                emi_id, paid_via=PaidVia.API, funded_after=funded_now
            )
        else:
            self.mark_emi_pending_repay(emi_id)
        return result

    def confirm_emi_manual(self, emi_id: int) -> bool:
        return self.confirm_emi_verified(emi_id, paid_via=PaidVia.MANUAL)

    def count_buys_on(self, on: date) -> int:
        prefix = _date_iso(on)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM orders_log
                WHERE side = 'buy' AND ts LIKE ?
                """,
                (f"{prefix}%",),
            ).fetchone()
        return int(row["c"])

    def count_buys_in_month(self, year: int, month: int) -> int:
        prefix = f"{year:04d}-{month:02d}"
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM orders_log
                WHERE side = 'buy' AND ts LIKE ?
                """,
                (f"{prefix}%",),
            ).fetchone()
        return int(row["c"])

    def count_sells_on(self, on: date) -> int:
        prefix = _date_iso(on)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM orders_log
                WHERE side = 'sell' AND ts LIKE ?
                """,
                (f"{prefix}%",),
            ).fetchone()
        return int(row["c"])

    def log_order_intent(
        self,
        side: str,
        symbol: str,
        *,
        qty: int | None = None,
        product: str = "MTF",
        mode: str = "dry_run",
        reason: str = "",
        gate_results: GateResult | None = None,
        idempotency_key: str | None = None,
    ) -> int | None:
        """Insert order intent; skip if idempotency_key already exists."""
        gate_json = json.dumps(
            {"allowed": gate_results.allowed, "reasons": list(gate_results.reasons)}
        ) if gate_results else None
        try:
            with self._tx() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO orders_log(
                        ts, side, symbol, qty, product, mode, reason,
                        gate_results, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _utc_now_iso(),
                        side,
                        symbol,
                        qty,
                        product,
                        mode,
                        reason,
                        gate_json,
                        idempotency_key,
                    ),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def set_cash_reservation(self, res_date: date, purpose: str, amount: float) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO cash_reservations(res_date, purpose, amount)
                VALUES (?, ?, ?)
                ON CONFLICT(res_date, purpose) DO UPDATE SET amount = excluded.amount
                """,
                (_date_iso(res_date), purpose, amount),
            )

    def get_cash_reservation(self, res_date: date, purpose: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT amount FROM cash_reservations
                WHERE res_date = ? AND purpose = ?
                """,
                (_date_iso(res_date), purpose),
            ).fetchone()
        return float(row["amount"]) if row else 0.0

    def evaluate_buy(
        self,
        free_cash: float,
        ticket_immediate_need: float,
        *,
        as_of: date | None = None,
        fire_shop_reserve: float = 6000.0,
        max_buys_per_day: int = 1,
        max_mtf_buys_per_month: int = 2,
        buy_blocked_by_rms: bool = False,
    ) -> GateResult:
        as_of = as_of or date.today()
        self.refresh_emi_statuses(as_of)
        inp = BuyGateInput(
            free_cash=free_cash,
            emi_obligation=self.remaining_emi_obligation(as_of),
            fire_shop_reserve=fire_shop_reserve,
            ticket_immediate_need=ticket_immediate_need,
            buys_today=self.count_buys_on(as_of),
            buys_this_month=self.count_buys_in_month(as_of.year, as_of.month),
            max_buys_per_day=max_buys_per_day,
            max_mtf_buys_per_month=max_mtf_buys_per_month,
            buy_blocked_by_rms=buy_blocked_by_rms,
        )
        return evaluate_buy_gate(inp)

    def evaluate_sell(
        self,
        has_eligible_winner: bool,
        *,
        as_of: date | None = None,
        max_sells_per_day: int = 1,
    ) -> GateResult:
        as_of = as_of or date.today()
        inp = SellGateInput(
            sells_today=self.count_sells_on(as_of),
            max_sells_per_day=max_sells_per_day,
            has_eligible_winner=has_eligible_winner,
        )
        return evaluate_sell_gate(inp)

    def status_summary(self, as_of: date | None = None) -> dict[str, Any]:
        as_of = as_of or date.today()
        self.refresh_emi_statuses(as_of)
        open_pos = self.list_positions(status="open_mtf")
        pending_emis = self.list_emis_needing_alert(as_of)
        return {
            "as_of": _date_iso(as_of),
            "open_positions": len(open_pos),
            "remaining_emi_obligation": self.remaining_emi_obligation(as_of),
            "current_ticket": self.current_ticket(),
            "pending_emi_alerts": [
                {
                    "emi_id": e.id,
                    "symbol": e.symbol,
                    "installment_no": e.installment_no,
                    "due_date": _date_iso(e.due_date),
                    "amount": e.amount,
                    "status": e.status,
                }
                for e in pending_emis
            ],
            "buys_today": self.count_buys_on(as_of),
            "buys_this_month": self.count_buys_in_month(as_of.year, as_of.month),
        }


def _row_to_position(row: sqlite3.Row) -> Position:
    return Position(
        id=row["id"],
        symbol=row["symbol"],
        buy_date=_parse_date(row["buy_date"]),
        qty=row["qty"],
        avg_price=row["avg_price"],
        buy_value=row["buy_value"],
        initial_margin=row["initial_margin"],
        funded_baseline=row["funded_baseline"],
        buffer_10pct=row["buffer_10pct"],
        broker_remaining0=row["broker_remaining0"],
        weekly_emi=row["weekly_emi"],
        funded_current=row["funded_current"],
        status=row["status"],
        step_id=row["step_id"],
        force_tag=row["force_tag"],
        product=row["product"],
    )


def _row_to_emi(row: sqlite3.Row) -> EmiRow:
    return EmiRow(
        id=row["id"],
        position_id=row["position_id"],
        symbol=row["symbol"],
        installment_no=row["installment_no"],
        due_date=_parse_date(row["due_date"]),
        amount=row["amount"],
        status=row["status"],
        paid_at=row["paid_at"],
        paid_via=row["paid_via"],
        funded_snapshot_before=row["funded_snapshot_before"],
    )
