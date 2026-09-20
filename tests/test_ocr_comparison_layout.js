const test = require("node:test");
const assert = require("node:assert/strict");
const { chooseOcrLeader, assignOcrLeaderLanes, positionOcrTags, updateOcrVisibility } =
  require("../src/reservoir_data_translator/ui/ocr-comparison-layout.js");

test("visibility switches preserve their specified coupling", () => {
  let state = { boundary: true, number: true, type: true };
  state = updateOcrVisibility(state, "type", false);
  assert.deepEqual(state, { boundary: true, number: true, type: false });
  state = updateOcrVisibility(state, "boundary", false);
  assert.deepEqual(state, { boundary: false, number: false, type: false });
  state = updateOcrVisibility(state, "boundary", true);
  assert.deepEqual(state, { boundary: true, number: false, type: false });
  state = updateOcrVisibility(state, "type", true);
  assert.deepEqual(state, { boundary: true, number: false, type: true });
});

test("leader chooses a clear horizontal entry and switches rails only when necessary", () => {
  const target = { bbox: [100, 20, 180, 80] };
  assert.equal(chooseOcrLeader(target, [target], 200).side, "left");
  const leftObstacle = { bbox: [0, 10, 90, 90] };
  assert.equal(chooseOcrLeader(target, [target, leftObstacle], 200).side, "right");
  const shortObstacle = { bbox: [0, 20, 90, 40] };
  const route = chooseOcrLeader(target, [target, shortObstacle], 200);
  assert.equal(route.side, "left");
  assert.ok(route.y > shortObstacle.bbox[3] && route.y < target.bbox[3]);
});

test("nearby leaders use separate entry heights when their boxes allow it", () => {
  const first = { bbox: [90, 20, 160, 70] };
  const second = { bbox: [90, 20, 160, 70] };
  const firstRoute = chooseOcrLeader(first, [first, second], 200);
  const secondRoute = chooseOcrLeader(second, [first, second], 200,
    [firstRoute]);
  assert.equal(firstRoute.side, secondRoute.side);
  assert.ok(Math.abs(firstRoute.y - secondRoute.y) >= 4);
});

test("overlapping leader stems use distant lanes and clear stems reuse lanes", () => {
  const routes = [
    { side: "left", anchor: 35, tagY: 10 },
    { side: "left", anchor: 40, tagY: 15 },
    { side: "left", anchor: 150, tagY: 125 },
    { side: "right", anchor: 38, tagY: 12 },
  ];
  assignOcrLeaderLanes(routes, "left", 20, 1);
  assignOcrLeaderLanes(routes, "right", 20, 1);
  assert.ok(Math.abs(routes[0].lane - routes[1].lane) >= 2);
  assert.equal(routes[2].lane, 0);
  assert.equal(routes[3].lane, 0);
});

test("tags stay ordered and separate in one fixed rail", () => {
  const routes = [
    { side: "left", anchor: 40 },
    { side: "left", anchor: 43 },
    { side: "left", anchor: 46 },
  ];
  const height = positionOcrTags(routes, "left", 100, 20, 4);
  assert.equal(height, 100);
  assert.ok(routes[0].tagY + 24 <= routes[1].tagY);
  assert.ok(routes[1].tagY + 24 <= routes[2].tagY);
  assert.ok(routes.every(route => route.tagY >= 0 && route.tagY + 20 <= height));
});
