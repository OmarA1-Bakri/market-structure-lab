export type PageId =
  | "overview"
  | "data"
  | "auction"
  | "features"
  | "discovery"
  | "artifacts"

export type SymbolState =
  | "recovered"
  | "partially_recovered"
  | "provider_absent"
  | "source_conflict"

export interface SymbolHealth {
  symbol: string
  status: SymbolState
  compatibility: "compatible" | "source_conflict"
  before: number
  recovered: number
  remaining: number
  current: boolean
}

export interface FeatureDefinition {
  name: string
  family: "auction" | "sequence"
  units: string
  prior: number
  leakage: "AT_CUTOFF" | "TRAILING_ONLY"
  definition: string
}

export const evidenceTimestamp = "2026-07-16T11:23:00Z"

export const phases = [
  { label: "00", name: "Foundation", state: "complete" },
  { label: "01", name: "Data truth", state: "complete" },
  { label: "02", name: "Auction representation", state: "complete" },
  { label: "03", name: "Features & events", state: "complete" },
  { label: "04", name: "Discovery software", state: "complete" },
  { label: "04R", name: "Real market discovery", state: "next" },
  { label: "05", name: "Untouched validation", state: "locked" },
  { label: "06", name: "Strategy & backtest", state: "locked" },
  { label: "07", name: "Paper & live", state: "locked" },
] as const

export const headlineMetrics = {
  canonicalRows: 60_796_518,
  immutableRows: 35_748_117,
  supplements: 25_048_401,
  symbols: 25,
  sourceGapRuns: 19_192,
  sourceMissingMinutes: 23_372_460,
  recoveredLatest: 8_567_720,
  remainingLatest: 7_731_254,
}

export const verification = {
  tests: 711,
  postgresProfiles: 3,
  sourceFilesTyped: 68,
  benchmarkCandles: 100_000,
  benchmarkThroughput: 730.1536,
  peakMemoryMib: 1.0248,
  equivalenceChecks: 512,
  absoluteTolerance: 1e-12,
}

export const symbolHealth: SymbolHealth[] = [
  {
    symbol: "ADAUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 11_183,
    recovered: 6_442,
    remaining: 4_741,
    current: false,
  },
  {
    symbol: "ALGOUSDT",
    status: "partially_recovered",
    compatibility: "compatible",
    before: 2_777_493,
    recovered: 2_776_499,
    remaining: 994,
    current: false,
  },
  {
    symbol: "APTUSDT",
    status: "recovered",
    compatibility: "compatible",
    before: 781_162,
    recovered: 781_162,
    remaining: 0,
    current: true,
  },
  {
    symbol: "ARUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 63_179,
    recovered: 0,
    remaining: 63_179,
    current: false,
  },
  {
    symbol: "AVAXUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 826_208,
    recovered: 0,
    remaining: 826_208,
    current: false,
  },
  {
    symbol: "BNBUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 8_303,
    recovered: 3_562,
    remaining: 4_741,
    current: false,
  },
  {
    symbol: "BTCUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 1_738_856,
    recovered: 0,
    remaining: 1_738_856,
    current: false,
  },
  {
    symbol: "DOGEUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 8_092,
    recovered: 5_002,
    remaining: 3_090,
    current: false,
  },
  {
    symbol: "DOTUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 4_946,
    recovered: 3_501,
    remaining: 1_445,
    current: false,
  },
  {
    symbol: "ETHUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 1_414_997,
    recovered: 0,
    remaining: 1_414_997,
    current: false,
  },
  {
    symbol: "FETUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 1_761_329,
    recovered: 0,
    remaining: 1_761_329,
    current: false,
  },
  {
    symbol: "FTMUSDT",
    status: "partially_recovered",
    compatibility: "compatible",
    before: 956_842,
    recovered: 165_779,
    remaining: 791_063,
    current: false,
  },
  {
    symbol: "IMXUSDT",
    status: "recovered",
    compatibility: "compatible",
    before: 1_234_762,
    recovered: 1_234_762,
    remaining: 0,
    current: true,
  },
  {
    symbol: "INJUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 5_186,
    recovered: 3_741,
    remaining: 1_445,
    current: false,
  },
  {
    symbol: "LINKUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 7_723,
    recovered: 3_613,
    remaining: 4_110,
    current: false,
  },
  {
    symbol: "NEARUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 4_921,
    recovered: 3_477,
    remaining: 1_444,
    current: false,
  },
  {
    symbol: "PENDLEUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 62_673,
    recovered: 0,
    remaining: 62_673,
    current: false,
  },
  {
    symbol: "RENDERUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 62_139,
    recovered: 0,
    remaining: 62_139,
    current: false,
  },
  {
    symbol: "SOLUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 918_306,
    recovered: 0,
    remaining: 918_306,
    current: false,
  },
  {
    symbol: "SUIUSDT",
    status: "source_conflict",
    compatibility: "source_conflict",
    before: 62_606,
    recovered: 0,
    remaining: 62_606,
    current: false,
  },
  {
    symbol: "TAOUSDT",
    status: "recovered",
    compatibility: "compatible",
    before: 4_066,
    recovered: 4_066,
    remaining: 0,
    current: true,
  },
  {
    symbol: "THETAUSDT",
    status: "recovered",
    compatibility: "compatible",
    before: 735_082,
    recovered: 735_082,
    remaining: 0,
    current: true,
  },
  {
    symbol: "XLMUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 62_137,
    recovered: 61_144,
    remaining: 993,
    current: false,
  },
  {
    symbol: "XRPUSDT",
    status: "provider_absent",
    compatibility: "compatible",
    before: 9_290,
    recovered: 3_388,
    remaining: 5_902,
    current: false,
  },
  {
    symbol: "ZECUSDT",
    status: "partially_recovered",
    compatibility: "compatible",
    before: 2_777_493,
    recovered: 2_776_500,
    remaining: 993,
    current: false,
  },
]

export const featureDefinitions: FeatureDefinition[] = [
  {
    name: "auction_location",
    family: "auction",
    units: "category",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Current observable auction-location category.",
  },
  {
    name: "poc_distance_close",
    family: "auction",
    units: "fraction_of_close",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Distance from close to the current POC.",
  },
  {
    name: "poc_velocity_close_1",
    family: "auction",
    units: "fraction_of_previous_close",
    prior: 1,
    leakage: "TRAILING_ONLY",
    definition: "One-observation POC velocity.",
  },
  {
    name: "value_width_close",
    family: "auction",
    units: "fraction_of_close",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Current VAH-to-VAL width relative to close.",
  },
  {
    name: "value_midpoint_velocity_close_1",
    family: "auction",
    units: "fraction_of_previous_close",
    prior: 1,
    leakage: "TRAILING_ONLY",
    definition: "One-observation value-midpoint velocity.",
  },
  {
    name: "poc_volume_share",
    family: "auction",
    units: "fraction",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Current POC-bin share of profile volume.",
  },
  {
    name: "vwap_distance_close",
    family: "auction",
    units: "fraction_of_close",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Distance from close to current VWAP.",
  },
  {
    name: "vwap_slope_close_1",
    family: "auction",
    units: "fraction_of_previous_close",
    prior: 1,
    leakage: "TRAILING_ONLY",
    definition: "One-observation VWAP slope.",
  },
  {
    name: "close_value_position",
    family: "auction",
    units: "fraction",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Close position inside the current value area.",
  },
  {
    name: "value_area_jaccard_1",
    family: "auction",
    units: "fraction",
    prior: 1,
    leakage: "TRAILING_ONLY",
    definition: "Overlap of previous and current value-area bins.",
  },
  {
    name: "nearest_hvn_distance_close",
    family: "auction",
    units: "fraction_of_close",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Signed distance to the nearest current HVN zone.",
  },
  {
    name: "nearest_lvn_distance_close",
    family: "auction",
    units: "fraction_of_close",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Signed distance to the nearest current LVN zone.",
  },
  {
    name: "max_node_persistence_bars",
    family: "auction",
    units: "bars",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Longest current profile-node persistence.",
  },
  {
    name: "inside_value_rate_20",
    family: "auction",
    units: "fraction",
    prior: 19,
    leakage: "TRAILING_ONLY",
    definition: "Rate of observations inside value over 20 snapshots.",
  },
  {
    name: "value_reentry_rate_20",
    family: "auction",
    units: "events_per_bar",
    prior: 19,
    leakage: "TRAILING_ONLY",
    definition: "Value re-entry event rate over 20 snapshots.",
  },
  {
    name: "location_dwell_bars",
    family: "auction",
    units: "bars",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Consecutive observations in the current location.",
  },
  {
    name: "log_return_1",
    family: "sequence",
    units: "log_return",
    prior: 1,
    leakage: "TRAILING_ONLY",
    definition: "Natural log of current close over previous close.",
  },
  {
    name: "range_close_fraction",
    family: "sequence",
    units: "fraction_of_close",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Candle range relative to close.",
  },
  {
    name: "body_range_ratio",
    family: "sequence",
    units: "fraction_of_range",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Signed candle body relative to range.",
  },
  {
    name: "upper_wick_range_ratio",
    family: "sequence",
    units: "fraction_of_range",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Upper wick relative to candle range.",
  },
  {
    name: "lower_wick_range_ratio",
    family: "sequence",
    units: "fraction_of_range",
    prior: 0,
    leakage: "AT_CUTOFF",
    definition: "Lower wick relative to candle range.",
  },
  {
    name: "log_volume_ratio_1",
    family: "sequence",
    units: "log_ratio",
    prior: 1,
    leakage: "TRAILING_ONLY",
    definition: "Natural log of current volume over previous volume.",
  },
  {
    name: "realized_volatility_20",
    family: "sequence",
    units: "log_return",
    prior: 20,
    leakage: "TRAILING_ONLY",
    definition: "Trailing root-mean-square log return.",
  },
  {
    name: "volatility_normalized_return_20",
    family: "sequence",
    units: "ratio",
    prior: 20,
    leakage: "TRAILING_ONLY",
    definition: "Current return divided by trailing realized volatility.",
  },
  {
    name: "return_autocorrelation_1_20",
    family: "sequence",
    units: "correlation",
    prior: 20,
    leakage: "TRAILING_ONLY",
    definition: "Lag-one autocorrelation over trailing returns.",
  },
  {
    name: "volume_relative_median_20",
    family: "sequence",
    units: "fraction",
    prior: 19,
    leakage: "TRAILING_ONLY",
    definition: "Current volume relative to trailing median volume.",
  },
]

export const auctionFrames = [
  {
    minute: 0,
    price: 100,
    volume: 100,
    bins: [
      { index: 100, volume: 100 },
    ],
    poc: 100,
    val: 100,
    vah: 100,
    vwap: 100,
    location: "point_of_control",
    active: [0],
  },
  {
    minute: 1,
    price: 101,
    volume: 20,
    bins: [
      { index: 100, volume: 100 },
      { index: 101, volume: 20 },
    ],
    poc: 100,
    val: 100,
    vah: 100,
    vwap: 100.1666666667,
    location: "above_value",
    active: [0, 1],
  },
  {
    minute: 2,
    price: 102,
    volume: 30,
    bins: [
      { index: 100, volume: 100 },
      { index: 101, volume: 20 },
      { index: 102, volume: 30 },
    ],
    poc: 100,
    val: 100,
    vah: 101,
    vwap: 100.5333333333,
    location: "above_value",
    active: [0, 1, 2],
  },
  {
    minute: 3,
    price: 103,
    volume: 40,
    bins: [
      { index: 101, volume: 20 },
      { index: 102, volume: 30 },
      { index: 103, volume: 40 },
    ],
    poc: 103,
    val: 102,
    vah: 103,
    vwap: 102.2222222222,
    location: "point_of_control",
    active: [1, 2, 3],
  },
] as const

export const discoveryFixture = {
  stable: {
    runId: "DR-000601",
    status: "completed",
    manifest:
      "86d754f5be1c3d0b5e2a9d403a7af9c814231f7679421a560f14dc1bbc65b8f2",
    behaviours: 2,
    motifs: 6,
    transitions: 2,
  },
  rejected: {
    runId: "DR-000602",
    status: "rejected_unstable",
    manifest:
      "cbadd46a15144ce89424b40c0d84687ce99764725b323d4f2f9dbb3b66f9e500",
    behaviours: 0,
    motifs: 6,
    transitions: 2,
  },
  rows: { discovery: 16, development: 8 },
  behaviours: [
    {
      id: "B-AFFBC6438DE4E321",
      name: "Low Volume Auction State",
      centroid: [-10.05, -1.005],
      frequency: 8,
      duration: 60,
      coverage: ["BTCUSDT", "ETHUSDT"],
      seedAri: 1,
      subsampleAri: 1,
      perturbationAri: 0.7272727273,
      hypothesis:
        "Test 15-minute forward returns and realized volatility against time-matched negative controls.",
      caution:
        "Synthetic fixture separation is unusually strong and transition support contains only two dwell-run events.",
    },
    {
      id: "B-CC5D0EE76718D3E7",
      name: "High Volume Auction State",
      centroid: [10.05, 1.005],
      frequency: 8,
      duration: 60,
      coverage: ["BTCUSDT", "ETHUSDT"],
      seedAri: 1,
      subsampleAri: 1,
      perturbationAri: 0.7272727273,
      hypothesis:
        "Test 15-minute forward returns and realized volatility against time-matched negative controls.",
      caution:
        "Synthetic fixture separation is unusually strong and OHLCV-derived auction location remains approximate.",
    },
  ],
}

export const formatCompact = (value: number) =>
  new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value)

export const formatNumber = (value: number) =>
  new Intl.NumberFormat("en").format(value)

export const formatPercent = (value: number) =>
  new Intl.NumberFormat("en", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value)
