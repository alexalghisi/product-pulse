/** Round axis maximum and evenly spaced ticks: 0, 2k, 4k ... rather than 0, 1873, 3746. */
export function niceTicks(max: number, count = 4): number[] {
    if (!(max > 0)) return [0, 1];
    const rough = max / count;
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const residual = rough / magnitude;
    const step = (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1) * magnitude;
    const ticks: number[] = [];
    for (let t = 0; t < max + step * 0.999; t += step) ticks.push(Number(t.toPrecision(12)));
    return ticks;
}

export function linear(domain: [number, number], range: [number, number]): (v: number) => number {
    const [d0, d1] = domain;
    const [r0, r1] = range;
    const span = d1 - d0 || 1;
    return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** Indices of at most `max` evenly spread labels, always including the first and last. */
export function labelIndices(length: number, max: number): number[] {
    if (length <= max) return Array.from({ length }, (_, i) => i);
    const step = Math.ceil((length - 1) / (max - 1));
    const out: number[] = [];
    for (let i = 0; i < length - 1; i += step) out.push(i);
    if (length - 1 - (out[out.length - 1] ?? 0) < step / 2) out.pop();
    out.push(length - 1);
    return out;
}
