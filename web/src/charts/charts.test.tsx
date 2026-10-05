import { fireEvent, render, screen, within } from "@testing-library/react";
import { colorTrendLines, lastBucketIsPartial } from "../components/trendLines";
import { funnel, funnelByPlan, retention, trend } from "../test/fixtures";
import { FunnelChart } from "./FunnelChart";
import { LineChart } from "./LineChart";
import { RetentionTable } from "./RetentionTable";

describe("LineChart", () => {
    it("draws one line per series, keeps Other neutral and shows a legend", () => {
        const lines = colorTrendLines(trend);
        render(<LineChart buckets={trend.buckets} interval="day" lines={lines} />);
        expect(screen.getAllByTestId("trend-line")).toHaveLength(2);
        expect(lines[1]?.color).toBe("var(--series-other)");
        expect(screen.getByText("Other")).toBeInTheDocument();
    });

    it("shows every series value for the hovered bucket", () => {
        const { container } = render(<LineChart buckets={trend.buckets} interval="day" lines={colorTrendLines(trend)} />);
        const overlay = container.querySelector("rect[fill='transparent']");
        if (!overlay) throw new Error("no hover layer");
        overlay.getBoundingClientRect = () => ({ left: 0, width: 100, top: 0, height: 100, right: 100, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });
        fireEvent.mouseMove(overlay, { clientX: 0 });
        const tip = screen.getByRole("tooltip");
        expect(within(tip).getByText("1 Jun")).toBeInTheDocument();
        expect(within(tip).getByText("3")).toBeInTheDocument();
    });

    it("dashes the last segment while its bucket is still filling up", () => {
        const partial = { ...trend, date_to: "2026-06-03T10:00:00Z" };
        expect(lastBucketIsPartial(trend)).toBe(false);
        expect(lastBucketIsPartial(partial)).toBe(true);
        render(<LineChart buckets={trend.buckets} interval="day" lines={colorTrendLines(trend)} partialLast />);
        expect(screen.getAllByTestId("partial-segment")).toHaveLength(2);
    });
});

describe("FunnelChart", () => {
    it("labels conversion from the first step", () => {
        render(<FunnelChart result={funnel} />);
        expect(screen.getAllByTestId("funnel-bar")).toHaveLength(3);
        const labels = [...document.querySelectorAll("svg text.bar-label")].map((t) => t.textContent);
        expect(labels).toEqual(["100%", "67%", "33%"]);
    });

    it("groups bars by breakdown value with a legend and table rows", () => {
        render(<FunnelChart result={funnelByPlan} />);
        expect(screen.getAllByTestId("funnel-bar")).toHaveLength(6);
        const rows = screen.getAllByRole("row").slice(1);
        expect(rows.map((r) => r.firstChild?.textContent)).toEqual(["free", "pro"]);
    });

    it("explains a bar on hover", () => {
        render(<FunnelChart result={funnel} />);
        const bar = screen.getAllByTestId("funnel-bar")[1];
        if (!bar) throw new Error("missing bar");
        fireEvent.mouseEnter(bar);
        expect(within(screen.getByRole("tooltip")).getByText("From previous")).toBeInTheDocument();
    });
});

it("RetentionTable leaves periods that haven't started blank", () => {
    render(<RetentionTable result={retention} />);
    expect(screen.getByLabelText("not yet")).toBeEmptyDOMElement();
    expect(screen.getAllByText("100%")).toHaveLength(2);
    expect(screen.getByText("Day 3")).toBeInTheDocument();
});
