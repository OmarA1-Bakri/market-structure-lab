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

## Current deterministic state model

`src.auction.AuctionLocation` classifies the latest close as one of:

- below value
- lower value
- point of control
- upper value
- above value

These states are intentionally simple. They provide the first transition-analysis vocabulary before
more complex acceptance, rejection, balance, and imbalance features are introduced.

## Current structural features

`src.structure.detect_profile_nodes` detects local HVNs and LVNs from adjacent price-bin volume.
Endpoints and flat ties are ignored until wider plateau rules are explicitly researched.

`src.structure.compare_value_migration` compares two profiles and classifies value migration as:

- lower
- overlapping lower
- overlapping
- overlapping higher
- higher

This gives transition-analysis code a deterministic vocabulary for value migration before any
probabilistic modelling is introduced.

## Current transition model

`src.transitions.estimate_transition_matrix` estimates empirical transition probabilities from any
deterministic state sequence. It does not smooth, infer hidden states, or fit a probabilistic model.
Those steps should only be introduced after observed deterministic transitions have enough support.
