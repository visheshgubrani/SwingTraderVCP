import React from "react";
import Svg, { SvgProps, Path } from "react-native-svg";
import { clsx } from "clsx";

export type IconSize =
  | "2xs"
  | "xs"
  | "sm"
  | "md"
  | "lg"
  | "xl"
  | "2xl";

const sizeMap: Record<IconSize, number> = {
  "2xs": 12,
  xs: 14,
  sm: 16,
  md: 20,
  lg: 24,
  xl: 28,
  "2xl": 32,
};

export interface IconProps {
  as: React.ComponentType<any>;
  size?: IconSize | number;
  color?: string;
  className?: string;
  [key: string]: any;
}

export function Icon({
  as: Component,
  size = "md",
  color,
  className,
  ...props
}: IconProps) {
  const pixelSize = typeof size === "number" ? size : sizeMap[size] ?? 20;

  return (
    <Component
      width={pixelSize}
      height={pixelSize}
      size={pixelSize}
      color={color}
      className={clsx("text-foreground", className)}
      {...props}
    />
  );
}

export interface CreateIconOptions {
  viewBox?: string;
  path?: React.ReactNode;
  d?: string;
  displayName?: string;
}

export function createIcon({
  viewBox = "0 0 24 24",
  path,
  d,
  displayName = "CustomIcon",
}: CreateIconOptions) {
  const IconComponent = React.forwardRef<any, SvgProps>(function IconComponent(
    { width = 24, height = 24, color = "currentColor", fill = "none", stroke = "currentColor", strokeWidth = 2, strokeLinecap = "round", strokeLinejoin = "round", ...props },
    ref
  ) {
    return (
      <Svg
        ref={ref}
        viewBox={viewBox}
        width={width}
        height={height}
        fill={fill}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap={strokeLinecap}
        strokeLinejoin={strokeLinejoin}
        {...props}
      >
        {path ?? (d ? <Path d={d} /> : null)}
      </Svg>
    );
  });

  IconComponent.displayName = displayName;
  return IconComponent;
}

// Pre-built common icons
export const ChevronRightIcon = createIcon({
  d: "M9 18l6-6-6-6",
  displayName: "ChevronRightIcon",
});

export const ChevronDownIcon = createIcon({
  d: "M6 9l6 6 6-6",
  displayName: "ChevronDownIcon",
});

export const ChevronLeftIcon = createIcon({
  d: "M15 18l-6-6 6-6",
  displayName: "ChevronLeftIcon",
});

export const SearchIcon = createIcon({
  d: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z",
  displayName: "SearchIcon",
});

export const CheckIcon = createIcon({
  d: "M20 6L9 17l-5-5",
  displayName: "CheckIcon",
});

export const XIcon = createIcon({
  d: "M18 6L6 18M6 6l12 12",
  displayName: "XIcon",
});

export const AlertCircleIcon = createIcon({
  d: "M12 8v4m0 4h.01M22 12c0 5.523-4.477 10-10 10S2 17.523 2 12 6.477 2 12 2s10 4.477 10 10z",
  displayName: "AlertCircleIcon",
});
