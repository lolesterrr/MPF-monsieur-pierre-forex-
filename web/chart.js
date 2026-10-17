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

  let chart = null, candles = null, zonePrim = null, el = null, markers = null;
  let priceLines = [];
  let state = {
    zones: [], asia: null, colors: {},
    tf: "1h",
    // Which things to draw. Every key here is read in render() — a key that
    // nothing reads is a switch that does nothing, which is exactly the bug
    // the asia/crt/plp/eq entries were in before.
    show: {
      fvg: true, order_block: true, swing: false,
      asia: true, crt: true, plp: true, eq: true,
    },
    // Order blocks you looked at and rejected. Your judgement outranks the
    // detector's, so a dropped block leaves the chart entirely.
    dropped: {},
  };

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

  // Re-alpha an existing rgba() string.
  function rgba2(s, a) {
    const m = /rgba?\(([^)]+)\)/.exec(s);
    if (!m) return s;
    const p = m[1].split(",").map(x => x.trim());
    return `rgba(${p[0]},${p[1]},${p[2]},${a})`;
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

            state.zones.forEach(z => {
              draw(z.top, z.bottom, z.ts, z.fill, z.stroke, false);
              // Label each zone with the timeframe it came from, so an HTF
              // level is obvious when you are looking at a 5m chart.
              if (!z.label) return;
              const y = series.priceToCoordinate((z.top + z.bottom) / 2);
              if (y == null) return;
              ctx.font = `${Math.round(10 * vy)}px "DotGothic16", monospace`;
              ctx.fillStyle = z.stroke;
              ctx.textBaseline = "middle";
              ctx.fillText(z.label, 6 * hx, y * vy);
            });

            // The focused candle gets a column behind it. Price lines alone
            // meant counting bars by eye to find WHICH candle was meant.
            // The focused zone drawn bright, over the muted ones.
            if (state.focus && state.focus.zone) {
              const z = state.focus.zone;
              const zy1 = series.priceToCoordinate(z.top);
              const zy2 = series.priceToCoordinate(z.bottom);
              let zx = ts.timeToCoordinate(state.focus.ts);
              if (zy1 != null && zy2 != null) {
                if (zx == null) zx = 0;
                const bx = Math.max(0, zx * hx);
                ctx.fillStyle = rgba2(state.focus.stroke, 0.20);
                ctx.fillRect(bx, Math.min(zy1, zy2) * vy,
                             W - bx, Math.abs(zy2 - zy1) * vy);
                ctx.strokeStyle = state.focus.stroke;
                ctx.lineWidth = Math.max(2, 2 * hx);
                ctx.strokeRect(bx, Math.min(zy1, zy2) * vy,
                               W - bx, Math.abs(zy2 - zy1) * vy);
              }
            }

            if (state.focus && state.focus.ts) {
              const fx = ts.timeToCoordinate(state.focus.ts);
              if (fx != null) {
                const bw = Math.max(6, ts.options().barSpacing || 8) * hx;
                const cx = fx * hx;
                ctx.fillStyle = state.focus.fill;
                ctx.fillRect(cx - bw / 2, 0, bw, scope.bitmapSize.height);
                ctx.strokeStyle = state.focus.stroke;
                ctx.lineWidth = Math.max(1, hx);
                ctx.beginPath();
                ctx.moveTo(cx - bw / 2, 0);
                ctx.lineTo(cx - bw / 2, scope.bitmapSize.height);
                ctx.moveTo(cx + bw / 2, 0);
                ctx.lineTo(cx + bw / 2, scope.bitmapSize.height);
                ctx.stroke();
              }
            }

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

    // v5 moved markers off the series onto a plugin.
    markers = LWC().createSeriesMarkers(candles, []);
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
  function render(d, tf) {
    if (!chart) return;
    state.data = d;
    if (tf) state.tf = tf;
    const c = colors();
    state.colors = c;
    const LS = LWC().LineStyle;

    // candles is a map of timeframe -> bars
    const series = (d.candles && d.candles[state.tf]) || [];
    if (!series.length) return;

    candles.setData(series.map(b => ({
      time: b.t, open: b.o, high: b.h, low: b.l, close: b.c,
    })));

    /* zones: HTF only, nearest price, oversized ones culled.
       Every timeframe at once is 300+ bands and the chart becomes a smear. */
    const lo = Math.min(...series.map(b => b.l));
    const hi = Math.max(...series.map(b => b.h));
    const span = hi - lo;
    const px = d.meta.price;

    // Levels from EVERY timeframe stay on screen, so switching tabs lets you
    // confirm an HTF level against LTF candles — the whole point of the tool.
    state.zones = d.levels
      .filter(l => state.show[l.type]
                && !(state.strict && l.type === "order_block"
                     && (l.meta || {}).strength === "weak")
                && !state.dropped[l.id]
                && l.top > lo && l.bottom < hi
                && (l.top - l.bottom) < span * 0.3)
      .sort((a, b) => Math.abs((a.top + a.bottom) / 2 - px)
                    - Math.abs((b.top + b.bottom) / 2 - px))
      .slice(0, 18)
      .map(l => ({
        top: l.top, bottom: l.bottom, ts: l.ts, tf: l.tf, type: l.type,
        fill: rgba(l.type === "fvg" ? c.crown : c.cool,
                   l.tf === state.tf ? 0.22 : 0.11),
        stroke: rgba(l.type === "fvg" ? c.crown : c.cool,
                     l.tf === state.tf ? 0.75 : 0.35),
        label: `${l.tf} ${l.type === "order_block" ? "OB" : l.type}`,
      }));

    /* The switches above the chart used to gate ONLY the shaded bands, because
       that filter is the only place state.show was ever read. Everything drawn
       as a price line — asia, CRT, PLP, entry, stop, daily 50% — ignored them,
       so four of the seven checkboxes moved and did nothing at all. One test
       per group, here, is the whole fix. */
    const vis = k => state.show[k] !== false;

    state.asia = (d.asia && vis("asia"))
      ? { top: d.asia.high, bottom: d.asia.low, ts: series[0].t }
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

    if (d.zone && vis("eq")) line(d.zone.eq, c.deep, "eq 50%", LS.Dashed);
    if (d.plp && vis("plp")) line(d.plp.price, c.wattle, "PLP", LS.Dotted);
    if (d.crt && vis("crt")) {
      line(d.crt.eq, c.crown, "crt 50% · T1", LS.Dashed);
      line(d.crt.opposing, c.crown, "crt T2", LS.LargeDashed);
    }
    // Entry and stop belong to the PLP switch — its label says so.
    if (d.model1 && vis("plp")) {
      line(d.model1.entry, c.ink, "entry");
      line(d.model1.stop, c.wattle, "stop", LS.Dashed);
    }
    if (d.asia && vis("asia")) {
      line(d.asia.high, c.soft, "asia H", LS.SparseDotted);
      line(d.asia.low, c.soft, "asia L", LS.SparseDotted);
    }

    /* fitContent() crushed a few thousand bars into a smear against the right
       edge, and it fired on EVERY re-render — so flicking a switch threw away
       wherever you had panned to. Only reset the view when the bars underneath
       actually changed, and open on the recent end, where the decision is. */
    const key = state.tf + ":" + series.length + ":" + series[0].t;
    if (key !== state.viewKey) {
      state.viewKey = key;
      const span = Math.min(series.length, 160);
      chart.timeScale().setVisibleLogicalRange({
        from: series.length - span, to: series.length + 6,
      });
    }
  }

  function subscribeCrosshair(fn) {
    if (chart) chart.subscribeCrosshairMove(fn);
  }

  /** Switch timeframe without refetching. */
  function setTimeframe(tf) {
    if (!state.data) return;
    state.tf = tf;
    render(state.data, tf);
  }

  /** Turn a level type on or off. */
  function toggle(type, on) {
    state.show[type] = on;
    if (state.data) render(state.data);
  }

  /* Strict mode: only draw order blocks whose displacement broke structure.
     The payload always carries both, so this is a filter, not a refetch. */
  function setStrict(on) {
    state.strict = !!on;
    if (state.data) render(state.data);
  }

  /** Order blocks you dropped in the queue, by level id. Store only — the
      page sets this immediately before its own render() call. */
  function setDropped(map) {
    state.dropped = map || {};
  }

  /* Where the focused candle sits on screen right now, in viewport pixels.
     Pierre walks there, so he becomes a third cue for "which candle" on top
     of the marker and the column. Null when nothing is focused or the chart
     has scrolled out of sight — he just goes back to his own business. */
  function focusPoint() {
    if (!chart || !el || !state.focus) return null;
    const x = chart.timeScale().timeToCoordinate(state.focus.ts);
    if (x == null) return null;
    const r = el.getBoundingClientRect();
    if (r.bottom < 40 || r.top > innerHeight - 40) return null;
    const z = state.focus.zone;
    const at = z ? (z.top + z.bottom) / 2 : state.focus.price;
    let y = at != null ? candles.priceToCoordinate(at) : null;
    if (y == null) y = r.height / 2;
    return { x: r.left + x, y: r.top + y };
  }

  /** Scroll to a moment and flash a line at a price — "go and look at this". */
  /* Jump to a confluence AND make it obvious which candle it is.
     Three cues, because one was not enough: a labelled marker on the bar, a
     highlighted column behind it, and the bar's OHLC handed back for the
     readout. A price line alone meant counting bars by eye. */
  /* Jump to a confluence and show it in the shape it actually is.

     A zone (order block, FVG, Asian range) is a REGION: two edges and a
     fill, with the origin candle outlined. An arrow pointing at a region is
     a category error, which is what this used to do.

     A level (swing, PLP, entry, stop) is a single price: a line plus a
     marker on the bar where it formed. */
  function goTo(tf, ts, price, label, zone) {
    if (!state.data) return null;
    if (tf && tf !== state.tf) setTimeframe(tf);

    const api = chart.timeScale();
    const bars = (state.data.candles || {})[state.tf] || [];
    if (!bars.length) return null;

    /* If the moment is not in the window, SAY SO. The old code clamped a
       missing timestamp to bar 0 or the last bar and carried on, so the focus
       bar then described a candle that had nothing to do with the level you
       clicked — specific, confident and wrong. The server now sizes the window
       to cover its own levels, but a deep history can still outrun it. */
    let idx = 0;
    if (ts) {
      if (ts < bars[0].t || ts > bars[bars.length - 1].t) {
        clearFocus();   // don't leave the previous selection lit up
        return { bar: null, offWindow: true, tf: state.tf,
                 first: bars[0].t, last: bars[bars.length - 1].t };
      }
      idx = bars.findIndex(b => b.t >= ts);
      if (idx < 0) idx = bars.length - 1;
      if (idx > 0 && bars[idx].t > ts) idx -= 1;
    }
    const bar = bars[idx];
    const isZone = !!(zone && zone.top != null && zone.bottom != null
                      && zone.top !== zone.bottom);

    const pad = Math.max(14, Math.round(bars.length * 0.10));
    api.setVisibleLogicalRange({ from: idx - pad, to: idx + pad });

    const c = state.colors;
    state.focus = {
      ts: bar.t,
      price: price,
      fill: rgba(c.crown, 0.10),
      stroke: rgba(c.crown, 0.8),
      zone: isZone ? { top: zone.top, bottom: zone.bottom } : null,
    };

    if (state.flash) {
      state.flash.forEach(f => { try { candles.removePriceLine(f); } catch (_) {} });
    }
    state.flash = [];

    if (isZone) {
      // Both edges, because the edges are what you trade against. The
      // midpoint is meaningless for a zone and used to be all it showed.
      state.flash.push(candles.createPriceLine({
        price: zone.top, color: c.crown, lineWidth: 2,
        lineStyle: LWC().LineStyle.Solid, axisLabelVisible: true,
        axisLabelColor: c.crown, axisLabelTextColor: c.panel,
        title: (label || "zone") + " top",
      }));
      state.flash.push(candles.createPriceLine({
        price: zone.bottom, color: c.crown, lineWidth: 2,
        lineStyle: LWC().LineStyle.Solid, axisLabelVisible: true,
        axisLabelColor: c.crown, axisLabelTextColor: c.panel,
        title: (label || "zone") + " base",
      }));
      // No arrow for a region — the box IS the pointer.
      if (markers) markers.setMarkers([]);
    } else {
      if (price != null) {
        state.flash.push(candles.createPriceLine({
          price, color: c.wattle, lineWidth: 2,
          lineStyle: LWC().LineStyle.Solid, axisLabelVisible: true,
          axisLabelColor: c.wattle, axisLabelTextColor: c.panel,
          title: label || "here",
        }));
      }
      const above = price != null ? price >= bar.c : true;
      if (markers) {
        markers.setMarkers([{
          time: bar.t,
          position: above ? "aboveBar" : "belowBar",
          shape: above ? "arrowDown" : "arrowUp",
          color: c.crown,
          text: label || "here",
        }]);
      }
    }

    return { bar, index: idx, tf: state.tf, isZone };
  }

  function clearFocus() {
    state.focus = null;
    if (markers) markers.setMarkers([]);
    if (state.flash) {
      state.flash.forEach(f => { try { candles.removePriceLine(f); } catch (_) {} });
      state.flash = null;
    }
  }

  return { mount, render, theme, subscribeCrosshair, setTimeframe, toggle,
           goTo, clearFocus, setStrict, setDropped, focusPoint,
           tf: () => state.tf, series: () => candles };
})();
