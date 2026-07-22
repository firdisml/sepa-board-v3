// Market-aware number formatting.
//
// Bursa quotes to THREE decimals and much of the board trades under RM1
// (0.610, 0.075). The inherited US formatting rounded everything to 2dp, which
// collapses distinct ticks onto one number and makes a pivot, a stop and a
// price look identical when they are not. Precision here must match the
// scanner, which stores MY candles at 3dp.

export const CCY = { MY: "RM", US: "$" };
export const DP = { MY: 3, US: 2 };
export const LOT = { MY: 100, US: 1 };

export const ccy = (market) => CCY[market] ?? "RM";
export const dp = (market) => DP[market] ?? 3;
export const lotSize = (market) => LOT[market] ?? 1;

/** Bare price, no currency symbol — for table cells and chart axes. */
export function price(v, market = "MY") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(dp(market));
}

/** Price with currency prefix — for headline figures and the calculator. */
export function money(v, market = "MY") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return ccy(market) + Number(v).toFixed(dp(market));
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
