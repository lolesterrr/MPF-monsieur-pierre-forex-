/* Chart layer — TradingView Lightweight Charts v5 (Apache 2.0).
 *
 * v5 unified the series API: chart.addSeries(CandlestickSeries, opts), not the
 * v4 chart.addCandlestickSeries(opts). Zones are drawn with a series primitive,
 * since the library has no built-in box/rectangle.
 *
 * Exposes: Chart.mount(el), Chart.render(data), Chart.theme()
 */
const Chart = (() => {
  const LWC = () => window.LightweightCharts;

  let chart = null, candles = null, zonePrim = null, el = null;
  let priceLines = [];
  let state = { zones: [], asia: null, colors: {} };

  /* ---- theme ---- */
  function colors() {
    const c = getComputedStyle(document.documentElement);
    const v = n => c.getPropertyValue(n).trim();
    return {
      ink: v("--ink"), soft: v("--ink-soft"), panel: v("--panel"),
      crown: v("--crown"), deep: v("--crown-deep"),
      wattle: v("--wattle"), cool: v("--cool"), tone: v("--tone"),
    };
  }

  function rgba(hex, a) {
    const h = hex.replace("#", "");
    const n = h.length === 3
      ? h.split("").map(x => parseInt(x + x, 16))
      : [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
    return `rgba(${n[0]},${n[1]},${n[2]},${a})`;
  }

  /* ---- zone primitive -------------------------------------------------
   * Lightweight Charts draws series, not boxes. A primitive gets a canvas
   * and the same coordinate converters the chart uses, so the zones stay
   * pinned to price and time through every zoom and pan.
   */
  function makeZonePrimitive() {
    let series = null, api = null;

    const view = {
      zOrder: () => "bottom",
      renderer: () => ({
        draw(target) {
          if (!series || !api) return;
          target.useBitmapCoordinateSpace(scope => {
            const ctx = scope.context;
            const hx = scope.horizontalPixelRatio, vy = scope.verticalPixelRatio;
            const W = scope.bitmapSize.width;
            const ts = api.timeScale();

            const draw = (top, bottom, from, fill, stroke, dashed) => {
              const y1 = series.priceToCoordinate(top);
              const y2 = series.priceToCoordinate(bottom);
              if (y1 == null || y2 == null) return;

              // A zone created before the visible window starts off-screen
              // left; clamp rather than skip it.
              let x = ts.timeToCoordinate(from);
              if (x == null) x = 0;

              const bx = Math.max(0, x * hx);
              const by = Math.min(y1, y2) * vy;
              const bh = Math.max(1.5, Math.abs(y2 - y1) * vy);

              ctx.fillStyle = fill;
              ctx.fillRect(bx, by, W - bx, bh);

              if (stroke) {
                ctx.strokeStyle = stroke;
                ctx.lineWidth = Math.max(1, hx);
                if (dashed) ctx.setLineDash([4 * hx, 4 * hx]);
                ctx.strokeRect(bx, by, W - bx, bh);
                ctx.setLineDash([]);
              }
            };

            state.zones.forEach(z => draw(
              z.top, z.bottom, z.ts, z.fill, z.stroke, false));

            if (state.asia) {
              const a = state.asia;
              draw(a.top, a.bottom, a.ts, "transparent", state.colors.soft, true);
            }
          });
        },
      }),
    };

    return {
      attached(p) { series = p.series; api = p.chart; },
      detached() { series = null; api = null; },
      updateAllViews() {},
      paneViews() { return [view]; },
    };
  }

  /* ---- mount ---- */
  function mount(container) {
    el = container;
    if (!LWC()) throw new Error("Lightweight Charts failed to load");

    const c = colors();
    chart = LWC().createChart(el, {
      autoSize: true,
      // Pin the locale. The library formats axis labels through Intl, and an
      // odd system locale (en-US@posix) throws mid-render and blanks the chart.
      localization: {
        locale: "en-US",
        priceFormatter: p => p.toFixed(5),
      },
      layout: {
        background: { color: "transparent" },
        textColor: c.soft,
        fontFamily: '"DotGothic16", ui-monospace, monospace',
        fontSize: 11,
        attributionLogo: true,   // Apache 2.0 — leave the credit on
      },
      grid: {
        vertLines: { color: c.tone },
        horzLines: { color: c.tone },
      },
      rightPriceScale: { borderColor: c.ink, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: c.ink, timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: LWC().CrosshairMode.Normal,
        vertLine: { color: c.soft, labelBackgroundColor: c.ink },
        horzLine: { color: c.soft, labelBackgroundColor: c.ink },
      },
    });

    candles = chart.addSeries(LWC().CandlestickSeries, {
      upColor: "transparent",
      downColor: c.ink,
      borderUpColor: c.ink,
      borderDownColor: c.ink,
      wickUpColor: c.ink,
      wickDownColor: c.ink,
      priceFormat: { type: "price", precision: 5, minMove: 0.00001 },
    });

    zonePrim = makeZonePrimitive();
    candles.attachPrimitive(zonePrim);
    return chart;
  }

  /* ---- theme reapply ---- */
  function theme() {
    if (!chart) return;
    const c = colors();
    state.colors = c;
    chart.applyOptions({
      layout: { textColor: c.soft },
      grid: { vertLines: { color: c.tone }, horzLines: { color: c.tone } },
      rightPriceScale: { borderColor: c.ink },
      timeScale: { borderColor: c.ink },
      crosshair: {
        vertLine: { color: c.soft, labelBackgroundColor: c.ink },
        horzLine: { color: c.soft, labelBackgroundColor: c.ink },
      },
    });
    candles.applyOptions({
      downColor: c.ink, borderUpColor: c.ink, borderDownColor: c.ink,
      wickUpColor: c.ink, wickDownColor: c.ink,
    });
    if (state.data) render(state.data);
  }

  /* ---- render ---- */
  function render(d) {
    if (!chart) return;
    state.data = d;
    const c = colors();
    state.colors = c;
    const LS = LWC().LineStyle;

    candles.setData(d.candles.map(b => ({
      time: b.t, open: b.o, high: b.h, low: b.l, close: b.c,
    })));

    /* zones: HTF only, nearest price, oversized ones culled.
       Every timeframe at once is 300+ bands and the chart becomes a smear. */
    const lo = Math.min(...d.candles.map(b => b.l));
    const hi = Math.max(...d.candles.map(b => b.h));
    const span = hi - lo;
    const px = d.meta.price;

    state.zones = d.levels
      .filter(l => l.type !== "swing"
                && (l.tf === "4h" || l.tf === "1d")
                && l.top > lo && l.bottom < hi
                && (l.top - l.bottom) < span * 0.25)
      .sort((a, b) => Math.abs((a.top + a.bottom) / 2 - px)
                    - Math.abs((b.top + b.bottom) / 2 - px))
      .slice(0, 14)
      .map(l => ({
        top: l.top, bottom: l.bottom, ts: l.ts,
        fill: rgba(l.type === "fvg" ? c.crown : c.cool, 0.15),
        stroke: rgba(l.type === "fvg" ? c.crown : c.cool, 0.55),
      }));

    state.asia = d.asia
      ? { top: d.asia.high, bottom: d.asia.low, ts: d.candles[0].t }
      : null;

    /* price lines — the library labels and keeps these pinned itself,
       so no hand-rolled collision nudging any more. */
    priceLines.forEach(l => candles.removePriceLine(l));
    priceLines = [];

    const line = (price, color, title, style) => {
      if (price == null) return;
      priceLines.push(candles.createPriceLine({
        price, color, title,
        lineWidth: 2, lineStyle: style ?? LS.Solid,
        axisLabelVisible: true, axisLabelColor: color,
        axisLabelTextColor: c.panel,
      }));
    };

    if (d.zone) line(d.zone.eq, c.deep, "eq 50%", LS.Dashed);
    if (d.plp) line(d.plp.price, c.wattle, "PLP", LS.Dotted);
    if (d.crt) {
      line(d.crt.eq, c.crown, "crt 50% · T1", LS.Dashed);
      line(d.crt.opposing, c.crown, "crt T2", LS.LargeDashed);
    }
    if (d.model1) {
      line(d.model1.entry, c.ink, "entry");
      line(d.model1.stop, c.wattle, "stop", LS.Dashed);
    }
    if (d.asia) {
      line(d.asia.high, c.soft, "asia H", LS.SparseDotted);
      line(d.asia.low, c.soft, "asia L", LS.SparseDotted);
    }

    chart.timeScale().fitContent();
  }

  function subscribeCrosshair(fn) {
    if (chart) chart.subscribeCrosshairMove(fn);
  }

  return { mount, render, theme, subscribeCrosshair, series: () => candles };
})();
