import React from "react";
import { View, type ViewProps } from "react-native";
import { clsx } from "clsx";
import { StackSpace } from "../vstack";

export interface HStackProps extends ViewProps {
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

export const HStack = React.forwardRef<View, HStackProps>(function HStack(
  { space = "md", reversed = false, className, children, ...props },
  ref
) {
  return (
    <View
      ref={ref}
      className={clsx(
        "flex-row items-center",
        reversed && "flex-row-reverse",
        space && spaceClasses[space],
        className
      )}
      {...props}
    >
      {children}
    </View>
  );
});

HStack.displayName = "HStack";
