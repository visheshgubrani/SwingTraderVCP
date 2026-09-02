import React from "react";
import { View, type ViewProps } from "react-native";
import { clsx } from "clsx";

export interface CenterProps extends ViewProps {
  className?: string;
  children?: React.ReactNode;
}

export const Center = React.forwardRef<View, CenterProps>(function Center(
  { className, children, ...props },
  ref
) {
  return (
    <View
      ref={ref}
      className={clsx("justify-center items-center", className)}
      {...props}
    >
      {children}
    </View>
  );
});

Center.displayName = "Center";
