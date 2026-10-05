import { labelIndices, linear, niceTicks } from "./scale";

describe("niceTicks", () => {
    it("rounds to 1/2/5 steps and covers the maximum", () => {
        expect(niceTicks(1873)).toEqual([0, 500, 1000, 1500, 2000]);
        expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8]);
        expect(niceTicks(0.42)).toEqual([0, 0.2, 0.4, 0.6]);
    });
    it("handles an empty series", () => {
        expect(niceTicks(0)).toEqual([0, 1]);
    });
});

it("linear maps domain to range, including inverted ranges", () => {
    const y = linear([0, 100], [200, 0]);
    expect(y(0)).toBe(200);
    expect(y(50)).toBe(100);
});

it("labelIndices spreads labels and keeps both ends", () => {
    expect(labelIndices(5, 10)).toEqual([0, 1, 2, 3, 4]);
    const picked = labelIndices(90, 7);
    expect(picked[0]).toBe(0);
    expect(picked[picked.length - 1]).toBe(89);
    expect(picked.length).toBeLessThanOrEqual(7);
});
