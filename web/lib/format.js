// Market-aware number formatting.
//
// Bursa's own tick size shrinks to 0.5 sen (RM0.005) below RM1, so a flat 2dp
// would show two genuinely different ticks (0.070 vs 0.075) as the same
// "0.07"/"0.08" — real information loss on the many sub-RM1 counters on this
// board. But at 3.770 (99SMART, tick = 1 sen once price clears RM3) the third
// decimal is always a trailing zero — noise, not precision. So: 2dp once the
// tick itself is a full sen (price >= RM1), 3dp only below that, where it is
// load-bearing. Matches the scanner's stored candle precision either way.

export const CCY = { MY: "RM", US: "$" };
export const DP = { MY: 3, US: 2 };
export const LOT = { MY: 100, US: 1 };

export const ccy = (market) => CCY[market] ?? "RM";
export const lotSize = (market) => LOT[market] ?? 1;

/** Decimal places for THIS value on THIS market — sub-RM1 Bursa counters
 * keep tick-level precision; everything else is 2dp. */
export function dp(market, v) {
  const base = DP[market] ?? 3;
  return (market === "MY" || base > 2) && v != null && Math.abs(Number(v)) < 1
    ? base : 2;
}

/** Bare price, no currency symbol — for table cells and chart axes. */
export function price(v, market = "MY") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(dp(market, v));
}

/** Price with currency prefix — for headline figures and the calculator. */
export function money(v, market = "MY") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return ccy(market) + Number(v).toFixed(dp(market, v));
}

/** Large cash amounts (position size, traded value) — no sub-cent noise. */
export function cash(v, market = "MY") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return ccy(market) + Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

export function pct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return `${Number(v).toFixed(digits)}%`;
}
