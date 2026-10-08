"use strict";

// Geometry is in top-left PDF points; only the final drawing uses CSS pixels.
function chooseOcrLeader(region, regions, pageWidth, placed = []) {
  const [x0, top, x1, bottom] = region.bbox;
  const margin = Math.min(1, (bottom - top) / 4);
  const preferred = Math.min(bottom - margin, top + Math.min(9, (bottom - top) / 2));
  const options = [];
  for (const side of ["left", "right"]) {
    const blockers = regions.filter(other => other !== region &&
      (side === "left" ? other.bbox[0] < x0 && other.bbox[2] > 0 :
        other.bbox[2] > x1 && other.bbox[0] < pageWidth));
    const candidates = [preferred, top + margin, bottom - margin];
    for (const offset of [-16, -12, -8, -4, 4, 8, 12, 16]) {
      candidates.push(preferred + offset);
    }
    blockers.forEach(other => candidates.push(other.bbox[1] - 1, other.bbox[3] + 1));
    for (const y of candidates) {
      if (y < top + margin || y > bottom - margin) continue;
      const crossings = blockers.filter(other => other.bbox[1] <= y && y <= other.bbox[3]).length;
      const proximity = placed.filter(route => route.side === side)
        .reduce((total, route) => total + Math.max(0, 8 - Math.abs(route.y - y)), 0);
      options.push({ side, y, crossings, proximity, distance: Math.abs(y - preferred) });
    }
  }
  const rightPenalty = Math.min(12, (bottom - top) / 4);
  options.sort((a, b) => a.crossings - b.crossings ||
    (a.distance + a.proximity * 2 + (a.side === "right" ? rightPenalty : 0)) -
    (b.distance + b.proximity * 2 + (b.side === "right" ? rightPenalty : 0)) ||
    (a.side === b.side ? 0 : a.side === "left" ? -1 : 1));
  return options[0] || { side: "left", y: preferred };
}

// Put nearby vertical leader segments on different tracks in the blank page margin.
function assignOcrLeaderLanes(routes, side, tagHeight, zoom, laneCount = 5) {
  const placed = [];
  const nearby = 8 * zoom;
  const ordered = routes.filter(route => route.side === side)
    .sort((a, b) => Math.min(a.anchor, a.tagY + tagHeight / 2) -
      Math.min(b.anchor, b.tagY + tagHeight / 2));
  for (const route of ordered) {
    const center = route.tagY + tagHeight / 2;
    const low = Math.min(center, route.anchor);
    const high = Math.max(center, route.anchor);
    let bestLane = 0;
    let bestScore = Infinity;
    for (let lane = 0; lane < laneCount; lane++) {
      let score = lane * 0.01;
      for (const other of placed) {
        if (high + nearby < other.low || other.high + nearby < low) continue;
        const distance = Math.abs(lane - other.lane);
        score += distance === 0 ? 100 : 10 / distance;
      }
      if (score < bestScore) {
        bestScore = score;
        bestLane = lane;
      }
    }
    route.lane = bestLane;
    placed.push({ low, high, lane: bestLane });
  }
}

function positionOcrTags(routes, side, pageHeight, tagHeight, gap) {
  const ordered = routes.filter(route => route.side === side).sort((a, b) => a.anchor - b.anchor);
  const requiredHeight = ordered.length * (tagHeight + gap) + gap;
  const height = Math.max(pageHeight, requiredHeight);
  let next = gap;
  for (const route of ordered) {
    route.tagY = Math.max(next, Math.min(route.anchor - tagHeight / 2, height - tagHeight - gap));
    next = route.tagY + tagHeight + gap;
  }
  let ceiling = height - tagHeight - gap;
  for (let i = ordered.length - 1; i >= 0; i--) {
    ordered[i].tagY = Math.min(ordered[i].tagY, ceiling);
    ceiling = ordered[i].tagY - tagHeight - gap;
  }
  return height;
}

function updateOcrVisibility(current, key, checked) {
  const next = { ...current, [key]: checked };
  if (key === "boundary" && !checked) {
    next.number = false;
    next.type = false;
  } else if (checked && key !== "boundary") {
    next.boundary = true;
  }
  return next;
}

const OcrComparisonLayout = {
  chooseOcrLeader, assignOcrLeaderLanes, positionOcrTags, updateOcrVisibility,
};
if (typeof module !== "undefined" && module.exports) module.exports = OcrComparisonLayout;
