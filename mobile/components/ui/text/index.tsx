import React from "react";
import { Text as RNText, type TextProps as RNTextProps } from "react-native";
import { clsx } from "clsx";

export type TextSize =
  | "2xs"
  | "xs"
  | "sm"
  | "md"
  | "lg"
  | "xl"
  | "2xl"
  | "3xl"
  | "4xl"
  | "5xl"
  | "6xl";

export interface TextProps extends RNTextProps {
  size?: TextSize;
  bold?: boolean;
  isTruncated?: boolean;
  className?: string;
  children?: React.ReactNode;
}

const sizeClasses: Record<TextSize, string> = {
  "2xs": "text-[10px] leading-[14px]",
  xs: "text-xs leading-4",
  sm: "text-sm leading-5",
  md: "text-base leading-6",
  lg: "text-lg leading-7",
  xl: "text-xl leading-7",
  "2xl": "text-2xl leading-8",
  "3xl": "text-3xl leading-9",
  "4xl": "text-4xl leading-10",
  "5xl": "text-5xl leading-none",
  "6xl": "text-6xl leading-none",
};

export const Text = React.forwardRef<RNText, TextProps>(function Text(
  {
    size = "md",
    bold = false,
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
        "text-foreground font-normal",
        sizeClasses[size],
        bold && "font-bold",
        className
      )}
      {...props}
    >
      {children}
    </RNText>
  );
});

Text.displayName = "Text";
