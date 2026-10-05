import { formatBucket, formatNumber, formatPercent, seriesLabel } from "./format";

it("formats counts compactly", () => {
    expect(formatNumber(950)).toBe("950");
    expect(formatNumber(1234)).toBe("1,234");
    expect(formatNumber(23_400)).toBe("23.4k");
    expect(formatNumber(1_954_841)).toBe("2M");
    expect(formatNumber(11.833)).toBe("11.83");
});

it("formats rates", () => {
    expect(formatPercent(0.6667)).toBe("67%");
    expect(formatPercent(0.0412)).toBe("4.1%");
    expect(formatPercent(0)).toBe("0%");
});

it("labels buckets in UTC", () => {
    expect(formatBucket("2026-06-01T00:00:00Z", "day")).toBe("1 Jun");
    expect(formatBucket("2026-06-01T13:00:00Z", "hour")).toBe("1 Jun 13:00");
    expect(formatBucket("2026-06-01T00:00:00Z", "month")).toBe("Jun 2026");
});

it("names special breakdown values", () => {
    expect(seriesLabel("$pageview", "total", null)).toBe("$pageview");
    expect(seriesLabel("$pageview", "unique_persons", null)).toBe("$pageview (unique persons)");
    expect(seriesLabel("$pageview", "total", "$none")).toBe("(not set)");
    expect(seriesLabel("$pageview", "total", "$other")).toBe("Other");
});
