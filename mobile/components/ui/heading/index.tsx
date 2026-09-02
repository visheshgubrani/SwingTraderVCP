import React from "react";
import { Text as RNText, type TextProps as RNTextProps } from "react-native";
import { clsx } from "clsx";

export type HeadingSize =
  | "xs"
  | "sm"
  | "md"
  | "lg"
  | "xl"
  | "2xl"
  | "3xl"
  | "4xl"
  | "5xl";

export interface HeadingProps extends RNTextProps {
  size?: HeadingSize;
  bold?: boolean;
  isTruncated?: boolean;
  className?: string;
  children?: React.ReactNode;
}

const headingSizeClasses: Record<HeadingSize, string> = {
  xs: "text-sm font-semibold leading-5",
  sm: "text-base font-semibold leading-6",
  md: "text-lg font-semibold leading-7",
  lg: "text-xl font-bold leading-7",
  xl: "text-2xl font-bold leading-8",
  "2xl": "text-3xl font-bold leading-9",
  "3xl": "text-4xl font-extrabold leading-10",
  "4xl": "text-5xl font-extrabold leading-none",
  "5xl": "text-6xl font-extrabold leading-none",
};

export const Heading = React.forwardRef<RNText, HeadingProps>(function Heading(
  {
    size = "lg",
    bold = true,
    isTruncated = false,
    className,
    children,
    numberOfLines,
    ...props
  },
  ref
) {
  return (
    <RNText
      ref={ref}
      numberOfLines={isTruncated ? 1 : numberOfLines}
      className={clsx(
        "text-foreground",
        headingSizeClasses[size],
        !bold && "font-normal",
        className
      )}
      {...props}
    >
      {children}
    </RNText>
  );
});

Heading.displayName = "Heading";
