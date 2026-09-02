import React from "react";
import { View, type ViewProps } from "react-native";
import { clsx } from "clsx";

export type StackSpace =
  | "xs"
  | "sm"
  | "md"
  | "lg"
  | "xl"
  | "2xl"
  | "3xl"
  | "4xl";

export interface VStackProps extends ViewProps {
  space?: StackSpace;
  reversed?: boolean;
  className?: string;
  children?: React.ReactNode;
}

const spaceClasses: Record<StackSpace, string> = {
  xs: "gap-1",
  sm: "gap-2",
  md: "gap-3",
  lg: "gap-4",
  xl: "gap-5",
  "2xl": "gap-6",
  "3xl": "gap-7",
  "4xl": "gap-8",
};

export const VStack = React.forwardRef<View, VStackProps>(function VStack(
  { space = "md", reversed = false, className, children, ...props },
  ref
) {
  return (
    <View
      ref={ref}
      className={clsx(
        "flex-col",
        reversed && "flex-col-reverse",
        space && spaceClasses[space],
        className
      )}
      {...props}
    >
      {children}
    </View>
  );
});

VStack.displayName = "VStack";
