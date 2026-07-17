export type PageId =
  | "overview"
  | "data"
  | "auction"
  | "features"
  | "discovery"
  | "artifacts"

export const verification = {
  benchmarkCandles: 100_000,
  benchmarkThroughput: 730.1536,
  peakMemoryMib: 1.0248,
  equivalenceChecks: 512,
  absoluteTolerance: 1e-12,
}

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
