# Market Structure

Reference notes for market-structure concepts, data definitions, and modeling assumptions.

## Core auction concepts

- Value: the price region where the auction has accepted trade.
- Point of Control: the highest-volume price level in a profile window.
- Value Area: the price range containing the selected share of traded volume.
- HVN: high-volume node, interpreted as accepted value.
- LVN: low-volume node, interpreted as rejected or inefficient value.
- Balance: bounded auction behavior around accepted value.
- Imbalance: directional movement away from a prior value area.
- Acceptance: time and volume sustained beyond a prior reference.
- Rejection: failed movement beyond a reference followed by return into value.

These definitions should become deterministic code in `src/` before probabilistic models or
strategy research are introduced.
