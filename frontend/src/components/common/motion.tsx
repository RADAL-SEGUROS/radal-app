import * as React from "react";
import {
  AnimatePresence,
  motion,
  useReducedMotion,
  type HTMLMotionProps,
  type Variants,
} from "framer-motion";

/**
 * Shared motion system for Radal. One small set of primitives so entrance
 * animation, staggering and hover micro-interactions stay consistent app-wide.
 * All primitives respect prefers-reduced-motion.
 */

const EASE = [0.22, 0.72, 0.24, 1] as const;

/** Container that staggers its FadeUp children. */
export const staggerContainer: Variants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.06, delayChildren: 0.04 },
  },
};

/** Item variant: fade + rise. Pairs with staggerContainer. */
export const fadeUpItem: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.5, ease: EASE },
  },
};

/** Dropdown/menu variant. */
export const dropIn: Variants = {
  hidden: { opacity: 0, y: -6, scale: 0.985 },
  show: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.18, ease: "easeOut" } },
  exit: { opacity: 0, y: -6, scale: 0.985, transition: { duration: 0.12, ease: "easeIn" } },
};

interface FadeUpProps extends HTMLMotionProps<"div"> {
  /** Explicit entrance delay in seconds (used outside a Stagger container). */
  delay?: number;
  as?: "div" | "section" | "li";
}

/**
 * Fade + rise on mount. When rendered inside a <Stagger>, it inherits the
 * staggered timing automatically (variants propagate); when standalone, pass
 * `delay` for a manual cascade.
 */
export const FadeUp = React.forwardRef<HTMLDivElement, FadeUpProps>(
  ({ delay, children, as = "div", ...props }, ref) => {
    const reduce = useReducedMotion();
    const MotionTag = motion[as] as typeof motion.div;
    if (reduce) {
      return (
        <MotionTag ref={ref as never} {...props}>
          {children}
        </MotionTag>
      );
    }
    // Standalone (with explicit delay) vs. inside a <Stagger> (variant-driven).
    const motionProps =
      delay !== undefined
        ? {
            initial: { opacity: 0, y: 10 },
            animate: { opacity: 1, y: 0 },
            transition: { duration: 0.5, ease: EASE, delay },
          }
        : {
            variants: fadeUpItem,
            initial: "hidden" as const,
            animate: "show" as const,
          };
    return (
      <MotionTag ref={ref as never} {...motionProps} {...props}>
        {children}
      </MotionTag>
    );
  },
);
FadeUp.displayName = "FadeUp";

interface StaggerProps extends HTMLMotionProps<"div"> {
  as?: "div" | "section";
}

/** Container that triggers a staggered FadeUp cascade for its children. */
export const Stagger = React.forwardRef<HTMLDivElement, StaggerProps>(
  ({ children, as = "div", ...props }, ref) => {
    const reduce = useReducedMotion();
    const MotionTag = motion[as] as typeof motion.div;
    if (reduce) {
      return (
        <MotionTag ref={ref as never} {...props}>
          {children}
        </MotionTag>
      );
    }
    return (
      <MotionTag
        ref={ref as never}
        variants={staggerContainer}
        initial="hidden"
        animate="show"
        {...props}
      >
        {children}
      </MotionTag>
    );
  },
);
Stagger.displayName = "Stagger";

/** Standard hover-lift props for cards. */
export const hoverLift = {
  whileHover: { y: -3 },
  transition: { duration: 0.2, ease: "easeOut" },
} as const;

/** Standard hover-lift props for buttons (subtler). */
export const hoverLiftSm = {
  whileHover: { y: -1 },
  whileTap: { y: 0 },
  transition: { duration: 0.16, ease: "easeOut" },
} as const;

interface CountUpProps {
  value: number;
  /** Formatter applied to the interpolated value each frame. */
  format?: (n: number) => string;
  /** Duration in ms. */
  duration?: number;
  className?: string;
}

/**
 * Counts a number up from 0 to `value` on mount (~1s easeOut).
 * Respects prefers-reduced-motion (renders the final value immediately).
 */
export function CountUp({
  value,
  format = (n) => Math.round(n).toLocaleString("de-DE"),
  duration = 1000,
  className,
}: CountUpProps) {
  const reduce = useReducedMotion();
  const [display, setDisplay] = React.useState(() =>
    reduce ? value : 0,
  );

  React.useEffect(() => {
    if (reduce) {
      setDisplay(value);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const step = (now: number) => {
      let p = Math.min(1, (now - start) / duration);
      p = 1 - Math.pow(1 - p, 3); // easeOutCubic
      setDisplay(value * p);
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, duration, reduce]);

  return <span className={className}>{format(display)}</span>;
}

/** UF formatter matching the reference: "UF 4.540" (de-DE grouping, rounded). */
export function formatUFCompact(n: number): string {
  return "UF " + Math.round(n).toLocaleString("de-DE");
}

/**
 * Animates a content swap keyed by `swapKey`.
 *
 * Radix tabs already animate themselves (see `components/ui/tabs.tsx`), but a
 * lot of the app changes content WITHOUT remounting: picking another vigencia,
 * flipping a sub-view, paging a table. Those used to blink from one state to
 * the next with nothing in between. Wrap them in <Swap swapKey={value}> and the
 * outgoing content leaves before the incoming one arrives.
 *
 * `mode="wait"` (default) is right when the two states share the same space —
 * two vigencias in one panel. Use `mode="popLayout"` when the height changes a
 * lot and you would rather not collapse to zero mid-transition.
 */
export function Swap({
  swapKey,
  children,
  className,
  mode = "wait",
  /** Vertical travel in px. 0 gives a pure cross-fade. */
  distance = 8,
}: {
  swapKey: React.Key;
  children: React.ReactNode;
  className?: string;
  mode?: "wait" | "popLayout" | "sync";
  distance?: number;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  return (
    <AnimatePresence mode={mode} initial={false}>
      <motion.div
        key={swapKey}
        className={className}
        initial={{ opacity: 0, y: distance }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -distance }}
        transition={{ duration: 0.2, ease: EASE }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}

/**
 * Route-level transition: the same idea as <Swap>, keyed on the pathname, for
 * pages that replace each other inside a persistent shell (the group hub, the
 * account journey). Keeps the sidebar still while the panel moves.
 */
export function RouteSwap({
  routeKey,
  children,
  className,
}: {
  routeKey: React.Key;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Swap swapKey={routeKey} className={className} distance={10}>
      {children}
    </Swap>
  );
}
