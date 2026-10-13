"""Generate synthetic CSVs with a known, exact mix of bad rows.

Every bad row has exactly one defect, so the pipeline's rejection counts must match the
expected counts printed here (also written to <out>.expected.json).

  python scripts/generate_sample.py --rows 1000 --bad-ratio 0.05 --out sample_data/dirty_small.csv
  python scripts/generate_sample.py --poison --out sample_data/poison.csv
  python scripts/generate_sample.py --dataset returns --rows 50 --out sample_data/returns_small.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SALES_HEADER = ["transaction_id", "transaction_date", "product_category", "quantity",
                "unit_price", "region"]
RETURNS_HEADER = ["return_id", "return_date", "product_category", "quantity", "refund_amount",
                  "reason"]
CATEGORIES = {  # category -> (min price, max price)
    "Electronics": (40, 900), "Clothing": (8, 120), "Home & Kitchen": (10, 300),
    "Books": (5, 60), "Sports": (12, 250), "Beauty": (6, 90), "Toys": (7, 150),
}
REGIONS = ["North", "South", "East", "West", "Central"]
RETURN_REASONS = ["damaged", "wrong size", "not as described", "changed mind", "late delivery"]
BAD_KINDS = ["missing_transaction_id", "negative_quantity", "invalid_transaction_date",
             "invalid_unit_price", "duplicate_id"]
BAD_DATES = ["2026-13-45", "31/02/2026", "not-a-date", "2026/10/01"]
BAD_PRICES = ["abc", "12.3.4", "N/A", "$19.99"]


def money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def messy(rng: random.Random, label: str) -> str:
    """Valid but untidy label the transformer must clean: case and whitespace noise."""
    choice = rng.random()
    if choice < 0.15:
        return label.lower()
    if choice < 0.25:
        return f"  {label.upper()} "
    return label


def sales_row(rng: random.Random, i: int, start: date, days: int) -> tuple[list[str], Decimal]:
    category = rng.choice(list(CATEGORIES))
    low, high = CATEGORIES[category]
    price = money(rng.uniform(low, high))
    quantity = rng.randint(1, 10)
    day = start + timedelta(days=rng.randrange(days))
    day_text = day.strftime("%d/%m/%Y") if rng.random() < 0.2 else day.isoformat()
    row = [f"TXN-{i:07d}", day_text, messy(rng, category), str(quantity), str(price),
           messy(rng, rng.choice(REGIONS))]
    return row, (price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def generate_sales(rows: int, bad_ratio: float, seed: int, start: date, days: int):
    rng = random.Random(seed)
    n_bad = round(rows * bad_ratio)
    # Row 0 is always good so duplicate_id rows always have an earlier ID to copy.
    bad_positions = sorted(rng.sample(range(1, rows), n_bad)) if n_bad else []
    kinds = {pos: BAD_KINDS[k % len(BAD_KINDS)] for k, pos in enumerate(bad_positions)}

    out, expected, good_ids = [], Counter(), []
    total_sales = Decimal(0)
    for i in range(rows):
        row, amount = sales_row(rng, i + 1, start, days)
        kind = kinds.get(i)
        if kind is None:
            good_ids.append(row[0])
            total_sales += amount
        elif kind == "missing_transaction_id":
            row[0] = rng.choice(["", "   "])
        elif kind == "negative_quantity":
            row[3] = str(-rng.randint(1, 5))
        elif kind == "invalid_transaction_date":
            row[1] = rng.choice(BAD_DATES)
        elif kind == "invalid_unit_price":
            row[4] = rng.choice(BAD_PRICES)
        elif kind == "duplicate_id":
            row[0] = rng.choice(good_ids)
        if kind:
            expected[kind] += 1
        out.append(row)

    summary = {
        "dataset": "sales",
        "rows": rows,
        "accepted": rows - n_bad,
        "rejected": n_bad,
        "rejection_reasons": dict(sorted(expected.items())),
        "total_sales": float(total_sales),
    }
    return SALES_HEADER, out, summary


def generate_returns(rows: int, seed: int, start: date, days: int):
    rng = random.Random(seed)
    out, total = [], Decimal(0)
    for i in range(rows):
        refund = money(rng.uniform(5, 400))
        total += refund
        out.append([f"RET-{i + 1:06d}", (start + timedelta(days=rng.randrange(days))).isoformat(),
                    rng.choice(list(CATEGORIES)), str(rng.randint(1, 3)), str(refund),
                    rng.choice(RETURN_REASONS)])
    summary = {"dataset": "returns", "rows": rows, "accepted": rows, "rejected": 0,
               "rejection_reasons": {}, "total_refunds": float(total)}
    return RETURNS_HEADER, out, summary


def generate_poison(rows: int, seed: int, start: date, days: int):
    """A sales file without the required unit_price column: the whole file must be rejected."""
    header, data, _ = generate_sales(rows, 0, seed, start, days)
    drop = header.index("unit_price")
    summary = {"dataset": "sales", "rows": rows, "poison": True,
               "missing_columns": ["unit_price"]}
    return ([h for k, h in enumerate(header) if k != drop],
            [[v for k, v in enumerate(r) if k != drop] for r in data], summary)


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--bad-ratio", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 9, 1))
    parser.add_argument("--days", type=int, default=40)
    parser.add_argument("--dataset", choices=["sales", "returns"], default="sales")
    parser.add_argument("--poison", action="store_true",
                        help="write a sales file missing a required column")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 0 <= args.bad_ratio < 1:
        parser.error("--bad-ratio must be in [0, 1)")

    if args.poison:
        header, rows, summary = generate_poison(20, args.seed, args.start_date, args.days)
    elif args.dataset == "returns":
        header, rows, summary = generate_returns(args.rows, args.seed, args.start_date,
                                                 args.days)
    else:
        header, rows, summary = generate_sales(args.rows, args.bad_ratio, args.seed,
                                               args.start_date, args.days)

    write_csv(args.out, header, rows)
    args.out.with_suffix(".expected.json").write_text(json.dumps(summary, indent=2) + "\n",
                                                      encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
