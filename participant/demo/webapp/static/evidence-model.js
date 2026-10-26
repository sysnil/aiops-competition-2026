(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AIOpsEvidenceModel = api;
})(typeof window === "undefined" ? globalThis : window, function () {
  "use strict";
  function finite(value) { return value !== null && value !== "" && value !== undefined && Number.isFinite(Number(value)); }
  function time(value) { const n = typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : Date.parse(value); return Number.isFinite(n) ? n : null; }
  function labels(value) {
    if (typeof value === "string") { try { value = JSON.parse(value); } catch (_) { return value; } }
    return value && typeof value === "object" ? JSON.stringify(Object.keys(value).sort().map(k => [k, value[k]])) : "";
  }
  function metricFamilies(rows) { return [...new Set(rows.filter(r => r.metric_name).map(r => r.metric_name))].sort(); }
  function metricSeries(rows, family) {
    const groups = new Map();
    for (const r of rows) {
      if (r.metric_name !== family || !finite(r.value)) continue;
      const t = time(r.timestamp || r.time); if (t === null) continue;
      const entity = r.pod || r.node || r.service || r.service_name || r.entity || "UNATTRIBUTED";
      const key = JSON.stringify([entity, labels(r.labels), r.unit || ""]);
      if (!groups.has(key)) groups.set(key, {name: entity, unit: r.unit || "", points: new Map()});
      groups.get(key).points.set(t, Number(r.value));
    }
    return [...groups.values()].map(g => ({name: g.name, unit: g.unit, data: [...g.points].sort((a,b) => a[0]-b[0])}));
  }
  function traceGroups(rows) {
    const groups = new Map();
    for (const r of rows) {
      const start = time(r.start_time || r.timestamp);
      if (!r.trace_id || !r.span_id || start === null || !finite(r.duration_ms) || Number(r.duration_ms) < 0) continue;
      if (!groups.has(r.trace_id)) groups.set(r.trace_id, new Map());
      groups.get(r.trace_id).set(r.span_id, {...r, start_ms: start, duration_ms: Number(r.duration_ms)});
    }
    return [...groups].map(([trace_id, spans]) => ({trace_id, spans: [...spans.values()].sort((a,b) => a.start_ms-b.start_ms)}));
  }
  function waterfall(group) {
    if (!group || !group.spans.length) return {rows: [], origin_ms: null, end_ms: 0};
    const all = new Map(group.spans.map(s => [s.span_id, s])), visited = new Set(), ordered = [];
    const origin = Math.min(...group.spans.map(s => s.start_ms));
    function visit(span, depth) {
      if (visited.has(span.span_id)) return;
      visited.add(span.span_id); ordered.push({...span, depth, offset_ms: span.start_ms-origin, parent_missing: !!span.parent_span_id && !all.has(span.parent_span_id)});
      group.spans.filter(s => s.parent_span_id === span.span_id).forEach(s => visit(s, depth+1));
    }
    group.spans.filter(s => !s.parent_span_id || !all.has(s.parent_span_id)).forEach(s => visit(s,0));
    group.spans.forEach(s => visit(s,0)); // Bound malformed/cyclic parent relations.
    return {rows: ordered, origin_ms: origin, end_ms: Math.max(...ordered.map(s => s.offset_ms+s.duration_ms))};
  }
  function topologyForRun(own, candidate, selectedCase, runCase) {
    return candidate && (!runCase || selectedCase===runCase) && (candidate.nodes||[]).length ? candidate : own;
  }
  return {metricFamilies, metricSeries, traceGroups, waterfall, topologyForRun};
});
