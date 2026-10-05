import { useEffect, useRef, useState } from "react";

export function useWidth<T extends HTMLElement>(fallback = 800) {
    const ref = useRef<T>(null);
    const [width, setWidth] = useState(fallback);
    useEffect(() => {
        const node = ref.current;
        if (!node || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(([entry]) => {
            if (entry) setWidth(Math.max(280, Math.floor(entry.contentRect.width)));
        });
        observer.observe(node);
        return () => observer.disconnect();
    }, []);
    return [ref, width] as const;
}
